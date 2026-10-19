"""Sensor bytes cross the worker boundary without evaluator ground truth."""

from dataclasses import replace
import json
import struct
import sys
from threading import Event

import pytest

from backend.services.persistence_service import PersistenceService
from scenarios import validate_scenario
from simulator import SimulationRunner
from simulator.agent_sensors import resolve_sensor_specs
from simulator.agents import ProcessAgent
from simulator.errors import SimulationCancelled, SimulationError
from simulator.protocols import SensorSample
from simulator.test_adapter import TestSimulationAdapter


def scene(*, route_owner="framework"):
    task = {"destination": {"position": {"x": 8}}, "target_speed_mps": 4,
            "route_owner": route_owner}
    if route_owner == "framework":
        task["route"] = [{}, {"position": {"x": 8}}]
    return validate_scenario({
        "name": "sensor-worker", "duration_s": 0.1, "fixed_delta_seconds": 0.05,
        "actors": [
            {"id": "ego", "type": "ego_vehicle", "speed_mps": 2, "spawn": {}},
            {"id": "npc", "type": "vehicle", "speed_mps": 1, "spawn": {"position": {"x": 80}}},
        ],
        "driving_task": task,
    })


@pytest.fixture
def sensor_package(tmp_path):
    package = tmp_path / "sensor-driver"
    package.mkdir()
    log = tmp_path / "observed.jsonl"
    manifest = {
        "protocol_version": "1.3", "agent_id": "sensor-worker-test", "name": "Sensor worker test",
        "version": "1", "entry_point": "agent:Driver", "observation_mode": "sensors",
        "navigation_mode": "agent_planner",
        "sensors": [
            {"id": "front", "type": "sensor.camera.rgb", "attributes": {
                "image_size_x": 2, "image_size_y": 1, "fov": 80,
            }},
            {"id": "roof", "type": "sensor.lidar.ray_cast", "attributes": {
                "channels": 2, "points_per_second": 2000,
            }},
        ],
        "configuration": {"log": str(log)},
    }
    (package / "agent.json").write_text(json.dumps(manifest), encoding="utf-8")
    (package / "agent.py").write_text('''
import json
from pathlib import Path
from simulator.agents import AutonomousAgent
class Driver(AutonomousAgent):
    def record(self, event, value):
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, "value": value}) + "\\n")
    def load(self, configuration):
        self.log = Path(configuration["log"])
        self.record("load", {})
    def reset(self, context):
        self.record("reset", context.model_dump(mode="json"))
    def step(self, observation):
        self.record("step", observation.model_dump(mode="json"))
        camera = next(sample for sample in observation.sensors if sample.sensor_id == "front")
        raw = camera.raw_data()
        assert raw == bytes([20 + observation.frame]) * 8
        return {"protocol_version": "1.3", "frame": observation.frame,
                "control": {"throttle": raw[0] / 100, "steer": 0.1},
                "diagnostics": {"metadata": {"camera_sha256": camera.sha256}}}
    def close(self):
        self.record("close", {})
''', encoding="utf-8")
    return package, log


def calls(log):
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


class SensorFixtureAdapter(TestSimulationAdapter):
    def __init__(self, *, mutation=None):
        super().__init__()
        self.specs = []
        self.packet_frames = []
        self.controls = []
        self.open_calls = self.close_calls = 0
        self.mutation = mutation

    def configure_agent_sensors(self, specs):
        self.specs = resolve_sensor_specs(specs)

    def open(self, recipe):
        self.open_calls += 1
        return super().open(recipe)

    def agent_sensor_samples(self, frame):
        self.packet_frames.append(frame.frame)
        samples = []
        for spec in self.specs:
            camera = spec.type == "sensor.camera.rgb"
            metadata = {"source": "deterministic_fixture", "agent_sensor_id": spec.id,
                        "sensor_type": spec.type, "mount": spec.mount.model_dump(mode="json")}
            if camera:
                metadata.update(encoding="bgra8", width=2, height=1, fov_deg=80.0)
                payload = bytes([20 + frame.frame]) * 8
            else:
                metadata.update(encoding="float32_xyzi", channels=2)
                payload = struct.pack("<ffff", 1.0 + frame.frame, 2, 3, 0.5)
            samples.append(SensorSample(kind="camera" if camera else "lidar", frame=frame.frame,
                                        timestamp=frame.time_s, data=payload, metadata=metadata))
        return tuple(samples) if self.mutation is None else self.mutation(tuple(samples), frame)

    def sensor_time_origin_s(self):
        return 0.0

    def observation(self, *args, **kwargs):
        raise AssertionError("Sensor path called privileged observation provider")

    def agent_context(self, *args, **kwargs):
        raise AssertionError("Sensor path called privileged scene provider")

    def traffic_light_observation(self):
        raise AssertionError("Sensor path called privileged signal provider")

    def step(self, control, commands):
        self.controls.append(control.model_copy(deep=True))
        return super().step(control, commands)

    def close(self):
        self.close_calls += 1
        super().close()


@pytest.mark.parametrize("route_owner", ["framework", "agent"])
def test_sensor_worker_gets_exact_packets_and_ego_localization_without_ground_truth(sensor_package, route_owner):
    package, log = sensor_package
    adapter = SensorFixtureAdapter()
    agent = ProcessAgent(package, python_executable=sys.executable)
    recorded, decisions = [], []
    output = SimulationRunner(adapter).run(scene(route_owner=route_owner), agent=agent,
                                           on_sensor=recorded.append, on_decision=decisions.append)
    assert output.result.simulation.status == "completed", output.result.simulation.error
    assert adapter.close_calls == 1 and not agent.is_running
    assert adapter.packet_frames == [0, 1, 2]
    assert len(recorded) == 6
    assert output.result.runtime.sensor_samples_count == 6
    assert len(decisions) == 2
    worker_calls = calls(log)
    assert [entry["event"] for entry in worker_calls] == ["load", "reset", "step", "step", "close"]
    reset = worker_calls[1]["value"]
    assert reset["sensors"] == [spec.model_dump(mode="json") for spec in adapter.specs]
    assert bool(reset["route"]) == (route_owner == "framework")
    assert reset["destination"]["position"]["x"] == 8
    observed = [entry["value"] for entry in worker_calls if entry["event"] == "step"]
    for index, observation in enumerate(observed):
        assert observation == decisions[index]["observation"]
        assert observation["observation_mode"] == "sensors"
        assert observation["actors"] is observation["scene"] is observation["traffic_light_red"] is None
        assert observation["ego"]["bounding_box_extent"] is None
        assert observation["ego"]["bounding_box_transform"] is None
        assert observation["ego"]["speed_limit_mps"] is None
        assert observation["ego_state_source"] == "simulated_ego_kinematics"
        assert observation["sensor_source"] == "deterministic_fixture"
        assert observation["ego"]["transform"] == output.telemetry[index].ego.transform.model_dump(mode="json")
        assert [sample["frame"] for sample in observation["sensors"]] == [index, index]
        assert [sample["timestamp_s"] for sample in observation["sensors"]] == pytest.approx([index * 0.05] * 2)
        assert adapter.controls[index].throttle == pytest.approx((20 + index) / 100)
        assert decisions[index]["applied_control"] == decisions[index]["proposed_control"]
        assert decisions[index]["interventions"] == []
        if route_owner == "agent":
            assert observation["route_heading_deg"] is None
    # Sensor isolation affects the agent boundary; evaluator telemetry remains complete.
    assert all(frame.actors and frame.actors[0].actor_id == "npc" for frame in output.telemetry)
    assert all(frame.ego.bounding_box_extent is not None for frame in output.telemetry)
    inputs = output.result.agent_inputs
    assert inputs.observation_mode == "sensors" and inputs.source == "deterministic_fixture"
    assert not inputs.ground_truth_actor_inputs
    assert "ideal_ego_kinematics" in inputs.assistance
    assert ("framework_navigation" in inputs.assistance) == (route_owner == "framework")
    assert output.result.control_audit.mode == "authoritative"


def changed_first(samples, **changes):
    return (replace(samples[0], **changes), *samples[1:])


@pytest.mark.parametrize("mutation", [
    lambda samples, frame: samples[:-1],
    lambda samples, frame: (samples[0], samples[0]),
    lambda samples, frame: changed_first(samples, frame=frame.frame + 1),
    lambda samples, frame: changed_first(samples, frame=max(0, frame.frame - 1)) if frame.frame else samples,
    lambda samples, frame: changed_first(samples, timestamp=frame.time_s + 0.05),
    lambda samples, frame: changed_first(samples, timestamp=frame.time_s - 0.05),
    lambda samples, frame: changed_first(samples, metadata=dict(samples[0].metadata) | {"width": 1}),
    lambda samples, frame: changed_first(samples, metadata=dict(samples[0].metadata) | {"agent_sensor_id": "unknown"}),
    lambda samples, frame: changed_first(samples, metadata=dict(samples[0].metadata) | {"source": "carla_sensors"}),
    lambda samples, frame: changed_first(samples, data=b"bad"),
])
def test_invalid_packets_fail_without_silent_fallback_or_lingering_worker(sensor_package, mutation):
    package, _ = sensor_package
    adapter = SensorFixtureAdapter(mutation=mutation)
    agent = ProcessAgent(package, python_executable=sys.executable)
    output = SimulationRunner(adapter).run(scene(), agent=agent)
    assert output.result.simulation.status == "failed", output.result.simulation.error
    assert output.result.simulation.error
    assert adapter.close_calls == 1 and not agent.is_running
    assert len(adapter.controls) <= 1
    assert PersistenceService._validate_trace(output.result, output.telemetry) == output.telemetry


def test_final_measured_packet_is_validated_even_without_another_agent_action(sensor_package):
    package, _ = sensor_package
    adapter = SensorFixtureAdapter(mutation=lambda samples, frame: samples if frame.frame < 2 else samples[:-1])
    agent = ProcessAgent(package, python_executable=sys.executable)
    output = SimulationRunner(adapter).run(scene(), agent=agent)
    assert output.result.simulation.status == "failed"
    assert len(adapter.controls) == 2
    assert adapter.packet_frames == [0, 1, 2]
    assert adapter.close_calls == 1 and not agent.is_running
    assert PersistenceService._validate_trace(output.result, output.telemetry) == output.telemetry


def test_packet_capture_cancellation_remains_cancelled_and_cleans_resources(sensor_package):
    package, log = sensor_package
    def cancelled(samples, frame):
        raise SimulationCancelled("Sensor wait cancelled")
    adapter = SensorFixtureAdapter(mutation=cancelled)
    agent = ProcessAgent(package, python_executable=sys.executable)
    output = SimulationRunner(adapter).run(scene(), agent=agent)
    assert output.result.simulation.status == "cancelled", output.result.simulation.error
    assert output.result.simulation.error is None
    assert adapter.controls == []
    assert adapter.close_calls == 1 and not agent.is_running
    assert calls(log)[-1]["event"] == "close"
    assert PersistenceService._validate_trace(output.result, output.telemetry) == output.telemetry


def test_recording_callback_failure_does_not_drop_cleanup_or_change_controls(sensor_package):
    package, _ = sensor_package
    adapter = SensorFixtureAdapter()
    agent = ProcessAgent(package, python_executable=sys.executable)
    def broken(sample):
        raise OSError("sensor recording failed")
    output = SimulationRunner(adapter).run(scene(), agent=agent, on_sensor=broken)
    assert output.result.simulation.status == "failed"
    assert "sensor recording failed" in output.result.simulation.error
    assert adapter.controls == []
    assert adapter.close_calls == 1 and not agent.is_running
    assert PersistenceService._validate_trace(output.result, output.telemetry) == output.telemetry


def test_precancelled_sensor_run_starts_no_world_or_worker(sensor_package):
    package, log = sensor_package
    cancellation = Event()
    cancellation.set()
    adapter = SensorFixtureAdapter()
    agent = ProcessAgent(package, python_executable=sys.executable)
    output = SimulationRunner(adapter).run(scene(), agent=agent, cancellation=cancellation)
    assert output.result.simulation.status == "cancelled"
    assert adapter.open_calls == 0 and not agent.is_running
    assert calls(log) == []


def test_explicit_scripted_sensor_control_and_missing_task_are_rejected_before_spawn(sensor_package):
    package, log = sensor_package
    agent = ProcessAgent(package, python_executable=sys.executable)
    adapter = SensorFixtureAdapter()
    with pytest.raises(SimulationError, match="[Aa]uthoritative|scripted"):
        SimulationRunner(adapter).run(scene(), agent=agent, agent_control_mode="scripted")
    assert adapter.open_calls == 0 and calls(log) == []
    agent.close()
    agent = ProcessAgent(package, python_executable=sys.executable)
    no_task = scene().model_copy(update={"driving_task": None})
    with pytest.raises(SimulationError, match="driving task|driving_task"):
        SimulationRunner(SensorFixtureAdapter()).run(no_task, agent=agent)
    assert calls(log) == []
    agent.close()
