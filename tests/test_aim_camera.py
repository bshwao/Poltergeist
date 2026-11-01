"""Released camera calibration, pixel scaling, route axes and fail-closed inputs."""
import base64
from collections import deque
import hashlib
from types import SimpleNamespace

import pytest

from companion.model_catalog import aim_manifest
from shared.models import ActorState, AgentManifest, AgentObservation, AgentReset, AgentSensorFrame, DrivingTask, Transform, Vector3
from simulator.agent_sensors import resolve_sensor_specs
from simulator.agents.aim import AIMCameraAgent
from simulator.agents.aim import checkpoint
from simulator.agents.aim.preprocessing import camera_rgb, RouteTarget, world_waypoints
from simulator.agents.sdk import AgentCompatibilityError, AgentLifecycleError


def test_catalogue_and_companion_startup_do_not_require_optional_model_dependencies():
    import subprocess
    import sys
    code = '''import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'numpy', 'torch', 'torchvision'}:
        raise AssertionError('Optional model dependency imported during startup: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from companion.controller import CompanionController
from companion.model_catalog import model_options
assert [item['id'] for item in model_options()] == ['plant', 'aim']
'''
    subprocess.run([sys.executable, '-B', '-c', code], check=True, timeout=30, capture_output=True)


def pose(x=0., y=0., yaw=0.):
    return Transform.model_validate({"position": {"x": float(x), "y": float(y)}, "rotation": {"yaw_deg": float(yaw)}})


def frame(raw=None, **changes):
    raw = bytes((11, 37, 255, 255)) * (400 * 300) if raw is None else raw
    return AgentSensorFrame.model_validate({"sensor_id": "front-camera", "type": "sensor.camera.rgb", "frame": 500,
        "timestamp_s": 0., "encoding": "bgra8", "data_base64": base64.b64encode(raw).decode(),
        "sha256": hashlib.sha256(raw).hexdigest(), "width": 400, "height": 300, "fov_deg": 100.} | changes)


def context():
    manifest = AgentManifest.model_validate(aim_manifest("C:/owned-fixture/aim.pth"))
    task = DrivingTask(destination=pose(20), target_speed_mps=4.)
    return AgentReset(protocol_version="1.3", seed=7, map_name="fixture", ego_actor_id="ego", fixed_delta_seconds=.05,
        destination=task.destination, route=[pose(0), pose(20)], driving_task=task,
        sensors=resolve_sensor_specs(manifest.sensors))


def resettable():
    agent = AIMCameraAgent()
    controller = lambda: SimpleNamespace(_window=deque([1.] * 40, maxlen=40), _min=-1., _max=1.)
    agent._model = SimpleNamespace(turn_controller=controller(), speed_controller=controller())
    agent.reset(context())
    return agent


def observation():
    return AgentObservation(protocol_version="1.3", observation_mode="sensors", frame=500, time_s=0., dt_s=.05,
        ego=ActorState(actor_id="ego", transform=pose(), velocity_mps=Vector3(x=2.)),
        ego_state_source="simulated_ego_kinematics", sensor_source="deterministic_fixture",
        driving_task=context().driving_task, sensors=[frame()])


def test_released_rgb_crop_and_training_pixel_scale_are_preserved():
    np = pytest.importorskip("numpy")
    image = camera_rgb(frame())
    assert image.shape == (3, 256, 256) and image.dtype == np.float32 and image.flags.c_contiguous
    assert list(image[:, 0, 0]) == [255., 37., 11.]
    pixels = np.zeros((300, 400, 4), dtype=np.uint8)
    pixels[22, 72] = [1, 2, 3, 255]
    pixels[277, 327] = [4, 5, 6, 255]
    image = camera_rgb(frame(pixels.tobytes()))
    assert list(image[:, 0, 0]) == [3., 2., 1.] and list(image[:, -1, -1]) == [6., 5., 4.]


def test_framework_route_and_predicted_waypoint_axes_match_released_policy():
    route = RouteTarget([pose(0, 0), pose(0, 10), pose(0, 20)])
    assert route.next(pose(0, 0, 90)) == pytest.approx((0., -10.))
    result = world_waypoints([[2., -3.]], pose(1, 2, 90))[0].position
    assert (result.x, result.y, result.z) == pytest.approx((-1., 5., 0.))
    assert RouteTarget([pose(0)]).next(pose(0)) == (0., 0.)


@pytest.mark.parametrize("change", [lambda c: setattr(c, "fixed_delta_seconds", .1),
    lambda c: c.sensors[0].attributes.update(fov=90.), lambda c: setattr(c.sensors[0].mount.position, "z", 1.5),
    lambda c: c.sensors.clear()])
def test_incompatible_camera_rig_or_rate_is_rejected_before_controls(change):
    agent = resettable()
    value = context()
    change(value)
    with pytest.raises(AgentCompatibilityError): agent.reset(value)
    with pytest.raises(AgentLifecycleError, match="Reset AIM"):
        agent.step(observation())


@pytest.mark.parametrize("changes", [{"actors": []}, {"observation_mode": "simulator_state"},
    {"sensor_source": None}, {"ego_state_source": "simulator_state"}, {"traffic_light_red": False},
    {"dt_s": .1}, {"frame": 501}])
def test_privileged_or_unsynchronized_observations_never_reach_the_model(changes):
    agent = resettable()
    with pytest.raises(AgentCompatibilityError): agent.step(observation().model_copy(update=changes))


def test_only_verified_bounded_checkpoint_bytes_are_returned(tmp_path, monkeypatch):
    path = tmp_path / "model.pth"
    path.write_bytes(b"verified fixture")
    monkeypatch.setattr(checkpoint, "CHECKPOINT_BYTES", len(b"verified fixture"))
    monkeypatch.setattr(checkpoint, "CHECKPOINT_SHA256", hashlib.sha256(b"verified fixture").hexdigest())
    verified = checkpoint.checkpoint_bytes(path)
    path.write_bytes(b"different bytes!")
    assert verified == b"verified fixture"
    with pytest.raises(ValueError): checkpoint.checkpoint_bytes(path)


def test_missing_weights_do_not_import_ml_libraries(tmp_path, monkeypatch):
    import builtins
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name in {"torch", "torchvision"}: pytest.fail("Missing checkpoint reached ML initialization")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    with pytest.raises(FileNotFoundError):
        AIMCameraAgent().load({"checkpoint_path": str(tmp_path / "missing.pth")})
