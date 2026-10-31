"""Bounded evidence analysis and descriptive, paired regression decisions."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from .base import ContractModel, NonEmptyString, NonNegativeFloat, NonNegativeInt
from .simulation import SimulationStatus


Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Statement = Annotated[str, Field(min_length=1, max_length=600)]
MetricName = Literal["severity", "collision_count", "min_ttc_s", "min_clearance_m",
                     "max_deceleration_mps2", "hard_braking_count", "traffic_violation_count"]


class AnalysisReference(ContractModel):
    source: Literal["telemetry", "result", "control_audit"]
    field: Annotated[str, Field(min_length=1, max_length=120)]
    frame: NonNegativeInt | None = None
    time_s: NonNegativeFloat | None = None

    @model_validator(mode="after")
    def timeline(self) -> Self:
        if (self.frame is None) != (self.time_s is None):
            raise ValueError("Frame references need both frame and recorded time")
        if self.source in {"telemetry", "control_audit"} and self.frame is None:
            raise ValueError("Timeline observations need an exact recorded frame")
        return self


class AnalysisObservation(ContractModel):
    kind: Literal["execution", "contact", "braking", "intervention", "traffic_rule", "coverage"]
    statement: Statement
    value: float | None = None
    unit: Annotated[str, Field(max_length=40)] | None = None
    coverage: Literal["complete", "partial", "unavailable"]
    references: list[AnalysisReference] = Field(min_length=1, max_length=8)


class AnalysisHypothesis(ContractModel):
    status: Literal["unverified"] = "unverified"
    statement: Statement
    review: Statement
    references: list[AnalysisReference] = Field(min_length=1, max_length=8)


class AnalysisCoverage(ContractModel):
    telemetry_frames: NonNegativeInt | None = None
    telemetry_gaps: NonNegativeInt | None = None
    safety_complete: bool | None = None
    interventions_recorded: bool | None = None
    decision_trace: Literal["not_reviewed", "unavailable"] = "not_reviewed"


class EvidenceAnalysis(ContractModel):
    schema_version: Literal["poltergeist-evidence-analysis-v1"] = "poltergeist-evidence-analysis-v1"
    method: Literal["recorded-evidence-rules-v1"] = "recorded-evidence-rules-v1"
    simulation_id: UUID
    mode: Literal["test", "carla"]
    execution_status: SimulationStatus
    state: Literal["pending", "complete", "partial", "unavailable"]
    source_digest: Digest | None = None
    telemetry_digest: Digest | None = None
    summary: Statement
    coverage: AnalysisCoverage
    observations: list[AnalysisObservation] = Field(max_length=32)
    hypotheses: list[AnalysisHypothesis] = Field(default_factory=list, max_length=4)
    unresolved_questions: list[Statement] = Field(max_length=8)
    limitations: list[Statement] = Field(max_length=10)


class RegressionPolicy(ContractModel):
    minimum_pairs: Annotated[int, Field(ge=2, le=200, strict=True)] = 3
    # A positive worsening difference must exceed the threshold in its native
    # unit. Unselected or unavailable measurements do not become zero.
    thresholds: dict[MetricName, NonNegativeFloat] = Field(
        default_factory=lambda: {"severity": 5.0, "collision_count": 0.0}, min_length=1, max_length=7)


class FrozenController(ContractModel):
    agent_index: NonNegativeInt
    registration_id: UUID | None
    name: NonEmptyString
    version: str | None
    snapshot_digest: Digest | None
    execution_digest: Digest | None
    python_digest: Digest | None
    dependencies_digest: Digest | None
    configuration_digest: Digest | None


class RegressionPair(ContractModel):
    case_index: NonNegativeInt
    compared_agent_index: NonNegativeInt
    seed: NonNegativeInt
    repeat_index: NonNegativeInt
    reference_simulation_id: UUID | None
    compared_simulation_id: UUID | None
    comparable: bool
    reason: Statement
    differences: dict[MetricName, float | None]
    reference_values: dict[MetricName, float | None]
    compared_values: dict[MetricName, float | None]

    @model_validator(mode="after")
    def comparable_values(self) -> Self:
        if not self.comparable and any(value is not None for value in self.differences.values()):
            raise ValueError("Incomparable pairs cannot supply regression differences")
        return self


class RegressionAssessment(ContractModel):
    case_index: NonNegativeInt
    case_name: NonEmptyString
    compared_agent_index: NonNegativeInt
    metric: MetricName
    unit: NonEmptyString
    worsening_direction: Literal["higher", "lower"]
    threshold: NonNegativeFloat
    decision: Literal["regression_observed", "no_regression_observed", "inconclusive"]
    planned_pairs: NonNegativeInt
    observed_pairs: NonNegativeInt
    worsening_pairs: NonNegativeInt
    mean_change: float | None
    minimum_change: float | None
    maximum_change: float | None
    sample_stddev: NonNegativeFloat | None
    explanation: Statement

    @model_validator(mode="after")
    def counts(self) -> Self:
        if not 0 <= self.worsening_pairs <= self.observed_pairs <= self.planned_pairs:
            raise ValueError("Regression counts must retain their observed denominators")
        return self


class RegressionReviewCoverage(ContractModel):
    available_runs: NonNegativeInt
    unavailable_runs: NonNegativeInt
    limited_runs: NonNegativeInt
    frames_read: NonNegativeInt
    bytes_read: NonNegativeInt
    frame_limit: NonNegativeInt
    byte_limit: NonNegativeInt


class RegressionReport(ContractModel):
    schema_version: Literal["poltergeist-regression-v1"] = "poltergeist-regression-v1"
    method: Literal["matched-observation-rules-v1"] = "matched-observation-rules-v1"
    operation_id: UUID
    execution_status: str
    source_digest: Digest
    plan_digest: Digest
    policy: RegressionPolicy
    state: Literal["regression_observed", "no_regression_observed", "inconclusive", "pending"]
    summary: Statement
    evidence_review: RegressionReviewCoverage
    context_notes: list[Statement] = Field(max_length=16)
    controllers: list[FrozenController] = Field(max_length=8)
    pairs: list[RegressionPair] = Field(max_length=200)
    assessments: list[RegressionAssessment] = Field(max_length=980)
    limitations: list[Statement] = Field(max_length=10)


class AnalysisContracts(ContractModel):
    analysis: EvidenceAnalysis
    regression: RegressionReport
    policy: RegressionPolicy
