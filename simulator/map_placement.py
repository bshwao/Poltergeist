"""Resolve built-in scenario intent against the connected CARLA lane graph."""

from collections.abc import Iterator
from dataclasses import dataclass, field
import math
import random
from typing import Any

from shared.models import ActorType, Rotation, Scenario, ScenarioTemplate, Transform

from .transforms import compose, from_carla


def angle_error(target: float, actual: float) -> float:
    return (target - actual + 180.0) % 360.0 - 180.0


def waypoint_key(point: Any) -> tuple:
    return (point.road_id, point.section_id, point.lane_id, point.s)


def spawn_pose(point: Any) -> Transform:
    pose = from_carla(point.transform)
    pose.position.z += 0.5
    return pose


def walk_lane(point: Any, distance: float) -> list[Any]:
    """Walk connected lanes in small increments with stable branch selection."""
    result = [point]
    remaining = abs(distance)
    while remaining > 1e-6:
        step = min(2.0, remaining)
        choices = list(point.previous(step) if distance < 0 else point.next(step))
        if not choices:
            return []
        point = min(choices, key=lambda other: (
            abs(angle_error(other.transform.rotation.yaw, point.transform.rotation.yaw)), waypoint_key(other),
        ))
        result.append(point)
        remaining -= step
    return result


@dataclass
class TemplatePlacement:
    spawns: dict[str, Transform]
    # Connected waypoint paths disambiguate the two lanes at a junction crossing.
    routes: dict[str, list[Transform]] = field(default_factory=dict)


class TemplatePlacementPlanner:
    def __init__(self, road_map: Any, carla_module: Any, scenario: Scenario):
        self.map, self.carla, self.scenario = road_map, carla_module, scenario
        self.ego = next(actor for actor in scenario.actors if actor.type == ActorType.EGO_VEHICLE)
        self.rng = random.Random(scenario.seed)

    @property
    def automatic(self) -> bool:
        # An explicit ego transform pins the whole built-in layout. Custom
        # recipes always retain their original strict placement semantics.
        return self.scenario.template is not None and self.ego.spawn is None

    def candidates(self) -> Iterator[TemplatePlacement]:
        if self.scenario.template == ScenarioTemplate.INTERSECTION:
            yield from self._intersections()
            return
        spawns = list(self.map.get_spawn_points())
        index = self.ego.spawn_point_index
        if index is not None and index < len(spawns):
            yield self._relative_layout(from_carla(spawns[index]))
        # Sorted, seeded map samples provide anchors between the packaged spawn
        # points, without global navigation RNG or hard-coded Town coordinates.
        points = sorted(self.map.generate_waypoints(2.0), key=waypoint_key)
        self.rng.shuffle(points)
        for point in points:
            if not point.is_junction:
                yield self._relative_layout(spawn_pose(point))

    def _relative_layout(self, ego_pose: Transform) -> TemplatePlacement:
        poses = {self.ego.id: ego_pose}
        pending = [actor for actor in self.scenario.actors if actor.id != self.ego.id]
        while pending:
            ready = next((actor for actor in pending if actor.relative_spawn is not None
                          and actor.relative_spawn.anchor_actor_id in poses), None)
            if ready is None:
                break
            relative = ready.relative_spawn
            poses[ready.id] = compose(poses[relative.anchor_actor_id], Transform(
                position=relative.offset_m, rotation=Rotation(yaw_deg=relative.yaw_offset_deg),
            ))
            pending.remove(ready)
        return TemplatePlacement(poses)

    def _junction_lanes(self, junction: Any) -> list[tuple[Any, Any]]:
        # Use nearly straight internal lanes. Their center-line intersections
        # are real conflict points, unlike a junction bounding-box center.
        lanes = []
        for start, end in junction.get_waypoints(self.carla.LaneType.Driving):
            if (start.road_id, start.lane_id) != (end.road_id, end.lane_id):
                continue
            if abs(angle_error(end.transform.rotation.yaw, start.transform.rotation.yaw)) > 10:
                continue
            a, b = start.transform.location, end.transform.location
            length = math.hypot(b.x - a.x, b.y - a.y)
            if length < 3:
                continue
            chord_yaw = math.degrees(math.atan2(b.y - a.y, b.x - a.x))
            if abs(angle_error(chord_yaw, start.transform.rotation.yaw)) > 5:
                continue
            lanes.append((start, end))
        return sorted(lanes, key=lambda pair: waypoint_key(pair[0]))

    def _conflict(self, first: tuple[Any, Any], second: tuple[Any, Any]) -> tuple[Any, Any] | None:
        a, b = (point.transform.location for point in first)
        c, d = (point.transform.location for point in second)
        ux, uy, vx, vy = b.x - a.x, b.y - a.y, d.x - c.x, d.y - c.y
        determinant = ux * vy - uy * vx
        if abs(determinant) < 1e-6:
            return None
        t = ((c.x - a.x) * vy - (c.y - a.y) * vx) / determinant
        u = ((c.x - a.x) * uy - (c.y - a.y) * ux) / determinant
        if not (0.01 < t < 0.99 and 0.01 < u < 0.99):
            return None
        points = []
        for (start, end), fraction in ((first, t), (second, u)):
            point = self.map.get_waypoint_xodr(start.road_id, start.lane_id,
                                              start.s + (end.s - start.s) * fraction)
            if point is None or not point.is_junction:
                return None
            points.append(point)
        p, q = (point.transform.location for point in points)
        if math.hypot(p.x - q.x, p.y - q.y) > 0.5 or abs(p.z - q.z) > 0.5:
            return None
        return points[0], points[1]

    def _intersections(self) -> Iterator[TemplatePlacement]:
        junctions = {}
        for point in self.map.generate_waypoints(3.0):
            if point.is_junction and point.junction_id not in junctions:
                junctions[point.junction_id] = point.get_junction()
        ordered = sorted(junctions)
        self.rng.shuffle(ordered)
        params = self.scenario.parameters
        for junction_id in ordered:
            lanes = self._junction_lanes(junctions[junction_id])
            for first in lanes:
                for second in lanes:
                    # The crossing vehicle approaches from the ego's left,
                    # matching the template intent, allowing real road angles.
                    angle = angle_error(second[0].transform.rotation.yaw, first[0].transform.rotation.yaw)
                    if not 45 <= angle <= 135:
                        continue
                    conflict = self._conflict(first, second)
                    if conflict is None:
                        continue
                    poses, routes = {}, {}
                    for actor_id, point, distance in (
                        (self.ego.id, conflict[0], float(params['intersection_distance_m'])),
                        ('crossing', conflict[1], float(params['crossing_distance_m'])),
                    ):
                        approach = walk_lane(point, -distance)
                        continuation = walk_lane(point, 60.0)
                        if not approach or not continuation:
                            break
                        # Do not place a vehicle in the middle of the conflict.
                        pose = spawn_pose(approach[-1])
                        poses[actor_id] = pose
                        routes[actor_id] = [from_carla(item.transform) for item in
                                            [*reversed(approach), *continuation[1:]]]
                    if len(poses) == 2:
                        yield TemplatePlacement(poses, routes)
