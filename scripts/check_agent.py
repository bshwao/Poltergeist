"""Inspect a local agent package; optionally run a synthetic SDK smoke test."""

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

from shared.models import ActorState, AgentObservation, AgentReset, DrivingTask, Transform, Vector3
from simulator.agents.package import inspect_agent_package, load_agent_package
from simulator.agents.sdk import AgentSession


def check_package(path: str | Path, *, smoke_test: bool = False) -> dict:
    package = inspect_agent_package(path)
    report = {
        "compatible": True,
        "protocol_version": package.manifest.protocol_version,
        "agent_id": package.manifest.agent_id,
        "agent_version": package.manifest.version,
        "observation_mode": package.manifest.observation_mode,
        "control_mode": package.manifest.control_mode,
        "package_directory": str(package.directory),
        "checks": ["manifest", "sdk_capabilities", "entry_module", "declared_files"],
        "code_executed": False,
        "smoke_test": None,
    }
    if smoke_test:
        # This calls trusted user code in this process. It is not CARLA execution,
        # a deadline-enforced worker, or evidence of autonomous driving quality.
        # Keep Python agent prints separate from the machine-readable report.
        with redirect_stdout(sys.stderr):
            agent = load_agent_package(package)
            with AgentSession(agent, package.manifest) as session:
                session.load()
                sensor_mode = package.manifest.observation_mode == "sensors"
                destination = Transform(position=Vector3(x=30.0))
                task = DrivingTask(destination=destination, target_speed_mps=12.0) if sensor_mode else None
                session.reset(AgentReset(
                    protocol_version=package.manifest.protocol_version,
                    seed=0, map_name="SDKSmokeTest", ego_actor_id="ego", fixed_delta_seconds=0.05,
                    driving_task=task, destination=destination if sensor_mode else None,
                    route=[Transform(), destination] if sensor_mode else [],
                    sensors=package.manifest.sensors,
                ))
                results = []
                for frame in (0, 1):
                    ego = ActorState(actor_id="ego", transform=Transform(), velocity_mps=Vector3(),
                                     acceleration_mps2=Vector3())
                    scene = None
                    if "structured_scene" in package.manifest.required_features:
                        from simulator.reference_scene import synthetic_scene
                        from shared.models import TelemetryFrame
                        scene = synthetic_scene(TelemetryFrame(frame=frame, time_s=frame * .05,
                            ego=ego, actors=[]), {}, 0.0, 13.9)
                    samples = []
                    if sensor_mode:
                        from simulator.sensor_fixtures import build_sensor_fixture
                        samples = build_sensor_fixture(package.manifest.sensors, frame, frame * .05)
                    results.append(session.step(AgentObservation(
                        protocol_version=package.manifest.protocol_version,
                        frame=frame, time_s=frame * 0.05, dt_s=0.05,
                        ego=ego, actors=None if sensor_mode else [], target_speed_mps=12.0,
                        route_heading_deg=0.0, traffic_light_red=None if sensor_mode else False,
                        driving_task=task, scene=scene,
                        observation_mode="sensors" if sensor_mode else "simulator_state",
                        ego_state_source="simulated_ego_kinematics" if sensor_mode else "simulator_state",
                        sensor_source="deterministic_fixture" if sensor_mode else None, sensors=samples,
                    )))
        report["code_executed"] = True
        report["smoke_test"] = {
            "passed": True, "frames": [result.frame for result in results],
            "results": [result.model_dump(mode="json") for result in results],
            "input_source": "deterministic_fixture" if sensor_mode else "synthetic_state",
        }
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, help="Directory containing agent.json")
    parser.add_argument(
        "--smoke-test", action="store_true",
        help="Execute trusted local agent code on two synthetic observations (no CARLA)",
    )
    args = parser.parse_args(argv)
    try:
        report = check_package(args.package, smoke_test=args.smoke_test)
    except Exception as error:
        print(f"Agent check failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
