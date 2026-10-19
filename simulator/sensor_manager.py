"""Bounded, frame-indexed CARLA sensor collection without importing CARLA.

The IMU is a periodic heartbeat, not a collision-stream delivery guarantee.
Collision callbacks receive a bounded settling window after each tick and again
when listeners stop. Late events are merged into their original frames; an
event that cannot be accounted for fails the run.
"""

from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from math import hypot, isfinite
import struct
from threading import Condition
from time import perf_counter as monotonic
from typing import Any

from shared.models import AgentSensorSpec, CollisionEvent, TelemetryFrame
from shared.models.agent import MAX_SENSOR_PAYLOAD_BYTES

from .errors import SimulationCancelled, SimulationError
from .protocols import SensorSample, SimulationOptions


class SensorManager:
    MAX_PENDING_FRAMES = 8
    MAX_PENDING_COLLISIONS = 4096
    MAX_SAMPLE_BYTES = 64 * 1024 * 1024

    def __init__(
        self, world: Any, ego: Any, carla_module: Any, *,
        options: SimulationOptions, actor_ids: Mapping[int, str],
        actor_id_resolver: Callable[[int], str] | None = None,
        agent_sensors: tuple[AgentSensorSpec, ...] | list[AgentSensorSpec] = (),
        cancellation: Callable[[], bool] | None = None,
    ) -> None:
        self.world, self.ego, self.carla = world, ego, carla_module
        self.options = options
        self.actor_ids = dict(actor_ids)
        self._actor_id_resolver = actor_id_resolver
        self._cancellation = (lambda: False) if cancellation is None else cancellation
        if not callable(self._cancellation):
            raise TypeError("Sensor cancellation check must be callable")
        if agent_sensors:
            from .agent_sensors import resolve_sensor_specs
            self.agent_sensors = tuple(resolve_sensor_specs(list(agent_sensors)))
        else:
            self.agent_sensors = ()
        self._agent_specs = {spec.id: spec for spec in self.agent_sensors}
        self._condition = Condition()
        self._sensors: list[Any] = []
        self._stopped: set[int] = set()
        self._periodic: dict[str, set[int]] = {"imu": set()}
        self._agent_periodic: dict[str, set[int]] = {spec.id: set() for spec in self.agent_sensors}
        self._agent_samples: dict[int, dict[str, SensorSample]] = {}
        self._agent_frame_bytes: dict[int, int] = {}
        self._last_agent_delivered: int | None = None
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
            self._check_cancellation()
            needs_camera = self.options.camera or any(spec.type == "sensor.camera.rgb" for spec in self.agent_sensors)
            if needs_camera and self.world.get_settings().no_rendering_mode:
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
            for spec in self.agent_sensors:
                self._check_cancellation()
                self._attach(
                    spec.type, lambda data, sensor_id=spec.id: self._agent_data(sensor_id, data),
                    {name: str(value) for name, value in spec.attributes.items()},
                    transform=self._agent_mount(spec),
                )
        except Exception as exc:
            try:
                self.close()
            except Exception as cleanup_exc:
                raise SimulationError(f"Sensor attachment failed: {exc}; cleanup: {cleanup_exc}") from exc
            if isinstance(exc, SimulationCancelled):
                raise
            raise SimulationError(f"Sensor attachment failed: {exc}") from exc

    def _check_cancellation(self) -> None:
        if self._cancellation():
            raise SimulationCancelled("Simulation cancelled while waiting for sensor data")

    def set_cancellation(self, check: Callable[[], bool]) -> None:
        if not callable(check):
            raise TypeError("Sensor cancellation check must be callable")
        self._cancellation = check
        with self._condition:
            self._condition.notify_all()

    def _agent_mount(self, spec: AgentSensorSpec) -> Any:
        point, rotation = spec.mount.position, spec.mount.rotation
        return self.carla.Transform(
            self.carla.Location(x=point.x, y=point.y, z=point.z),
            self.carla.Rotation(pitch=rotation.pitch_deg, yaw=rotation.yaw_deg, roll=rotation.roll_deg),
        )

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

    def _agent_data(self, sensor_id: str, data: Any) -> None:
        """Keep declared streams separate from optional recording sensors."""
        with self._condition:
            try:
                if self._finalized or self._closed:
                    self._fail(f"Agent sensor {sensor_id} callback arrived after sensor finalization")
                    return
                frame = data.frame
                if type(frame) is not int or frame < 0:
                    raise SimulationError("Native sensor frame must be a non-negative integer")
                timestamp = data.timestamp
                if type(timestamp) not in (float, int) or not isfinite(timestamp) or timestamp < 0:
                    raise SimulationError("Native sensor timestamp must be finite and non-negative")
                timestamp = float(timestamp)
                pending = self._agent_periodic[sensor_id]
                if self._last_collected is not None and frame <= self._last_collected:
                    raise SimulationError(f"Duplicate/stale agent sensor {sensor_id} callback for frame {frame}")
                if frame in pending or sensor_id in self._agent_samples.get(frame, {}):
                    raise SimulationError(f"Duplicate agent sensor {sensor_id} callback for frame {frame}")
                if len(pending) >= self.MAX_PENDING_FRAMES:
                    raise SimulationError(f"Agent sensor {sensor_id} frame queue overflow")
                if frame not in self._agent_samples and len(self._agent_samples) >= self.MAX_PENDING_FRAMES:
                    raise SimulationError("Agent sensor sample frame queue overflow; drain samples after every tick")
                raw_view = memoryview(data.raw_data)
                frame_bytes = self._agent_frame_bytes.get(frame, 0) + raw_view.nbytes
                if frame_bytes > MAX_SENSOR_PAYLOAD_BYTES:
                    raise SimulationError("Agent sensor aggregate raw byte limit exceeded for frame")
                raw = bytes(raw_view)
                spec = self._agent_specs[sensor_id]
                metadata: dict[str, Any] = {
                    "source": "carla_sensors", "agent_sensor_id": sensor_id, "sensor_type": spec.type,
                    "mount": spec.mount.model_dump(mode="json"),
                }
                if spec.type == "sensor.camera.rgb":
                    width, height = data.width, data.height
                    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
                        raise SimulationError("Camera dimensions must be positive native integers")
                    if (width != int(spec.attributes["image_size_x"])
                            or height != int(spec.attributes["image_size_y"])):
                        raise SimulationError("Camera dimensions do not match declared calibration")
                    if len(raw) != width * height * 4:
                        raise SimulationError("Camera byte length does not match BGRA image dimensions")
                    fov = float(spec.attributes["fov"])
                    if hasattr(data, "fov") and (not isfinite(float(data.fov)) or abs(float(data.fov) - fov) > 1e-6):
                        raise SimulationError("Camera FOV does not match declared calibration")
                    metadata.update(encoding="bgra8", width=width, height=height, fov_deg=fov)
                    kind = "camera"
                elif spec.type == "sensor.lidar.ray_cast":
                    channels = data.channels
                    if type(channels) is not int or channels <= 0 or channels != int(spec.attributes["channels"]):
                        raise SimulationError("LiDAR channels do not match declared calibration")
                    if len(raw) % 16:
                        raise SimulationError("LiDAR data must contain complete float32 XYZI points")
                    if any(not isfinite(value[0]) for value in struct.iter_unpack("<f", raw)):
                        raise SimulationError("LiDAR data must contain finite float32 XYZI points")
                    metadata.update(encoding="float32_xyzi", channels=channels)
                    kind = "lidar"
                else:
                    raise SimulationError(f"Unsupported declared agent sensor: {spec.type}")
                self._agent_samples.setdefault(frame, {})[sensor_id] = SensorSample(
                    kind=kind, frame=frame, timestamp=timestamp, data=raw, metadata=metadata,
                )
                self._agent_frame_bytes[frame] = frame_bytes
                pending.add(frame)
                self._condition.notify_all()
            except Exception as exc:
                self._fail(f"Invalid agent sensor {sensor_id} callback: {exc}")

    def collect(self, frame: int) -> list[CollisionEvent]:
        if not self._opened or self._closed or self._finalized:
            raise SimulationError("Sensors must be open when collecting a frame")
        with self._condition:
            self._raise_error()
            self._check_cancellation()
            if self._last_collected is not None and frame <= self._last_collected:
                raise SimulationError("Sensor frames must be collected in strictly increasing order")
            deadline = monotonic() + self.options.sensor_timeout_s
            barriers = {**self._periodic, **{f"agent:{name}": pending for name, pending in self._agent_periodic.items()}}
            while not all(frame in pending for pending in barriers.values()):
                self._raise_error()
                self._check_cancellation()
                remaining = deadline - monotonic()
                if remaining <= 0:
                    missing = [kind for kind, pending in barriers.items() if frame not in pending]
                    raise SimulationError(f"Sensor timeout for frame {frame}: {', '.join(missing)}")
                self._condition.wait(min(remaining, 0.1))
            for kind, pending in barriers.items():
                if any(value < frame for value in pending):
                    raise SimulationError(f"Uncollected {kind} callbacks precede frame {frame}")
            # Separate streams have no cross-sensor ordering guarantee.
            settle_deadline = monotonic() + self.options.sensor_settle_s
            while (remaining := settle_deadline - monotonic()) > 0:
                self._check_cancellation()
                self._condition.wait(min(remaining, 0.1))
                self._raise_error()
            for pending in barriers.values():
                pending.remove(frame)
            self._last_collected = frame
            events = self._collisions.pop(frame, [])
            self._collision_count -= len(events)
            return events

    def agent_samples(self, frame: int) -> tuple[SensorSample, ...]:
        """Consume one complete native-frame packet in declaration order."""
        with self._condition:
            self._raise_error()
            if not self.agent_sensors:
                return ()
            if type(frame) is not int or frame < 0:
                raise SimulationError("Agent sensor sample frame must be a non-negative integer")
            if self._last_collected is None or frame > self._last_collected:
                raise SimulationError("Agent sensor samples require a collected telemetry frame")
            if self._last_agent_delivered is not None and frame <= self._last_agent_delivered:
                raise SimulationError("Agent sensor samples can only be consumed once per increasing frame")
            if not self._agent_samples or min(self._agent_samples) != frame:
                raise SimulationError("Agent sensor samples must consume every collected frame in order")
            indexed = self._agent_samples[frame]
            if set(indexed) != set(self._agent_specs):
                raise SimulationError("Agent sensor packet is incomplete")
            samples = tuple(indexed[spec.id] for spec in self.agent_sensors)
            del self._agent_samples[frame]
            del self._agent_frame_bytes[frame]
            self._last_agent_delivered = frame
            return samples

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
            if any(self._periodic.values()) or any(self._agent_periodic.values()):
                raise SimulationError("Periodic sensor callbacks have no collected telemetry frame")
            if self._agent_samples:
                # A failed/cancelled step can leave the last measured packet
                # without an agent action. Earlier packets must never vanish.
                if set(self._agent_samples) != {self._last_collected}:
                    raise SimulationError("Agent sensor samples were not consumed for every telemetry frame")
                self._agent_samples.clear()
                self._agent_frame_bytes.clear()
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
