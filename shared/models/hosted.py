"""Explicit public baseline-run projection; local records are never its base.

Additions to local simulation, agent, runtime or diagnostics models do not become
public fields automatically. Remote execution capability belongs to the service
capability document, not to an individual run summary.
"""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


Count = Annotated[int, Field(ge=0, le=2**63 - 1)]
Revision = Annotated[int, Field(ge=1, le=2**63 - 1)]
NonNegative = Annotated[float, Field(ge=0)]
Positive = Annotated[float, Field(gt=0)]
SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


class _PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False,
        str_strip_whitespace=False, hide_input_in_errors=True, frozen=True,
        validate_default=True, json_schema_serialization_defaults_required=True)


class HostedScenarioV1(_PublicModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    sha256: SHA256


class HostedEvaluationSettingsV1(_PublicModel):
    method_version: Literal["1.0"]
    collision_episode_gap_s: NonNegative
    violation_episode_gap_s: NonNegative
    speeding_tolerance_mps: NonNegative
    hard_braking_threshold_mps2: Positive
    stopped_speed_mps: NonNegative
    max_sample_gap_s: Positive
    ttc_horizon_s: Positive
    ttc_warning_s: Positive
    clearance_warning_m: Positive
    braking_full_severity_mps2: Positive
    violations_for_full_severity: Revision
    ttc_weight: NonNegative
    clearance_weight: NonNegative
    braking_weight: NonNegative
    traffic_rule_weight: NonNegative

    @model_validator(mode="after")
    def consistent_scoring(self) -> Self:
        if (self.ttc_horizon_s < self.ttc_warning_s
                or self.braking_full_severity_mps2 <= self.hard_braking_threshold_mps2
                or abs(self.ttc_weight + self.clearance_weight + self.braking_weight
                       + self.traffic_rule_weight - 100.) > 1e-9):
            raise ValueError("Invalid public evaluation settings")
        return self


class HostedCoverageV1(_PublicModel):
    total_frames: Count
    collision_frames: Count
    traffic_rule_frames: Count
    braking_frames: Count
    clearance_frames: Count
    ttc_frames: Count
    actor_pairs: Count
    clearance_pairs: Count
    ttc_pairs: Count
    has_gaps: bool
    complete: bool

    @model_validator(mode="after")
    def consistent_coverage(self) -> Self:
        counts = (self.collision_frames, self.traffic_rule_frames, self.braking_frames,
                  self.clearance_frames, self.ttc_frames)
        if any(count > self.total_frames for count in counts) or not (
                self.ttc_pairs <= self.clearance_pairs <= self.actor_pairs):
            raise ValueError("Invalid public measurement coverage")
        complete = (self.total_frames > 0 and not self.has_gaps
            and self.ttc_pairs == self.clearance_pairs == self.actor_pairs
            and all(count == self.total_frames for count in counts))
        if self.complete != complete:
            raise ValueError("Public coverage completeness does not match observations")
        return self


class HostedSafetyMetricsV1(_PublicModel):
    collision_count: Count | None
    traffic_violation_count: Count | None
    min_clearance_m: NonNegative | None
    min_ttc_s: NonNegative | None
    max_deceleration_mps2: NonNegative | None
    hard_braking_count: Count | None
    max_stopping_distance_m: NonNegative | None
    max_stopping_time_s: NonNegative | None
    severity: Annotated[float, Field(ge=0, le=100)] | None
    coverage: HostedCoverageV1
    evaluation_settings: HostedEvaluationSettingsV1 | None


class HostedRuntimeV1(_PublicModel):
    adapter: Literal["test", "carla"]
    # Unknown/unrecognized version and map strings are omitted, never forwarded.
    server_version: Annotated[str, Field(pattern=r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$")] | None
    client_version: Annotated[str, Field(pattern=r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$")] | None
    map_name: Annotated[str, Field(pattern=r"^Town[0-9]{2}(?:HD)?(?:_Opt)?$")] | None
    fixed_delta_seconds: Positive
    seed: Annotated[int, Field(ge=0, le=2**31 - 1)]


class HostedProvenanceV1(_PublicModel):
    observation_mode: Literal["simulator_state"] | None
    source: Literal["synthetic_state", "carla_ground_truth"] | None
    ego_state_source: Literal["simulator_state"] | None
    ground_truth_actor_inputs: bool | None
    control_mode: Literal["scripted"] | None
    controller: Literal["baseline"] | None
    navigation: Literal["legacy_assisted", "framework_route"] | None

    @model_validator(mode="after")
    def baseline_inputs(self) -> Self:
        if self.ground_truth_actor_inputs is not None and self.ground_truth_actor_inputs is not True:
            raise ValueError("Baseline input provenance requires ground-truth actor inputs")
        return self


class HostedPublicErrorV1(_PublicModel):
    code: Literal["execution_failed", "result_unavailable"]
    message: Literal[
        "The experiment failed on this computer. Review its local diagnostics.",
        "The finalized result is unavailable on this computer.",
    ]
    correlation_id: UUID

    @model_validator(mode="after")
    def fixed_message(self) -> Self:
        expected = ("The experiment failed on this computer. Review its local diagnostics."
            if self.code == "execution_failed" else
            "The finalized result is unavailable on this computer.")
        if self.message != expected:
            raise ValueError("Public errors must use the fixed message for their code")
        return self


class HostedEvidenceV1(_PublicModel):
    """The caller checks local availability; counts describe persisted metadata.

    No path, download URL or transfer capability is published. A known recorded
    frame count can survive a missing artifact without implying that it is zero.
    """

    state: Literal["local_only", "missing"]
    telemetry_frames: Count | None
    artifact_reference_count: Count | None


class HostedRunSummaryV1(_PublicModel):
    protocol_version: Literal["poltergeist-hosted-run-v1"] = "poltergeist-hosted-run-v1"
    device_id: UUID
    ownership_generation: Revision
    execute_request_id: UUID
    # Persisted remote projection sequence; cancellation can advance it without
    # changing the local simulation record. Connection fencing stays separate.
    receipt_revision: Revision
    # Local StoredSimulation revision, not a relay/projection sequence number.
    revision: Revision
    simulation_id: UUID
    mode: Literal["test", "carla"]
    status: RunStatus
    progress: Annotated[float, Field(ge=0, le=1)]
    cancel_requested: bool | None
    queue_position: Revision | None
    created_at: AwareDatetime
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    updated_at: AwareDatetime
    scenario: HostedScenarioV1
    agent: Literal["baseline"] = "baseline"
    result_available: bool
    measured_duration_s: NonNegative | None
    metrics: HostedSafetyMetricsV1 | None
    runtime: HostedRuntimeV1 | None
    provenance: HostedProvenanceV1 | None
    error: HostedPublicErrorV1 | None
    evidence: HostedEvidenceV1

    @model_validator(mode="after")
    def consistent_run(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("Public update precedes run creation")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("Public start precedes run creation")
        if self.finished_at is not None and self.finished_at < (self.started_at or self.created_at):
            raise ValueError("Public finish precedes run start")
        if self.updated_at < (self.finished_at or self.started_at or self.created_at):
            raise ValueError("Public update precedes the recorded lifecycle")
        if self.status == "queued":
            if self.started_at is not None or self.finished_at is not None or self.progress != 0:
                raise ValueError("Invalid public queued lifecycle")
        elif self.status == "running":
            if self.started_at is None or self.finished_at is not None or self.progress >= 1:
                raise ValueError("Invalid public running lifecycle")
        elif self.finished_at is None or (self.status == "completed"
                and (self.started_at is None or self.progress != 1)):
            raise ValueError("Invalid public terminal lifecycle")
        if self.status != "queued" and self.queue_position is not None:
            raise ValueError("Only queued runs have a waiting position")
        if self.result_available != (self.metrics is not None):
            raise ValueError("Public result presence does not match metrics")
        if self.status in ("queued", "running") and self.result_available:
            raise ValueError("Unfinished public runs cannot have finalized results")
        if self.result_available and (self.measured_duration_s is None
                or self.evidence.telemetry_frames is None or self.evidence.artifact_reference_count is None):
            raise ValueError("Finalized public results require measured duration and evidence metadata counts")
        if not self.result_available and (self.measured_duration_s is not None
                or self.runtime is not None or self.provenance is not None
                or self.evidence.state != "missing" or self.evidence.telemetry_frames is not None
                or self.evidence.artifact_reference_count is not None):
            raise ValueError("Missing public results cannot advertise measured evidence")
        expected = ("execution_failed" if self.status == "failed" else
            "result_unavailable" if self.status in ("completed", "cancelled")
            and not self.result_available else None)
        if (None if self.error is None else self.error.code) != expected:
            raise ValueError("Public error does not match run lifecycle")
        if self.runtime is not None and self.runtime.adapter != self.mode:
            raise ValueError("Public runtime adapter does not match run mode")
        return self
