"""Finite route distance with elevation clipping and a reusable spatial index."""

from dataclasses import dataclass
from heapq import heappop, heappush
import math

from .validation import EvaluationError


def _finite(value):
    if not math.isfinite(value):
        raise EvaluationError("Route distance arithmetic exceeded the supported finite range")
    return value


@dataclass(frozen=True, slots=True)
class _Segment:
    start: tuple
    delta: tuple
    length: float
    bounds: tuple


@dataclass(frozen=True, slots=True)
class _Node:
    bounds: tuple
    segments: tuple = ()
    children: tuple = ()


def _build(segments):
    bounds = tuple(value for axis in range(3) for value in (
        min(segment.bounds[axis * 2] for segment in segments),
        max(segment.bounds[axis * 2 + 1] for segment in segments)))
    if len(segments) <= 8:
        return _Node(bounds, tuple(segments))
    axis = max(range(3), key=lambda i: bounds[i * 2 + 1] - bounds[i * 2])
    # Halved endpoints avoid overflow when computing segment-center sort keys.
    ordered = sorted(segments, key=lambda item: item.bounds[axis * 2] / 2 + item.bounds[axis * 2 + 1] / 2)
    middle = len(ordered) // 2
    return _Node(bounds, children=(_build(ordered[:middle]), _build(ordered[middle:])))


class PolylineReference:
    """Closest planar point on finite segments inside an ego-height band.

    This measures reference-path geometry, not lane membership or route order.
    In particular, no progress estimate is inferred at route crossings.
    """

    def __init__(self, route):
        points = [tuple(getattr(item.position, axis) for axis in ("x", "y", "z")) for item in route]
        segments = []
        for start, end in zip(points, points[1:] if len(points) > 1 else points):
            delta = tuple(_finite(b - a) for a, b in zip(start, end))
            bounds = tuple(value for a, b in zip(start, end) for value in (min(a, b), max(a, b)))
            segments.append(_Segment(start, delta, _finite(math.hypot(*delta[:2])), bounds))
        self._root = _build(segments) if segments else None

    def distance(self, pose, *, height_tolerance_m):
        if self._root is None:
            return None
        p = pose.position
        point = p.x, p.y, p.z

        def lower_bound(bounds):
            if p.z < bounds[4] - height_tolerance_m or p.z > bounds[5] + height_tolerance_m:
                return math.inf
            return _finite(math.hypot(max(bounds[0] - p.x, 0.0, p.x - bounds[1]),
                                      max(bounds[2] - p.y, 0.0, p.y - bounds[3])))

        best = math.inf
        queue = [(lower_bound(self._root.bounds), 0, self._root)]
        serial = 0
        while queue:
            lower, _, node = heappop(queue)
            if lower == math.inf or lower > best:
                continue
            for child in node.children:
                bound = lower_bound(child.bounds)
                if bound <= best:
                    serial += 1
                    heappush(queue, (bound, serial, child))
            for segment in node.segments:
                x, y, z = segment.start
                dx, dy, dz = segment.delta
                lo, hi = 0.0, 1.0
                if abs(dz) <= 1e-12:
                    if abs(p.z - z) > height_tolerance_m:
                        continue
                else:
                    low, high = sorted(((p.z - height_tolerance_m - z) / dz,
                                        (p.z + height_tolerance_m - z) / dz))
                    lo, hi = max(lo, low), min(hi, high)
                    if lo > hi:
                        continue
                t = lo if segment.length <= 1e-12 else min(hi, max(lo, _finite(math.fsum((
                    _finite(point[0] - x) * (dx / segment.length),
                    _finite(point[1] - y) * (dy / segment.length))) / segment.length)))
                value = _finite(math.hypot(_finite(x + t * dx - p.x), _finite(y + t * dy - p.y)))
                best = min(best, value)
                if best == 0.0:
                    return best
        return None if best == math.inf else best
