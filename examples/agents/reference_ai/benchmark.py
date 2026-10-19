"""Reproduce isolated pretrained inference timings on an explicit test fixture.

This does not launch CARLA or prove end-to-end real-time operation. The road
and hazards below are hand-authored synthetic numerical inputs, clearly marked
as synthetic; they are never used to fill missing live observations.
"""

import argparse
import json
import math
from pathlib import Path
import platform
import statistics
import sys
from time import perf_counter

from shared.models import (
    ActorState, AgentObservation, AgentReset, AgentRoadRaster, AgentSceneContext,
    AgentSceneObject, BoundingBoxExtent, Transform, Vector3,
)
from simulator.agents.reference_ai import ReferenceAIAgent
from simulator.agents.reference_ai.checkpoint import CHECKPOINT_SHA256, UPSTREAM_REVISION


def fixture(*, kind=None):
    classes = [0] * 16384
    for row in range(56, 72):
        classes[row * 128:(row + 1) * 128] = [3 if row in (56, 71) else 1] * 128
    objects = []
    if kind is not None:
        objects.append(AgentSceneObject(
            actor_id="fixture-hazard", kind=kind,
            transform=Transform(position=Vector3(x=8.)),
            extent=BoundingBoxExtent(x=2., y=1., z=1.),
            velocity_mps=Vector3(x=1. if kind == "pedestrian" else 0.),
            traffic_light_state="red" if kind == "traffic_light" else None,
        ))
    return AgentObservation(
        protocol_version="1.1", frame=0, time_s=0., dt_s=0.05,
        ego=ActorState(actor_id="ego", transform=Transform(), velocity_mps=Vector3(x=8.)),
        actors=[], target_speed_mps=12.,
        scene=AgentSceneContext(
            source="synthetic", objects=objects, speed_limit_mps=50. / 3.6,
            route=[Transform(position=Vector3(x=float(index))) for index in range(41)],
            road_raster=AgentRoadRaster(width=128, height=128, pixels_per_meter=2.,
                ego_row=64., ego_column=64., classes=classes),
        ),
    )


def measure(device, samples, warmup):
    agent = ReferenceAIAgent()
    context = AgentReset(protocol_version="1.1", seed=0, map_name="synthetic",
                         ego_actor_id="ego", fixed_delta_seconds=0.05)
    started = perf_counter()
    try:
        agent.load({"device": device, "torch_threads": 2})
        load_time = perf_counter() - started
        agent.reset(context)
        value = fixture()
        inference_times, step_times = [], []
        for _ in range(samples + warmup):
            started = perf_counter()
            result = agent.step(value)
            step_times.append(perf_counter() - started)
            inference_times.append(result.diagnostics.metadata["inference_wall_time_s"])
        responses = {}
        for name, kind in (("clear", None), ("stopped_vehicle", "vehicle"),
                           ("pedestrian", "pedestrian"), ("red_light", "traffic_light")):
            agent.reset(context)
            response = agent.step(fixture(kind=kind))
            responses[name] = {
                "control": response.control.model_dump(mode="json"),
                "target_speed_mps": response.diagnostics.target_speed_mps,
                "path_end": response.diagnostics.planned_trajectory[-1].position.model_dump(mode="json"),
            }

        def summary(times):
            measured = sorted(times[warmup:])
            return {"median_s": statistics.median(measured),
                    "p95_s": measured[math.ceil(0.95 * len(measured)) - 1],
                    "first_s": times[0], "samples_s": times}

        return {
            "load_s": load_time, "metadata": result.diagnostics.metadata,
            "inference": summary(inference_times), "agent_step": summary(step_times),
            "responses": responses,
            "gpu_name": agent._torch.cuda.get_device_name() if device == "cuda" else None,
        }
    finally:
        agent.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("cpu", "cuda", "both"), default="both")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.samples <= 0 or args.warmup < 0:
        parser.error("--samples must be positive and --warmup nonnegative")
    devices = ("cpu", "cuda") if args.device == "both" else (args.device,)
    payload = {
        "scope": "direct agent inference on a hand-authored synthetic straight-road fixture",
        "excluded": ["external worker transport and SDK validation", "CARLA", "live map rendering", "telemetry collection"],
        "python": sys.version, "platform": platform.platform(),
        "cpu": platform.processor(), "samples": args.samples, "warmup_samples": args.warmup,
        "percentile_method": "nearest_rank", "upstream_revision": UPSTREAM_REVISION,
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "devices": {device: measure(device, args.samples, args.warmup) for device in devices},
    }
    output = json.dumps(payload, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
