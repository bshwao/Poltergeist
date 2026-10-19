"""Bounded, frame-indexed CARLA sensor collection without importing CARLA.

The IMU is a periodic heartbeat, not a collision-stream delivery guarantee.
Collision callbacks receive a bounded settling window after each tick and again
when listeners stop. Late events are merged into their original frames; an
event that cannot be accounted for fails the run.
"""

from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from math import hypot
from threading import Condition
from time import perf_counter as monotonic
from typing import Any

from shared.models import CollisionEvent, TelemetryFrame

from .errors import SimulationError
from .protocols import SensorSample, SimulationOptions


class SensorManager:
    MAX_PENDING_FRAMES = 8
    MAX_PENDING_COLLISIONS = 4096
    MAX_SAMPLE_BYTES = 64 * 1024 * 1024

    def __init__(
        self, world: Any, ego: Any, carla_module: Any, *,
        options: SimulationOptions, actor_ids: Mapping[int, str],
        actor_id_resolver: Callable[[int], str] | None = None,
    ) -> None:
        self.world, self.ego, self.carla = world, ego, carla_module
        self.options = options
        self.actor_ids = dict(actor_ids)
        self._actor_id_resolver = actor_id_resolver
        self._condition = Condition()
        self._sensors: list[Any] = []
        self._stopped: set[int] = set()
        self._periodic: dict[str, set[int]] = {"imu": set()}
        self._collisions: dict[int, list[CollisionEvent]] = defaultdict(list)
        self._collision_count = 0
        self._sequence = 0
        self._samples: deque[SensorSample] = deque()
        self._sample_bytes = 0
        self._error: str | None = None
        self._opened = False
        self._closed = False
        self._finalized = False
        self._last_collected: int | None = None

    def open(self) -> None:
        if self._opened or self._closed:
            raise SimulationError("SensorManager can only be opened once")
        self._opened = True
        try:
            if self.options.camera and self.world.get_settings().no_rendering_mode:
                raise SimulationError("Camera capture requires CARLA rendering; disable no_rendering_mode before running")
            self._attach("sensor.other.collision", self._collision)
            self._attach("sensor.other.imu", lambda data: self._periodic_data("imu", data))
            if self.options.camera:
                self._periodic["camera"] = set()
                self._attach("sensor.camera.rgb", lambda data: self._periodic_data("camera", data), {
                    "image_size_x": str(self.options.camera_width),
                    "image_size_y": str(self.options.camera_height),
                    "sensor_tick": "0.0",
                }, transform=self._roof_mount())
            if self.options.lidar:
                self._periodic["lidar"] = set()
                self._attach("sensor.lidar.ray_cast", lambda data: self._periodic_data("lidar", data), {
                    "sensor_tick": "0.0", "range": "60", "channels": "32",
                }, transform=self._roof_mount())
        except Exception as exc:
            try:
                self.close()
            except Exception as cleanup_exc:
                raise SimulationError(f"Sensor attachment failed: {exc}; cleanup: {cleanup_exc}") from exc
            raise SimulationError(f"Sensor attachment failed: {exc}") from exc

    def _roof_mount(self) -> Any:
        box = self.ego.bounding_box
        return self.carla.Transform(self.carla.Location(
            x=float(box.location.x), y=float(box.location.y),
            z=float(box.location.z) + float(box.extent.z) + 0.3,
        ))

    def _attach(
        self, blueprint_id: str, callback: Any, attributes: dict[str, str] | None = None,
        *, transform: Any = None,
    ) -> None:
        blueprint = self.world.get_blueprint_library().find(blueprint_id)
        for name, value in (attributes or {}).items():
            blueprint.set_attribute(name, value)
        sensor = self.world.spawn_actor(
            blueprint, self.carla.Transform() if transform is None else transform, attach_to=self.ego,
        )
        if sensor is None:
            raise SimulationError(f"Could not spawn {blueprint_id}")
        self._sensors.append(sensor)  # Own the sensor before listen can fail.
        sensor.listen(callback)

    def _fail(self, message: str) -> None:
        if self._error is None:
            self._error = message
        self._condition.notify_all()

    def _collision(self, data: Any) -> None:
        with self._condition:
            try:
                if self._finalized or self._closed:
                    self._fail("Collision callback arrived after sensor finalization")
                    return
                if self._collision_count >= self.MAX_PENDING_COLLISIONS:
                    self._fail("Collision callback queue overflow; event coverage is incomplete")
                    return
                self._sequence += 1
                other = data.other_actor
                impulse = data.normal_impulse
                event = CollisionEvent(
                    other_actor_id=(self._actor_id_resolver(int(other.id)) if self._actor_id_resolver
                                    else self.actor_ids.get(int(other.id), f"world:{other.id}")),
                    impulse_n_s=float(hypot(impulse.x, impulse.y, impulse.z)),
                    event_id=f"collision:{self.ego.id}:{data.frame}:{self._sequence}",
                )
                self._collisions[int(data.frame)].append(event)
                self._collision_count += 1
                self._condition.notify_all()
            except Exception as exc:
                self._fail(f"Invalid collision callback: {exc}")

    def _periodic_data(self, kind: str, data: Any) -> None:
        with self._condition:
            try:
                if self._finalized or self._closed:
                    self._fail(f"{kind} callback arrived after sensor finalization")
                    return
                frame = int(data.frame)
                pending = self._periodic[kind]
                if self._last_collected is not None and frame <= self._last_collected:
                    self._fail(f"Duplicate/stale {kind} callback for frame {frame}")
                    return
                if frame in pending:
                    self._fail(f"Duplicate {kind} callback for frame {frame}")
                    return
                if len(pending) >= self.MAX_PENDING_FRAMES:
                    self._fail(f"{kind} frame queue overflow")
                    return
                if kind != "imu":
                    raw = bytes(data.raw_data)
                    if self._sample_bytes + len(raw) > self.MAX_SAMPLE_BYTES:
                        self._fail("Camera/LiDAR byte queue overflow; drain samples after every tick")
                        return
                    if len(self._samples) >= self.MAX_PENDING_FRAMES * 2:
                        self._fail("Camera/LiDAR sample queue overflow; drain samples after every tick")
                        return
                    metadata = {"encoding": "bgra8" if kind == "camera" else "float32_xyzi"}
                    if kind == "camera":
                        width, height = int(data.width), int(data.height)
                        if width <= 0 or height <= 0 or len(raw) != width * height * 4:
                            raise SimulationError("Camera byte length does not match positive BGRA image dimensions")
                        metadata.update(width=width, height=height)
                    else:
                        channels = int(data.channels)
                        if channels <= 0 or len(raw) % 16:
                            raise SimulationError("LiDAR data must contain float32 XYZI points and positive channels")
                        metadata.update(channels=channels)
                    self._samples.append(SensorSample(
                        kind=kind, frame=frame, timestamp=float(data.timestamp),
                        data=raw, metadata=metadata,
                    ))
                    self._sample_bytes += len(raw)
                pending.add(frame)
                self._condition.notify_all()
            except Exception as exc:
                self._fail(f"Invalid {kind} callback: {exc}")

    def _raise_error(self) -> None:
        if self._error is not None:
            raise SimulationError(self._error)

    def collect(self, frame: int) -> list[CollisionEvent]:
        if not self._opened or self._closed or self._finalized:
            raise SimulationError("Sensors must be open when collecting a frame")
        with self._condition:
            self._raise_error()
            if self._last_collected is not None and frame <= self._last_collected:
                raise SimulationError("Sensor frames must be collected in strictly increasing order")
            deadline = monotonic() + self.options.sensor_timeout_s
            while not all(frame in pending for pending in self._periodic.values()):
                self._raise_error()
                remaining = deadline - monotonic()
                if remaining <= 0:
                    missing = [kind for kind, pending in self._periodic.items() if frame not in pending]
                    raise SimulationError(f"Sensor timeout for frame {frame}: {', '.join(missing)}")
                self._condition.wait(remaining)
            for kind, pending in self._periodic.items():
                if any(value < frame for value in pending):
                    raise SimulationError(f"Uncollected {kind} callbacks precede frame {frame}")
            # Separate streams have no cross-sensor ordering guarantee.
            settle_deadline = monotonic() + self.options.sensor_settle_s
            while (remaining := settle_deadline - monotonic()) > 0:
                self._condition.wait(remaining)
                self._raise_error()
            for pending in self._periodic.values():
                pending.remove(frame)
            self._last_collected = frame
            events = self._collisions.pop(frame, [])
            self._collision_count -= len(events)
            return events

    def samples(self) -> tuple[SensorSample, ...]:
        with self._condition:
            self._raise_error()
            samples = tuple(self._samples)
            self._samples.clear()
            self._sample_bytes = 0
            return samples

    def _stop(self) -> list[str]:
        errors = []
        for sensor in self._sensors:
            if id(sensor) in self._stopped:
                continue
            try:
                sensor.stop()
                self._stopped.add(id(sensor))
            except Exception as exc:
                errors.append(f"stop sensor {getattr(sensor, 'id', '?')}: {exc}")
        return errors

    def finalize(self, frames: list[TelemetryFrame]) -> list[TelemetryFrame]:
        if self._closed or self._finalized:
            raise SimulationError("Sensors have already been finalized/closed")
        # Allow in-flight events to arrive before cutting off the stream.
        with self._condition:
            deadline = monotonic() + self.options.sensor_settle_s
            while (remaining := deadline - monotonic()) > 0:
                self._condition.wait(remaining)
        errors = self._stop()
        with self._condition:
            deadline = monotonic() + self.options.sensor_settle_s
            while (remaining := deadline - monotonic()) > 0:
                self._condition.wait(remaining)
            self._finalized = True
            self._raise_error()
            if errors:
                raise SimulationError("; ".join(errors))
            if any(self._periodic.values()):
                raise SimulationError("Periodic sensor callbacks have no collected telemetry frame")
            indexed = {frame.frame: index for index, frame in enumerate(frames)}
            if len(indexed) != len(frames):
                raise SimulationError("Cannot merge sensor events into duplicate telemetry frames")
            unmatched = set(self._collisions) - indexed.keys()
            if unmatched:
                raise SimulationError(f"Collision callbacks have no telemetry frame: {sorted(unmatched)}")
            result = list(frames)
            for frame_id, events in self._collisions.items():
                index = indexed[frame_id]
                previous = result[index]
                result[index] = previous.model_copy(update={"collisions": [*(previous.collisions or []), *events]})
            self._collisions.clear()
            self._collision_count = 0
            return result

    def close(self) -> None:
        if self._closed:
            return
        errors = self._stop()
        remaining = []
        for sensor in reversed(self._sensors):
            try:
                if sensor.destroy() is False:
                    errors.append(f"destroy sensor {getattr(sensor, 'id', '?')} returned False")
                    remaining.append(sensor)
            except Exception as exc:
                errors.append(f"destroy sensor {getattr(sensor, 'id', '?')}: {exc}")
                remaining.append(sensor)
        # Retain ownership after a transient server failure so callers can retry.
        self._sensors = list(reversed(remaining))
        self._closed = not self._sensors
        if self._error:
            errors.append(self._error)
        if errors:
            raise SimulationError("; ".join(errors))
