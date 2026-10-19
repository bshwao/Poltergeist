"""Validated scenario definitions; all distances are metres and speeds m/s."""

from enum import StrEnum
from math import dist
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from .base import ContractModel, NonEmptyString, NonNegativeFloat, NonNegativeInt, PositiveFloat, Seed


class ActorType(StrEnum):
    EGO_VEHICLE = "ego_vehicle"
    VEHICLE = "vehicle"
    LEAD_VEHICLE = "lead_vehicle"
    CROSSING_VEHICLE = "crossing_vehicle"
    PEDESTRIAN = "pedestrian"


class ScenarioTemplate(StrEnum):
    INTERSECTION = "intersection"
    PEDESTRIAN_CROSSING = "pedestrian_crossing"
    OCCLUDED_PEDESTRIAN = "occluded_pedestrian"
    SUDDEN_BRAKING = "sudden_braking"


class Vector3(ContractModel):
    """CARLA coordinates: x forward, y right, z up; units depend on the field."""

    x: Annotated[float, Field(strict=True)] = 0.0
    y: Annotated[float, Field(strict=True)] = 0.0
    z: Annotated[float, Field(strict=True)] = 0.0


class Rotation(ContractModel):
    pitch_deg: Annotated[float, Field(strict=True)] = 0.0
    yaw_deg: Annotated[float, Field(strict=True)] = 0.0
    roll_deg: Annotated[float, Field(strict=True)] = 0.0


class Transform(ContractModel):
    position: Vector3 = Field(default_factory=Vector3)
    rotation: Rotation = Field(default_factory=Rotation)


class DrivingTask(ContractModel):
    """Explicit navigation objective, independent of an actor's initial speed.

    Framework routes use world-coordinate waypoints. Agent-owned navigation
    supplies only a destination so the agent must plan the route itself.
    """

    route_owner: Literal["framework", "agent"] = "framework"
    destination: Transform | None = None
    route: list[Transform] = Field(default_factory=list, max_length=4096)
    target_speed_mps: NonNegativeFloat | None = None
    completion_radius_m: PositiveFloat = 2.0
    completion_height_tolerance_m: PositiveFloat = 2.0
    completion_speed_mps: NonNegativeFloat | None = None
    completion_hold_s: NonNegativeFloat = 0.0
    stop_on_completion: StrictBool = False

    @model_validator(mode="after")
    def validate_navigation(self) -> Self:
        if self.destination is None and not self.route:
            raise ValueError("Driving task requires a destination or a route")
        if self.route and len(self.route) < 2:
            raise ValueError("Driving task route must contain at least two points")
        if self.route_owner == "agent" and self.route:
            raise ValueError("Agent-owned navigation must not supply a framework route")
        for first, second in zip(self.route, self.route[1:]):
            if first.position == second.position:
                raise ValueError("Driving task route must not contain adjacent duplicate positions")
        if self.route:
            end = self.route[-1]
            if self.destination is None:
                self.destination = end.model_copy(deep=True)
            elif dist(
                (self.destination.position.x, self.destination.position.y, self.destination.position.z),
                (end.position.x, end.position.y, end.position.z),
            ) > 1e-6:
                raise ValueError("Driving task destination must match the final route position")
        return self


class Weather(ContractModel):
    cloudiness: Annotated[float, Field(ge=0, le=100, strict=True)] = 0.0
    precipitation: Annotated[float, Field(ge=0, le=100, strict=True)] = 0.0
    wetness: Annotated[float, Field(ge=0, le=100, strict=True)] = 0.0
    fog_density: Annotated[float, Field(ge=0, le=100, strict=True)] = 0.0
    sun_altitude_deg: Annotated[float, Field(ge=-90, le=90, strict=True)] = 70.0


class RelativeSpawn(ContractModel):
    """Spawn in an anchor actor's local frame: x forward, y right, z up.

    The simulation adapter resolves this placement after spawning the anchor.
    These coordinates do not imply the position lies on a drivable road.
    """

    anchor_actor_id: NonEmptyString
    offset_m: Vector3 = Field(default_factory=Vector3)
    yaw_offset_deg: Annotated[float, Field(strict=True)] = 0.0


class ActorSpec(ContractModel):
    type: ActorType
    id: NonEmptyString | None = None
    blueprint: NonEmptyString | None = None
    # With no explicit placement, the simulation adapter selects a spawn point.
    spawn: Transform | None = None
    spawn_point_index: NonNegativeInt | None = None
    relative_spawn: RelativeSpawn | None = None
    speed_mps: NonNegativeFloat = 0.0

    @model_validator(mode="after")
    def validate_placement(self) -> Self:
        if sum(value is not None for value in (self.spawn, self.spawn_point_index, self.relative_spawn)) > 1:
            raise ValueError("Actor placement must use only one of spawn, spawn_point_index, or relative_spawn")
        return self


class SpeedChangeEvent(ContractModel):
    """Set an actor's target speed at simulation time trigger_time_s."""

    action: Literal["set_speed"] = "set_speed"
    actor_id: NonEmptyString
    trigger_time_s: NonNegativeFloat
    speed_mps: NonNegativeFloat


class BrakingEvent(ContractModel):
    """Begin braking at a positive deceleration magnitude until stopped."""

    action: Literal["brake"] = "brake"
    actor_id: NonEmptyString
    trigger_time_s: NonNegativeFloat
    deceleration_mps2: PositiveFloat


ScenarioEvent = Annotated[SpeedChangeEvent | BrakingEvent, Field(discriminator="action")]


class Scenario(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    name: NonEmptyString
    template: ScenarioTemplate | None = None
    description: str = ""
    map: NonEmptyString = "Town03"
    seed: Seed = 0
    duration_s: PositiveFloat = 60.0
    fixed_delta_seconds: Annotated[float, Field(gt=0, le=0.1, strict=True)] = 0.05
    weather: Weather = Field(default_factory=Weather)
    actors: Annotated[list[ActorSpec], Field(min_length=1)]
    events: list[ScenarioEvent] = Field(default_factory=list)
    driving_task: DrivingTask | None = None
    # Legacy descriptive metadata is retained alongside executable events.
    location: NonEmptyString | None = None
    event: NonEmptyString | None = None
    occlusion: StrictBool = False
    parameters: dict[NonEmptyString, StrictBool | StrictInt | StrictFloat | str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_actors(self) -> Self:
        if sum(actor.type == ActorType.EGO_VEHICLE for actor in self.actors) != 1:
            raise ValueError("A scenario must contain exactly one ego_vehicle")
        ids = [actor.id for actor in self.actors if actor.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("Actor IDs must be unique within a scenario")
        if self.duration_s < self.fixed_delta_seconds:
            raise ValueError("duration_s must allow at least one simulation tick")

        known_ids = set(ids)
        anchors: dict[str, str] = {}
        for actor in self.actors:
            if actor.relative_spawn is None:
                continue
            anchor = actor.relative_spawn.anchor_actor_id
            if anchor not in known_ids:
                raise ValueError(f"Relative spawn references unknown actor ID: {anchor}")
            if actor.id == anchor:
                raise ValueError(f"Actor {actor.id} cannot spawn relative to itself")
            if actor.id is not None:
                anchors[actor.id] = anchor

        # Walk dependency chains without recursion so long valid chains are safe.
        resolved: set[str] = set()
        for actor_id in anchors:
            chain: set[str] = set()
            current = actor_id
            while current in anchors and current not in resolved:
                if current in chain:
                    raise ValueError("Relative actor spawn dependencies must not contain cycles")
                chain.add(current)
                current = anchors[current]
            resolved.update(chain)

        for event in self.events:
            if event.actor_id not in known_ids:
                raise ValueError(f"Scenario event references unknown actor ID: {event.actor_id}")
            if event.trigger_time_s >= self.duration_s:
                raise ValueError("Event trigger_time_s must be less than duration_s")
        return self
