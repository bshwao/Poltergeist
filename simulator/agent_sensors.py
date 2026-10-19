"""Supported, bounded sensor calibrations for the SDK sensor input boundary."""

import math
import base64
import hashlib
import struct

from shared.models import AgentSensorFrame, AgentSensorSpec, Transform


MAX_AGENT_SENSORS = 4
MAX_AGENT_SENSOR_BYTES = 2 * 1024 * 1024
CAMERA_DEFAULTS = {"image_size_x": 320, "image_size_y": 180, "fov": 90.0, "sensor_tick": 0.0}
LIDAR_DEFAULTS = {"channels": 32, "range": 60.0, "points_per_second": 56000,
                  "rotation_frequency": 20.0, "upper_fov": 10.0, "lower_fov": -30.0, "sensor_tick": 0.0}


def _number(value, name, minimum, maximum, *, integer=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"Sensor {name} must be a finite JSON number")
    if integer and (type(value) is not int):
        raise ValueError(f"Sensor {name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"Sensor {name} must lie between {minimum} and {maximum}")
    return int(value) if integer else float(value)


def resolve_sensor_specs(specs) -> list[AgentSensorSpec]:
    """Validate and resolve exact mounts/settings before opening any sensors.

    Only RGB and ray-cast XYZI are supported. Every stream samples each physics
    tick. The upper bound uses the largest supported timestep (0.1 s), so a
    default 4 MiB JSON worker envelope can carry a complete base64 frame bundle.
    """
    if not isinstance(specs, (list, tuple)) or not 1 <= len(specs) <= MAX_AGENT_SENSORS:
        raise ValueError("Sensor agents require one to four declared sensors")
    result, identities, estimated_bytes = [], set(), 0
    for raw in specs:
        spec = AgentSensorSpec.model_validate(raw.model_dump() if isinstance(raw, AgentSensorSpec) else raw)
        if spec.id in identities:
            raise ValueError("Agent sensor IDs must be unique")
        identities.add(spec.id)
        if any(abs(getattr(spec.mount.position, axis)) > 10 for axis in ("x", "y", "z")):
            raise ValueError("Agent sensor mounts must remain within 10 metres of the ego origin")
        if spec.type == "sensor.camera.rgb":
            defaults = CAMERA_DEFAULTS
        elif spec.type == "sensor.lidar.ray_cast":
            defaults = LIDAR_DEFAULTS
        else:
            raise ValueError("SDK sensor delivery supports RGB camera and ray-cast LiDAR only")
        unknown = set(spec.attributes) - defaults.keys()
        if unknown:
            raise ValueError(f"Unsupported attributes for {spec.type}: {', '.join(sorted(unknown))}")
        attrs = defaults | spec.attributes
        attrs["sensor_tick"] = _number(attrs["sensor_tick"], "sensor_tick", 0, 0)
        if spec.type == "sensor.camera.rgb":
            attrs["image_size_x"] = _number(attrs["image_size_x"], "image_size_x", 1, 1024, integer=True)
            attrs["image_size_y"] = _number(attrs["image_size_y"], "image_size_y", 1, 768, integer=True)
            attrs["fov"] = _number(attrs["fov"], "fov", 10, 140)
            # CARLA stores the camera FOV as float32. Resolve to that exact
            # calibration rather than rejecting a legitimate rounded sample.
            attrs["fov"] = struct.unpack("<f", struct.pack("<f", attrs["fov"]))[0]
            estimated_bytes += attrs["image_size_x"] * attrs["image_size_y"] * 4
        else:
            attrs["channels"] = _number(attrs["channels"], "channels", 1, 128, integer=True)
            attrs["range"] = _number(attrs["range"], "range", 1, 200)
            attrs["points_per_second"] = _number(attrs["points_per_second"], "points_per_second", 1000, 500000, integer=True)
            attrs["rotation_frequency"] = _number(attrs["rotation_frequency"], "rotation_frequency", 1, 100)
            attrs["upper_fov"] = _number(attrs["upper_fov"], "upper_fov", -89, 89)
            attrs["lower_fov"] = _number(attrs["lower_fov"], "lower_fov", -89, 89)
            if attrs["lower_fov"] >= attrs["upper_fov"]:
                raise ValueError("LiDAR lower_fov must be below upper_fov")
            estimated_bytes += math.ceil(attrs["points_per_second"] * 0.1) * 16
        result.append(AgentSensorSpec.model_validate(spec.model_dump() | {"attributes": attrs}))
    if estimated_bytes > MAX_AGENT_SENSOR_BYTES:
        raise ValueError("Declared sensor bundle exceeds the 2 MiB raw payload budget; reduce resolution or point rate")
    return result


def sensor_frames(specs, samples, frame, *, time_origin_s, expected_source):
    """Convert exact native samples without borrowing older or future frames."""
    if type(time_origin_s) not in (int, float) or not math.isfinite(time_origin_s):
        raise ValueError("Sensor time origin must be finite")
    declared = {spec.id: spec for spec in specs}
    indexed = {}
    total = 0
    for sample in samples:
        sensor_id = sample.metadata.get("agent_sensor_id")
        if sensor_id not in declared or sensor_id in indexed:
            raise ValueError("Agent samples must contain each declared sensor ID exactly once")
        spec = declared[sensor_id]
        if sample.metadata.get("source") != expected_source:
            raise ValueError("Agent sensor sample source does not match the execution mode")
        if "mount" not in sample.metadata or Transform.model_validate(sample.metadata["mount"]) != spec.mount:
            raise ValueError("Agent sensor mount does not match its declared calibration")
        if sample.frame != frame.frame or type(sample.frame) is not int:
            raise ValueError("Agent sensor sample must match the current native frame")
        if (type(sample.timestamp) not in (int, float) or not math.isfinite(sample.timestamp)
                or not math.isclose(sample.timestamp - time_origin_s, frame.time_s, rel_tol=0, abs_tol=1e-6)):
            raise ValueError("Agent sensor timestamp must match the current episode time")
        if sample.metadata.get("sensor_type") != spec.type or sample.kind != (
                "camera" if spec.type == "sensor.camera.rgb" else "lidar"):
            raise ValueError("Agent sample type must match its declared calibration")
        if not isinstance(sample.data, bytes):
            raise ValueError("Agent sensor data must be owned bytes")
        total += len(sample.data)
        if total > MAX_AGENT_SENSOR_BYTES:
            raise ValueError("Agent sensor bundle exceeds the 2 MiB raw payload limit")
        fields = {key: sample.metadata[key] for key in ("width", "height", "fov_deg", "channels")
                  if key in sample.metadata}
        packet = AgentSensorFrame(sensor_id=sensor_id, type=spec.type, frame=sample.frame,
            timestamp_s=frame.time_s, encoding=sample.metadata.get("encoding"),
            data_base64=base64.b64encode(sample.data).decode("ascii"),
            sha256=hashlib.sha256(sample.data).hexdigest(), **fields)
        if spec.type == "sensor.camera.rgb" and (packet.width != spec.attributes["image_size_x"]
                or packet.height != spec.attributes["image_size_y"]
                or not math.isclose(packet.fov_deg, spec.attributes["fov"], abs_tol=1e-6, rel_tol=0)):
            raise ValueError("Camera sample calibration does not match the manifest")
        if spec.type == "sensor.lidar.ray_cast" and packet.channels != spec.attributes["channels"]:
            raise ValueError("LiDAR sample calibration does not match the manifest")
        indexed[sensor_id] = packet
    if set(indexed) != set(declared):
        raise ValueError("Agent sensor bundle is missing declared sensor inputs")
    return [indexed[spec.id] for spec in specs]
