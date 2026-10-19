"""The documented sensor workflow records exact inputs without a live server."""

import json
from pathlib import Path

import pytest

from scenarios import save_scenario, validate_scenario
from scripts.check_agent import check_package
from shared.models import AgentObservation, SimulationResult
from simulator.agents.package import inspect_agent_package, load_agent_package
from simulator.agents import AgentSession
from simulator.simulation_runner import main
from shared.models import AgentReset, DrivingTask, Transform, Vector3

PACKAGE = Path(__file__).resolve().parents[1] / "examples" / "agents" / "sensor_pilot"


def test_sensor_smoke_reports_fixture_source_and_uses_manifest_protocol():
    report = check_package(PACKAGE, smoke_test=True)
    assert report["compatible"] and report["code_executed"]
    assert report["smoke_test"]["input_source"] == "deterministic_fixture"
    assert [result["protocol_version"] for result in report["smoke_test"]["results"]] == ["1.3", "1.3"]


def test_sensor_cli_writes_validated_decisions_and_input_provenance(tmp_path):
    scenario = validate_scenario({"name": "sensor-cli", "duration_s": .1,
        "actors": [{"id": "ego", "type": "ego_vehicle", "spawn_point_index": 0}],
        "driving_task": {"destination": {"position": {"x": 20}}, "target_speed_mps": 4}})
    path, result, trace = tmp_path / "scenario.json", tmp_path / "result.json", tmp_path / "inputs.jsonl"
    save_scenario(scenario, path)
    command = ["--scenario", str(path), "--mode", "test", "--agent-package", str(PACKAGE),
               "--output", str(result), "--decision-trace", str(trace)]
    assert main(command) == 0
    output = SimulationResult.model_validate_json(result.read_text())
    assert output.agent_inputs.source == "deterministic_fixture"
    assert output.agent_inputs.ground_truth_actor_inputs is False
    rows = [json.loads(line) for line in trace.read_text().splitlines()]
    assert len(rows) == 2
    for row in rows:
        observation = AgentObservation.model_validate(row["observation"])
        assert observation.observation_mode == "sensors" and observation.actors is None
        assert row["applied_control"] == row["agent_result"]["control"]
        assert {sample.type for sample in observation.sensors} == {"sensor.camera.rgb", "sensor.lidar.ray_cast"}
    original_trace = trace.read_bytes()
    with pytest.raises(SystemExit) as error:
        main(command)
    assert error.value.code == 1 and trace.read_bytes() == original_trace


def test_sensor_pilot_rejects_partial_scan_assumption():
    package = inspect_agent_package(PACKAGE)
    with AgentSession(load_agent_package(package), package.manifest) as session:
        session.load()
        task = DrivingTask(destination=Transform(position=Vector3(x=20.0)))
        with pytest.raises(ValueError, match="complete LiDAR sweep"):
            session.reset(AgentReset(protocol_version="1.3", seed=0, map_name="fixture", ego_actor_id="ego",
                fixed_delta_seconds=.01, destination=task.destination, driving_task=task,
                route=[Transform(), task.destination], sensors=package.manifest.sensors))


def test_result_and_decision_trace_cannot_share_a_path(tmp_path):
    path = tmp_path / "same.json"
    with pytest.raises(SystemExit) as error:
        main(["--template", "intersection", "--mode", "test", "--output", str(path), "--decision-trace", str(path)])
    assert error.value.code == 1 and not path.exists()
