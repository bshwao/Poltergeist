"""Finite driving tasks, directed CARLA routing, and measured goal outcomes.

Route length/progress use horizontal polyline metres. Elevation is checked
separately, so a car underneath its destination cannot complete the task.
This module never advances a world or sends vehicle controls.
"""

import heapq
import math
from typing import Any

from shared.models import TelemetryFrame, Transform, Vector3, Rotation

from .errors import SimulationError
from .transforms import from_carla, to_carla


def _distance(a: Transform, b: Transform) -> float:
    return math.hypot(a.position.x - b.position.x, a.position.y - b.position.y)


def _speed(frame: TelemetryFrame) -> float | None:
    v = frame.ego.velocity_mps
    return None if v is None else math.hypot(v.x, v.y, v.z)


def _projection(route: list[Transform], pose: Transform, height_tolerance: float = 4.0, *,
                min_progress_m: float = 0.0, max_progress_m: float = math.inf):
    """Return distance, horizontal arc coordinate, segment, fraction, height."""
    best, arc = None, 0.0
    for index, (a, b) in enumerate(zip(route, route[1:])):
        dx, dy = b.position.x - a.position.x, b.position.y - a.position.y
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            # Settled actor poses can be a few centimetres above the road
            # waypoint at the same XY position. This is a real terminal
            # endpoint, with zero horizontal length, rather than no route.
            if min_progress_m <= arc <= max_progress_m and abs(
                    b.position.z - pose.position.z) <= height_tolerance:
                candidate = (_distance(b, pose), arc, index, 1.0, b.position.z)
                if best is None or candidate[:2] < best[:2]:
                    best = candidate
            continue
        minimum = max(0.0, (min_progress_m - arc) / length)
        maximum = min(1.0, (max_progress_m - arc) / length)
        if minimum > maximum:
            arc += length
            continue
        t = min(maximum, max(minimum, ((pose.position.x - a.position.x) * dx +
                              (pose.position.y - a.position.y) * dy) / length**2))
        z = a.position.z + t * (b.position.z - a.position.z)
        distance = math.hypot(a.position.x + t * dx - pose.position.x,
                              a.position.y + t * dy - pose.position.y)
        if abs(z - pose.position.z) <= height_tolerance:
            candidate = (distance, arc + t * length, index, t, z)
            if best is None or candidate[:2] < best[:2]:
                best = candidate
        arc += length
    return best


def _lerp(a: Transform, b: Transform, t: float) -> Transform:
    return Transform(position=Vector3(**{axis: getattr(a.position, axis) + t *
        (getattr(b.position, axis) - getattr(a.position, axis)) for axis in ("x", "y", "z")}),
        rotation=Rotation(yaw_deg=a.rotation.yaw_deg + t *
            ((b.rotation.yaw_deg - a.rotation.yaw_deg + 180.0) % 360.0 - 180.0)))


def finite_local_route(route: list[Transform], pose: Transform, *, horizon_m: float = 80.0,
                       spacing_m: float = 1.0, min_progress_m: float = 0.0,
                       max_progress_m: float = math.inf) -> list[Transform] | None:
    """Local samples terminate at the real destination, including a short tail."""
    if not route:
        return None
    if len(route) == 1:
        return [route[0].model_copy(deep=True)] if _distance(route[0], pose) <= 8.0 and abs(
            route[0].position.z - pose.position.z) <= 4.0 else None
    projected = _projection(route, pose, min_progress_m=min_progress_m, max_progress_m=max_progress_m)
    if projected is None or projected[0] > 8.0:
        return None
    _, _, index, t, _ = projected
    current = _lerp(route[index], route[index + 1], t)
    result, travelled, until = [current], 0.0, spacing_m
    for end in route[index + 1:]:
        length = _distance(current, end)
        while length >= until - 1e-9 and travelled + until <= horizon_m + 1e-9:
            current = _lerp(current, end, min(1.0, until / length)) if length else end.model_copy(deep=True)
            result.append(current)
            travelled += until
            length = _distance(current, end)
            until = spacing_m
        if travelled + length >= horizon_m - 1e-9:
            break
        until -= length
        current = end
    # Include the actual end when it is inside the horizon; never extrapolate.
    remaining_length = sum(_distance(a, b) for a, b in zip(
        [_lerp(route[index], route[index + 1], t), *route[index + 1:]], route[index + 1:]))
    if remaining_length <= horizon_m + 1e-9 and _distance(result[-1], route[-1]) > 1e-8:
        result.append(route[-1].model_copy(deep=True))
    return result[:256]


class RouteTracker:
    """Keep finite guidance on the current traversal of a crossing/loop.

    Projection may move two metres backwards for measurement jitter. Forward
    search is bounded by measured pose displacement * 1.5 plus two metres;
    stationary repeated queries reuse their projection. Initial acquisition is
    limited to the first eight route metres. A distant future leg cannot become
    the current leg merely because it crosses or passes near the ego.
    """

    def __init__(self, route: list[Transform], *, height_tolerance_m: float = 4.0):
        self.route = [point.model_copy(deep=True) for point in route]
        self.height_tolerance_m = height_tolerance_m
        self.progress_m = None
        self.furthest_progress_m = None
        self._previous_pose = None
        self._projected = None

    def update(self, pose: Transform):
        if self._previous_pose is not None and pose.position == self._previous_pose.position:
            return self._projected
        distance = 0.0 if self._previous_pose is None else math.dist(
            (pose.position.x, pose.position.y, pose.position.z),
            (self._previous_pose.position.x, self._previous_pose.position.y, self._previous_pose.position.z))
        minimum = 0.0 if self.progress_m is None else max(0.0, self.progress_m - 2.0)
        maximum = 8.0 if self.progress_m is None else self.progress_m + 1.5 * distance + 2.0
        projected = _projection(self.route, pose, self.height_tolerance_m,
                                min_progress_m=minimum, max_progress_m=maximum)
        self._previous_pose = pose.model_copy(deep=True)
        if projected is not None and projected[0] <= 8.0:
            self.progress_m = projected[1]
            self.furthest_progress_m = max(self.furthest_progress_m or 0.0, projected[1])
            self._projected = projected
        else:
            self._projected = None
        return self._projected

    def local_route(self, pose: Transform) -> list[Transform] | None:
        projected = self.update(pose)
        if len(self.route) == 1:
            return finite_local_route(self.route, pose) if abs(
                self.route[0].position.z - pose.position.z) <= self.height_tolerance_m else None
        if projected is None:
            return None
        return finite_local_route(self.route, pose, min_progress_m=projected[1], max_progress_m=projected[1])


class TaskMonitor:
    """Measure a task without choosing controls or advancing simulation time."""

    def __init__(self, task, resolved_route: list[Transform], initial_frame: TelemetryFrame,
                 control_mode: str):
        from shared.models import DrivingTask
        self.task = DrivingTask.model_validate(task.model_dump()).model_copy(deep=True)
        self.resolved_route = [point.model_copy(deep=True) for point in resolved_route]
        self.control_mode = control_mode
        self.initial_speed_mps = _speed(initial_frame)
        self.route_length_m = sum(_distance(a, b) for a, b in zip(self.resolved_route, self.resolved_route[1:])) \
            if self.resolved_route else None
        self.route_progress_m = None
        self._route_tracker = RouteTracker(self.resolved_route,
            height_tolerance_m=self.task.completion_height_tolerance_m)
        self.final_distance_m = None
        self.final_height_error_m = None
        self.reached_at_s = None
        self._inside_since = None
        self._last_time = None
        self.update(initial_frame)

    @property
    def reached(self) -> bool:
        return self.reached_at_s is not None

    def update(self, frame: TelemetryFrame) -> None:
        if self._last_time is not None and frame.time_s < self._last_time:
            raise SimulationError("Driving task telemetry time must not move backwards")
        self._last_time = frame.time_s
        pose = frame.ego.transform
        if pose is None:
            self.final_distance_m = self.final_height_error_m = None
            self._inside_since = None
            return
        self.final_distance_m = _distance(pose, self.task.destination)
        self.final_height_error_m = abs(pose.position.z - self.task.destination.position.z)
        projected = self._route_tracker.update(pose)
        if projected is not None and projected[0] <= 8.0:
            self.route_progress_m = max(self.route_progress_m or 0.0, projected[1])
        elif (len(self.resolved_route) == 1 and _distance(self.resolved_route[0], pose) <= 8.0 and
              abs(self.resolved_route[0].position.z - pose.position.z) <= self.task.completion_height_tolerance_m):
            self.route_progress_m = 0.0
        speed = _speed(frame)
        inside = (self.final_distance_m <= self.task.completion_radius_m and
                  self.final_height_error_m <= self.task.completion_height_tolerance_m and
                  (self.task.completion_speed_mps is None or
                   (speed is not None and speed <= self.task.completion_speed_mps)))
        if not inside:
            self._inside_since = None
        elif not self.reached:
            if self._inside_since is None:
                self._inside_since = frame.time_s
            if frame.time_s - self._inside_since + 1e-9 >= self.task.completion_hold_s:
                self.reached_at_s = frame.time_s

    def local_route(self, frame: TelemetryFrame) -> list[Transform] | None:
        if self.task.route_owner != "framework" or frame.ego.transform is None:
            return None
        return self._route_tracker.local_route(frame.ego.transform)

    def guidance_heading(self, frame: TelemetryFrame) -> float | None:
        route = self.local_route(frame)
        if not route or frame.ego.transform is None:
            return None
        pose = frame.ego.transform
        speed = _speed(frame) or 0.0
        target_index = min(len(route) - 1, max(1, int(max(2.0, speed * 0.5))))
        target = route[target_index]
        if _distance(pose, target) < 1e-6:
            return target.rotation.yaw_deg
        return math.degrees(math.atan2(target.position.y - pose.position.y,
                                       target.position.x - pose.position.x))

    def outcome(self, status: str, interventions: list, termination_reason: str):
        from shared.models import DrivingOutcome
        if status not in {"failed", "cancelled"}:
            status = "reached" if self.reached else (
                "unavailable" if self.final_distance_m is None else "timed_out")
        return DrivingOutcome(status=status, route_owner=self.task.route_owner,
            control_mode=self.control_mode, destination=self.task.destination,
            target_speed_mps=self.task.target_speed_mps, resolved_route=self.resolved_route,
            initial_speed_mps=self.initial_speed_mps, final_distance_m=self.final_distance_m,
            final_height_error_m=self.final_height_error_m,
            reached_at_s=self.reached_at_s if status == "reached" else None,
            route_progress_m=self.route_progress_m,
            route_length_m=self.route_length_m if self.route_progress_m is not None else None,
            interventions=interventions, termination_reason=termination_reason).model_copy(deep=True)


def _waypoint_key(point: Any):
    # Half-metre quantization bounds the graph while preserving lane identity.
    return (int(point.road_id), int(getattr(point, "section_id", 0)), int(point.lane_id),
            round(float(point.s) * 2.0))


def _native_distance(a: Any, b: Any) -> float:
    p, q = a.transform.location, b.transform.location
    return math.hypot(p.x - q.x, p.y - q.y, p.z - q.z)


def _same_lane(a: Any, b: Any) -> bool:
    return (a.road_id, getattr(a, "section_id", 0), a.lane_id) == (
        b.road_id, getattr(b, "section_id", 0), b.lane_id)


def _map_point(map_: Any, carla: Any, pose: Transform, label: str):
    point = map_.get_waypoint(to_carla(pose, carla).location, project_to_road=False,
                              lane_type=carla.LaneType.Driving)
    if point is None or getattr(point, "lane_type", carla.LaneType.Driving) != carla.LaneType.Driving:
        raise SimulationError(f"Driving task {label} is outside a driving lane")
    actual = from_carla(point.transform)
    if abs(actual.position.z - pose.position.z) > 2.0:
        raise SimulationError(f"Driving task {label} has incompatible road elevation")
    return point


def _forward_path(start: Any, goal: Any, *, max_expansions: int = 20_000) -> list[Any]:
    """Directed A*: use actual next(2 m) edges, never bridge a disconnected road."""
    start_key = _waypoint_key(start)
    distances, parents, points = {start_key: 0.0}, {}, {start_key: start}
    pending, counter = [(_native_distance(start, goal), 0, start_key)], 0
    expanded, closed = 0, set()
    while pending:
        _, _, key = heapq.heappop(pending)
        if key in closed:
            continue
        closed.add(key)
        current = points[key]
        # Sampling can land immediately before/after a requested endpoint, but
        # a nearby opposite or adjacent lane never satisfies this test.
        close = _same_lane(current, goal) and _native_distance(current, goal) <= 2.05
        goal_pose, current_pose = goal.transform, current.transform
        yaw = math.radians(float(current_pose.rotation.yaw))
        forward_gap = ((goal_pose.location.x - current_pose.location.x) * math.cos(yaw) +
                       (goal_pose.location.y - current_pose.location.y) * math.sin(yaw))
        # A goal just behind the initial point is not a forward connection.
        # If an actual traversed edge straddles the goal, truncate that edge
        # instead of appending a backwards segment after the sampled point.
        straddled = False
        if close and forward_gap < -0.01 and key in parents:
            previous = points[parents[key]]
            p, c, g = previous.transform.location, current_pose.location, goal_pose.location
            dx, dy = c.x - p.x, c.y - p.y
            length2 = dx * dx + dy * dy
            fraction = ((g.x - p.x) * dx + (g.y - p.y) * dy) / length2 if length2 else -1.0
            straddled = (_same_lane(previous, goal) and 0.0 <= fraction <= 1.0 and
                         math.hypot(p.x + fraction * dx - g.x, p.y + fraction * dy - g.y) <= 0.25)
        if close and (forward_gap >= -0.01 or straddled):
            path = [current]
            while key in parents:
                key = parents[key]
                path.append(points[key])
            path.reverse()
            if straddled:
                path.pop()
            if _native_distance(path[-1], goal) > 0.01:
                path.append(goal)
            return path
        expanded += 1
        if expanded >= max_expansions:
            break
        for child in sorted(current.next(2.0), key=_waypoint_key):
            if getattr(child, "lane_type", None) != getattr(start, "lane_type", None):
                continue
            child_key = _waypoint_key(child)
            distance = _native_distance(current, child)
            if distance <= 1e-6 or child_key in closed:
                continue
            cost = distances[key] + distance
            if cost >= distances.get(child_key, math.inf):
                continue
            distances[child_key], parents[child_key], points[child_key] = cost, key, child
            counter += 1
            heapq.heappush(pending, (cost + _native_distance(child, goal), counter, child_key))
    raise SimulationError("Driving task destination is not reachable through the bounded forward road network")


def resolve_carla_route(map_: Any, carla: Any, task, initial: Transform) -> list[Transform]:
    """Validate task positions and resolve only explicitly requested guidance."""
    destination = _map_point(map_, carla, task.destination, "destination")
    if task.route_owner == "agent":
        return []
    start = _map_point(map_, carla, initial, "initial position")
    requested = list(task.route or [])
    if not requested:
        path = _forward_path(start, destination)
        route = [from_carla(point.transform) for point in path]
        # Preserve exact actor and goal coordinates after validating their lanes.
        route[0] = initial.model_copy(deep=True)
        if len(route) == 1:
            if math.dist((initial.position.x, initial.position.y, initial.position.z),
                    (task.destination.position.x, task.destination.position.y, task.destination.position.z)) > 1e-6:
                route.append(task.destination.model_copy(deep=True))
        else:
            route[-1] = task.destination.model_copy(deep=True)
    else:
        if _distance(initial, requested[0]) > 8.0 or abs(
                initial.position.z - requested[0].position.z) > 2.0:
            raise SimulationError("Driving task route must begin near the measured ego position")
        route = [point.model_copy(deep=True) for point in requested]
        if _distance(route[-1], task.destination) > task.completion_radius_m or abs(
                route[-1].position.z - task.destination.position.z) > task.completion_height_tolerance_m:
            raise SimulationError("Driving task route must end at its destination")
        previous = start
        approach = [initial.model_copy(deep=True)]
        for index, point in enumerate(route):
            mapped = _map_point(map_, carla, point, f"route point {index}")
            if _native_distance(previous, mapped) > 0.01:
                path = _forward_path(previous, mapped, max_expansions=2_000)
                if sum(_native_distance(a, b) for a, b in zip(path, path[1:])) > (
                        _native_distance(previous, mapped) + 8.0):
                    raise SimulationError("Driving task route contains a disconnected shortcut")
                if index == 0:
                    approach.extend(from_carla(item.transform) for item in path[1:-1])
            previous = mapped
        # A sparse straight segment must not cut across grass between valid
        # endpoints. Validate its corridor as well as directed connectivity.
        for a, b in zip(route, route[1:]):
            for step in range(1, max(1, math.ceil(_distance(a, b) / 2.0))):
                _map_point(map_, carla, _lerp(a, b, step / math.ceil(_distance(a, b) / 2.0)), "route segment")
        # The first supplied point may lie a few metres ahead beyond a turn.
        # Retain the directed approach rather than steering across its chord.
        merged = []
        for point in [*approach, *route]:
            if not merged or math.dist((point.position.x, point.position.y, point.position.z),
                    (merged[-1].position.x, merged[-1].position.y, merged[-1].position.z)) > 1e-6:
                merged.append(point)
        route = merged
    if len(route) > 4096:
        raise SimulationError("Driving task resolved route exceeds 4096 points")
    return route
