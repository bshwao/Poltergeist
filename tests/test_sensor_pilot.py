"""The teaching pilot's actual sensor bytes must determine its controls."""

import base64
import hashlib
import importlib.util
from pathlib import Path
import struct

import pytest

from shared.models import (
    ActorState, AgentManifest, AgentObservation, AgentReset, AgentSensorFrame,
    DrivingTask, Rotation, Transform, Vector3,
)
from simulator.sensor_fixtures import build_sensor_fixture


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "examples" / "agents" / "sensor_pilot"
module_spec = importlib.util.spec_from_file_location("sensor_pilot_example_under_test", PACKAGE / "agent.py")
module = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(module)
SensorPilot = module.SensorPilot


def _manifest():
    return AgentManifest.model_validate_json((PACKAGE / "agent.json").read_text(encoding="utf-8"))


def _context(sensors=None, target=4.0):
    goal = Transform(position=Vector3(x=30.0))
    return AgentReset(protocol_version="1.3", seed=4, map_name="synthetic",
        ego_actor_id="ego", fixed_delta_seconds=0.05, destination=goal,
        route=[Transform(), goal], sensors=sensors or _manifest().sensors,
        driving_task=DrivingTask(destination=goal, target_speed_mps=target,
            completion_radius_m=1.5, completion_speed_mps=0.5, stop_on_completion=True))


def _pilot(context=None):
    pilot = SensorPilot()
    pilot.load({})
    pilot.reset(context or _context())
    return pilot


def _observation(context=None, *, frame=0, x=0.0, speed=2.0, obstacle=None, black=False, samples=None):
    context = context or _context()
    fields = dict(protocol_version="1.3", observation_mode="sensors", frame=frame,
        time_s=frame * 0.05, dt_s=0.05, driving_task=context.driving_task,
        target_speed_mps=context.driving_task.target_speed_mps,
        ego_state_source="simulated_ego_kinematics", sensor_source="deterministic_fixture",
        ego=ActorState(actor_id="ego", transform=Transform(position=Vector3(x=x)),
            velocity_mps=Vector3(x=speed)),
        sensors=samples if samples is not None else build_sensor_fixture(
            context.sensors, frame, frame * 0.05, obstacle_distance_m=obstacle, black_camera=black))
    # Contracts were added with phase 4; keeping the fixture honest also makes
    # accidental removal of the source label fail at model validation.
    return AgentObservation.model_validate(fields)


def _replace_lidar(samples, raw):
    result = [sample.model_copy(deep=True) for sample in samples]
    index = next(i for i, sample in enumerate(result) if sample.type == "sensor.lidar.ray_cast")
    result[index] = AgentSensorFrame.model_validate(result[index].model_dump() | {
        "data_base64": base64.b64encode(raw).decode("ascii"), "sha256": hashlib.sha256(raw).hexdigest()})
    return result


def test_manifest_declares_sensor_inputs_and_framework_navigation():
    manifest = _manifest()
    assert manifest.protocol_version == "1.3"
    assert manifest.observation_mode == "sensors"
    assert manifest.navigation_mode == "provided_route"
    assert {sensor.type for sensor in manifest.sensors} == {"sensor.camera.rgb", "sensor.lidar.ray_cast"}


def test_fixture_bytes_hash_calibration_and_native_frame_are_consistent():
    samples = build_sensor_fixture(_manifest().sensors, 7, 0.35)
    assert len(samples) == 2
    for sample in samples:
        assert sample.frame == 7 and sample.timestamp_s == 0.35
        assert sample.sha256 == hashlib.sha256(sample.raw_data()).hexdigest()
        assert AgentSensorFrame.model_validate_json(sample.model_dump_json()) == sample
    camera = next(sample for sample in samples if sample.type == "sensor.camera.rgb")
    assert len(camera.raw_data()) == 320 * 180 * 4
    assert camera.fov_deg == 90.0


def test_clear_ground_and_self_returns_allow_acceleration_without_privileged_state():
    result = _pilot().step(_observation())
    assert result.control.throttle > 0.0 and result.control.brake == 0.0
    assert result.diagnostics.metadata["nearest_lidar_return_m"] is None
    assert result.diagnostics.metadata["lidar_corridor_points"] == 0
    assert result.diagnostics.metadata["lidar_total_points"] > 0
    assert "simulated_ego_kinematics" in result.diagnostics.assistance
    assert result.diagnostics.hazard_actor_ids == []


def test_changed_lidar_bytes_produce_a_different_control_and_measured_reason():
    clear, hazard = _observation(), _observation(obstacle=6.0)
    assert clear.sensors[0] == hazard.sensors[0]  # Identical camera, ego and task.
    assert clear.ego == hazard.ego and clear.driving_task == hazard.driving_task
    assert clear.sensors[1].sha256 != hazard.sensors[1].sha256
    clear_result, hazard_result = _pilot().step(clear), _pilot().step(hazard)
    assert clear_result.control.throttle > 0.0
    assert hazard_result.control.throttle == 0.0 and hazard_result.control.brake == 1.0
    assert hazard_result.diagnostics.metadata["nearest_lidar_return_m"] == pytest.approx(3.5)
    assert "lidar_corridor_hazard" in hazard_result.diagnostics.reason_codes


def test_a_distant_return_does_not_cause_immediate_braking():
    result = _pilot().step(_observation(obstacle=25.0))
    assert result.control.throttle > 0.0 and result.control.brake == 0.0
    assert result.diagnostics.metadata["nearest_lidar_return_m"] == pytest.approx(22.5)


def test_speed_and_stopping_distance_change_hazard_response():
    slow = _pilot().step(_observation(obstacle=12.0, speed=2.0))
    fast = _pilot().step(_observation(obstacle=12.0, speed=6.0))
    assert "lidar_corridor_hazard" not in slow.diagnostics.reason_codes
    assert "lidar_corridor_hazard" in fast.diagnostics.reason_codes
    assert fast.control.brake == 1.0


def test_actual_black_camera_pixels_stop_the_car_with_identical_lidar():
    bright, black = _observation(), _observation(black=True)
    assert bright.sensors[1] == black.sensors[1]
    assert bright.sensors[0].sha256 != black.sensors[0].sha256
    result = _pilot().step(black)
    assert result.control.throttle == 0.0 and result.control.brake == 1.0
    assert result.diagnostics.metadata["camera_mean_brightness"] == 0.0
    assert result.diagnostics.reason_codes == ["camera_visibility_insufficient"]


def test_full_mount_rotation_and_translation_preserve_measured_hazard_distance():
    sensors = [sensor.model_copy(deep=True) for sensor in _manifest().sensors]
    sensors[1].mount = Transform(position=Vector3(x=-0.5, y=0.7, z=2.3),
        rotation=Rotation(yaw_deg=90.0, pitch_deg=12.0, roll_deg=-9.0))
    context = _context(sensors)
    result = _pilot(context).step(_observation(context, obstacle=6.0))
    assert result.control.brake == 1.0
    assert result.diagnostics.metadata["nearest_lidar_return_m"] == pytest.approx(3.5, abs=1e-5)
    assert result.diagnostics.metadata["lidar_corridor_points"] == 9


def test_side_above_and_behind_returns_do_not_become_forward_hazards():
    context = _context()
    # Default LiDAR at x=1.5,z=2; these are sensor-local point coordinates.
    raw = b"".join(struct.pack("<ffff", *point) for point in [
        (3.5, 3.0, -1.0, 0.7), (3.5, 0.0, 2.0, 0.7), (-8.0, 0.0, -1.0, 0.7)])
    samples = _replace_lidar(build_sensor_fixture(context.sensors, 0, 0.0), raw)
    result = _pilot(context).step(_observation(context, samples=samples))
    assert result.control.throttle > 0.0 and result.diagnostics.metadata["nearest_lidar_return_m"] is None


def test_empty_lidar_fails_closed_instead_of_claiming_a_clear_corridor():
    samples = _replace_lidar(build_sensor_fixture(_context().sensors, 0, 0.0), b"")
    result = _pilot().step(_observation(samples=samples))
    assert result.control.brake == 1.0 and result.control.throttle == 0.0
    assert result.diagnostics.reason_codes == ["lidar_empty"]


def test_goal_stop_uses_only_the_finite_route_and_reset_goal():
    pilot = _pilot()
    for frame, x in enumerate((0.0, 6.0, 12.0, 18.0, 24.0, 29.2)):
        result = pilot.step(_observation(frame=frame, x=x, speed=0.3))
    assert result.control.brake == 1.0
    assert "goal_stop" in result.diagnostics.reason_codes
    assert result.diagnostics.metadata["goal_distance_m"] == pytest.approx(0.8)
    assert result.diagnostics.target_speed_mps == 0.0


def test_reset_copies_calibrations_and_clears_route_progress_and_close_state():
    context, pilot = _context(), _pilot()
    pilot.step(_observation())
    pilot.reset(context)
    context.sensors[1].mount.position.x = 999.0
    assert pilot.lidar.mount.position.x == 1.5
    assert pilot.route_tracker.progress_m is None
    pilot.close()
    assert pilot.task is None and pilot.camera is None and pilot.route_tracker is None
    with pytest.raises(ValueError, match="Reset"):
        pilot.step(_observation())


def test_missing_or_changed_sensor_calibration_is_rejected():
    observation = _observation()
    observation.sensors[0] = AgentSensorFrame.model_validate(observation.sensors[0].model_dump() | {"fov_deg": 88.0})
    with pytest.raises(ValueError, match="calibration changed"):
        _pilot().step(observation)
    observation = _observation()
    observation.sensors.pop()
    with pytest.raises(ValueError, match="match the reset calibrations"):
        _pilot().step(observation)


def test_bad_hash_and_nonfinite_point_payloads_do_not_reach_control_logic():
    sample = build_sensor_fixture(_context().sensors, 0, 0.0)[1]
    with pytest.raises(ValueError, match="SHA-256"):
        AgentSensorFrame.model_validate(sample.model_dump() | {"sha256": "0" * 64})
    raw = struct.pack("<ffff", float("nan"), 0.0, 1.0, 0.5)
    with pytest.raises(ValueError, match="finite"):
        AgentSensorFrame.model_validate(sample.model_dump() | {
            "data_base64": base64.b64encode(raw).decode(), "sha256": hashlib.sha256(raw).hexdigest()})


@pytest.mark.parametrize("distance", [-1, 0, True, float("nan"), float("inf")])
def test_fixture_obstacle_rejects_invalid_distances(distance):
    with pytest.raises(ValueError, match="finite and positive"):
        build_sensor_fixture(_context().sensors, 0, 0.0, obstacle_distance_m=distance)


def test_town03_example_has_explicit_goal_and_initial_conditions():
    from scenarios import load_scenario

    scenario = load_scenario(ROOT / "examples" / "scenarios" / "sensor_pilot_town03.json")
    assert scenario.map == "Town03" and scenario.actors[0].spawn_point_index == 0
    assert scenario.actors[0].speed_mps == 2.0 and scenario.driving_task.target_speed_mps == 4.0
    assert scenario.driving_task.stop_on_completion


def test_sdk_session_accepts_normalized_sensor_calibration_and_clears_episode_state():
    from simulator.agents import AgentSession

    session = AgentSession(SensorPilot(), _manifest())
    context = _context(sensors=session.manifest.sensors)
    try:
        session.load()
        session.reset(context)
        clear = session.step(_observation(context))
        hazard = session.step(_observation(context, frame=1, obstacle=6.0))
        assert clear.control.throttle > 0.0 and hazard.control.brake == 1.0
        session.reset(context)
        assert session.step(_observation(context)).control.throttle > 0.0
    finally:
        session.close()
    assert session.state == "closed"


def test_external_worker_consumes_lossless_sample_bytes_and_returns_associated_controls():
    from simulator.agents import ProcessAgent

    agent = ProcessAgent(PACKAGE)
    context = _context(sensors=agent.manifest.sensors)
    try:
        agent.load(agent.manifest.configuration)
        agent.reset(context)
        clear = agent.step(_observation(context))
        hazard = agent.step(_observation(context, frame=1, obstacle=6.0))
        assert clear.frame == 0 and clear.control.throttle > 0.0
        assert hazard.frame == 1 and hazard.control.brake == 1.0
        assert hazard.diagnostics.metadata["nearest_lidar_return_m"] == pytest.approx(3.5)
        assert agent.stats.steps == 2
    finally:
        agent.close()
    assert agent.state == "closed"
