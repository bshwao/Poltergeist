"""Simulator-independent telemetry contracts expressed in SI units."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, StrictBool, model_validator

from .base import ContractModel, NonEmptyString, NonNegativeFloat, NonNegativeInt, PositiveFloat
from .evaluation import EvaluationCoverage, EvaluationSettings
from .scenario import Transform, Vector3


class ControlInput(ContractModel):
    throttle: Annotated[float, Field(ge=0, le=1, strict=True)] = 0.0
    steer: Annotated[float, Field(ge=-1, le=1, strict=True)] = 0.0
    brake: Annotated[float, Field(ge=0, le=1, strict=True)] = 0.0
    hand_brake: StrictBool = False
    reverse: StrictBool = False


class BoundingBoxExtent(ContractModel):
    """Positive half-lengths of an actor's bounding box, in metres."""

    x: PositiveFloat
    y: PositiveFloat
    z: PositiveFloat


class ActorState(ContractModel):
    actor_id: NonEmptyString
    transform: Transform | None = None
    velocity_mps: Vector3 | None = None
    # Signed world-space acceleration; braking metrics derive deceleration.
    acceleration_mps2: Vector3 | None = None
    bounding_box_extent: BoundingBoxExtent | None = None
    # World pose of the actual box center, including CARLA's local box offset.
    # If absent, geometry assumes the box is centered/oriented at transform.
    bounding_box_transform: Transform | None = None
    speed_limit_mps: PositiveFloat | None = None


class CollisionEvent(ContractModel):
    other_actor_id: NonEmptyString
    impulse_n_s: NonNegativeFloat = 0.0
    event_id: NonEmptyString | None = None


class TrafficRule(StrEnum):
    RED_LIGHT = "red_light"
    STOP_SIGN = "stop_sign"
    SPEEDING = "speeding"
    WRONG_WAY = "wrong_way"
    SIDEWALK = "sidewalk"


class TrafficViolation(ContractModel):
    rule: TrafficRule
    actor_id: NonEmptyString
    details: str = ""
    source_id: NonEmptyString | None = None
    event_id: NonEmptyString | None = None


class TelemetryFrame(ContractModel):
    frame: NonNegativeInt
    time_s: NonNegativeFloat
    ego: ActorState
    # None means unobserved; [] means observed with no other actors/events.
    actors: list[ActorState] | None = None
    control: ControlInput = Field(default_factory=ControlInput)
    collisions: list[CollisionEvent] | None = None
    traffic_violations: list[TrafficViolation] | None = None

    @model_validator(mode="after")
    def validate_actor_ids(self) -> Self:
        ids = [self.ego.actor_id, *(actor.actor_id for actor in self.actors or [])]
        if len(ids) != len(set(ids)):
            raise ValueError("Telemetry actor IDs must be unique, including the ego")
        if any(event.other_actor_id == self.ego.actor_id for event in self.collisions or []):
            raise ValueError("An ego collision cannot reference the ego as the other actor")
        return self


class SafetyMetrics(ContractModel):
    """Null minima mean unavailable/no relevant interaction; never encode infinity.

    Severity is an observed collision score (100), or a score from a completely
    observed trace. Otherwise it is null. Settings preserve scoring provenance.
    """

    collision_count: NonNegativeInt | None = None
    traffic_violation_count: NonNegativeInt | None = None
    min_clearance_m: NonNegativeFloat | None = None
    min_ttc_s: NonNegativeFloat | None = None
    max_deceleration_mps2: NonNegativeFloat | None = None
    hard_braking_count: NonNegativeInt | None = None
    max_stopping_distance_m: NonNegativeFloat | None = None
    max_stopping_time_s: NonNegativeFloat | None = None
    severity: Annotated[float, Field(ge=0, le=100, strict=True)] | None = None
    coverage: EvaluationCoverage = Field(default_factory=EvaluationCoverage)
    evaluation_settings: EvaluationSettings | None = None
