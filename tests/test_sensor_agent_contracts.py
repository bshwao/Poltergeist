"""SDK 1.3 delivers calibrated bytes without leaking privileged scene state."""

import base64
import hashlib
import struct
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from shared.models import (
    ActorState, AgentInputInfo, AgentManifest, AgentObservation, AgentReset, AgentSensorFrame,
    AgentSensorSpec, AgentStepResult, ControlAudit, ControlInput, DrivingTask,
    SafetyMetrics, Scenario, Simulation, SimulationResult, SimulationStatus, Transform, Vector3,
)
from shared.models.agent import MAX_SENSOR_PAYLOAD_BYTES
from simulator.agents.sdk import (
    AgentCompatibilityError, AgentProtocolError, AgentSession, AutonomousAgent,
    check_agent_capabilities,
)


def sensor_spec(**changes):
    return AgentSensorSpec.model_validate({
        "id": "front", "type": "sensor.camera.rgb",
        "attributes": {"image_size_x": 2, "image_size_y": 1, "fov": 90.0},
    } | changes)


def sensor_frame(raw=b"\x00\x20\x40\xff" * 2, **changes):
    return AgentSensorFrame.model_validate({
        "sensor_id": "front", "type": "sensor.camera.rgb", "frame": 500,
        "timestamp_s": 0.0, "encoding": "bgra8",
        "data_base64": base64.b64encode(raw).decode("ascii"),
        "sha256": hashlib.sha256(raw).hexdigest(), "width": 2, "height": 1,
        "fov_deg": 90.0,
    } | changes)


def lidar_frame(raw=struct.pack("<ffff", 1.0, 2.0, 3.0, 0.5), **changes):
    return sensor_frame(raw, type="sensor.lidar.ray_cast", encoding="float32_xyzi",
                        width=None, height=None, fov_deg=None, channels=16, **changes)


def task(**changes):
    return DrivingTask(destination=Transform(position=Vector3(x=10.0)),
                       target_speed_mps=4.0, **changes)


def manifest(**changes):
    return AgentManifest.model_validate({
        "protocol_version": "1.3", "agent_id": "camera-driver", "name": "Camera driver",
        "version": "1", "entry_point": "agent:Driver", "observation_mode": "sensors",
        "sensors": [sensor_spec()],
    } | changes)


def reset(**changes):
    driving_task = task()
    return AgentReset.model_validate({
        "protocol_version": "1.3", "seed": 0, "map_name": "Town03",
        "ego_actor_id": "ego", "fixed_delta_seconds": 0.05,
        "destination": driving_task.destination,
        "route": [Transform(), driving_task.destination], "driving_task": driving_task,
        "sensors": [sensor_spec()],
    } | changes)


def observation(**changes):
    return AgentObservation.model_validate({
        "protocol_version": "1.3", "observation_mode": "sensors", "frame": 500,
        "time_s": 0.0, "dt_s": 0.05, "ego": ActorState(actor_id="ego"),
        "ego_state_source": "simulated_ego_kinematics", "driving_task": task(),
        "sensor_source": "deterministic_fixture",
        "target_speed_mps": 4.0, "sensors": [sensor_frame()],
    } | changes)


class SensorDriver(AutonomousAgent):
    def __init__(self):
        self.calls = []

    def load(self, configuration):
        self.calls.append(("load", configuration))

    def reset(self, context):
        self.calls.append(("reset", context))

    def step(self, value):
        self.calls.append(("step", value))
        return AgentStepResult(protocol_version=value.protocol_version, frame=value.frame,
                               control=ControlInput(throttle=0.2))

    def close(self):
        self.calls.append(("close",))


def test_camera_and_lidar_contracts_are_lossless_json_round_trips():
    for value in [sensor_frame(), lidar_frame(), lidar_frame(b"")]:
        copied = AgentSensorFrame.model_validate_json(value.model_dump_json())
        assert copied == value
        assert copied.raw_data() == value.raw_data()


def test_sensor_mount_is_ego_relative_and_default_is_independent():
    first, second = sensor_spec(), sensor_spec()
    assert first.mount.position == Vector3(x=1.5, z=2.0)
    first.mount.position.x = 10.0
    assert second.mount.position.x == 1.5


@pytest.mark.parametrize("changes", [
    {"data_base64": "not base64!"}, {"sha256": "0" * 64},
    {"sha256": "A" * 64}, {"encoding": "float32_xyzi"}, {"width": None},
    {"width": True}, {"height": 0}, {"width": 3}, {"height": 2049},
    {"fov_deg": None}, {"fov_deg": float("nan")}, {"fov_deg": 180.0},
    {"channels": 16}, {"frame": -1}, {"timestamp_s": -0.1},
    {"timestamp_s": float("inf")}, {"unrecognized": "field"},
])
def test_camera_payload_validation_rejects_corruption_and_wrong_calibration(changes):
    with pytest.raises(ValidationError):
        sensor_frame(**changes)


@pytest.mark.parametrize("raw", [
    b"\x00", b"\x00" * 15, b"\x00" * 17,
    struct.pack("<ffff", float("nan"), 0.0, 0.0, 1.0),
    struct.pack("<ffff", 0.0, float("inf"), 0.0, 1.0),
    struct.pack("<ffff", 0.0, 0.0, 0.0, float("-inf")),
])
def test_lidar_requires_complete_finite_xyzi_points(raw):
    with pytest.raises(ValidationError):
        lidar_frame(raw)


@pytest.mark.parametrize("changes", [
    {"channels": None}, {"channels": 0}, {"channels": True},
    {"channels": 129}, {"encoding": "bgra8"}, {"width": 1},
    {"height": 1}, {"fov_deg": 90.0},
])
def test_lidar_contract_rejects_camera_calibration_and_invalid_channels(changes):
    values = lidar_frame().model_dump() | changes
    with pytest.raises(ValidationError):
        AgentSensorFrame.model_validate(values)


def test_one_sensor_and_aggregate_raw_payload_limits_are_enforced():
    full = sensor_frame(b"\x00" * MAX_SENSOR_PAYLOAD_BYTES, width=1024, height=512)
    assert len(full.raw_data()) == MAX_SENSOR_PAYLOAD_BYTES
    with pytest.raises(ValidationError):
        sensor_frame(b"\x00" * (MAX_SENSOR_PAYLOAD_BYTES + 4), width=1024, height=512)
    second = sensor_frame(sensor_id="second")
    with pytest.raises(ValidationError, match="Aggregate"):
        observation(sensors=[full, second])


@pytest.mark.parametrize("changes", [
    {"protocol_version": "1.2"}, {"driving_task": None}, {"sensors": []},
    {"ego_state_source": "simulator_state"}, {"actors": []},
    {"sensor_source": None},
    {"actors": [ActorState(actor_id="other")]}, {"traffic_light_red": False},
    {"scene": {"source": "carla_ground_truth"}},
    {"target_speed_mps": 5.0},
    {"ego": {"actor_id": "ego", "bounding_box_extent": {"x": 1.0, "y": 1.0, "z": 1.0}}},
    {"ego": {"actor_id": "ego", "bounding_box_transform": {}}},
    {"ego": {"actor_id": "ego", "speed_limit_mps": 15.0}},
    {"sensors": [sensor_frame(), sensor_frame()]},
    {"sensors": [sensor_frame(frame=499)]},
    {"sensors": [sensor_frame(timestamp_s=0.01)]},
])
def test_sensor_observations_reject_privileged_and_misaligned_inputs(changes):
    with pytest.raises(ValidationError):
        observation(**changes)


def test_sensor_observation_accepts_explicit_ideal_ego_localization_and_null_speed_goal():
    value = observation(ego=ActorState(
        actor_id="ego", transform=Transform(position=Vector3(x=1.0)),
        velocity_mps=Vector3(x=2.0), acceleration_mps2=Vector3(x=0.1),
    ), driving_task=task().model_copy(update={"target_speed_mps": None}), target_speed_mps=None)
    assert value.actors is None and value.scene is None
    assert value.ego.velocity_mps.x == 2.0


def test_sensor_timestamps_tolerate_only_native_float_rounding():
    assert observation(sensors=[sensor_frame(timestamp_s=0.0000005)]).frame == 500
    with pytest.raises(ValidationError):
        observation(sensors=[sensor_frame(timestamp_s=0.000002)])


def test_state_observations_cannot_smuggle_sensor_samples():
    with pytest.raises(ValidationError):
        observation(observation_mode="simulator_state")
    assert AgentObservation(protocol_version="1.3", frame=0, time_s=0.0, dt_s=.05,
                            ego=ActorState(actor_id="ego")).observation_mode == "simulator_state"


@pytest.mark.parametrize("protocol", ["1.0", "1.1", "1.2"])
def test_older_manifests_can_declare_future_sensors_but_runtime_rejects_them(protocol):
    value = manifest(protocol_version=protocol)
    with pytest.raises(AgentCompatibilityError, match="1.3"):
        check_agent_capabilities(value)


def test_sensor_capability_resolution_is_canonical_and_does_not_mutate_caller():
    original = manifest()
    before = original.model_dump()
    resolved = check_agent_capabilities(original)
    assert original.model_dump() == before
    assert resolved.sensors[0].attributes["sensor_tick"] == 0.0
    assert resolved.sensors[0].attributes["image_size_x"] == 2


def test_unsupported_sensor_is_rejected_before_agent_load():
    driver = SensorDriver()
    with pytest.raises(AgentCompatibilityError):
        AgentSession(driver, manifest(sensors=[sensor_spec(type="sensor.other.imu", attributes={})]))
    assert driver.calls == []


def test_sensor_session_passes_copied_calibration_and_exact_frame_bytes():
    driver = SensorDriver()
    configured = manifest()
    context = reset()
    value = observation()
    with AgentSession(driver, configured) as session:
        session.load()
        session.reset(context)
        assert session.step(value).frame == 500
        assert session.step(observation(
            frame=501, time_s=.05, sensors=[sensor_frame(frame=501, timestamp_s=.05)],
        )).frame == 501
        received_reset = driver.calls[1][1]
        assert received_reset.sensors[0].attributes["sensor_tick"] == 0.0
        received_reset.sensors[0].mount.position.x = 99.0
        assert context.sensors[0].mount.position.x == 1.5
        received_observation = driver.calls[2][1]
        assert received_observation.sensors[0].raw_data() == sensor_frame().raw_data()
        assert received_observation.actors is None


@pytest.mark.parametrize("change", ["missing", "extra", "wrong_type", "wrong_width", "wrong_fov"])
def test_session_checks_exact_declared_sensor_set_and_calibration(change):
    driver = SensorDriver()
    sample = sensor_frame()
    samples = [sample]
    if change == "missing":
        samples = [sensor_frame(sensor_id="another")]
    elif change == "extra":
        samples.append(sensor_frame(sensor_id="another"))
    elif change == "wrong_type":
        samples = [lidar_frame()]
    elif change == "wrong_width":
        samples = [sensor_frame(b"\x00" * 12, width=3)]
    elif change == "wrong_fov":
        samples = [sensor_frame(fov_deg=80.0)]
    with AgentSession(driver, manifest()) as session:
        session.load()
        session.reset(reset())
        with pytest.raises(AgentProtocolError):
            session.step(observation(sensors=samples))
        assert [entry[0] for entry in driver.calls] == ["load", "reset"]


@pytest.mark.parametrize("change", ["mount", "attrs", "missing"])
def test_reset_requires_the_manifest_sensor_calibration(change):
    value = reset().model_dump()
    if change == "mount":
        value["sensors"][0]["mount"]["position"]["z"] = 3.0
    elif change == "attrs":
        value["sensors"][0]["attributes"]["fov"] = 80.0
    else:
        value["sensors"] = []
    with AgentSession(SensorDriver(), manifest()) as session:
        session.load()
        with pytest.raises(AgentProtocolError):
            session.reset(value)


def test_revalidating_mutated_sensor_frame_blocks_corrupted_bytes_before_agent_call():
    value = observation()
    value.sensors[0].data_base64 = base64.b64encode(b"corrupt!").decode("ascii")
    with AgentSession(SensorDriver(), manifest()) as session:
        session.load()
        session.reset(reset())
        with pytest.raises(AgentProtocolError, match="SHA-256"):
            session.step(value)


def test_sensor_and_state_modes_cannot_change_mid_session():
    with AgentSession(SensorDriver(), manifest()) as session:
        session.load()
        session.reset(reset())
        state_value = observation().model_dump() | {
            "observation_mode": "simulator_state", "ego_state_source": "simulator_state", "sensors": [],
            "sensor_source": None,
        }
        with pytest.raises(AgentProtocolError, match="mode"):
            session.step(state_value)


def test_sdk_1_3_state_agent_keeps_scene_and_driving_task_support():
    configured = manifest(observation_mode="simulator_state", sensors=[], required_features=["structured_scene"])
    with AgentSession(SensorDriver(), configured) as session:
        session.load()
        session.reset(reset(sensors=[]))
        value = observation().model_dump() | {
            "observation_mode": "simulator_state", "ego_state_source": "simulator_state", "sensors": [],
            "sensor_source": None,
            "scene": {"source": "synthetic"},
        }
        assert session.step(value).frame == 500


def test_reset_and_observation_models_preserve_caller_owned_data():
    value = observation().model_dump()
    before = deepcopy(value)
    AgentObservation.model_validate(value)
    assert value == before


def test_sensor_source_cannot_change_during_an_episode():
    with AgentSession(SensorDriver(), manifest()) as session:
        session.load()
        session.reset(reset())
        session.step(observation())
        with pytest.raises(AgentProtocolError, match="source"):
            session.step(observation(frame=501, time_s=.05, sensor_source="carla_sensors",
                                     sensors=[sensor_frame(frame=501, timestamp_s=.05)]))


def input_info(**changes):
    resolved = check_agent_capabilities(manifest()).sensors
    return AgentInputInfo.model_validate({
        "observation_mode": "sensors", "source": "deterministic_fixture",
        "ego_state_source": "simulated_ego_kinematics", "sensors": resolved,
        "ground_truth_actor_inputs": False,
        "assistance": ["ideal_ego_kinematics", "framework_navigation"],
    } | changes)


def terminal_result(**changes):
    now = datetime.now(UTC)
    scenario = Scenario(name="Sensor goal", actors=[{"type": "ego_vehicle"}], driving_task=task())
    return SimulationResult.model_validate({
        "simulation": Simulation(scenario=scenario, mode="test", status=SimulationStatus.COMPLETED,
                                 created_at=now, started_at=now, finished_at=now, progress=1.0),
        "metrics": SafetyMetrics(), "telemetry_frames": 0, "measured_duration_s": 0.0,
        "agent_inputs": input_info(),
    } | changes)


def test_agent_input_provenance_round_trips_and_legacy_results_remain_unknown():
    result = terminal_result()
    assert SimulationResult.model_validate_json(result.model_dump_json()) == result
    assert terminal_result(agent_inputs=None).agent_inputs is None


@pytest.mark.parametrize("changes", [
    {"source": "carla_ground_truth"}, {"ego_state_source": "simulator_state"},
    {"ground_truth_actor_inputs": True}, {"ground_truth_actor_inputs": 0},
    {"sensors": []}, {"sensors": [sensor_spec()]}, {"assistance": []},
    {"assistance": ["ideal_ego_kinematics", "ideal_ego_kinematics"]},
])
def test_sensor_provenance_cannot_claim_privileged_or_incomplete_inputs(changes):
    with pytest.raises(ValidationError):
        input_info(**changes)


def test_state_provenance_explicitly_labels_ground_truth_inputs():
    values = {"observation_mode": "simulator_state", "source": "synthetic_state",
              "ego_state_source": "simulator_state", "ground_truth_actor_inputs": True,
              "sensors": [], "assistance": []}
    assert input_info(**values).ground_truth_actor_inputs is True
    for change in [{"ground_truth_actor_inputs": False}, {"source": "carla_sensors"},
                   {"ego_state_source": "simulated_ego_kinematics"}, {"sensors": [sensor_spec()]}]:
        with pytest.raises(ValidationError):
            input_info(**(values | change))


@pytest.mark.parametrize("attr,value", [
    ("image_size_x", 0), ("image_size_x", 2.0), ("image_size_y", True),
    ("fov", 180.0), ("sensor_tick", 0.1), ("fov", "90"), ("unrecognized", 0),
])
def test_persisted_calibration_is_complete_strict_and_supported(attr, value):
    data = input_info().model_dump()
    data["sensors"][0]["attributes"][attr] = value
    with pytest.raises(ValidationError):
        AgentInputInfo.model_validate(data)


def test_result_provenance_matches_simulator_source_and_task_control_ownership():
    with pytest.raises(ValidationError, match="simulation mode"):
        terminal_result(agent_inputs=input_info(source="carla_sensors"))
    with pytest.raises(ValidationError, match="navigation assistance"):
        terminal_result(agent_inputs=input_info(assistance=["ideal_ego_kinematics"]))
    scripted = ControlAudit(mode="scripted", controller="baseline", navigation="framework_route")
    with pytest.raises(ValidationError, match="authoritative"):
        terminal_result(control_audit=scripted)
    simulation = terminal_result().simulation.model_dump()
    simulation["scenario"]["driving_task"] = None
    with pytest.raises(ValidationError, match="explicit driving task"):
        terminal_result(simulation=simulation)


def test_agent_owned_sensor_goal_does_not_claim_framework_navigation():
    simulation = terminal_result().simulation.model_dump()
    simulation["scenario"]["driving_task"]["route_owner"] = "agent"
    info = input_info(assistance=["ideal_ego_kinematics"])
    assert terminal_result(simulation=simulation, agent_inputs=info).agent_inputs == info
