"""CARLA rotation/transform composition without importing CARLA or NumPy."""

import math
from typing import Any

from shared.models import Rotation, Transform, Vector3


def from_carla(value: Any) -> Transform:
    return Transform(
        position=Vector3(x=float(value.location.x), y=float(value.location.y), z=float(value.location.z)),
        rotation=Rotation(pitch_deg=float(value.rotation.pitch), yaw_deg=float(value.rotation.yaw),
                          roll_deg=float(value.rotation.roll)),
    )


def to_carla(value: Transform, carla_module: Any) -> Any:
    return carla_module.Transform(
        carla_module.Location(x=value.position.x, y=value.position.y, z=value.position.z),
        carla_module.Rotation(pitch=value.rotation.pitch_deg, yaw=value.rotation.yaw_deg,
                              roll=value.rotation.roll_deg),
    )


def rotation_matrix(rotation: Rotation) -> tuple[tuple[float, ...], ...]:
    pitch, yaw, roll = (math.radians(value) for value in
                        (rotation.pitch_deg, rotation.yaw_deg, rotation.roll_deg))
    cp, sp, cy, sy, cr, sr = (math.cos(pitch), math.sin(pitch), math.cos(yaw),
                              math.sin(yaw), math.cos(roll), math.sin(roll))
    return (
        (cp * cy, cy * sp * sr - sy * cr, -cy * sp * cr - sy * sr),
        (cp * sy, sy * sp * sr + cy * cr, -sy * sp * cr + cy * sr),
        (sp, -cp * sr, cp * cr),
    )


def rotate(vector: Vector3, rotation: Rotation) -> Vector3:
    values = (vector.x, vector.y, vector.z)
    return Vector3(**dict(zip(("x", "y", "z"),
                             (sum(a * b for a, b in zip(row, values))
                              for row in rotation_matrix(rotation)))))


def compose(parent: Transform, local: Transform) -> Transform:
    """Compose full 3D poses, including local box offsets and box rotations."""
    offset = rotate(local.position, parent.rotation)
    a, b = rotation_matrix(parent.rotation), rotation_matrix(local.rotation)
    matrix = tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))
    pitch = math.asin(max(-1.0, min(1.0, matrix[2][0])))
    if abs(math.cos(pitch)) > 1e-9:
        yaw = math.atan2(matrix[1][0], matrix[0][0])
        roll = math.atan2(-matrix[2][1], matrix[2][2])
    else:
        yaw, roll = math.atan2(-matrix[0][1], matrix[1][1]), 0.0
    return Transform(
        position=Vector3(x=parent.position.x + offset.x, y=parent.position.y + offset.y,
                         z=parent.position.z + offset.z),
        rotation=Rotation(pitch_deg=math.degrees(pitch), yaw_deg=math.degrees(yaw),
                          roll_deg=math.degrees(roll)),
    )


def forward(rotation: Rotation) -> Vector3:
    return rotate(Vector3(x=1.0), rotation)
