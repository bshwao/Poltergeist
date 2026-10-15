"""Owned actor spawning and measured telemetry through an injected CARLA API."""

from collections.abc import Mapping
import math
import random
from typing import Any

from evaluation.geometry import actor_box, overlap
from scenarios.scenario_loader import validate_scenario
from shared.models import (
    ActorSpec, ActorState, ActorType, BoundingBoxExtent, Rotation, Scenario,
    ScenarioTemplate, Transform, Vector3,
)

from .errors import SimulationError
from .protocols import ActorCommand
from .transforms import compose, forward, from_carla, to_carla


def _vector(value: Any) -> Vector3:
    return Vector3(x=float(value.x), y=float(value.y), z=float(value.z))


def _angle_error(target: float, actual: float) -> float:
    return (target - actual + 180.0) % 360.0 - 180.0


class ActorManager:
    """Own only actors created by this manager; observe foreign road users.

    Relative positions are validated in place and never snapped to another
    road. Built-in templates additionally require their straight approaches
    and crossing paths to fit the actual map. Invalid symbolic starter layouts
    therefore fail with an actionable error instead of running off-road.
    """

    def __init__(self, world: Any, carla_module: Any, scenario: Scenario):
        self.world, self.carla = world, carla_module
        self.scenario = validate_scenario(scenario)
        self.actors: dict[str, Any] = {}
        self.resolved_spawns: dict[str, Transform] = {}
        self._specs = {actor.id: actor for actor in self.scenario.actors}
        self._rng = random.Random(self.scenario.seed)
        self._foreign_ids: dict[int, str] = {}
        self._spawn_points = list(world.get_map().get_spawn_points())
        self._map = world.get_map()
        self._targets = {actor.id: actor.speed_mps for actor in self.scenario.actors}
        self._last_commands: dict[str, ActorCommand] = {}

    def _world_road_users(self) -> list[Any]:
        return [actor for actor in self.world.get_actors()
                if actor.type_id.startswith(("vehicle.", "walker.pedestrian."))]

    @property
    def actor_ids(self) -> dict[int, str]:
        """Native integer ID -> stable telemetry ID, including foreign actors."""
        result = {actor.id: actor_id for actor_id, actor in self.actors.items()}
        reserved = set(self._specs) | set(self._foreign_ids.values())
        for actor in self._world_road_users():
            if actor.id in result:
                continue
            if actor.id not in self._foreign_ids:
                candidate = f"world:{actor.id}"
                while candidate in reserved:
                    candidate = f"world:{candidate}"
                self._foreign_ids[actor.id] = candidate
                reserved.add(candidate)
            result[actor.id] = self._foreign_ids[actor.id]
        return result

    def _blueprint(self, spec: ActorSpec) -> Any:
        expected_prefix = "walker.pedestrian." if spec.type == ActorType.PEDESTRIAN else "vehicle."
        if not spec.blueprint or not spec.blueprint.startswith(expected_prefix):
            raise SimulationError(f"Actor {spec.id}: blueprint {spec.blueprint!r} does not match {spec.type}")
        try:
            blueprint = self.world.get_blueprint_library().find(spec.blueprint)
        except Exception as exc:
            raise SimulationError(f"Actor {spec.id}: blueprint is unavailable: {spec.blueprint}") from exc
        if blueprint is None or blueprint.id != spec.blueprint:
            raise SimulationError(f"Actor {spec.id}: exact blueprint is unavailable: {spec.blueprint}")
        for name in ("color", "driver_id"):
            if blueprint.has_attribute(name):
                attribute = blueprint.get_attribute(name)
                values = list(attribute.recommended_values)
                if attribute.is_modifiable and values:
                    blueprint.set_attribute(name, self._rng.choice(values))
        for name, value in (("role_name", spec.id), ("is_invincible", "false")):
            if blueprint.has_attribute(name) and blueprint.get_attribute(name).is_modifiable:
                blueprint.set_attribute(name, value)
        return blueprint

    def _candidates(self, spec: ActorSpec) -> list[Transform]:
        if spec.spawn is not None:
            return [spec.spawn.model_copy(deep=True)]
        if spec.relative_spawn is not None:
            relative = spec.relative_spawn
            return [compose(self.resolved_spawns[relative.anchor_actor_id], Transform(
                position=relative.offset_m,
                rotation=Rotation(yaw_deg=relative.yaw_offset_deg),
            ))]
        if spec.spawn_point_index is not None:
            if spec.spawn_point_index >= len(self._spawn_points):
                raise SimulationError(f"Actor {spec.id}: spawn point index {spec.spawn_point_index} is out of range "
                                      f"for {len(self._spawn_points)} map spawn points")
            return [from_carla(self._spawn_points[spec.spawn_point_index])]
        indices = list(range(len(self._spawn_points)))
        self._rng.shuffle(indices)
        if not indices:
            raise SimulationError(f"Actor {spec.id}: map has no spawn points; supply an explicit spawn")
        if spec.type == ActorType.PEDESTRIAN:
            # A fallback has no requested position to preserve. Derive nearby
            # sidewalk candidates from a seeded ordering of map spawn points;
            # never use CARLA's global unseeded navigation sampler.
            candidates = []
            for index in indices:
                point = self._map.get_waypoint(self._spawn_points[index].location,
                                               project_to_road=True,
                                               lane_type=self.carla.LaneType.Sidewalk)
                if point is not None:
                    pose = from_carla(point.transform)
                    pose.position.z += 0.5
                    candidates.append(pose)
            return candidates
        return [from_carla(self._spawn_points[index]) for index in indices]

    def _waypoint(self, pose: Transform, lane_type: Any) -> Any:
        return self._map.get_waypoint(to_carla(pose, self.carla).location,
                                      project_to_road=False, lane_type=lane_type)

    def _ground(self, spec: ActorSpec, pose: Transform) -> None:
        # Vertical probing checks geometry without moving an actor horizontally.
        point = self.carla.Location(x=pose.position.x, y=pose.position.y, z=pose.position.z + 2.0)
        hit = self.world.ground_projection(point, 4.0)
        if hit is None or abs(float(hit.location.z) - pose.position.z) > 2.0:
            raise SimulationError(f"Actor {spec.id}: spawn has no nearby ground surface")

    def _validate_placement(self, spec: ActorSpec, pose: Transform) -> None:
        self._ground(spec, pose)
        lanes = self.carla.LaneType
        if self.scenario.occlusion and spec.id == "occluder":
            if self._waypoint(pose, lanes.Any) is None:
                raise SimulationError("Actor occluder: position must lie on a mapped road, shoulder, or sidewalk")
            return
        if spec.type == ActorType.PEDESTRIAN:
            waypoint = self._waypoint(pose, lanes.Sidewalk)
            crossing = self.scenario.template in (ScenarioTemplate.PEDESTRIAN_CROSSING,
                                                  ScenarioTemplate.OCCLUDED_PEDESTRIAN)
            if waypoint is None and crossing:
                waypoint = self._waypoint(pose, lanes.Driving)
            if waypoint is None:
                raise SimulationError(f"Actor {spec.id}: pedestrian spawn must be on a sidewalk "
                                      "or a validated template crossing")
            return
        waypoint = self._waypoint(pose, lanes.Driving)
        if waypoint is None:
            raise SimulationError(f"Actor {spec.id}: spawn is outside a driving lane; "
                                  "provide map-valid scenario coordinates")
        lane_yaw = float(waypoint.transform.rotation.yaw)
        if abs(_angle_error(lane_yaw, pose.rotation.yaw_deg)) > 45.0:
            raise SimulationError(f"Actor {spec.id}: spawn heading is not aligned with its driving lane")

    def spawn(self) -> dict[str, Any]:
        if self.actors:
            raise SimulationError("ActorManager has already spawned actors")
        pending = list(self.scenario.actors)
        try:
            while pending:
                ready = next((spec for spec in pending if spec.relative_spawn is None
                              or spec.relative_spawn.anchor_actor_id in self.resolved_spawns), None)
                if ready is None:
                    raise SimulationError("Actor relative spawn references cannot be resolved")
                blueprint = self._blueprint(ready)
                last_error = "no free spawn point"
                fallback = ready.spawn is None and ready.spawn_point_index is None and ready.relative_spawn is None
                for pose in self._candidates(ready):
                    try:
                        self._validate_placement(ready, pose)
                    except SimulationError as exc:
                        last_error = str(exc)
                        continue
                    actor = self.world.try_spawn_actor(blueprint, to_carla(pose, self.carla))
                    if actor is None:
                        last_error = f"Actor {ready.id}: spawn is occupied or collides with world geometry"
                        continue
                    # Register immediately so every subsequent failure owns cleanup.
                    self.actors[ready.id] = actor
                    self.resolved_spawns[ready.id] = from_carla(actor.get_transform())
                    new_box = actor_box(self._state(ready.id, actor))
                    collision = None
                    for other in self._world_road_users():
                        if other.id == actor.id:
                            continue
                        other_box = actor_box(self._state(f"native:{other.id}", other))
                        if new_box is not None and other_box is not None and overlap(new_box, other_box):
                            collision = f"Actor {ready.id}: actual bounding box overlaps actor {other.id}"
                            break
                    if collision is not None:
                        if not fallback:
                            raise SimulationError(collision)
                        # CARLA's physical collision mesh and its measured box
                        # need not have identical boundaries. Retry a fallback
                        # only after this owned candidate is actually removed.
                        if actor.destroy() is False:
                            raise SimulationError(f"{collision}; failed to destroy rejected fallback actor")
                        del self.actors[ready.id]
                        del self.resolved_spawns[ready.id]
                        last_error = collision
                        continue
                    if ready.type == ActorType.PEDESTRIAN:
                        self._walker_control(actor, ready.speed_mps, self.resolved_spawns[ready.id])
                    elif ready.speed_mps > 0.0:
                        direction = forward(self.resolved_spawns[ready.id].rotation)
                        actor.set_target_velocity(self.carla.Vector3D(
                            x=direction.x * ready.speed_mps, y=direction.y * ready.speed_mps,
                            z=direction.z * ready.speed_mps,
                        ))
                    break
                else:
                    raise SimulationError(last_error)
                pending.remove(ready)
            self._validate_template_layout()
            return dict(self.actors)
        except Exception as exc:
            try:
                self.close()
            except Exception as cleanup:
                raise SimulationError(f"Actor setup failed: {exc}; cleanup failed: {cleanup}") from exc
            if isinstance(exc, SimulationError):
                raise
            raise SimulationError(f"Actor setup failed: {exc}") from exc

    def _check_route(self, start: Transform, distance: float, *, junction_at_end: bool = False) -> None:
        steps = max(1, math.ceil(distance / 2.0))
        for index in range(steps + 1):
            pose = compose(start, Transform(position=Vector3(x=distance * index / steps)))
            waypoint = self._waypoint(pose, self.carla.LaneType.Driving)
            if waypoint is None or abs(_angle_error(float(waypoint.transform.rotation.yaw),
                                                    pose.rotation.yaw_deg)) > 45.0:
                raise SimulationError("Template approach does not follow a driving lane; provide map-valid coordinates")
            if junction_at_end and index == steps and not waypoint.is_junction:
                raise SimulationError("Intersection template conflict point is not inside a mapped junction")

    def _validate_template_layout(self) -> None:
        template = self.scenario.template
        if template is None:
            return
        ego_id = next(spec.id for spec in self.scenario.actors if spec.type == ActorType.EGO_VEHICLE)
        ego = self.resolved_spawns[ego_id]
        params = self.scenario.parameters
        if template == ScenarioTemplate.SUDDEN_BRAKING:
            self._check_route(ego, float(params["lead_distance_m"]))
        elif template == ScenarioTemplate.INTERSECTION:
            self._check_route(ego, float(params["intersection_distance_m"]), junction_at_end=True)
            self._check_route(self.resolved_spawns["crossing"], float(params["crossing_distance_m"]),
                              junction_at_end=True)
        else:
            self._check_route(ego, float(params["pedestrian_distance_m"]))
            start = self.resolved_spawns["pedestrian"]
            distance = 2.0 * float(params["pedestrian_lateral_offset_m"])
            steps = max(1, math.ceil(distance))
            for index in range(steps + 1):
                pose = compose(start, Transform(position=Vector3(x=distance * index / steps)))
                if self._waypoint(pose, self.carla.LaneType.Any) is None:
                    raise SimulationError("Pedestrian template crossing leaves mapped ground; provide map-valid coordinates")

    def _state(self, actor_id: str, actor: Any, sample: Any = None) -> ActorState:
        sample = actor if sample is None else sample
        transform = from_carla(sample.get_transform())
        box = actor.bounding_box
        extent = BoundingBoxExtent(x=float(box.extent.x), y=float(box.extent.y), z=float(box.extent.z))
        box_pose = compose(transform, Transform(
            position=_vector(box.location),
            rotation=Rotation(pitch_deg=float(box.rotation.pitch), yaw_deg=float(box.rotation.yaw),
                              roll_deg=float(box.rotation.roll)),
        ))
        speed_limit = None
        if actor.type_id.startswith("vehicle."):
            try:
                limit = float(actor.get_speed_limit()) / 3.6
                if math.isfinite(limit) and limit > 0:
                    speed_limit = limit
            except RuntimeError:
                # A missing map speed-limit value does not invalidate measured
                # pose/velocity. Preserve it as unknown in the shared contract.
                pass
        return ActorState(actor_id=actor_id, transform=transform, velocity_mps=_vector(sample.get_velocity()),
                          acceleration_mps2=_vector(sample.get_acceleration()), bounding_box_extent=extent,
                          bounding_box_transform=box_pose, speed_limit_mps=speed_limit)

    def states(self, snapshot: Any = None) -> list[ActorState]:
        ids = self.actor_ids
        users = {actor.id: actor for actor in self._world_road_users()}
        users.update({actor.id: actor for actor in self.actors.values()})
        result = []
        # Keep scenario order so telemetry ordering does not depend on server IDs.
        ordered = [actor.id for actor_id in self._specs if (actor := self.actors.get(actor_id)) is not None]
        ordered.extend(native for native in sorted(users) if native not in ordered)
        for native in ordered:
            sample = snapshot.find(native) if snapshot is not None else users[native]
            if sample is None:
                # Never substitute data from another tick for missing snapshot data.
                result.append(ActorState(actor_id=ids[native]))
            else:
                result.append(self._state(ids[native], users[native], sample))
        return result

    def _walker_control(self, actor: Any, speed: float, pose: Transform) -> None:
        direction = forward(pose.rotation)
        actor.apply_control(self.carla.WalkerControl(
            direction=self.carla.Vector3D(x=direction.x, y=direction.y, z=direction.z), speed=speed,
            jump=False,
        ))

    def control_npcs(self, commands: Mapping[str, ActorCommand], dt_s: float) -> None:
        if not math.isfinite(dt_s) or dt_s <= 0:
            raise SimulationError("NPC control dt_s must be finite and positive")
        unknown = set(commands) - set(self.actors)
        if unknown:
            raise SimulationError(f"NPC command references unknown actors: {sorted(unknown)}")
        for actor_id, actor in self.actors.items():
            spec = self._specs[actor_id]
            if spec.type == ActorType.EGO_VEHICLE:
                continue
            command = commands.get(actor_id)
            if command is not None:
                self._last_commands[actor_id] = command
            command = self._last_commands.get(actor_id)
            target = self._targets[actor_id]
            if command is not None:
                if not math.isfinite(command.target_speed_mps) or command.target_speed_mps < 0:
                    raise SimulationError(f"Actor {actor_id}: invalid target speed")
                if command.deceleration_mps2 is not None:
                    if not math.isfinite(command.deceleration_mps2) or command.deceleration_mps2 <= 0:
                        raise SimulationError(f"Actor {actor_id}: invalid braking deceleration")
                    target = max(command.target_speed_mps, target - command.deceleration_mps2 * dt_s)
                else:
                    target = command.target_speed_mps
                self._targets[actor_id] = target
            pose = from_carla(actor.get_transform())
            if spec.type == ActorType.PEDESTRIAN:
                self._walker_control(actor, target, self.resolved_spawns[actor_id])
                continue
            velocity = actor.get_velocity()
            speed = math.hypot(velocity.x, velocity.y, velocity.z)
            if target <= 0.01:
                throttle, brake = 0.0, 1.0
            else:
                error = target - speed
                throttle, brake = min(1.0, max(0.0, error * 0.4)), min(1.0, max(0.0, -error * 0.3))
            waypoint = self._waypoint(pose, self.carla.LaneType.Driving)
            steer = 0.0
            if waypoint is None and not (self.scenario.occlusion and actor_id == "occluder" and target <= 0.01):
                raise SimulationError(f"Actor {actor_id}: NPC left the mapped driving lane")
            if waypoint is not None:
                upcoming = list(waypoint.next(max(2.0, speed * 0.5)))
                if not upcoming and target > 0.01:
                    raise SimulationError(f"Actor {actor_id}: NPC route reached the end of a driving lane")
                if upcoming:
                    upcoming.sort(key=lambda point: (abs(_angle_error(float(point.transform.rotation.yaw),
                                                                      pose.rotation.yaw_deg)), point.id))
                    point = upcoming[0].transform.location
                    desired = math.degrees(math.atan2(point.y - pose.position.y, point.x - pose.position.x))
                    steer = max(-1.0, min(1.0, _angle_error(desired, pose.rotation.yaw_deg) / 45.0))
            actor.apply_control(self.carla.VehicleControl(throttle=throttle, brake=brake, steer=steer,
                                                         hand_brake=False, reverse=False))

    def close(self) -> None:
        errors = []
        for actor_id, actor in reversed(list(self.actors.items())):
            try:
                if getattr(actor, "is_alive", True) and actor.destroy() is False:
                    raise SimulationError("destroy() returned False")
                del self.actors[actor_id]
                self.resolved_spawns.pop(actor_id, None)
            except Exception as exc:
                errors.append(f"Actor {actor_id} cleanup failed: {exc}")
        if errors:
            raise SimulationError("; ".join(errors))
