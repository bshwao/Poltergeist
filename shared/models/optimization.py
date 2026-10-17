"""Bounded optimization requests and a durable journal of actual executions."""

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4
from datetime import UTC, datetime

from pydantic import AwareDatetime, Field, StrictBool, StrictFloat, StrictInt, model_validator

from .base import ContractModel, NonEmptyString, NonNegativeInt, Seed
from .evaluation import EvaluationSettings
from .runtime import SimulationOptions
from .scenario import Scenario, ScenarioTemplate
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


OptimizationRequest = Annotated[SearchRequest | ReproductionRequest | MinimizeRequest, Field(discriminator="kind")]


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


class OptimizationRecord(ContractModel):
    id: UUID = Field(default_factory=uuid4)
    request: OptimizationRequest
    execution: ExecutionSpec
    source_failure: FailureReport | None = None
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
        if self.request.kind == "search":
            if self.source_failure is not None:
                raise ValueError("Search cannot replace its recipe with a source failure")
        elif self.source_failure is None or self.source_failure.id != self.request.failure_id:
            raise ValueError("Reproduction/minimization must snapshot their source failure")
        return self


class OptimizationTrial(ContractModel):
    operation_id: UUID
    number: NonNegativeInt
    simulation_id: UUID
    role: Literal["search", "reproduce", "minimize_baseline", "minimize_candidate"]
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
