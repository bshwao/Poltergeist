"""Deterministic, synthetic kinematics for developing without a CARLA server.

This adapter has no road network or perception model. CARLA spawn index ``i``
maps to the synthetic straight-road anchor ``(80 * (i // 4), 12 * (i % 4), 0)``
with yaw zero; it does not represent that index on a real CARLA map. Missing
placements select unoccupied anchors using a local seeded random generator.
Relative placements retain their full parent rotation, so bundled straight and
crossing scenarios execute without inventing CARLA map coordinates.

Vehicles accelerate at 3 m/s² and brake at 8 m/s² unless an NPC braking command
specifies another magnitude. Ego contacts are sampled OBB overlaps at tick end,
with a simple inelastic response that stops both contacting bodies. There are
no inferred impulses, collision meshes, substep contacts, friction, or signals.
The virtual map has a 13.9 m/s limit, no stop signs, sidewalks, or opposing lanes;
all rule observations are therefore known, with speeding derived from motion.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
import random
from typing import Any

from evaluation.geometry import actor_box, overlap
from scenarios.scenario_loader import validate_scenario
from shared.models import (
    ActorSpec, ActorState, ActorType, BoundingBoxExtent, CollisionEvent,
    ControlInput, Rotation, Scenario, TelemetryFrame, TrafficRule,
    Transform, Vector3,
)

from .errors import SimulationError
from .protocols import ActorCommand, DrivingObservation, SensorSample, SimulationOptions
from .transforms import compose


@dataclass
class _Body:
    spec: ActorSpec
    transform: Transform
    speed_mps: float
    acceleration_mps2: Vector3 = field(default_factory=Vector3)


class TestSimulationAdapter:
    """Protocol-compatible test world. This is not a physics simulator."""

    # Avoid pytest treating this public adapter class as a test container.
    __test__ = False
    mode = "test"
    speed_limit_mps = 13.9

    def __init__(self, options: SimulationOptions | None = None) -> None:
        self.options = options or SimulationOptions()
        self._scenario: Scenario | None = None
        self._bodies: dict[str, _Body] = {}
        self._commands: dict[str, ActorCommand] = {}
        self._resolved_spawns: dict[str, Transform] = {}
        self._frame_index = 0
        self._ego_id: str | None = None
        self._route_heading_deg = 0.0
        self._active = False
        self._driving_task = None
        self._driving_route = []
        self._task_route_tracker = None

    def open(self, scenario: Scenario) -> TelemetryFrame:
        if self._active:
            raise SimulationError("Test simulation is already open")
        if self.options.camera or self.options.lidar:
            raise SimulationError("Camera and LiDAR capture require CARLA mode")
        scenario = validate_scenario(scenario)
        placements = _resolve_spawns(scenario)
        self._scenario = scenario
        self._resolved_spawns = {key: value.model_copy(deep=True) for key, value in placements.items()}
        self._bodies = {
            actor.id: _Body(actor, placements[actor.id].model_copy(deep=True), actor.speed_mps)
            for actor in scenario.actors
        }
        self._commands = {}
        self._frame_index = 0
        self._ego_id = next(actor.id for actor in scenario.actors if actor.type == ActorType.EGO_VEHICLE)
        self._route_heading_deg = placements[self._ego_id].rotation.yaw_deg
        self._active = True
        return self._snapshot(ControlInput(), [])

    def observation(self, frame: TelemetryFrame, target_speed_mps: float) -> DrivingObservation:
        self._require_open()
        _nonnegative(target_speed_mps, "target_speed_mps")
        return DrivingObservation(
            ego=frame.ego.model_copy(deep=True),
            actors=tuple(actor.model_copy(deep=True) for actor in frame.actors or []),
            dt_s=self._scenario.fixed_delta_seconds,
            target_speed_mps=target_speed_mps,
            route_heading_deg=self._route_heading_deg,
            traffic_light_red=False,
        )

    def step(self, control: ControlInput, commands: Mapping[str, ActorCommand]) -> TelemetryFrame:
        self._require_open()
        if not isinstance(control, ControlInput):
            raise TypeError("control must be ControlInput")
        control = ControlInput.model_validate(control.model_dump())
        for actor_id, command in commands.items():
            if actor_id not in self._bodies:
                raise SimulationError(f"Unknown test-world actor: {actor_id}")
            if not isinstance(command, ActorCommand):
                raise TypeError("commands must contain ActorCommand values")
            _nonnegative(command.target_speed_mps, "target_speed_mps")
            if command.deceleration_mps2 is not None:
                _nonnegative(command.deceleration_mps2, "deceleration_mps2", positive=True)
        self._commands.update(commands)
        dt = self._scenario.fixed_delta_seconds
        for actor_id, body in self._bodies.items():
            previous_velocity = _velocity(body.transform.rotation, body.speed_mps)
            if actor_id == self._ego_id:
                self._move_ego(body, control, dt)
            else:
                command = self._commands.get(actor_id, ActorCommand(body.spec.speed_mps))
                self._move_actor(body, command, dt)
            velocity = _velocity(body.transform.rotation, body.speed_mps)
            body.acceleration_mps2 = Vector3(**{
                key: (getattr(velocity, key) - getattr(previous_velocity, key)) / dt
                for key in ("x", "y", "z")
            })
        self._frame_index += 1

        # Contact detection and response are deliberately simple and explicit.
        states = {actor_id: self._state(actor_id, body) for actor_id, body in self._bodies.items()}
        ego_box = actor_box(states[self._ego_id])
        collisions = []
        stopped = set()
        for actor_id, state in states.items():
            if actor_id == self._ego_id:
                continue
            if overlap(ego_box, actor_box(state)):
                collisions.append(CollisionEvent(
                    other_actor_id=actor_id,
                    event_id=f"test-contact:{self._frame_index}:{actor_id}",
                ))
                stopped.update((self._ego_id, actor_id))
        for actor_id in stopped:
            body = self._bodies[actor_id]
            before = states[actor_id].velocity_mps
            body.speed_mps = 0.0
            # Total measured acceleration includes this tick's contact response.
            body.acceleration_mps2 = Vector3(**{
                key: getattr(body.acceleration_mps2, key) - getattr(before, key) / dt
                for key in ("x", "y", "z")
            })
        return self._snapshot(control, collisions)

    def agent_context(self, frame: TelemetryFrame):
        """Virtual straight-road ground truth, with no invented CARLA raster."""
        self._require_open()
        from .reference_scene import synthetic_scene
        actor_types = {actor.id: ("pedestrian" if actor.type == ActorType.PEDESTRIAN else "vehicle")
                       for actor in self._scenario.actors}
        scene = synthetic_scene(frame, actor_types, self._route_heading_deg, self.speed_limit_mps)
        if self._driving_task is not None:
            scene.route = self._task_route_tracker.local_route(frame.ego.transform) if (
                self._driving_task.route_owner == "framework" and frame.ego.transform is not None) else None
        return scene

    def prepare_driving_task(self, task, frame: TelemetryFrame):
        """Virtual straight routing only; this world has no real road graph."""
        self._require_open()
        from shared.models import DrivingTask
        task = DrivingTask.model_validate(task.model_dump()).model_copy(deep=True)
        if frame.ego.transform is None:
            raise SimulationError("Driving task requires a measured initial ego position")
        if task.route_owner == "agent":
            route = []
        elif task.route:
            route = [point.model_copy(deep=True) for point in task.route]
        else:
            route = [frame.ego.transform.model_copy(deep=True), task.destination.model_copy(deep=True)]
            if route[0].position == route[-1].position:
                route = route[:1]
        self._driving_task, self._driving_route = task, route
        from .navigation import RouteTracker
        self._task_route_tracker = RouteTracker(route,
            height_tolerance_m=task.completion_height_tolerance_m)
        return [point.model_copy(deep=True) for point in route]

    def traffic_light_observation(self) -> bool:
        """The explicitly virtual world has no traffic signals."""
        self._require_open()
        return False

    def finalize(self, frames: list[TelemetryFrame]) -> list[TelemetryFrame]:
        self._require_open()
        return frames

    def close(self) -> None:
        self._active = False
        self._bodies.clear()
        self._commands.clear()
        self._driving_task = None
        self._driving_route = []
        self._task_route_tracker = None

    def sensor_samples(self) -> tuple[SensorSample, ...]:
        return ()

    def runtime_info(self) -> dict[str, Any]:
        if self._scenario is None:
            return {}
        return {
            "adapter": self.mode,
            "server_version": None,
            "client_version": None,
            "map_name": f"test:{self._scenario.map}",
            "fixed_delta_seconds": self._scenario.fixed_delta_seconds,
            "seed": self._scenario.seed,
            "resolved_spawns": {key: value.model_copy(deep=True) for key, value in self._resolved_spawns.items()},
        }

    def _require_open(self) -> None:
        if not self._active:
            raise SimulationError("Test simulation is not open")

    @staticmethod
    def _move_actor(body: _Body, command: ActorCommand, dt: float) -> None:
        target = command.target_speed_mps
        if command.deceleration_mps2 is not None:
            target, magnitude = 0.0, command.deceleration_mps2
        else:
            magnitude = 3.0 if target >= body.speed_mps else 8.0
        speed, distance = _integrate_toward(body.speed_mps, target, magnitude, dt)
        _translate(body, distance)
        body.speed_mps = speed

    @staticmethod
    def _move_ego(body: _Body, control: ControlInput, dt: float) -> None:
        speed, direction = abs(body.speed_mps), (-1.0 if body.speed_mps < 0.0 else 1.0)
        desired_direction = -1.0 if control.reverse else 1.0
        if speed == 0.0:
            direction = desired_direction
        if control.hand_brake:
            acceleration = -8.0
        elif desired_direction != direction:
            # A gear change decelerates to zero before moving the other way.
            acceleration = -3.0 * control.throttle - 8.0 * control.brake
        else:
            acceleration = 3.0 * control.throttle - 8.0 * control.brake
        if acceleration < 0.0:
            new_speed, distance = _integrate_toward(speed, 0.0, -acceleration, dt)
        else:
            new_speed, distance = speed + acceleration * dt, speed * dt + 0.5 * acceleration * dt * dt
        distance *= direction
        old_yaw = body.transform.rotation.yaw_deg
        turn = math.degrees(distance / 2.7 * math.tan(math.radians(35.0 * control.steer)))
        turn = max(-90.0 * dt, min(90.0 * dt, turn))
        _translate(body, distance, yaw_deg=old_yaw + turn / 2.0)
        body.transform.rotation.yaw_deg = (old_yaw + turn + 180.0) % 360.0 - 180.0
        body.speed_mps = direction * new_speed

    def _state(self, actor_id: str, body: _Body) -> ActorState:
        extent = (
            BoundingBoxExtent(x=0.3, y=0.3, z=0.9)
            if body.spec.type == ActorType.PEDESTRIAN
            else BoundingBoxExtent(x=2.2, y=0.9, z=0.8)
        )
        box_pose = body.transform.model_copy(deep=True)
        pitch, yaw, roll = (math.radians(value % 360.0) for value in (
            box_pose.rotation.pitch_deg, box_pose.rotation.yaw_deg, box_pose.rotation.roll_deg
        ))
        # Rotate the local box-center offset (0, 0, half-height) into world axes.
        up = (
            -math.cos(yaw) * math.sin(pitch) * math.cos(roll) - math.sin(yaw) * math.sin(roll),
            -math.sin(yaw) * math.sin(pitch) * math.cos(roll) + math.cos(yaw) * math.sin(roll),
            math.cos(pitch) * math.cos(roll),
        )
        box_pose.position = Vector3(**{
            key: getattr(box_pose.position, key) + extent.z * value
            for key, value in zip(("x", "y", "z"), up)
        })
        return ActorState(
            actor_id=actor_id,
            transform=body.transform.model_copy(deep=True),
            velocity_mps=_velocity(body.transform.rotation, body.speed_mps),
            acceleration_mps2=body.acceleration_mps2.model_copy(deep=True),
            bounding_box_extent=extent,
            bounding_box_transform=box_pose,
            speed_limit_mps=self.speed_limit_mps,
        )

    def _snapshot(self, control: ControlInput, collisions: list[CollisionEvent]) -> TelemetryFrame:
        states = {actor_id: self._state(actor_id, body) for actor_id, body in self._bodies.items()}
        ego = states.pop(self._ego_id)
        return TelemetryFrame(
            frame=self._frame_index,
            time_s=self._frame_index * self._scenario.fixed_delta_seconds,
            ego=ego,
            actors=list(states.values()),
            control=control.model_copy(deep=True),
            collisions=collisions,
            # Speeding is derived from measured speed/limit by evaluation so
            # the caller's configured speeding tolerance remains effective.
            traffic_violations=[],
            rule_checks=list(TrafficRule),
        )


def _nonnegative(value: float, name: str, *, positive: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite nonnegative number")
    try:
        valid = math.isfinite(value) and (value > 0.0 if positive else value >= 0.0)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a finite {'positive' if positive else 'nonnegative'} number")


def _integrate_toward(speed: float, target: float, magnitude: float, dt: float) -> tuple[float, float]:
    if speed == target:
        return speed, speed * dt
    acceleration = magnitude if target > speed else -magnitude
    transition = min(dt, abs(target - speed) / magnitude)
    new_speed = speed + acceleration * transition
    if transition < dt:
        new_speed = target
    distance = speed * transition + 0.5 * acceleration * transition * transition + new_speed * (dt - transition)
    return new_speed, distance


def _velocity(rotation: Rotation, speed: float) -> Vector3:
    pitch, yaw = math.radians(rotation.pitch_deg % 360.0), math.radians(rotation.yaw_deg % 360.0)
    return Vector3(
        x=speed * math.cos(pitch) * math.cos(yaw),
        y=speed * math.cos(pitch) * math.sin(yaw),
        z=speed * math.sin(pitch),
    )


def _translate(body: _Body, distance: float, *, yaw_deg: float | None = None) -> None:
    rotation = body.transform.rotation.model_copy(deep=True)
    if yaw_deg is not None:
        rotation.yaw_deg = yaw_deg
    offset = _velocity(rotation, distance)
    position = body.transform.position
    body.transform.position = Vector3(x=position.x + offset.x, y=position.y + offset.y, z=position.z + offset.z)


def _resolve_spawns(scenario: Scenario) -> dict[str, Transform]:
    rng = random.Random(scenario.seed)
    used_indices = {actor.spawn_point_index for actor in scenario.actors if actor.spawn_point_index is not None}
    candidates = [index for index in range(max(256, len(scenario.actors) * 2)) if index not in used_indices]
    rng.shuffle(candidates)
    resolved = {}
    pending = list(scenario.actors)
    while pending:
        progress = False
        for actor in pending[:]:
            if actor.relative_spawn is not None:
                relative = actor.relative_spawn
                if relative.anchor_actor_id not in resolved:
                    continue
                anchor = resolved[relative.anchor_actor_id]
                pose = compose(anchor, Transform(
                    position=relative.offset_m,
                    rotation=Rotation(yaw_deg=relative.yaw_offset_deg),
                ))
            elif actor.spawn is not None:
                pose = actor.spawn.model_copy(deep=True)
            else:
                index = actor.spawn_point_index if actor.spawn_point_index is not None else candidates.pop()
                pose = Transform(position=Vector3(x=float((index // 4) * 80), y=float((index % 4) * 12)))
            resolved[actor.id] = pose
            pending.remove(actor)
            progress = True
        if not progress:
            raise SimulationError("Could not resolve relative test-world spawn hierarchy")
    return resolved
