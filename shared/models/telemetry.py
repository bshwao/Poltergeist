"""Simulator-independent telemetry contracts expressed in SI units."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, StrictBool, model_validator

from .base import ContractModel, NonEmptyString, NonNegativeFloat, NonNegativeInt, PositiveFloat
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
    transform: Transform = Field(default_factory=Transform)
    velocity_mps: Vector3 = Field(default_factory=Vector3)
    # Signed world-space acceleration; braking metrics derive deceleration.
    acceleration_mps2: Vector3 = Field(default_factory=Vector3)
    bounding_box_extent: BoundingBoxExtent | None = None


class CollisionEvent(ContractModel):
    other_actor_id: NonEmptyString
    impulse_n_s: NonNegativeFloat = 0.0


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


class TelemetryFrame(ContractModel):
    frame: NonNegativeInt
    time_s: NonNegativeFloat
    ego: ActorState
    actors: list[ActorState] = Field(default_factory=list)
    control: ControlInput = Field(default_factory=ControlInput)
    collisions: list[CollisionEvent] = Field(default_factory=list)
    traffic_violations: list[TrafficViolation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_actor_ids(self) -> Self:
        ids = [self.ego.actor_id, *(actor.actor_id for actor in self.actors)]
        if len(ids) != len(set(ids)):
            raise ValueError("Telemetry actor IDs must be unique, including the ego")
        return self


class SafetyMetrics(ContractModel):
    """Null minima mean unavailable/no relevant interaction; never encode infinity.

    Severity ranges from 0 (least severe) to 100 (most severe). The scoring
    algorithm and detection thresholds will be implemented in section 3.
    """

    collision_count: NonNegativeInt = 0
    traffic_violation_count: NonNegativeInt = 0
    min_clearance_m: NonNegativeFloat | None = None
    min_ttc_s: NonNegativeFloat | None = None
    max_deceleration_mps2: NonNegativeFloat = 0.0
    severity: Annotated[float, Field(ge=0, le=100, strict=True)] = 0.0
