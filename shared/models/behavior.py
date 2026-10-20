"""Versioned, deterministic analysis of recorded behavior (not model reasoning)."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StrictBool, model_validator

from .agent import AgentInputInfo
from .base import ContractModel, NonEmptyString, NonNegativeFloat, NonNegativeInt, PositiveFloat
from .evaluation import EvaluationSettings
from .registry import AgentSnapshot
from .simulation import DrivingOutcome, SimulationStatus
from .telemetry import SafetyMetrics


class BehaviorSettings(ContractModel):
    method_version: Literal["1.0"] = "1.0"
    response_window_s: Annotated[float, Field(gt=0, le=60, strict=True)] = 3.0
    brake_threshold: Annotated[float, Field(gt=0, le=1, strict=True)] = 0.1
    steering_change_threshold: Annotated[float, Field(gt=0, le=2, strict=True)] = 0.15
    deceleration_threshold_mps2: PositiveFloat = 0.5
    heading_change_deg: Annotated[float, Field(gt=0, le=180, strict=True)] = 5.0
    hazard_ttc_s: Annotated[float, Field(gt=0, le=60, strict=True)] = 3.0
    hazard_clearance_m: NonNegativeFloat = 2.0
    max_sample_gap_s: PositiveFloat = 0.5
    route_height_tolerance_m: PositiveFloat = 2.0
    max_response_events: Annotated[int, Field(ge=1, le=2000, strict=True)] = 200
    max_stopping_episodes: Annotated[int, Field(ge=1, le=2000, strict=True)] = 200


class EvidenceSpan(ContractModel):
    source: Literal["telemetry", "decisions", "driving_outcome", "stored_safety_metrics"]
    first_frame: NonNegativeInt
    last_frame: NonNegativeInt
    first_time_s: NonNegativeFloat
    last_time_s: NonNegativeFloat

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.last_frame < self.first_frame or self.last_time_s < self.first_time_s:
            raise ValueError("Evidence span must preserve frame and time ordering")
        return self


class BehaviorMeasure(ContractModel):
    value: NonNegativeFloat | None
    unit: Literal["m", "m/s²", "episodes", "fraction"]
    coverage: Literal["complete", "partial", "unavailable"]
    observed_samples: NonNegativeInt
    eligible_samples: NonNegativeInt
    reason: NonEmptyString | None = None
    evidence: list[EvidenceSpan] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.observed_samples > self.eligible_samples:
            raise ValueError("Observed samples cannot exceed eligible samples")
        if (self.value is None) != (self.coverage == "unavailable"):
            raise ValueError("Unavailable measurements must be null; observed zero is a value")
        if self.value is not None and (not self.observed_samples or not self.evidence):
            raise ValueError("Measurements require supporting observations")
        return self


class StoppingEpisode(ContractModel):
    duration_s: NonNegativeFloat
    distance_m: NonNegativeFloat
    evidence: EvidenceSpan
    script_influenced: StrictBool | None
    intervention_frames: NonNegativeInt


class BrakingBehavior(ContractModel):
    settings: EvaluationSettings
    settings_source: Literal["recorded_evaluation", "legacy_defaults"]
    max_deceleration: BehaviorMeasure
    hard_braking_count: BehaviorMeasure
    stopping_episodes: list[StoppingEpisode]
    total_stopping_episodes: NonNegativeInt
    episodes_truncated: StrictBool


class RouteBehavior(ContractModel):
    route_owner: Literal["framework", "agent"] | None
    reference_source: Literal["recorded_resolved_route"] | None
    # Planar distance to a finite route polyline with height gating, not lane compliance.
    max_deviation: BehaviorMeasure
    mean_deviation: BehaviorMeasure
    # Copied from the recorded online task monitor; no global-nearest progress guess.
    recorded_progress: BehaviorMeasure
    recorded_length: BehaviorMeasure
    recorded_completion_fraction: BehaviorMeasure


class BehaviorAnchor(ContractModel):
    id: NonEmptyString
    kind: Literal["scenario_event", "measured_hazard"]
    source: Literal["scenario_script", "evaluator_ground_truth"]
    actor_id: NonEmptyString
    time_s: NonNegativeFloat
    frame: NonNegativeInt | None
    event_index: NonNegativeInt | None = None
    event_action: Literal["set_speed", "brake"] | None = None
    scheduled_time_s: NonNegativeFloat | None = None
    ego_event: StrictBool = False
    onset: Literal["scheduled_command", "threshold_crossing", "first_observed"]
    ttc_s: NonNegativeFloat | None = None
    clearance_m: NonNegativeFloat | None = None
    reason: NonEmptyString | None = None


class BehaviorResponse(ContractModel):
    channel: Literal["proposed_braking", "applied_braking", "proposed_steering_change",
                     "applied_steering_change", "measured_deceleration", "measured_heading_change"]
    status: Literal["observed", "already_active", "not_observed", "unavailable"]
    # Only a continuously observed threshold onset receives a latency.
    latency_s: NonNegativeFloat | None
    response_frame: NonNegativeInt | None
    response_time_s: NonNegativeFloat | None
    value: NonNegativeFloat | None
    threshold: PositiveFloat
    unit: Literal["normalized_control", "m/s²", "degrees"]
    reference_frame: NonNegativeInt | None = None
    reference_value: Annotated[float, Field(strict=True)] | None = None
    attribution: Literal["sdk_agent", "baseline_controller", "legacy_custom", "script_influenced",
                         "unknown", "measured_motion"]
    observed_until_s: NonNegativeFloat | None
    window_end_s: NonNegativeFloat
    window_complete: StrictBool
    reason: NonEmptyString | None = None
    evidence: list[EvidenceSpan] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.status == "observed") != (self.latency_s is not None):
            raise ValueError("Only measured response onsets can have a latency")
        if self.status in {"observed", "already_active"} and (
                self.response_frame is None or self.response_time_s is None or self.value is None or not self.evidence):
            raise ValueError("Observed behavior needs frame/time/value evidence")
        return self


class EventBehavior(ContractModel):
    anchor: BehaviorAnchor
    responses: list[BehaviorResponse]


class BehaviorCoverage(ContractModel):
    telemetry_frames: NonNegativeInt
    telemetry_gaps: NonNegativeInt
    pose_frames: NonNegativeInt
    velocity_frames: NonNegativeInt
    measured_duration_s: NonNegativeFloat
    decision_coverage: Literal["unavailable", "empty", "partial", "complete"]
    applied_decisions: NonNegativeInt
    unresolved_inputs: NonNegativeInt
    script_intervention_frames: NonNegativeInt
    changed_control_frames: NonNegativeInt
    hazard_clearance_pairs: NonNegativeInt
    hazard_ttc_pairs: NonNegativeInt
    total_response_events: NonNegativeInt
    response_events_truncated: StrictBool


class BehaviorAnalysis(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    simulation_id: UUID
    mode: Literal["carla", "test"]
    execution_status: SimulationStatus
    agent: Literal["baseline", "custom"]
    agent_snapshot: AgentSnapshot | None
    agent_inputs: AgentInputInfo | None
    control_mode: Literal["scripted", "authoritative"] | None
    settings: BehaviorSettings
    coverage: BehaviorCoverage
    # Stored scoring provenance is copied unchanged, never replaced by analysis settings.
    stored_safety_metrics: SafetyMetrics
    safety_evidence: list[EvidenceSpan]
    driving_outcome: DrivingOutcome | None
    task_evidence: list[EvidenceSpan]
    braking: BrakingBehavior
    route: RouteBehavior
    events: list[EventBehavior]
    interpretation_notes: list[NonEmptyString]

    @model_validator(mode="after")
    def finalized(self) -> Self:
        if self.execution_status in {SimulationStatus.QUEUED, SimulationStatus.RUNNING}:
            raise ValueError("Behavior analysis requires a finalized run")
        return self
