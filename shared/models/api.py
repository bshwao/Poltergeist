"""HTTP request/response contracts; domain model serialization stays unchanged."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BeforeValidator, Field, StrictBool, StrictFloat, StrictInt, model_validator

from .base import ContractModel, NonEmptyString, NonNegativeFloat, NonNegativeInt, PositiveFloat, Seed
from .evaluation import EvaluationSettings
from .runtime import SimulationOptions
from .registry import AgentReadiness, AgentRegistration, AgentSnapshot, RegisteredAgent
from .scenario import Scenario, ScenarioTemplate
from .simulation import Simulation, SimulationResult, SimulationStatus
from .telemetry import TelemetryFrame
from .decision import DecisionPage


def _normalize(value):
    # Load lazily so importing shared contracts never imports the scenario engine.
    from scenarios import validate_scenario
    return validate_scenario(value)


ScenarioInput = Annotated[Scenario, BeforeValidator(_normalize)]
PositiveRevision = Annotated[int, Field(gt=0, strict=True)]
Scalar = StrictBool | StrictInt | StrictFloat | str
Mode = Literal["carla", "test"]


class ScenarioRecord(ContractModel):
    id: UUID
    scenario: Scenario
    revision: PositiveRevision
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ScenarioUpdate(ContractModel):
    scenario: ScenarioInput
    expected_revision: PositiveRevision


class ParameterInfo(ContractModel):
    name: NonEmptyString
    minimum: StrictFloat
    maximum: StrictFloat
    default: StrictFloat
    unit: str
    description: str
    integer: StrictBool = False


class TemplateInfo(ContractModel):
    template: ScenarioTemplate
    scenario: Scenario
    parameters: list[ParameterInfo]


class ScenarioGenerate(ContractModel):
    template: ScenarioTemplate
    seed: Seed = 0
    duration_s: PositiveFloat | None = None
    parameters: dict[NonEmptyString, Scalar] = Field(default_factory=dict)
    ranges: dict[NonEmptyString, tuple[StrictFloat, StrictFloat]] = Field(default_factory=dict)
    sample: StrictBool = False


class SimulationLaunch(ContractModel):
    scenario_id: UUID | None = None
    scenario: ScenarioInput | None = None
    mode: Mode = "carla"
    agent_id: UUID | None = None
    options: SimulationOptions = Field(default_factory=SimulationOptions)
    evaluation_settings: EvaluationSettings = Field(default_factory=EvaluationSettings)

    @model_validator(mode="after")
    def choose_recipe(self) -> Self:
        if (self.scenario_id is None) == (self.scenario is None):
            raise ValueError("Supply exactly one of scenario_id or scenario")
        if self.mode == "test" and (self.options.camera or self.options.lidar):
            raise ValueError("Camera and LiDAR capture require CARLA mode")
        return self


class JobInfo(ContractModel):
    cancel_requested: StrictBool
    queue_position: Annotated[int, Field(ge=1, strict=True)] | None
    options: SimulationOptions
    evaluation_settings: EvaluationSettings
    agent_snapshot: AgentSnapshot | None = None


class SimulationRecord(ContractModel):
    simulation: Simulation
    result: SimulationResult | None
    scenario_id: UUID | None
    revision: PositiveRevision
    created_at: AwareDatetime
    updated_at: AwareDatetime
    job: JobInfo | None = None


class SimulationProgress(ContractModel):
    simulation_id: UUID
    status: SimulationStatus
    progress: Annotated[float, Field(ge=0, le=1, strict=True)]
    revision: PositiveRevision
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    cancel_requested: StrictBool
    queue_position: Annotated[int, Field(ge=1, strict=True)] | None
    error: str | None


class QueueStatus(ContractModel):
    started: StrictBool
    accepting: StrictBool
    worker_alive: StrictBool
    error: str | None
    pending: NonNegativeInt
    active_id: UUID | None


class TelemetryPage(ContractModel):
    frames: list[TelemetryFrame]
    total: NonNegativeInt
    offset: NonNegativeInt
    limit: Annotated[int, Field(ge=1, le=1000, strict=True)]


class ReproductionUpdate(ContractModel):
    reproduced: StrictBool | None


class ModeAnalytics(ContractModel):
    mode: Mode
    total_runs: NonNegativeInt
    status_counts: dict[str, NonNegativeInt]
    result_count: NonNegativeInt
    scored_runs: NonNegativeInt
    unscored_results: NonNegativeInt
    mean_severity: NonNegativeFloat | None
    collision_runs: NonNegativeInt
    collision_observed_runs: NonNegativeInt
    traffic_violation_runs: NonNegativeInt
    traffic_observed_runs: NonNegativeInt
    failure_reports: NonNegativeInt
    failure_kinds: dict[str, NonNegativeInt]


class AnalyticsSummary(ContractModel):
    groups: list[ModeAnalytics]


class ApiContracts(ContractModel):
    agent_registration: AgentRegistration
    registered_agent: RegisteredAgent
    agent_readiness: AgentReadiness
    scenario_record: ScenarioRecord
    scenario_update: ScenarioUpdate
    scenario_generate: ScenarioGenerate
    template: TemplateInfo
    simulation_launch: SimulationLaunch
    simulation_record: SimulationRecord
    simulation_progress: SimulationProgress
    queue_status: QueueStatus
    telemetry_page: TelemetryPage
    decision_page: DecisionPage
    reproduction_update: ReproductionUpdate
    analytics: AnalyticsSummary
