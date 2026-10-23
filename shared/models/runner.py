"""Proposed coordinator/local-runner v1 boundary; no remote transport or auth.

Coordinator requests reference locally approved agent IDs. They cannot register
code, select executable/model paths, change local settings or run shell commands.
"""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from .agent import AgentProtocolVersion
from .api import SimulationProgress
from .base import ContractModel, NonEmptyString
from .evaluation import EvaluationSettings
from .readiness import ReadinessStatus
from .runtime import SimulationOptions
from .scenario import Scenario
from .simulation import SimulationResult


class RunnerCapabilities(ContractModel):
    protocol_version: Literal["poltergeist-runner-v1"] = "poltergeist-runner-v1"
    checked_at: AwareDatetime
    implementation_version: NonEmptyString
    platform: Literal["windows-x86_64"] = "windows-x86_64"
    execution_modes: list[Literal["test", "carla"]] = Field(default_factory=lambda: ["test", "carla"])
    synthetic_status: ReadinessStatus
    carla_status: ReadinessStatus
    max_concurrent_runs: Literal[1] = 1
    sdk_protocols: list[AgentProtocolVersion] = Field(default_factory=lambda: ["1.0", "1.1", "1.2", "1.3"])
    local_features: list[Literal["baseline", "approved_sdk_agents", "synthetic_sensor_fixtures", "behavior_reports"]] = Field(
        default_factory=lambda: ["baseline", "approved_sdk_agents", "synthetic_sensor_fixtures", "behavior_reports"])
    remote_execution_enabled: Literal[False] = False


class RunnerExecuteRequest(ContractModel):
    protocol_version: Literal["poltergeist-runner-v1"] = "poltergeist-runner-v1"
    request_id: UUID
    scenario: Scenario
    mode: Literal["test", "carla"]
    agent_id: UUID | None = None
    options: SimulationOptions = Field(default_factory=SimulationOptions)
    evaluation_settings: EvaluationSettings = Field(default_factory=EvaluationSettings)


class RunnerCancelRequest(ContractModel):
    protocol_version: Literal["poltergeist-runner-v1"] = "poltergeist-runner-v1"
    request_id: UUID
    simulation_id: UUID


class RunnerContracts(ContractModel):
    capabilities: RunnerCapabilities
    execute: RunnerExecuteRequest
    cancel: RunnerCancelRequest
    progress: SimulationProgress
    result: SimulationResult
