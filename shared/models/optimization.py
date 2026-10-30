"""Bounded optimization requests and a durable journal of actual executions."""

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4
from datetime import UTC, datetime

from pydantic import AwareDatetime, Field, StrictBool, StrictFloat, StrictInt, field_validator, model_validator

from .base import ContractModel, NonEmptyString, NonNegativeInt, Seed
from .evaluation import EvaluationSettings
from .runtime import SimulationOptions
from .scenario import DrivingTask, Scenario, ScenarioTemplate
from .registry import AgentSnapshot
from .simulation import FailureKind, FailureReport

Severity = Annotated[float, Field(ge=0, le=100, strict=True)]
Scalar = StrictBool | StrictInt | StrictFloat | str


class ExecutionSpec(ContractModel):
    mode: Literal["carla", "test"] = "carla"
    options: SimulationOptions = Field(default_factory=SimulationOptions)
    evaluation_settings: EvaluationSettings = Field(default_factory=EvaluationSettings)


class SearchRequest(ExecutionSpec):
    kind: Literal["search"] = "search"
    template: ScenarioTemplate
    strategy: Literal["random", "optuna"] = "random"
    trials: Annotated[int, Field(ge=1, le=1000, strict=True)] = 20
    seed: Seed = 0
    parameters: dict[NonEmptyString, Scalar] = Field(default_factory=dict)
    ranges: dict[NonEmptyString, tuple[StrictFloat, StrictFloat]] = Field(default_factory=dict)
    minimum_severity: Severity = 0.0
    failure_kind: FailureKind | None = None
    agent_id: UUID | None = None
    driving_task: DrivingTask | None = None
    duration_s: Annotated[float, Field(gt=0, le=120, strict=True)] | None = None


class ComparisonRequest(ExecutionSpec):
    kind: Literal["compare"] = "compare"
    scenario: Scenario
    agents: Annotated[list[UUID | None], Field(min_length=1, max_length=8)] = Field(default_factory=lambda: [None])
    seeds: Annotated[list[Seed], Field(min_length=1, max_length=20)] = Field(default_factory=lambda: [0])
    repeats: Annotated[int, Field(ge=1, le=10, strict=True)] = 1
    failure_kind: FailureKind | None = None
    minimum_severity: Severity = 0.0

    @field_validator("scenario", mode="before")
    @classmethod
    def normalize_scenario(cls, value):
        from scenarios import validate_scenario
        return validate_scenario(value)

    @model_validator(mode="after")
    def bounded_unique_plan(self) -> Self:
        if len(set(self.agents)) != len(self.agents) or len(set(self.seeds)) != len(self.seeds):
            raise ValueError("Comparison agents and seeds must be unique")
        if self.trial_budget > 200:
            raise ValueError("A comparison allows at most 200 executions across agents, seeds and repetitions")
        return self

    @property
    def trial_budget(self):
        return len(self.agents) * len(self.seeds) * self.repeats


class ComparisonAgent(ContractModel):
    agent_id: UUID | None
    snapshot: AgentSnapshot | None

    @model_validator(mode="after")
    def identity(self) -> Self:
        if (self.agent_id is None) != (self.snapshot is None) or (
                self.snapshot is not None and self.snapshot.registration_id != self.agent_id):
            raise ValueError("Comparison agent identity must match its frozen registration snapshot")
        return self


class SuiteCase(ContractModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    scenario: Scenario

    @field_validator("scenario", mode="before")
    @classmethod
    def normalize_scenario(cls, value):
        from scenarios import validate_scenario
        return validate_scenario(value)


class SuiteRequest(ExecutionSpec):
    kind: Literal["suite"] = "suite"
    name: Annotated[str, Field(min_length=1, max_length=128)]
    suite_id: UUID | None = None
    suite_revision: Annotated[int, Field(ge=1, strict=True)] | None = None
    cases: Annotated[list[SuiteCase], Field(min_length=1, max_length=20)]
    agents: Annotated[list[UUID | None], Field(min_length=1, max_length=8)] = Field(default_factory=lambda: [None])
    seeds: Annotated[list[Seed], Field(min_length=1, max_length=20)] = Field(default_factory=lambda: [0])
    repeats: Annotated[int, Field(ge=1, le=10, strict=True)] = 1
    failure_kind: FailureKind | None = None
    minimum_severity: Severity = 0.0

    @model_validator(mode="after")
    def bounded_plan(self) -> Self:
        if len(set(self.agents)) != len(self.agents) or len(set(self.seeds)) != len(self.seeds):
            raise ValueError("Suite agents and seeds must be unique")
        if len({case.name.casefold() for case in self.cases}) != len(self.cases):
            raise ValueError("Suite case names must be unique")
        if (self.suite_id is None) != (self.suite_revision is None):
            raise ValueError("A saved suite requires both its identity and revision")
        if self.trial_budget > 200:
            raise ValueError("A suite allows at most 200 executions across cases, agents, seeds and repetitions")
        return self

    @property
    def trial_budget(self):
        return len(self.cases) * len(self.agents) * len(self.seeds) * self.repeats


class DiscoveryBudget(ContractModel):
    max_executions: Annotated[int, Field(ge=3, le=200, strict=True)] = 12
    max_search_trials: Annotated[int, Field(ge=1, le=100, strict=True)] = 6
    confirmation_repeats: Annotated[int, Field(ge=2, le=10, strict=True)] = 2
    required_successes: Annotated[int, Field(ge=2, le=10, strict=True)] = 2
    max_candidates: Annotated[int, Field(ge=1, le=20, strict=True)] = 3
    max_wall_time_s: Annotated[float, Field(ge=1, le=7200, strict=True)] = 300.0
    max_simulated_time_s: Annotated[float, Field(gt=0, le=36000, strict=True)] = 360.0

    @model_validator(mode="after")
    def room_for_confirmation(self) -> Self:
        if self.required_successes > self.confirmation_repeats:
            raise ValueError("Required confirmations cannot exceed confirmation repetitions")
        if self.max_executions < 1 + self.confirmation_repeats:
            raise ValueError("Execution budget must include a candidate and its independent confirmation runs")
        return self


class DiscoveryGoal(ContractModel):
    description: Annotated[str, Field(min_length=1, max_length=500)]
    failure_kind: FailureKind = FailureKind.COLLISION
    template: ScenarioTemplate = ScenarioTemplate.SUDDEN_BRAKING
    agent_id: UUID | None = None
    seed: Seed = 0
    parameters: dict[NonEmptyString, Scalar] = Field(default_factory=dict)
    ranges: dict[NonEmptyString, tuple[StrictFloat, StrictFloat]] = Field(default_factory=dict)
    minimum_severity: Severity = 0.0
    driving_task: DrivingTask | None = None
    duration_s: Annotated[float, Field(gt=0, le=120, strict=True)] = 8.0


class DiscoveryRequest(ExecutionSpec):
    """Internal approved plan, admitted only through discovery approval."""
    kind: Literal["discover"] = "discover"
    plan_id: UUID
    plan_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    goal: DiscoveryGoal
    budget: DiscoveryBudget
    search: SearchRequest
    scenarios: Annotated[list[Scenario], Field(min_length=1, max_length=100)]
    approved_at: AwareDatetime
    deadline_at: AwareDatetime

    @model_validator(mode="after")
    def consistent_approval(self) -> Self:
        expected = SearchRequest(template=self.goal.template, seed=self.goal.seed,
            agent_id=self.goal.agent_id, parameters=self.goal.parameters, ranges=self.goal.ranges,
            failure_kind=self.goal.failure_kind, minimum_severity=self.goal.minimum_severity,
            driving_task=self.goal.driving_task, trials=self.budget.max_search_trials,
            duration_s=self.goal.duration_s,
            mode=self.mode, options=self.options, evaluation_settings=self.evaluation_settings)
        if self.search != expected:
            raise ValueError("Discovery search must match its approved goal, agent, budgets and settings")
        if len(self.scenarios) != self.budget.max_search_trials or any(
                scenario.template != self.goal.template or scenario.seed != (self.goal.seed + index) % (2**31)
                or abs(scenario.duration_s - self.goal.duration_s) > 1e-9
                for index, scenario in enumerate(self.scenarios)):
            raise ValueError("Frozen discovery candidates must match the approved family, seeds and execution windows")
        if abs((self.deadline_at - self.approved_at).total_seconds() - self.budget.max_wall_time_s) > 1e-6:
            raise ValueError("Discovery deadline must match its approved wall-time budget")
        return self

    @property
    def minimum_severity(self):
        return self.goal.minimum_severity


class ReproductionRequest(ContractModel):
    kind: Literal["reproduce"] = "reproduce"
    failure_id: UUID
    repeats: Annotated[int, Field(ge=1, le=20, strict=True)] = 3
    required_successes: Annotated[int, Field(ge=1, le=20, strict=True)] | None = None
    minimum_severity: Severity = 0.0

    @model_validator(mode="after")
    def threshold_fits(self) -> Self:
        if self.required_successes is not None and self.required_successes > self.repeats:
            raise ValueError("required_successes cannot exceed repeats")
        return self


class MinimizeRequest(ContractModel):
    kind: Literal["minimize"] = "minimize"
    failure_id: UUID
    max_trials: Annotated[int, Field(ge=1, le=200, strict=True)] = 20
    repeats: Annotated[int, Field(ge=1, le=10, strict=True)] = 2
    minimum_severity: Severity = 0.0

    @model_validator(mode="after")
    def budget_fits(self) -> Self:
        if self.max_trials < self.repeats:
            raise ValueError("max_trials must allow the initial baseline repetitions")
        return self


OptimizationRequest = Annotated[SearchRequest | ComparisonRequest | SuiteRequest | DiscoveryRequest | ReproductionRequest | MinimizeRequest, Field(discriminator="kind")]
PublicOptimizationRequest = Annotated[SearchRequest | ComparisonRequest | SuiteRequest | ReproductionRequest | MinimizeRequest, Field(discriminator="kind")]


class OptimizationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class OptimizationOutcome(ContractModel):
    trials_used: NonNegativeInt = 0
    successes: NonNegativeInt = 0
    unknowns: NonNegativeInt = 0
    best_simulation_id: UUID | None = None
    best_failure_id: UUID | None = None
    best_scenario: Scenario | None = None
    reproduced: StrictBool | None = None
    summary: str = ""
    confirmed_failures: NonNegativeInt = 0
    budget_exhausted: StrictBool = False
    stop_reason: str | None = None


class OptimizationRecord(ContractModel):
    id: UUID = Field(default_factory=uuid4)
    request: OptimizationRequest
    execution: ExecutionSpec
    source_failure: FailureReport | None = None
    agent_snapshot: AgentSnapshot | None = None
    comparison_agents: list[ComparisonAgent] = Field(default_factory=list, max_length=8)
    status: OptimizationStatus = OptimizationStatus.QUEUED
    revision: Annotated[int, Field(ge=1, strict=True)] = 1
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    cancel_requested: StrictBool = False
    engine_version: NonEmptyString | None = None
    error: NonEmptyString | None = None
    outcome: OptimizationOutcome | None = None

    @model_validator(mode="after")
    def lifecycle(self) -> Self:
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("Optimization start cannot precede creation")
        if self.finished_at is not None and self.finished_at < (self.started_at or self.created_at):
            raise ValueError("Optimization finish cannot precede its start")
        terminal = self.status not in {OptimizationStatus.QUEUED, OptimizationStatus.RUNNING}
        if terminal != (self.finished_at is not None):
            raise ValueError("Only terminal optimization records require a finish time")
        if self.status == OptimizationStatus.RUNNING and self.started_at is None:
            raise ValueError("Running optimization needs a start time")
        if (self.status == OptimizationStatus.FAILED) != (self.error is not None):
            raise ValueError("Only failed optimization records require an error")
        if self.request.kind in {"search", "compare", "suite", "discover"}:
            if self.source_failure is not None:
                raise ValueError("Search cannot replace its recipe with a source failure")
        elif self.source_failure is None or self.source_failure.id != self.request.failure_id:
            raise ValueError("Reproduction/minimization must snapshot their source failure")
        if self.request.kind in {"compare", "suite"}:
            if self.agent_snapshot is not None or [a.agent_id for a in self.comparison_agents] != self.request.agents:
                raise ValueError("Comparison selections must match their frozen agent snapshots")
        elif self.comparison_agents:
            raise ValueError("Only comparison operations contain multiple agents")
        elif self.request.kind == "search" and self.request.agent_id != (None if self.agent_snapshot is None else self.agent_snapshot.registration_id):
            raise ValueError("Search agent must match its frozen registration snapshot")
        elif self.request.kind == "discover" and self.request.goal.agent_id != (None if self.agent_snapshot is None else self.agent_snapshot.registration_id):
            raise ValueError("Discovery agent must match its frozen registration snapshot")
        return self


class OptimizationTrial(ContractModel):
    operation_id: UUID
    number: NonNegativeInt
    simulation_id: UUID
    role: Literal["search", "compare", "suite", "discovery_search", "discovery_confirm", "reproduce", "minimize_baseline", "minimize_candidate"]
    agent_index: NonNegativeInt = 0
    candidate_index: NonNegativeInt = 0
    repeat_index: NonNegativeInt = 0
    scenario: Scenario
    assessed: StrictBool = False
    score: Severity | None = None
    matches: StrictBool | None = None
    reason: str | None = None
    failure_id: UUID | None = None


class OptimizationView(ContractModel):
    operation: OptimizationRecord
    trials: list[OptimizationTrial]


class OptimizationResume(ContractModel):
    expected_revision: Annotated[int, Field(ge=1, strict=True)]


class OptimizationContracts(ContractModel):
    request: OptimizationRequest
    record: OptimizationRecord
    trial: OptimizationTrial
    view: OptimizationView
    resume: OptimizationResume
