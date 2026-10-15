"""Private 3D oriented-box operations with finite arithmetic.

Rotation axes follow CARLA's ``Rotation::RotateVector``:
https://carla.org/Doxygen/html/d0/d8f/Rotation_8h_source.html
The 15-axis static/swept SAT follows Eberly, *Dynamic Collision Detection
using Oriented Bounding Boxes* (sections 2.1 and 2.2):
https://www.geometrictools.com/Documentation/DynamicCollisionDetection.pdf
"""

from dataclasses import dataclass, replace
from itertools import product
import math

from shared.models import ActorState


Vec = tuple[float, float, float]
Axes = tuple[Vec, Vec, Vec]
# A nanometre of absolute tolerance covers trigonometric roundoff at contact.
CONTACT_TOLERANCE_M = 1e-9
_PARALLEL_TOLERANCE = 1e-14


def finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("Safety geometry arithmetic produced a non-finite value")
    return value


def add(a: Vec, b: Vec) -> Vec:
    return tuple(finite(x + y) for x, y in zip(a, b))


def sub(a: Vec, b: Vec) -> Vec:
    return tuple(finite(x - y) for x, y in zip(a, b))


def scale(a: Vec, factor: float) -> Vec:
    return tuple(finite(x * factor) for x in a)


def dot(a: Vec, b: Vec) -> float:
    try:
        return finite(math.fsum(finite(x * y) for x, y in zip(a, b)))
    except OverflowError as exc:
        raise ValueError("Safety geometry arithmetic overflowed") from exc


def norm(a: Vec) -> float:
    return finite(math.hypot(*a))


def cross(a: Vec, b: Vec) -> Vec:
    return (
        finite(a[1] * b[2] - a[2] * b[1]),
        finite(a[2] * b[0] - a[0] * b[2]),
        finite(a[0] * b[1] - a[1] * b[0]),
    )


@dataclass(frozen=True)
class Box:
    center: Vec
    axes: Axes
    extent: Vec


def actor_box(actor: ActorState) -> Box | None:
    """Called after contract validation; an absent extent is never a point."""
    pose = actor.bounding_box_transform or actor.transform
    if pose is None or actor.bounding_box_extent is None:
        return None
    rotation = pose.rotation
    pitch, yaw, roll = (
        math.radians(value % 360.0)
        for value in (rotation.pitch_deg, rotation.yaw_deg, rotation.roll_deg)
    )
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cr, sr = math.cos(roll), math.sin(roll)
    axes = (
        (cp * cy, cp * sy, sp),
        (cy * sp * sr - sy * cr, sy * sp * sr + cy * cr, -cp * sr),
        (-cy * sp * cr - sy * sr, -sy * sp * cr + cy * sr, cp * cr),
    )
    position = pose.position
    extent = actor.bounding_box_extent
    return Box((position.x, position.y, position.z), axes, (extent.x, extent.y, extent.z))


def relative_boxes(a: Box, b: Box) -> tuple[Box, Box]:
    """Keep vertex arithmetic near the pair instead of near the world origin."""
    return replace(a, center=(0.0, 0.0, 0.0)), replace(b, center=sub(b.center, a.center))


def separating_axes(a: Box, b: Box) -> list[Vec]:
    axes = [*a.axes, *b.axes]
    for first in a.axes:
        for second in b.axes:
            axis = cross(first, second)
            length = norm(axis)
            # Parallel axes contribute no new separating direction.
            if length > _PARALLEL_TOLERANCE:
                axes.append(scale(axis, 1.0 / length))
    return axes


def projected_radius(box: Box, axis: Vec) -> float:
    try:
        return finite(math.fsum(finite(extent * abs(dot(direction, axis)))
                               for extent, direction in zip(box.extent, box.axes)))
    except OverflowError as exc:
        raise ValueError("Safety geometry arithmetic overflowed") from exc


def projections(a: Box, b: Box) -> list[tuple[Vec, float, float]]:
    displacement = sub(b.center, a.center)
    return [(axis, dot(displacement, axis), finite(projected_radius(a, axis) + projected_radius(b, axis)))
            for axis in separating_axes(a, b)]


def overlap(a: Box, b: Box) -> bool:
    return all(abs(distance) <= finite(radius + CONTACT_TOLERANCE_M)
               for _, distance, radius in projections(a, b))


def vertices(box: Box) -> list[Vec]:
    points = []
    for signs in product((-1.0, 1.0), repeat=3):
        point = box.center
        for axis, extent, sign in zip(box.axes, box.extent, signs):
            point = add(point, scale(axis, extent * sign))
        points.append(point)
    return points


def point_box_distance(point: Vec, box: Box) -> float:
    local = sub(point, box.center)
    outside = tuple(max(0.0, finite(abs(dot(local, axis)) - extent))
                    for axis, extent in zip(box.axes, box.extent))
    return norm(outside)


def edges(box: Box) -> list[tuple[Vec, Vec, float]]:
    """Edges as a start point, unit direction, and length."""
    result = []
    for index, axis in enumerate(box.axes):
        others = [other for other in range(3) if other != index]
        for signs in product((-1.0, 1.0), repeat=2):
            start = sub(box.center, scale(axis, box.extent[index]))
            for other, sign in zip(others, signs):
                start = add(start, scale(box.axes[other], box.extent[other] * sign))
            result.append((start, axis, finite(2.0 * box.extent[index])))
    return result


def edge_distance(first: tuple[Vec, Vec, float], second: tuple[Vec, Vec, float]) -> float:
    """Closest distance on two closed segments, including parallel edges."""
    start_a, direction_a, length_a = first
    start_b, direction_b, length_b = second
    delta = sub(start_a, start_b)
    cosine = max(-1.0, min(1.0, dot(direction_a, direction_b)))
    d, e = dot(direction_a, delta), dot(direction_b, delta)
    # cross^2 avoids cancellation in 1-cosine^2 for nearly parallel edges.
    denominator = dot(cross(direction_a, direction_b), cross(direction_a, direction_b))
    s = finite(finite(cosine * e - d) / denominator) if denominator > 1e-28 else 0.0
    s = max(0.0, min(length_a, s))
    t = finite(cosine * s + e)
    if t < 0.0:
        t, s = 0.0, max(0.0, min(length_a, -d))
    elif t > length_b:
        t = length_b
        s = max(0.0, min(length_a, finite(cosine * length_b - d)))
    return norm(sub(add(delta, scale(direction_a, s)), scale(direction_b, t)))


def box_clearance(a: Box, b: Box) -> float:
    a, b = relative_boxes(a, b)
    if overlap(a, b):
        return 0.0
    # Closest convex-polyhedron features are vertex/face or edge/edge pairs.
    distances = [point_box_distance(point, b) for point in vertices(a)]
    distances.extend(point_box_distance(point, a) for point in vertices(b))
    edges_a, edges_b = edges(a), edges(b)
    distances.extend(edge_distance(first, second) for first in edges_a for second in edges_b)
    return min(distances)
