"""Persisted episode evidence. Diagnostics are agent reports, not inferred reasoning."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, StrictBool, model_validator

from .agent import AgentReset, AgentStepResult
from .base import ContractModel, NonNegativeFloat, NonNegativeInt
from .telemetry import ActorState, ControlInput, TelemetryFrame


class DecisionSensor(ContractModel):
    # Exact wire header, excluding data_base64. Bytes live once in raw capture.
    header: dict[str, JsonValue]
    sample_index: NonNegativeInt
    byte_length: NonNegativeInt


class DecisionRecord(ContractModel):
    frame: NonNegativeInt
    time_s: NonNegativeFloat
    status: Literal["applied", "unresolved"]
    # Exact wire observation with sensors replaced by references below.
    observation: dict[str, JsonValue]
    sensors: list[DecisionSensor] = Field(default_factory=list, max_length=4)
    agent_result: AgentStepResult | None = None
    proposed_control: ControlInput | None = None
    applied_control: ControlInput | None = None
    interventions: list[Literal["scripted_ego_speed_target", "scripted_ego_braking"]] = Field(default_factory=list)
    worker_step_wall_time_s: NonNegativeFloat | None = None
    round_trip_wall_time_s: NonNegativeFloat | None = None
    full_step_wall_time_s: NonNegativeFloat | None = None
    resulting_frame: NonNegativeInt | None = None
    resulting_ego: ActorState | None = None
    control_mode: Literal["scripted", "authoritative"]

    @model_validator(mode="after")
    def validate_transition(self) -> Self:
        if (self.observation.get("frame") != self.frame or self.observation.get("time_s") != self.time_s
                or self.observation.get("sensors") != []):
            raise ValueError("Decision must identify its preceding observation and externalize sensor bytes")
        if self.status == "applied":
            if (self.proposed_control is None or self.applied_control is None or self.resulting_ego is None
                    or self.resulting_frame != self.frame + 1 or self.full_step_wall_time_s is None):
                raise ValueError("Applied decisions require controls and a measured next frame")
            if self.agent_result is not None and (
                    self.agent_result.frame != self.frame or self.agent_result.control != self.proposed_control
                    or self.agent_result.protocol_version != self.observation.get("protocol_version")):
                raise ValueError("Agent response must match the input frame, protocol and proposed control")
            if self.control_mode == "authoritative" and (self.interventions or self.proposed_control != self.applied_control):
                raise ValueError("Authoritative decisions must preserve agent control")
        elif (any(value is not None for value in (self.agent_result, self.proposed_control, self.applied_control,
                self.resulting_frame, self.resulting_ego, self.full_step_wall_time_s,
                self.worker_step_wall_time_s, self.round_trip_wall_time_s)) or self.interventions):
            raise ValueError("Unresolved inputs cannot claim an action or measured decision outcome")
        return self


class DecisionPage(ContractModel):
    simulation_id: UUID
    available: StrictBool
    coverage: Literal["unavailable", "empty", "partial", "complete"]
    total: NonNegativeInt
    applied: NonNegativeInt
    transitions: NonNegativeInt
    offset: NonNegativeInt
    limit: Annotated[int, Field(ge=1, le=100, strict=True)]
    reset: AgentReset | None
    recording_artifact_index: NonNegativeInt | None
    records: list[DecisionRecord]
    telemetry: list[TelemetryFrame]
