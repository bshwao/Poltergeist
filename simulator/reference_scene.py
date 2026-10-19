"""Privileged map and snapshot features for the reference driving pilot.

This module neither advances CARLA nor creates a client. Dynamic road-user
geometry comes exclusively from the supplied telemetry frame. Map geometry is
cached once and rasterized locally; missing map capabilities remain unknown.
The semantic raster is a map rendering, not a camera or perception output.
"""

from dataclasses import dataclass
import base64
import math
from typing import Any

from shared.models import (
    AgentRoadRaster, AgentSceneContext, AgentSceneObject, BoundingBoxExtent,
    Rotation, TelemetryFrame, Transform, Vector3,
)

from .transforms import compose, from_carla, to_carla

_READ_ERRORS = (AttributeError, RuntimeError, TypeError, ValueError, OverflowError)


def _distance(a: Transform, b: Transform) -> float:
    return math.hypot(a.position.x - b.position.x, a.position.y - b.position.y)


def _angle(a: float, b: float) -> float:
    return (a - b + 180.0) % 360.0 - 180.0


def _interpolate(a: Transform, b: Transform, fraction: float) -> Transform:
    start, end = a.position, b.position
    return Transform(
        position=Vector3(x=start.x + fraction * (end.x - start.x),
                         y=start.y + fraction * (end.y - start.y),
                         z=start.z + fraction * (end.z - start.z)),
        rotation=Rotation(yaw_deg=a.rotation.yaw_deg + fraction * _angle(b.rotation.yaw_deg, a.rotation.yaw_deg)),
    )


def resample_route(route: list[Transform], ego: Transform, *, horizon_m: float = 80.0,
                   spacing_m: float = 1.0) -> list[Transform] | None:
    """Project onto a connected polyline and sample its genuine forward length.

    No extrapolated straight points are added at a road end. A route farther
    than eight metres from the ego is unavailable rather than silently snapped.
    """
    result = [Transform(position=Vector3(x=x, y=y, z=z), rotation=Rotation(yaw_deg=yaw))
              for x, y, z, yaw in _route_samples(route, ego, horizon_m=horizon_m, spacing_m=spacing_m)]
    return result or None


def _route_samples(route: list[Transform], ego: Transform, *, horizon_m: float = 80.0,
                   spacing_m: float = 1.0):
    """Yield exact numeric samples, optionally counted without model allocation."""
    if len(route) < 2:
        return
    # Projection is numeric until the closest segment is known. Constructing
    # and validating a Transform for every candidate used to cost more than
    # sampling the actual route, even though all but one were discarded.
    closest = None
    ex, ey = ego.position.x, ego.position.y
    for index, (a, b) in enumerate(zip(route, route[1:])):
        dx, dy = b.position.x - a.position.x, b.position.y - a.position.y
        length2 = dx * dx + dy * dy
        if length2 <= 1e-12:
            continue
        fraction = max(0.0, min(1.0, ((ex - a.position.x) * dx +
                                    (ey - a.position.y) * dy) / length2))
        distance = math.hypot(a.position.x + fraction * dx - ex,
                              a.position.y + fraction * dy - ey)
        if closest is None or distance < closest[0]:
            closest = (distance, index, fraction)
    if closest is None:
        return
    distance, index, fraction = closest
    first, second = route[index], route[index + 1]
    ax = first.position.x + fraction * (second.position.x - first.position.x)
    ay = first.position.y + fraction * (second.position.y - first.position.y)
    az = first.position.z + fraction * (second.position.z - first.position.z)
    yaw = first.rotation.yaw_deg + fraction * _angle(second.rotation.yaw_deg, first.rotation.yaw_deg)
    if distance > 8.0 or abs(az - ego.position.z) > 4.0:
        return
    yield ax, ay, az, yaw
    remaining, travelled = spacing_m, 0.0
    for point in route[index + 1:]:
        bx, by, bz, byaw = point.position.x, point.position.y, point.position.z, point.rotation.yaw_deg
        length = math.hypot(ax - bx, ay - by)
        while length >= remaining - 1e-9 and travelled + remaining <= horizon_m + 1e-9:
            fraction = 0.0 if length <= 1e-12 else remaining / length
            ax += fraction * (bx - ax)
            ay += fraction * (by - ay)
            az += fraction * (bz - az)
            yaw += fraction * _angle(byaw, yaw)
            yield ax, ay, az, yaw
            travelled += remaining
            length -= remaining
            remaining = spacing_m
        remaining -= length
        if travelled >= horizon_m - 1e-9:
            break
        ax, ay, az, yaw = bx, by, bz, byaw


@dataclass(frozen=True)
class _MapPatch:
    corners: tuple[tuple[float, float], ...]
    z: float
    category: int


class CarlaReferenceScene:
    """Read-only scene provider, constructed only for structured-scene agents."""

    def __init__(self, world: Any, carla: Any, actor_manager: Any, ego_id: str):
        self.world, self.carla, self.manager, self.ego_id = world, carla, actor_manager, ego_id
        self.map = world.get_map()
        self._route: list[Transform] = []
        self._patches: list[_MapPatch] | None = None
        self._patch_grid: dict[tuple[int, int], list[_MapPatch]] = {}
        self._raster_window: tuple[int, int] | None = None
        self._raster_arrays: tuple[Any, Any, Any] | None = None
        self._map_loaded = False
        self._cleared_stops: set[int] = set()
        self._signal_geometry: dict[tuple[str, int], list[Transform]] = {}

    def context(self, frame: TelemetryFrame) -> AgentSceneContext:
        pose = frame.ego.transform
        route = None if pose is None else self._route_ahead(pose)
        objects = self._objects(frame, route)
        raster = None if pose is None else self._raster(pose)
        context = AgentSceneContext(
            source="carla_ground_truth", route=route, objects=objects,
            speed_limit_mps=frame.ego.speed_limit_mps, road_raster=raster,
        )
        # Dump/validation creates the caller's independent graph in Pydantic's
        # compiled path. No returned model aliases frame evidence, cached signal
        # geometry or the route, and every boundary remains fully validated.
        return AgentSceneContext.model_validate(context.model_dump(mode="python"))

    def _route_ahead(self, ego: Transform) -> list[Transform] | None:
        if not self._route:
            selected = getattr(self.manager, "_routes", {}).get(self.ego_id)
            if selected:
                self._route = [item.model_copy(deep=True) for item in selected]
            else:
                try:
                    waypoint = self.map.get_waypoint(to_carla(ego, self.carla).location,
                        project_to_road=False, lane_type=self.carla.LaneType.Driving)
                    if waypoint is None:
                        return None
                    self._route = [from_carla(waypoint.transform)]
                except _READ_ERRORS:
                    return None
        # Reuse the selected junction path; only extend its actual end through
        # connected waypoints. This never changes ActorManager's baseline route.
        # Only the count is needed to decide how many connected waypoints to
        # request. Constructing eighty disposable Transform graphs here would
        # repeat the complete output allocation before extending the route.
        current_count = sum(1 for _ in _route_samples(self._route, ego))
        if not current_count and len(self._route) > 1:
            return None
        missing = 82 - current_count
        if missing > 0:
            try:
                point = self.map.get_waypoint(to_carla(self._route[-1], self.carla).location,
                    project_to_road=False, lane_type=self.carla.LaneType.Driving)
                for _ in range(missing):
                    if point is None:
                        break
                    choices = list(point.next(1.0))
                    if not choices:
                        break
                    point = min(choices, key=lambda other: (
                        abs(_angle(float(other.transform.rotation.yaw), float(point.transform.rotation.yaw))),
                        int(other.road_id), int(other.lane_id), float(other.s)))
                    pose = from_carla(point.transform)
                    if _distance(self._route[-1], pose) < 0.01:
                        break
                    self._route.append(pose)
            except _READ_ERRORS:
                return None
        # Trim obsolete points, bounding memory during long experiments.
        result = resample_route(self._route, ego)
        if result and len(result) > 1:
            # These newly created points are scene-owned. context() isolates
            # them once at the public boundary, instead of copying twice.
            self._route = result
        return result

    def _objects(self, frame: TelemetryFrame, route: list[Transform] | None) -> list[AgentSceneObject] | None:
        if frame.actors is None:
            return None
        try:
            actors = list(self.world.get_actors())
            identities = self.manager.actor_ids
            native = {identities.get(actor.id): actor for actor in actors}
            result = []
            for actor in frame.actors:
                handle = native.get(actor.actor_id)
                if handle is None or actor.transform is None or actor.bounding_box_extent is None:
                    return None
                type_id = str(handle.type_id)
                kind = "pedestrian" if type_id.startswith("walker.pedestrian.") else "vehicle"
                if any(name in type_id.lower() for name in ("ambulance", "firetruck", "police")):
                    kind = "emergency"
                result.append(AgentSceneObject(
                    actor_id=actor.actor_id, kind=kind,
                    transform=actor.bounding_box_transform or actor.transform,
                    extent=actor.bounding_box_extent, velocity_mps=actor.velocity_mps,
                ))
            if frame.ego.transform is not None and route:
                result.extend(self._signals(actors, frame, route))
            # CARLA's dynamic telemetry intentionally covers road users only.
            # Include actual world cone/obstacle props without moving them.
            for actor in actors:
                if not str(actor.type_id).startswith("static.prop."):
                    continue
                pose = from_carla(actor.get_transform())
                if frame.ego.transform is None or _distance(pose, frame.ego.transform) > 64.0:
                    continue
                box = actor.bounding_box
                box_pose = compose(pose, from_carla(self.carla.Transform(box.location, box.rotation)))
                velocity = None
                try:
                    measured = actor.get_velocity()
                    velocity = Vector3(x=float(measured.x), y=float(measured.y), z=float(measured.z))
                except _READ_ERRORS:
                    pass
                result.append(AgentSceneObject(actor_id=f"static:{actor.id}", kind="static",
                    transform=box_pose,
                    extent=BoundingBoxExtent(x=float(box.extent.x), y=float(box.extent.y), z=float(box.extent.z)),
                    velocity_mps=velocity, is_static=None if velocity is None else
                    math.hypot(velocity.x, velocity.y, velocity.z) < 0.01))
            return result
        except _READ_ERRORS:
            return None

    @staticmethod
    def _on_route(pose: Transform, route: list[Transform]) -> bool:
        return any(_distance(pose, point) <= 2.5 and abs(pose.position.z - point.position.z) <= 3.0
                   and abs(_angle(pose.rotation.yaw_deg, point.rotation.yaw_deg)) < 60.0
                   for point in route[:32])

    def _signals(self, actors: list[Any], frame: TelemetryFrame,
                 route: list[Transform]) -> list[AgentSceneObject]:
        result = []
        ego = frame.ego.transform
        speed = math.hypot(*(getattr(frame.ego.velocity_mps, axis) for axis in ("x", "y", "z"))) \
            if frame.ego.velocity_mps is not None else None
        for actor in actors:
            type_id = str(actor.type_id)
            if type_id.startswith("traffic.traffic_light"):
                key = ("light", actor.id)
                if key not in self._signal_geometry:
                    self._signal_geometry[key] = [from_carla(point.transform) for point in actor.get_stop_waypoints()]
                points = self._signal_geometry[key]
                relevant = [point for point in points if _distance(ego, point) <= 30.0 and self._on_route(point, route)]
                if relevant:
                    # Signal state is dynamic; geometry is static. Avoid a
                    # server RPC for every distant traffic light on every tick.
                    state = str(actor.get_state()).split(".")[-1].lower()
                    if state not in {"red", "yellow", "green", "off"}:
                        state = "unknown"
                    point = min(relevant, key=lambda item: _distance(ego, item))
                    result.append(AgentSceneObject(actor_id=f"traffic-light:{actor.id}", kind="traffic_light",
                        transform=point, extent=BoundingBoxExtent(x=1.5, y=1.5, z=0.5),
                        traffic_light_state=state, is_static=True))
            elif type_id.startswith("traffic.stop"):
                key = ("stop", actor.id)
                if key not in self._signal_geometry:
                    pose = from_carla(actor.get_transform())
                    trigger = actor.trigger_volume
                    center = compose(pose, Transform(position=Vector3(
                        x=float(trigger.location.x), y=float(trigger.location.y), z=float(trigger.location.z))))
                    waypoint = self.map.get_waypoint(to_carla(center, self.carla).location,
                        project_to_road=True, lane_type=self.carla.LaneType.Driving)
                    if waypoint is None:
                        raise ValueError("Stop-sign affected lane unavailable")
                    self._signal_geometry[key] = [from_carla(waypoint.transform)]
                point = self._signal_geometry[key][0]
                distance = _distance(ego, point)
                if distance > 10.0:
                    self._cleared_stops.discard(actor.id)
                if distance < 3.0 and speed is not None and speed < 0.1:
                    self._cleared_stops.add(actor.id)
                if actor.id not in self._cleared_stops and distance <= 30.0 and self._on_route(point, route):
                    result.append(AgentSceneObject(actor_id=f"stop-sign:{actor.id}", kind="stop_sign",
                        transform=point, extent=BoundingBoxExtent(x=1.5, y=1.5, z=0.5), is_static=True))
        return result

    def _load_map(self) -> None:
        """Cache strips for driving lanes, adjacent sidewalks and lane markings."""
        self._map_loaded = True
        self._patch_grid.clear()
        self._raster_window = None
        self._raster_arrays = None
        try:
            patches = []
            seen = set()
            points = list(self.map.generate_waypoints(1.0))
            if not points:
                raise ValueError("CARLA map has no sampled lane geometry")
            for point in points:
                chain = [point]
                for method in ("get_left_lane", "get_right_lane"):
                    adjacent = point
                    for _ in range(12):
                        adjacent = getattr(adjacent, method)()
                        if adjacent is None:
                            break
                        chain.append(adjacent)
                for lane in chain:
                    key = (int(lane.road_id), int(lane.section_id), int(lane.lane_id), round(float(lane.s), 2))
                    if key in seen:
                        continue
                    seen.add(key)
                    lane_type = str(lane.lane_type).split(".")[-1]
                    if lane_type not in {"Driving", "Sidewalk"}:
                        continue
                    width = float(lane.lane_width)
                    if not math.isfinite(width) or width <= 0:
                        raise ValueError("Mapped lane width unavailable")
                    pose = from_carla(lane.transform)
                    # Half-metre overlap avoids holes caused by rounded map
                    # samples while keeping road boundaries at measured width.
                    patches.append(self._strip(pose, -0.55, 0.55, -width / 2, width / 2,
                                               1 if lane_type == "Driving" else 2))
                    if lane_type == "Driving" and not bool(lane.is_junction):
                        for side, sign in (("left", -1.0), ("right", 1.0)):
                            mark = getattr(lane, f"{side}_lane_marking")
                            mark_type = str(mark.type).split(".")[-1]
                            if mark_type == "NONE" or mark_type == "None":
                                continue
                            color = str(mark.color).split(".")[-1]
                            broken = mark_type == "Broken" and color == "White"
                            # CARLA marks broken lines in metric lane s. Their
                            # individual dashes are represented, not solidified.
                            if broken and float(lane.s) % 6.0 >= 3.0:
                                continue
                            half = max(0.075, float(mark.width) / 2.0)
                            lateral = sign * width / 2.0
                            patches.append(self._strip(pose, -0.55, 0.55, lateral - half,
                                                       lateral + half, 4 if broken else 3))
            if not any(patch.category == 1 for patch in patches):
                raise ValueError("CARLA road surface geometry unavailable")
            self._patches = sorted(patches, key=lambda patch: patch.category)
            for patch in self._patches:
                # A local spatial index keeps each 20 Hz observation from
                # scanning every road sample in the complete CARLA town.
                x = sum(point[0] for point in patch.corners) / 4.0
                y = sum(point[1] for point in patch.corners) / 4.0
                self._patch_grid.setdefault((math.floor(x / 16.0), math.floor(y / 16.0)), []).append(patch)
        except _READ_ERRORS:
            self._patches = None

    @staticmethod
    def _strip(pose: Transform, start: float, end: float, left: float, right: float,
               category: int) -> _MapPatch:
        yaw = math.radians(pose.rotation.yaw_deg)
        c, s = math.cos(yaw), math.sin(yaw)
        corners = tuple((pose.position.x + x * c - y * s, pose.position.y + x * s + y * c)
                        for x, y in ((start, left), (end, left), (end, right), (start, right)))
        return _MapPatch(corners, pose.position.z, category)

    def _raster(self, ego: Transform) -> AgentRoadRaster | None:
        if not self._map_loaded:
            self._load_map()
        if self._patches is None:
            return None
        try:
            import cv2
            import numpy as np
        except ImportError:
            return None
        raster = np.zeros((128, 128), dtype=np.uint8)
        yaw = math.radians(ego.rotation.yaw_deg)
        c, s = math.cos(yaw), math.sin(yaw)
        center_x, center_y = math.floor(ego.position.x / 16.0), math.floor(ego.position.y / 16.0)
        window = (center_x, center_y)
        if self._raster_window != window or self._raster_arrays is None:
            local = [patch for gx in range(center_x - 4, center_x + 5)
                     for gy in range(center_y - 4, center_y + 5)
                     for patch in self._patch_grid.get((gx, gy), [])]
            ordered = sorted(local, key=lambda item: item.category)
            # These world-space arrays are immutable for the whole 16 m cell.
            # Rebuilding thousands of corner tuples on every observation adds
            # work unrelated to either the ego pose or the simulation frame.
            self._raster_arrays = (
                np.asarray([patch.corners for patch in ordered], dtype=np.float64).reshape(-1, 4, 2),
                np.asarray([patch.z for patch in ordered]),
                np.asarray([patch.category for patch in ordered], dtype=np.uint8),
            )
            self._raster_window = window
        xy, elevations, categories = self._raster_arrays
        if len(xy):
            # Vectorize the unchanged world-to-ego transform and distance gate.
            # Paint patches individually: batching overlapping polygons through
            # fillPoly would change its even/odd fill semantics and create holes.
            delta = xy - np.asarray([ego.position.x, ego.position.y])
            mask = ((np.abs(elevations - ego.position.z) <= 4.0) &
                    ((delta * delta).sum(axis=2).min(axis=1) <= 48.0**2))
            delta = delta[mask]
            forward = delta[:, :, 0] * c + delta[:, :, 1] * s
            right = -delta[:, :, 0] * s + delta[:, :, 1] * c
            pixels = np.rint(np.stack((64.0 + 2.0 * forward,
                                      64.0 + 2.0 * right), axis=2)).astype(np.int32)
            # A polygon with its complete integer bounding box outside the
            # image cannot paint a pixel. Keep the original individual fills
            # and category order for every polygon that could touch it.
            visible = ((pixels[:, :, 0].max(axis=1) >= 0) & (pixels[:, :, 0].min(axis=1) < 128) &
                       (pixels[:, :, 1].max(axis=1) >= 0) & (pixels[:, :, 1].min(axis=1) < 128))
            for category, vertices in zip(categories[mask][visible], pixels[visible]):
                cv2.fillConvexPoly(raster, vertices, int(category))
        return AgentRoadRaster(width=128, height=128, pixels_per_meter=2.0,
            ego_row=64.0, ego_column=64.0, data_base64=base64.b64encode(raster.tobytes()).decode("ascii"))


def synthetic_scene(frame: TelemetryFrame, actor_types: dict[str, str], heading_deg: float,
                    speed_limit_mps: float) -> AgentSceneContext:
    """Explicit virtual straight-road context; it never pretends to be CARLA."""
    route, objects = None, None
    if frame.ego.transform is not None:
        pose = frame.ego.transform
        yaw = math.radians(heading_deg)
        route = [Transform(position=Vector3(x=pose.position.x + index * math.cos(yaw),
                    y=pose.position.y + index * math.sin(yaw), z=pose.position.z),
                    rotation=Rotation(yaw_deg=heading_deg)) for index in range(81)]
    if frame.actors is not None and all(item.transform is not None and item.bounding_box_extent is not None
                                        for item in frame.actors):
        objects = [AgentSceneObject(actor_id=item.actor_id, kind=actor_types[item.actor_id],
            transform=item.bounding_box_transform or item.transform, extent=item.bounding_box_extent,
            velocity_mps=item.velocity_mps) for item in frame.actors]
    # The test adapter has no actual lane network or marking geometry. Leaving
    # its raster unknown prevents synthetic tests from claiming visual validity.
    return AgentSceneContext(source="synthetic", route=route, objects=objects,
                             speed_limit_mps=speed_limit_mps, road_raster=None).model_copy(deep=True)
