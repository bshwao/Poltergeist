"""Exercise asynchronous delivery, frame integrity, bounded memory and ownership."""

from threading import Timer
from types import SimpleNamespace as NS

import pytest

from shared.models import ActorState, TelemetryFrame
from simulator.errors import SimulationError
from simulator.protocols import SimulationOptions
from simulator.sensor_manager import SensorManager


class Blueprint:
    def __init__(self, name):
        self.name, self.attributes = name, {}

    def set_attribute(self, name, value):
        self.attributes[name] = value


class Sensor:
    def __init__(self, name, *, listen_error=False, stop_error=False, destroy_error=False):
        self.name, self.callback = name, None
        self.listen_error, self.stop_error, self.destroy_error = listen_error, stop_error, destroy_error
        self.stop_calls, self.destroy_calls = 0, 0
        self.destroy_result = True
        self.id = name

    def listen(self, callback):
        self.callback = callback
        if self.listen_error:
            raise RuntimeError("listen failed")

    def stop(self):
        self.stop_calls += 1
        if self.stop_error:
            raise RuntimeError("stop failed")

    def destroy(self):
        self.destroy_calls += 1
        if self.destroy_error:
            raise RuntimeError("destroy failed")
        return self.destroy_result


class World:
    def __init__(self):
        self.sensors, self.blueprints = {}, {}
        self.fail_listen = None
        self.no_rendering_mode = False

    def get_settings(self):
        return NS(no_rendering_mode=self.no_rendering_mode)

    def get_blueprint_library(self):
        return self

    def find(self, name):
        self.blueprints[name] = Blueprint(name)
        return self.blueprints[name]

    def spawn_actor(self, blueprint, transform, attach_to):
        sensor = Sensor(blueprint.name, listen_error=self.fail_listen == blueprint.name)
        sensor.transform = transform
        self.sensors[blueprint.name] = sensor
        return sensor

    def emit(self, name, frame, **kwargs):
        self.sensors[f"sensor.{name}"].callback(NS(frame=frame, **kwargs))


def setup_manager(**options):
    world = World()
    ego = NS(id=7, bounding_box=NS(location=NS(x=0.1, y=0.2, z=0.3), extent=NS(z=1.2)))
    manager = SensorManager(world, ego, NS(Transform=lambda location=None: NS(location=location), Location=NS), options=SimulationOptions(
        sensor_timeout_s=0.05, sensor_settle_s=0.002, **options,
    ), actor_ids={9: "pedestrian"})
    manager.open()
    return world, manager


def collision(world, frame, other=9):
    world.emit("other.collision", frame, other_actor=NS(id=other), normal_impulse=NS(x=3.0, y=4.0, z=0.0))


def telemetry(frame, collisions=None):
    return TelemetryFrame(frame=frame, time_s=float(frame), ego=ActorState(actor_id="ego"), collisions=collisions)


def test_collision_heartbeat_and_actor_mapping():
    world, manager = setup_manager()
    collision(world, 12)
    world.emit("other.imu", 12)
    events = manager.collect(12)
    assert [(event.other_actor_id, event.impulse_n_s) for event in events] == [("pedestrian", 5.0)]
    assert events[0].event_id.startswith("collision:7:12:")
    assert manager.finalize([telemetry(12, events)])[0].collisions == events
    manager.close()
    manager.close()
    assert all(sensor.stop_calls == sensor.destroy_calls == 1 for sensor in world.sensors.values())


def test_no_events_is_known_only_after_matching_heartbeat():
    world, manager = setup_manager()
    world.emit("other.imu", 11)
    with pytest.raises(SimulationError, match="timeout.*12.*imu"):
        manager.collect(12)
    manager.close()


def test_async_callback_can_arrive_while_collect_waits():
    world, manager = setup_manager()
    timer = Timer(0.005, lambda: world.emit("other.imu", 12))
    timer.start()
    assert manager.collect(12) == []
    timer.join()
    manager.close()


def test_settling_does_not_discard_late_collision_for_old_frame():
    world, manager = setup_manager()
    world.emit("other.imu", 12)
    assert manager.collect(12) == []
    collision(world, 12)
    collision(world, 13, other=10)
    world.emit("other.imu", 13)
    current = manager.collect(13)
    assert [event.other_actor_id for event in current] == ["world:10"]
    result = manager.finalize([telemetry(12, []), telemetry(13, current)])
    assert result[0].collisions[0].other_actor_id == "pedestrian"
    assert result[1].collisions == current
    manager.close()


def test_last_frame_callbacks_are_drained_after_stop():
    world, manager = setup_manager()
    world.emit("other.imu", 12)
    assert manager.collect(12) == []
    original_stop = world.sensors["sensor.other.collision"].stop

    def stop():
        collision(world, 12)
        original_stop()

    world.sensors["sensor.other.collision"].stop = stop
    result = manager.finalize([telemetry(12, [])])
    assert len(result[0].collisions) == 1
    manager.close()


def test_unmatched_collision_fails_instead_of_disappearing():
    world, manager = setup_manager()
    collision(world, 99)
    with pytest.raises(SimulationError, match="no telemetry frame.*99"):
        manager.finalize([])
    manager.close()


def test_periodic_stale_frame_fails_instead_of_satisfying_newer_barrier():
    world, manager = setup_manager()
    world.emit("other.imu", 10)
    world.emit("other.imu", 11)
    with pytest.raises(SimulationError, match="Uncollected imu"):
        manager.collect(11)
    manager.close()


def test_bounded_collision_queue_reports_loss():
    world, manager = setup_manager()
    manager.MAX_PENDING_COLLISIONS = 1
    collision(world, 10)
    collision(world, 11)
    with pytest.raises(SimulationError, match="overflow"):
        manager.collect(10)
    with pytest.raises(SimulationError, match="overflow"):
        manager.close()
    assert all(sensor.destroy_calls == 1 for sensor in world.sensors.values())


def test_optional_camera_and_lidar_are_exact_frame_barriers_and_drain_bytes():
    world, manager = setup_manager(camera=True, lidar=True)
    world.emit("other.imu", 12)
    world.emit("camera.rgb", 12, raw_data=b"abcd", timestamp=0.6, width=1, height=1)
    with pytest.raises(SimulationError, match="timeout.*lidar"):
        manager.collect(12)
    world.emit("lidar.ray_cast", 12, raw_data=b"0123456789abcdef", timestamp=0.6, channels=32)
    assert manager.collect(12) == []
    samples = manager.samples()
    assert [(sample.kind, sample.frame, sample.data) for sample in samples] == [
        ("camera", 12, b"abcd"), ("lidar", 12, b"0123456789abcdef"),
    ]
    assert samples[0].metadata == {"encoding": "bgra8", "width": 1, "height": 1}
    assert samples[1].metadata == {"encoding": "float32_xyzi", "channels": 32}
    assert manager.samples() == ()
    assert world.blueprints["sensor.camera.rgb"].attributes["sensor_tick"] == "0.0"
    assert world.sensors["sensor.camera.rgb"].transform.location.z == pytest.approx(1.8)
    assert world.sensors["sensor.lidar.ray_cast"].transform.location.z == pytest.approx(1.8)
    manager.finalize([telemetry(12, [])])
    manager.close()


def test_byte_overflow_propagates_and_cleanup_still_destroys_every_sensor():
    world, manager = setup_manager(camera=True)
    manager.MAX_SAMPLE_BYTES = 2
    world.emit("camera.rgb", 12, raw_data=b"abcd", timestamp=0.6, width=1, height=1)
    with pytest.raises(SimulationError, match="byte queue overflow"):
        manager.samples()
    with pytest.raises(SimulationError, match="byte queue overflow"):
        manager.close()
    assert all(sensor.destroy_calls == 1 for sensor in world.sensors.values())


def test_camera_rejects_no_rendering_world_before_attaching_sensors():
    world = World()
    world.no_rendering_mode = True
    manager = SensorManager(world, NS(id=7), NS(Transform=NS), options=SimulationOptions(camera=True), actor_ids={})
    with pytest.raises(SimulationError, match="requires CARLA rendering"):
        manager.open()
    assert not world.sensors
    manager.close()


@pytest.mark.parametrize("raw,width,height", [(b"", 1, 1), (b"abc", 1, 1), (b"", 0, 1)])
def test_invalid_camera_bytes_cannot_be_counted_as_capture(raw, width, height):
    world, manager = setup_manager(camera=True)
    world.emit("camera.rgb", 12, raw_data=raw, timestamp=0.6, width=width, height=height)
    with pytest.raises(SimulationError, match="BGRA image dimensions"):
        manager.samples()
    with pytest.raises(SimulationError, match="BGRA image dimensions"):
        manager.close()
    assert all(sensor.destroy_calls == 1 for sensor in world.sensors.values())


def test_lidar_rejects_incomplete_xyzi_point():
    world, manager = setup_manager(lidar=True)
    world.emit("lidar.ray_cast", 12, raw_data=b"bad", timestamp=0.6, channels=32)
    with pytest.raises(SimulationError, match="float32 XYZI"):
        manager.samples()
    with pytest.raises(SimulationError, match="float32 XYZI"):
        manager.close()


def test_partial_attachment_cleans_up_even_sensor_whose_listen_failed():
    world = World()
    world.fail_listen = "sensor.other.imu"
    manager = SensorManager(world, NS(id=7), NS(Transform=NS), options=SimulationOptions(), actor_ids={})
    with pytest.raises(SimulationError, match="listen failed"):
        manager.open()
    assert len(world.sensors) == 2
    assert all(sensor.destroy_calls == 1 for sensor in world.sensors.values())
    manager.close()


def test_cleanup_attempts_all_sensors_after_individual_failures():
    world, manager = setup_manager()
    world.sensors["sensor.other.imu"].stop_error = True
    world.sensors["sensor.other.collision"].destroy_error = True
    with pytest.raises(SimulationError, match="stop failed.*destroy failed"):
        manager.close()
    assert all(sensor.destroy_calls == 1 for sensor in world.sensors.values())
    assert manager._sensors == [world.sensors["sensor.other.collision"]]
    world.sensors["sensor.other.collision"].destroy_error = False
    manager.close()


@pytest.mark.parametrize("failure", ["exception", "false"])
def test_failed_destroy_retains_handle_and_retry_only_destroys_remaining_sensor(failure):
    world, manager = setup_manager()
    sensor = world.sensors["sensor.other.imu"]
    if failure == "exception":
        sensor.destroy_error = True
    else:
        sensor.destroy_result = False
    with pytest.raises(SimulationError, match="destroy sensor"):
        manager.close()
    assert manager._sensors == [sensor]
    assert not manager._closed
    assert all(item.destroy_calls == 1 for item in world.sensors.values())
    sensor.destroy_error, sensor.destroy_result = False, True
    manager.close()
    assert manager._sensors == []
    assert manager._closed
    assert sensor.destroy_calls == 2
    assert world.sensors["sensor.other.collision"].destroy_calls == 1
    manager.close()
    assert sensor.destroy_calls == 2


def test_callback_after_finalization_is_explicit_error_on_close():
    world, manager = setup_manager()
    manager.finalize([])
    collision(world, 12)
    with pytest.raises(SimulationError, match="after sensor finalization"):
        manager.close()


def test_dynamic_actor_id_resolver_matches_late_foreign_actor_telemetry():
    world = World()
    actors = {9: "world:world:9"}
    manager = SensorManager(world, NS(id=7), NS(Transform=NS), options=SimulationOptions(
        sensor_timeout_s=0.05, sensor_settle_s=0.002,
    ), actor_ids={}, actor_id_resolver=lambda native: actors[native])
    manager.open()
    collision(world, 12)
    world.emit("other.imu", 12)
    assert manager.collect(12)[0].other_actor_id == "world:world:9"
    manager.close()
