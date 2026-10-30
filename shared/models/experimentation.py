"""Guided experiments, reusable suites and explicitly approved discovery plans."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, StrictBool, field_validator, model_validator

from .base import ContractModel, NonNegativeInt
from .comparison import ComparisonGroup, PairedComparison
from .optimization import (DiscoveryBudget, DiscoveryGoal, DiscoveryRequest, ExecutionSpec, OptimizationRecord,
    OptimizationView, ReproductionRequest, SearchRequest, SuiteRequest)
from .registry import AgentSnapshot
from .scenario import Scenario, ScenarioTemplate

Name = Annotated[str, Field(min_length=1, max_length=128)]


class GalleryScenario(ContractModel):
    id: Annotated[str, Field(min_length=1, max_length=80)]
    title: Name
    description: Annotated[str, Field(min_length=1, max_length=1000)]
    family: ScenarioTemplate
    level: Literal["starter", "low", "medium", "high"]
    learning_goal: Annotated[str, Field(min_length=1, max_length=500)]
    scenario: Scenario


class SavedSuite(ContractModel):
    id: UUID = Field(default_factory=uuid4)
    revision: Annotated[int, Field(ge=1, strict=True)] = 1
    definition: SuiteRequest
    description: Annotated[str, Field(max_length=1000)] = ""
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class SuiteSave(ContractModel):
    definition: SuiteRequest
    description: Annotated[str, Field(max_length=1000)] = ""


class SuiteUpdate(SuiteSave):
    expected_revision: Annotated[int, Field(ge=1, strict=True)]


class SuiteLaunch(ContractModel):
    expected_revision: Annotated[int, Field(ge=1, strict=True)]
    request_id: UUID


class SuiteCaseComparison(ContractModel):
    case_index: NonNegativeInt
    name: Name
    groups: list[ComparisonGroup]
    paired_comparisons: list[PairedComparison]
    notes: list[str]


class SuiteReport(ContractModel):
    schema_version: Literal["poltergeist-suite-report-v1"] = "poltergeist-suite-report-v1"
    evidence: OptimizationView
    cases: list[SuiteCaseComparison]


class DiscoveryProposal(ExecutionSpec):
    goal: DiscoveryGoal
    budget: DiscoveryBudget


class DiscoveryPlan(ContractModel):
    schema_version: Literal["poltergeist-discovery-plan-v1"] = "poltergeist-discovery-plan-v1"
    id: UUID = Field(default_factory=uuid4)
    revision: Annotated[int, Field(ge=1, strict=True)] = 1
    proposal: DiscoveryProposal
    search: SearchRequest
    scenarios: Annotated[list[Scenario], Field(min_length=1, max_length=100)]
    agent_snapshot: AgentSnapshot | None
    digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    maximum_executions: NonNegativeInt
    per_run_duration_s: Annotated[float, Field(gt=0)]
    steps: list[str] = Field(min_length=1, max_length=10)
    limitations: list[str] = Field(min_length=1, max_length=10)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: AwareDatetime
    operation_id: UUID | None = None
    approved_at: AwareDatetime | None = None
    deadline_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def approval_state(self) -> Self:
        if len({self.operation_id is None, self.approved_at is None, self.deadline_at is None}) != 1:
            raise ValueError("Approval identity and deadline must be stored together")
        if self.expires_at <= self.created_at:
            raise ValueError("Plan expiry must follow its creation")
        if self.operation_id is not None and self.operation_id != self.id:
            raise ValueError("Approved plan and execution identities must match")
        if len(self.scenarios) != self.proposal.budget.max_search_trials:
            raise ValueError("A plan freezes every approved search candidate")
        self._request(self.approved_at or self.created_at,
            self.deadline_at or self.created_at + timedelta(seconds=self.proposal.budget.max_wall_time_s))
        if (self.maximum_executions != min(self.proposal.budget.max_executions,
                self.proposal.budget.max_search_trials + self.proposal.budget.max_candidates * self.proposal.budget.confirmation_repeats)
                or self.per_run_duration_s != self.proposal.goal.duration_s):
            raise ValueError("Plan cost must match its exact execution policy")
        if ((self.proposal.goal.agent_id is None) != (self.agent_snapshot is None)
                or self.agent_snapshot is not None and self.agent_snapshot.registration_id != self.proposal.goal.agent_id):
            raise ValueError("A plan must freeze the selected registered agent")
        return self

    def _request(self, approved_at, deadline_at):
        return DiscoveryRequest(**self.proposal.model_dump(mode="python"), plan_id=self.id,
            plan_digest=self.digest, search=self.search, scenarios=self.scenarios,
            approved_at=approved_at, deadline_at=deadline_at)

    def approved_request(self) -> DiscoveryRequest:
        if self.operation_id is None:
            raise ValueError("A discovery request requires explicit saved approval")
        return self._request(self.approved_at, self.deadline_at)


class DiscoveryApproval(ContractModel):
    expected_revision: Annotated[int, Field(ge=1, strict=True)]
    digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    approve: Literal[True]

    @field_validator("approve", mode="before")
    @classmethod
    def explicit_approval(cls, value):
        if value is not True:
            raise ValueError("Discovery requires explicit boolean approval")
        return value


class DiscoveryCandidate(ContractModel):
    candidate_index: NonNegativeInt
    scenario: Scenario
    search_simulation_id: UUID
    failure_id: UUID | None
    confirmation_simulation_ids: list[UUID]
    status: Literal["confirmed", "not_reproduced", "inconclusive", "pending"]
    required_successes: NonNegativeInt
    matching_confirmations: NonNegativeInt
    negative_confirmations: NonNegativeInt
    unknown_confirmations: NonNegativeInt
    pending_confirmations: NonNegativeInt
    reproduction: ReproductionRequest | None


class DiscoveryReport(ContractModel):
    schema_version: Literal["poltergeist-discovery-report-v1"] = "poltergeist-discovery-report-v1"
    plan: DiscoveryPlan
    evidence: OptimizationView
    candidates: list[DiscoveryCandidate]
    confirmed_failures: NonNegativeInt
    executions_used: NonNegativeInt
    simulated_time_reserved_s: Annotated[float, Field(ge=0)]
    negative_trials: NonNegativeInt
    unknown_trials: NonNegativeInt
    unassessed_trials: NonNegativeInt
    summary: str
    limitations: list[str]


class ReplayHighlight(ContractModel):
    kind: Literal["collision", "near_miss", "hard_braking", "traffic_violation", "finish"]
    frame: NonNegativeInt
    time_s: Annotated[float, Field(ge=0)]
    title: Name
    description: str
    source: Literal["telemetry"] = "telemetry"


class ReplayHighlights(ContractModel):
    simulation_id: UUID
    mode: Literal["test", "carla"]
    highlights: list[ReplayHighlight] = Field(max_length=12)
    total_frames: NonNegativeInt
    coverage_complete: StrictBool
    explanation: str
    limitations: list[str]


class GuidanceFeedback(ContractModel):
    simulation_id: UUID | None = None
    could_explain_outcome: StrictBool
    confusing_step: Annotated[str, Field(max_length=1000)] = ""
    reason_to_return: Annotated[str, Field(max_length=1000)] = ""
    consent_to_save: Literal[True]

    @field_validator("consent_to_save", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("Saving feedback requires explicit boolean consent")
        return value


class SavedFeedback(ContractModel):
    id: UUID = Field(default_factory=uuid4)
    feedback: GuidanceFeedback
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class ExperimentationContracts(ContractModel):
    gallery: GalleryScenario
    suite: SavedSuite
    suite_save: SuiteSave
    suite_update: SuiteUpdate
    suite_launch: SuiteLaunch
    suite_report: SuiteReport
    proposal: DiscoveryProposal
    plan: DiscoveryPlan
    approval: DiscoveryApproval
    discovery_report: DiscoveryReport
    highlights: ReplayHighlights
    feedback: GuidanceFeedback
    saved_feedback: SavedFeedback
