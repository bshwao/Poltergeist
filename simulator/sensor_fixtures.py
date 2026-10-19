"""Explicit deterministic SDK fixtures, not CARLA rendering or perception.

The synthetic test adapter uses these small manufactured samples to exercise
transport, calibration, and agent behavior. Bright camera pixels are a test
pattern. LiDAR contains ground, harmless side returns, and an optional flat
obstacle surface. They are never evidence of real-world sensor performance.
"""

import base64
import hashlib
import math
import struct

from shared.models import AgentSensorFrame
from simulator.transforms import rotation_matrix


def build_sensor_fixture(specs, frame, time_s, obstacle_distance_m=None, black_camera=False):
    """Build canonical, same-frame fixture samples for normalized sensor specs.

    ``obstacle_distance_m`` is forward distance from the ego origin, before
    the teaching controller subtracts its configured front-bumper offset.
    Sensor-local coordinates use the inverse of each full 3D mount transform.
    """
    if obstacle_distance_m is not None and (
            isinstance(obstacle_distance_m, bool) or not isinstance(obstacle_distance_m, (int, float))
            or not math.isfinite(obstacle_distance_m) or obstacle_distance_m <= 0):
        raise ValueError("Fixture obstacle distance must be finite and positive")
    samples = []
    for spec in specs:
        common = {"sensor_id": spec.id, "type": spec.type, "frame": frame, "timestamp_s": time_s}
        if spec.type == "sensor.camera.rgb":
            width, height = int(spec.attributes["image_size_x"]), int(spec.attributes["image_size_y"])
            # Opaque, unmistakably artificial constant test-pattern pixels.
            raw = bytes((0, 0, 0, 255) if black_camera else (100, 140, 180, 255)) * (width * height)
            common |= {"encoding": "bgra8", "width": width, "height": height,
                       "fov_deg": float(spec.attributes["fov"])}
        elif spec.type == "sensor.lidar.ray_cast":
            ego_points = [(float(x), float(y), 0.0, 0.5) for x in range(3, 41, 2) for y in (-1, 0, 1)]
            ego_points += [(float(x), 5.0, 1.0, 0.5) for x in range(5, 41, 5)]
            # Hull returns deliberately ensure the example filters its own car.
            ego_points += [(1.0, 0.0, 1.0, 0.5), (-1.0, 0.5, 1.0, 0.5)]
            if obstacle_distance_m is not None:
                ego_points += [(float(obstacle_distance_m), y, z, 0.8)
                               for y in (-0.8, 0.0, 0.8) for z in (0.5, 1.0, 1.5)]
            rotation, mount = rotation_matrix(spec.mount.rotation), spec.mount.position
            packed = []
            for x, y, z, intensity in ego_points:
                translated = (x - mount.x, y - mount.y, z - mount.z)
                local = [sum(rotation[row][column] * translated[row] for row in range(3))
                         for column in range(3)]
                packed.append(struct.pack("<ffff", *local, intensity))
            raw = b"".join(packed)
            common |= {"encoding": "float32_xyzi", "channels": int(spec.attributes["channels"])}
        else:
            raise ValueError(f"No fixture generator for sensor type {spec.type}")
        common |= {"data_base64": base64.b64encode(raw).decode("ascii"), "sha256": hashlib.sha256(raw).hexdigest()}
        samples.append(AgentSensorFrame.model_validate(common))
    return samples
