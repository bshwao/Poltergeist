"""Worker source identity must not be replaced by timestamp-valid stale .pyc."""

import importlib.util
import json
import os
from pathlib import Path
import py_compile
import shutil
import sys
from tempfile import TemporaryDirectory

import pytest

from shared.models import ActorState, AgentObservation, AgentRegistration, AgentReset
from simulator.agents.fingerprints import build_agent_snapshot, verify_agent_snapshot
from simulator.agents.process import AgentProcessOptions, AgentTimeoutError, ProcessAgent


SOURCE = '''import sys
import time
from simulator.agents.sdk import AutonomousAgent
from .helper import STEER

THROTTLE = 0.2

class Driver(AutonomousAgent):
    def load(self, configuration):
        self.configuration = configuration

    def reset(self, context):
        pass

    def step(self, observation):
        if self.configuration.get("sleep_step"):
            time.sleep(10)
        return {"frame": observation.frame,
                "control": {"throttle": THROTTLE, "steer": STEER},
                "diagnostics": {"metadata": {
                    "cache_prefix": sys.pycache_prefix,
                    "no_bytecode": sys.dont_write_bytecode,
                }}}

    def close(self):
        pass
'''


def package(directory, **configuration):
    directory.mkdir()
    (directory / "agent.json").write_text(json.dumps({
        "agent_id": "cache-identity", "name": "Cache identity", "version": "1",
        "entry_point": "agent:Driver", "configuration": configuration,
    }), encoding="utf-8")
    (directory / "agent.py").write_bytes(SOURCE.encode("utf-8"))
    (directory / "helper.py").write_bytes(b"STEER = 0.4\n")
    return directory


def stale_cache(path, stale_source):
    """Make bytecode for different bytes with the current source's size/mtime."""
    source = path.read_bytes()
    cached_source = stale_source.encode("utf-8")
    assert len(cached_source) == len(source)
    stamp = 1_700_000_000_000_000_000
    path.write_bytes(cached_source)
    os.utime(path, ns=(stamp, stamp))
    cache = path.parent / "__pycache__" / f"{path.stem}.{sys.implementation.cache_tag}.pyc"
    cache.parent.mkdir(exist_ok=True)
    py_compile.compile(str(path), cfile=str(cache), doraise=True)
    path.write_bytes(source)
    os.utime(path, ns=(stamp, stamp))
    # The generated default timestamp cache is valid by Python's normal checks.
    header = cache.read_bytes()[:16]
    assert header[:4] == importlib.util.MAGIC_NUMBER
    assert int.from_bytes(header[4:8], "little") == 0
    assert int.from_bytes(header[8:12], "little") == stamp // 1_000_000_000
    assert int.from_bytes(header[12:16], "little") == len(source)
    return cache


def reset(agent):
    agent.load(agent.manifest.configuration)
    agent.reset(AgentReset(seed=1, map_name="Town03", ego_actor_id="ego", fixed_delta_seconds=.05))


def observation():
    return AgentObservation(frame=1, time_s=0, dt_s=.05, ego=ActorState(actor_id="ego"),
                            actors=[], target_speed_mps=2)


def test_registered_worker_uses_source_for_stale_entry_and_sibling_caches(tmp_path, monkeypatch):
    from uuid import uuid4

    directory = package(tmp_path / "agent")
    caches = [
        stale_cache(directory / "agent.py", SOURCE.replace("THROTTLE = 0.2", "THROTTLE = 0.1")),
        stale_cache(directory / "helper.py", "STEER = 0.3\n"),
    ]
    original_caches = [path.read_bytes() for path in caches]
    # Existing caller cache settings must not override the private worker prefix.
    inherited_prefix = tmp_path / "shared-cache"
    inherited_prefix.mkdir()
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(inherited_prefix))
    snapshot = build_agent_snapshot(AgentRegistration(
        package_path=str(directory), python_executable=sys.executable, trusted_local_code=True,
    ), uuid4())
    assert not any("__pycache__" in item.path for item in snapshot.files)
    driver = ProcessAgent(directory, python_executable=sys.executable,
                          options=AgentProcessOptions(**snapshot.process_options.model_dump()), snapshot=snapshot)
    cache_prefix = None
    try:
        reset(driver)
        result = driver.step(observation())
        assert result.control.throttle == .2
        assert result.control.steer == .4
        metadata = result.diagnostics.metadata
        cache_prefix = Path(metadata["cache_prefix"])
        assert cache_prefix.is_dir() and cache_prefix != inherited_prefix
        assert metadata["no_bytecode"] is True
        assert list(cache_prefix.iterdir()) == []
        verify_agent_snapshot(snapshot)
    finally:
        driver.close()
    assert cache_prefix is not None and not cache_prefix.exists()
    assert [path.read_bytes() for path in caches] == original_caches
    assert list(inherited_prefix.iterdir()) == []


def test_worker_runtime_is_imported_from_source_instead_of_stale_cache(tmp_path, monkeypatch):
    import simulator.agents.process as process_module

    runtime = tmp_path / "runtime"
    repo = Path(process_module.__file__).resolve().parents[2]
    for name in ("backend", "evaluation", "optimization", "scenarios", "shared", "simulator"):
        shutil.copytree(repo / name, runtime / name, ignore=shutil.ignore_patterns("__pycache__"))
    sdk = runtime / "simulator" / "agents" / "sdk.py"
    source = sdk.read_bytes()
    wrong = "raise RuntimeError('stale runtime bytecode executed')\n"
    stale_cache(sdk, wrong + " " * (len(source) - len(wrong)))
    # Change only this worker's cwd calculation; never modify the real runtime.
    monkeypatch.setattr(process_module, "__file__", str(runtime / "simulator" / "agents" / "process.py"))
    driver = ProcessAgent(package(tmp_path / "agent"), python_executable=sys.executable)
    cache_prefix = None
    try:
        reset(driver)
        result = driver.step(observation())
        assert result.control.throttle == .2
        cache_prefix = Path(result.diagnostics.metadata["cache_prefix"])
    finally:
        driver.close()
    assert cache_prefix is not None and not cache_prefix.exists()


def test_private_cache_is_removed_after_process_startup_failure(tmp_path, monkeypatch):
    import simulator.agents.process as process_module

    prefixes = []

    def cache_directory(**kwargs):
        temporary = TemporaryDirectory(dir=tmp_path, **kwargs)
        prefixes.append(Path(temporary.name))
        return temporary

    def fail_start(*args, **kwargs):
        raise OSError("deliberate process startup failure")

    monkeypatch.setattr(process_module, "TemporaryDirectory", cache_directory)
    monkeypatch.setattr(process_module.subprocess, "Popen", fail_start)
    driver = ProcessAgent(package(tmp_path / "agent"), python_executable=sys.executable)
    try:
        with pytest.raises(OSError, match="startup failure"):
            driver.load(driver.manifest.configuration)
    finally:
        driver.close()
    assert len(prefixes) == 1 and not prefixes[0].exists()


def test_private_cache_is_removed_after_worker_timeout(tmp_path):
    driver = ProcessAgent(package(tmp_path / "agent", sleep_step=True), python_executable=sys.executable,
                          options=AgentProcessOptions(step_timeout_s=.1))
    cache_prefix = None
    try:
        reset(driver)
        cache_prefix = Path(driver._bytecode_cache.name)
        assert cache_prefix.is_dir()
        with pytest.raises(AgentTimeoutError):
            driver.step(observation())
        assert not cache_prefix.exists()
    finally:
        driver.close()
    assert cache_prefix is not None and not cache_prefix.exists()
