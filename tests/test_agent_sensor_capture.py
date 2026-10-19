"""Declared CARLA streams retain their identity, bytes, and native-frame barrier."""

from math import nan
import struct
from threading import Event, Timer
from time import perf_counter
from types import SimpleNamespace as NS

import pytest

from shared.models import AgentSensorSpec, ActorState, TelemetryFrame, Transform, Vector3
from simulator.carla_adapter import CarlaSimulationAdapter
from simulator.errors import SimulationCancelled, SimulationError
from simulator.protocols import SimulationOptions
from simulator.sensor_manager import SensorManager


class Blueprint:
    def __init__(self, name):
        self.name, self.attributes = name, {}

    def set_attribute(self, name, value):
        self.attributes[name] = value


class Sensor:
    def __init__(self, blueprint, transform):
        self.blueprint, self.transform = blueprint, transform
        self.callback = None
        self.id = id(self)
        self.stop_calls = self.destroy_calls = 0

    def listen(self, callback):
        self.callback = callback

    def stop(self):
        self.stop_calls += 1

    def destroy(self):
        self.destroy_calls += 1
        return True


class World:
    def __init__(self, *, no_rendering=False):
        self.sensors = []
        self.no_rendering = no_rendering
        self.fail_listen_at = None

    def get_settings(self):
        return NS(no_rendering_mode=self.no_rendering)

    def get_blueprint_library(self):
        return self

    def find(self, name):
        return Blueprint(name)

    def spawn_actor(self, blueprint, transform, attach_to):
        sensor = Sensor(blueprint, transform)
        self.sensors.append(sensor)
        if len(self.sensors) == self.fail_listen_at:
            def fail(callback):
                raise RuntimeError("agent listener failed")
            sensor.listen = fail
        return sensor


def camera(sensor_id="front", **attrs):
    return AgentSensorSpec(id=sensor_id, type="sensor.camera.rgb", attributes={
        "image_size_x": 2, "image_size_y": 1, "fov": 80, **attrs,
    })


def lidar(sensor_id="roof", **attrs):
    return AgentSensorSpec(id=sensor_id, type="sensor.lidar.ray_cast", attributes={
        "channels": 2, "points_per_second": 2000, "rotation_frequency": 20,
        "range": 20, **attrs,
    })


def make_manager(specs, *, world=None, cancellation=None, **options):
    world = World() if world is None else world
    carla = NS(Transform=lambda location=None, rotation=None: NS(location=location, rotation=rotation),
               Location=NS, Rotation=NS)
    ego = NS(id=7, bounding_box=NS(location=NS(x=0, y=0, z=0), extent=NS(z=1)))
    manager = SensorManager(world, ego, carla, options=SimulationOptions(
        sensor_timeout_s=0.08, sensor_settle_s=0.001, **options,
    ), actor_ids={}, agent_sensors=specs, cancellation=cancellation)
    manager.open()
    return world, manager


def emit(world, index, frame=12, **kwargs):
    sensor = world.sensors[index]
    attrs = sensor.blueprint.attributes
    if sensor.blueprint.name == "sensor.camera.rgb":
        payload = dict(raw_data=b"abcdefgh", width=int(attrs["image_size_x"]),
                       height=int(attrs["image_size_y"]), fov=float(attrs.get("fov", 80)), timestamp=0.6)
    elif sensor.blueprint.name == "sensor.lidar.ray_cast":
        payload = dict(raw_data=struct.pack("<ffff", 1, 2, 3, 0.5), channels=int(attrs["channels"]), timestamp=0.6)
    else:
        payload = {}
    sensor.callback(NS(frame=frame, **(payload | kwargs)))


def telemetry(frame=12):
    return TelemetryFrame(frame=frame, time_s=0, ego=ActorState(actor_id="ego"))


def test_non_round_camera_fov_accepts_native_float32_calibration():
    requested = 139.1234567
    native = struct.unpack("<f", struct.pack("<f", requested))[0]
    assert abs(native - requested) > 1e-6
    world, manager = make_manager([camera(fov=requested)])
    try:
        emit(world, 1)
        emit(world, 2, fov=native)
        manager.collect(12)
        assert manager.agent_samples(12)[0].metadata["fov_deg"] == native
    finally:
        manager.close()


def test_two_cameras_with_same_type_keep_declared_ids_mounts_and_lossless_bytes():
    left = camera("left").model_copy(update={"mount": Transform(position=Vector3(x=2, y=-0.5, z=1.8))})
    world, manager = make_manager([left, camera("right")])
    emit(world, 1)
    emit(world, 3, raw_data=b"ijklmnop")
    emit(world, 2)
    manager.collect(12)
    samples = manager.agent_samples(12)
    assert [sample.metadata["agent_sensor_id"] for sample in samples] == ["left", "right"]
    assert [sample.data for sample in samples] == [b"abcdefgh", b"ijklmnop"]
    assert samples[0].timestamp == 0.6
    assert samples[0].metadata == {
        "source": "carla_sensors", "agent_sensor_id": "left", "sensor_type": "sensor.camera.rgb",
        "mount": left.mount.model_dump(mode="json"), "encoding": "bgra8", "width": 2, "height": 1,
        "fov_deg": 80.0,
    }
    assert world.sensors[2].transform.location.y == -0.5
    assert world.sensors[2].transform.location.z == 1.8
    assert manager.samples() == ()
    manager.finalize([telemetry()])
    manager.close()


def test_recording_and_agent_streams_of_same_type_are_independent():
    world, manager = make_manager([camera()], camera=True, camera_width=2, camera_height=1)
    emit(world, 1)
    emit(world, 2)
    with pytest.raises(SimulationError, match="timeout.*agent:front"):
        manager.collect(12)
    emit(world, 3, raw_data=b"ijklmnop")
    manager.collect(12)
    assert manager.samples()[0].data == b"abcdefgh"
    assert manager.agent_samples(12)[0].data == b"ijklmnop"
    manager.close()


def test_all_declared_streams_arrive_asynchronously_without_repeating_latest():
    world, manager = make_manager([camera(), lidar()])
    emit(world, 1)
    emit(world, 2)
    timer = Timer(0.01, lambda: emit(world, 3))
    timer.start()
    manager.collect(12)
    timer.join()
    samples = manager.agent_samples(12)
    assert [(sample.kind, sample.frame) for sample in samples] == [("camera", 12), ("lidar", 12)]
    assert samples[1].metadata["channels"] == 2
    emit(world, 1, 13)
    with pytest.raises(SimulationError, match="timeout.*13.*agent:front.*agent:roof"):
        manager.collect(13)
    manager.close()


def test_newer_sensor_frame_cannot_satisfy_missing_exact_frame():
    world, manager = make_manager([camera()])
    emit(world, 1, 12)
    emit(world, 2, 13)
    with pytest.raises(SimulationError, match="timeout.*12.*agent:front"):
        manager.collect(12)
    manager.close()


@pytest.mark.parametrize("changes,message", [
    ({"frame": True}, "Native sensor frame"),
    ({"frame": -1}, "Native sensor frame"),
    ({"timestamp": nan}, "timestamp"),
    ({"timestamp": -0.1}, "timestamp"),
    ({"timestamp": "0.6"}, "timestamp"),
    ({"width": 1}, "declared calibration"),
    ({"height": True}, "native integers"),
    ({"raw_data": b"bad"}, "BGRA image dimensions"),
    ({"fov": 90}, "FOV"),
])
def test_invalid_native_camera_callback_fails_run_and_all_resources_are_destroyed(changes, message):
    world, manager = make_manager([camera()])
    emit(world, 2, **changes)
    with pytest.raises(SimulationError, match=message):
        manager.collect(12)
    with pytest.raises(SimulationError, match=message):
        manager.close()
    assert all(sensor.destroy_calls == 1 for sensor in world.sensors)


@pytest.mark.parametrize("changes,message", [
    ({"channels": 3}, "declared calibration"),
    ({"channels": True}, "declared calibration"),
    ({"raw_data": b"bad"}, "complete float32 XYZI"),
    ({"raw_data": struct.pack("<ffff", nan, 0, 0, 0)}, "finite float32"),
])
def test_invalid_lidar_callback_is_never_delivered(changes, message):
    world, manager = make_manager([lidar()])
    emit(world, 2, **changes)
    with pytest.raises(SimulationError, match=message):
        manager.agent_samples(12)
    with pytest.raises(SimulationError, match=message):
        manager.close()


@pytest.mark.parametrize("after_collect", [False, True])
def test_duplicate_and_stale_callback_errors_are_explicit(after_collect):
    world, manager = make_manager([camera()])
    emit(world, 1)
    emit(world, 2)
    if after_collect:
        manager.collect(12)
    emit(world, 2)
    with pytest.raises(SimulationError, match="Duplicate"):
        manager.agent_samples(12)
    with pytest.raises(SimulationError, match="Duplicate"):
        manager.close()


def test_agent_packet_can_only_be_consumed_once_after_its_collection():
    world, manager = make_manager([camera()])
    emit(world, 1)
    emit(world, 2)
    with pytest.raises(SimulationError, match="collected telemetry"):
        manager.agent_samples(12)
    manager.collect(12)
    manager.agent_samples(12)
    with pytest.raises(SimulationError, match="only be consumed once"):
        manager.agent_samples(12)
    manager.close()


def test_unconsumed_agent_frames_have_bounded_memory():
    world, manager = make_manager([camera()])
    manager.MAX_PENDING_FRAMES = 2
    for frame in (12, 13):
        emit(world, 1, frame)
        emit(world, 2, frame)
        manager.collect(frame)
    emit(world, 2, 14)
    with pytest.raises(SimulationError, match="sample frame queue overflow"):
        manager.agent_samples(12)
    with pytest.raises(SimulationError, match="sample frame queue overflow"):
        manager.close()


def test_aggregate_byte_bound_cannot_be_bypassed_by_multiple_sensors(monkeypatch):
    import simulator.sensor_manager as module
    world, manager = make_manager([camera("one"), camera("two")])
    monkeypatch.setattr(module, "MAX_SENSOR_PAYLOAD_BYTES", 12)
    emit(world, 2)
    emit(world, 3)
    with pytest.raises(SimulationError, match="aggregate raw byte limit"):
        manager.collect(12)
    with pytest.raises(SimulationError, match="aggregate raw byte limit"):
        manager.close()


def test_no_rendering_rejects_agent_camera_before_any_attachment():
    world = World(no_rendering=True)
    with pytest.raises(SimulationError, match="requires CARLA rendering"):
        make_manager([camera()], world=world)
    assert world.sensors == []


def test_lidar_works_with_rendering_disabled():
    world, manager = make_manager([lidar()], world=World(no_rendering=True))
    emit(world, 1)
    emit(world, 2)
    manager.collect(12)
    assert manager.agent_samples(12)[0].kind == "lidar"
    manager.close()


def test_partial_agent_listener_failure_destroys_every_owned_sensor():
    world = World()
    world.fail_listen_at = 4
    with pytest.raises(SimulationError, match="agent listener failed"):
        make_manager([camera(), lidar()], world=world)
    assert len(world.sensors) == 4
    assert all(sensor.stop_calls == sensor.destroy_calls == 1 for sensor in world.sensors)


def test_cancelled_sensor_wait_returns_quickly_and_preserves_cancellation_type():
    cancelled = Event()
    world, manager = make_manager([camera()], cancellation=cancelled.is_set)
    manager.options = manager.options.model_copy(update={"sensor_timeout_s": 2})
    timer = Timer(0.01, cancelled.set)
    timer.start()
    started = perf_counter()
    with pytest.raises(SimulationCancelled, match="cancelled"):
        manager.collect(12)
    assert perf_counter() - started < 0.5
    timer.join()
    manager.close()


def test_final_packet_can_be_discarded_but_older_unconsumed_packets_cannot():
    world, manager = make_manager([camera()])
    emit(world, 1)
    emit(world, 2)
    manager.collect(12)
    manager.finalize([telemetry()])
    assert manager._agent_samples == {}
    manager.close()
    world, manager = make_manager([camera()])
    for frame in (12, 13):
        emit(world, 1, frame)
        emit(world, 2, frame)
        manager.collect(frame)
    with pytest.raises(SimulationError, match="not consumed for every telemetry"):
        manager.finalize([telemetry(12), telemetry(13)])
    manager.close()


def test_adapter_configuration_is_frozen_before_open_and_samples_delegate_exact_frame():
    adapter = CarlaSimulationAdapter()
    specs = [camera()]
    adapter.configure_agent_sensors(specs)
    specs[0].attributes["fov"] = 90
    assert float(adapter._agent_sensors[0].attributes["fov"]) == 80
    with pytest.raises(SimulationError, match="time origin"):
        adapter.sensor_time_origin_s()
    adapter._opened = True
    with pytest.raises(SimulationError, match="before opening"):
        adapter.configure_agent_sensors([])
    called = []
    adapter.sensors = NS(agent_samples=lambda frame: called.append(frame) or ("native",))
    assert adapter.agent_sensor_samples(telemetry(123)) == ("native",)
    assert called == [123]
    adapter._origin_time = 53.4
    assert adapter.sensor_time_origin_s() == 53.4


def test_adapter_rejects_explicit_no_rendering_camera_without_connecting():
    adapter = CarlaSimulationAdapter(options=SimulationOptions(no_rendering_mode=True))
    with pytest.raises(SimulationError, match="requires CARLA rendering"):
        adapter.configure_agent_sensors([camera()])
    assert adapter.world is None
    adapter.configure_agent_sensors([lidar()])
