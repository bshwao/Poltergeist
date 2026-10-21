"""Single schema entry point used to generate frontend wire types."""

from .models import AgentContracts, FailureReport, SafetyMetrics, Scenario, Simulation, SimulationResult, TelemetryFrame
from .models.base import ContractModel
from .models.api import ApiContracts
from .models.optimization import OptimizationContracts
from .models.readiness import ReadinessReport


class PoltergeistContracts(ContractModel):
    scenario: Scenario
    simulation: Simulation
    telemetry: TelemetryFrame
    metrics: SafetyMetrics
    result: SimulationResult
    failure: FailureReport
    api: ApiContracts
    optimization: OptimizationContracts
    agent: AgentContracts
    readiness: ReadinessReport
