"""Actual isolated imports and learned inference before managed registration."""

import json
import os
import subprocess
import tempfile
from time import monotonic

from shared.models import ActorState, AgentObservation, AgentReset, AgentRoadRaster, AgentSceneContext, Transform, Vector3
from simulator.agents.process import ProcessAgent, AgentProcessOptions
from simulator.agents.process_group import AgentProcessGroup
from .model_assets import AssetError, _check_pause


def probe(environment, cancelled=lambda: False):
    # A controlled subprocess checks dependency closure and import origins.
    # Output goes to an owned temporary file, never unbounded PIPE buffers.
    code = '''import importlib.metadata as m, json, pathlib, sys
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
site = pathlib.Path(sys.executable).parent / 'site-packages'
installed = {canonicalize_name(d.metadata['Name']): d for d in m.distributions(path=[str(site)])}
for name, distribution in installed.items():
 for raw in distribution.requires or []:
  r = Requirement(raw)
  if r.marker and not r.marker.evaluate({'extra': ''}): continue
  key = canonicalize_name(r.name)
  if key not in installed or installed[key].version not in r.specifier: raise RuntimeError('Incomplete pinned dependency closure: ' + name + ' -> ' + raw)
import torch, numpy, scipy, timm, transformers, omegaconf, pydantic
for module in (torch, numpy, scipy, timm, transformers, omegaconf, pydantic):
 if not pathlib.Path(module.__file__).is_relative_to(site): raise RuntimeError('Model dependency escaped its capsule')
assert sys.version_info[:3] == (3,12,10)
assert torch.version.cuda is None
assert not any('site-packages' in p and not pathlib.Path(p).is_relative_to(site) for p in sys.path)
print(json.dumps({'python': sys.version.split()[0], 'torch': torch.__version__, 'device': 'cpu', 'packages': {k:d.version for k,d in installed.items()}}))
'''
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}}
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen([str(environment.python), "-I", "-B", "-c", code],
            cwd=environment.directory, env=env, stdout=output, stderr=output, stdin=subprocess.DEVNULL,
            creationflags=(subprocess.CREATE_NO_WINDOW | 0x00000004) if os.name == "nt" else 0,
            start_new_session=os.name != "nt")
        group = None
        try:
            group = AgentProcessGroup(process)
            group.resume()
            deadline = monotonic() + 180
            while process.poll() is None:
                _check_pause(cancelled)
                if monotonic() >= deadline:
                    raise AssetError("Model imports exceeded the 180 second compatibility budget")
                try:
                    process.wait(timeout=.1)
                except subprocess.TimeoutExpired:
                    pass
            output.seek(0)
            data = output.read(128 * 1024)
            if process.returncode != 0:
                raise AssetError("Model dependencies could not import. Use Repair environment. " + data.decode("utf-8", errors="replace")[-2000:])
            imports = json.loads(data.decode("utf-8").strip().splitlines()[-1])
        finally:
            if group is not None:
                group.terminate()
            elif process.poll() is None:
                process.kill()
            process.wait(timeout=5)
    agent = ProcessAgent(environment.package, python_executable=str(environment.python),
                         options=AgentProcessOptions(load_timeout_s=180., reset_timeout_s=30., step_timeout_s=15.))
    agent.set_cancellation(cancelled)
    try:
        agent.load(agent.package.manifest.configuration)
        agent.reset(AgentReset(protocol_version="1.2", seed=7, map_name="synthetic", ego_actor_id="ego", fixed_delta_seconds=.05))
        pose = lambda x: Transform(position=Vector3(x=float(x)))
        observed = AgentObservation(protocol_version="1.2", frame=1, time_s=0., dt_s=.05,
            ego=ActorState(actor_id="ego", transform=pose(0), velocity_mps=Vector3(x=2.)), actors=[], target_speed_mps=5.,
            scene=AgentSceneContext(source="synthetic", route=[pose(x) for x in range(41)], objects=[], speed_limit_mps=50/3.6,
                road_raster=AgentRoadRaster(width=128, height=128, pixels_per_meter=2., classes=[1] * 16384, ego_row=64., ego_column=64.)))
        result = agent.step(observed)
        metadata = result.diagnostics.metadata
        if metadata["device"] != "cpu" or metadata["inference_wall_time_s"] <= 0:
            raise AssetError("The managed model did not produce a measured CPU inference")
        return {"imports": imports, "checkpoint_sha256": metadata["checkpoint_sha256"],
                "inference_wall_time_s": metadata["inference_wall_time_s"], "control": result.control.model_dump(mode="json"),
                "scope": "Actual pretrained inference from a synthetic observation; driving and real-time performance are separate checks."}
    finally:
        agent.close()
