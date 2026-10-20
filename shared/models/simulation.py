"""Simulation lifecycle, results, artifacts, and reproducible failure records."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, StrictBool, model_validator

from .agent import AgentInputInfo
from .registry import AgentSnapshot
from .base import ContractModel, NonEmptyString, NonNegativeFloat, NonNegativeInt, PositiveFloat, Seed
from .scenario import Scenario, Transform
from .telemetry import ControlInput, SafetyMetrics


class SimulationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Simulation(ContractModel):
    id: UUID = Field(default_factory=uuid4)
    scenario: Scenario
    agent: Literal["baseline", "custom"] = "baseline"
    agent_snapshot: AgentSnapshot | None = None
    mode: Literal["carla", "test"] = "carla"
    status: SimulationStatus = SimulationStatus.QUEUED
    progress: Annotated[float, Field(ge=0, le=1, strict=True)] = 0.0
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    error: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        if self.agent_snapshot is not None and self.agent != "custom":
            raise ValueError("A registered agent snapshot requires a custom simulation")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at cannot precede created_at")
        if self.finished_at is not None and self.finished_at < (self.started_at or self.created_at):
            raise ValueError("finished_at cannot precede the run's start/creation")
        if self.status == SimulationStatus.QUEUED:
            if self.started_at is not None or self.finished_at is not None or self.progress != 0:
                raise ValueError("Queued simulations must have no execution timestamps or progress")
        elif self.status == SimulationStatus.RUNNING:
            if self.started_at is None or self.finished_at is not None or self.progress >= 1:
                raise ValueError("Running simulations need started_at, no finished_at, and progress < 1")
        else:
            if self.finished_at is None:
                raise ValueError("Terminal simulations need finished_at")
            if self.status == SimulationStatus.COMPLETED and (self.started_at is None or self.progress != 1):
                raise ValueError("Completed simulations need started_at and progress = 1")
        if self.status == SimulationStatus.FAILED and self.error is None:
            raise ValueError("Failed simulations need an error message")
        if self.status != SimulationStatus.FAILED and self.error is not None:
            raise ValueError("Only failed simulations may have an error message")
        return self


class ArtifactReference(ContractModel):
    kind: Literal["telemetry", "recording", "report", "decisions"]
    # Relative to configured storage; never an arbitrary local file path.
    path: NonEmptyString
    media_type: NonEmptyString

    @model_validator(mode="after")
    def validate_relative_path(self) -> Self:
        normalized = self.path.replace("\\", "/")
        if normalized.startswith("/") or ":" in normalized or ".." in normalized.split("/"):
            raise ValueError("Artifact path must be relative to storage without '..' or a drive/scheme")
        return self


class SimulationRuntime(ContractModel):
    adapter: Literal["carla", "test"]
    server_version: NonEmptyString | None = None
    client_version: NonEmptyString | None = None
    map_name: NonEmptyString
    fixed_delta_seconds: PositiveFloat
    seed: Seed
    resolved_spawns: dict[NonEmptyString, Transform] = Field(default_factory=dict)
    sensor_samples_count: NonNegativeInt = 0


class ControlIntervention(ContractModel):
    """A recorded scenario-script influence on the ego's requested control."""

    frame: NonNegativeInt
    time_s: NonNegativeFloat
    kind: Literal["scripted_ego_speed_target", "scripted_ego_braking"]
    proposed_control: ControlInput
    applied_control: ControlInput
    target_speed_mps: NonNegativeFloat | None = None


class ControlAudit(ContractModel):
    """Control ownership and scenario assistance for both new and legacy runs."""

    mode: Literal["scripted", "authoritative"]
    controller: Literal["baseline", "legacy_custom", "sdk_agent"]
    navigation: Literal["legacy_assisted", "framework_route", "agent_planner"]
    interventions: list[ControlIntervention] = Field(default_factory=list, max_length=200000)

    @model_validator(mode="after")
    def validate_intervention_order(self) -> Self:
        if self.mode == "authoritative" and (self.controller != "sdk_agent" or self.interventions):
            raise ValueError("Authoritative control requires an SDK agent and no scripted interventions")
        for previous, current in zip(self.interventions, self.interventions[1:]):
            if current.frame < previous.frame or current.time_s < previous.time_s:
                raise ValueError("Control interventions must be ordered by frame and simulation time")
        return self


class DrivingOutcome(ContractModel):
    """Navigation measurements separate from execution and safety outcomes."""

    status: Literal["reached", "timed_out", "cancelled", "failed", "unavailable"]
    route_owner: Literal["framework", "agent"]
    control_mode: Literal["scripted", "authoritative"]
    destination: Transform
    target_speed_mps: NonNegativeFloat | None = None
    resolved_route: list[Transform] = Field(default_factory=list, max_length=4096)
    initial_speed_mps: NonNegativeFloat | None = None
    final_distance_m: NonNegativeFloat | None = None
    final_height_error_m: NonNegativeFloat | None = None
    reached_at_s: NonNegativeFloat | None = None
    route_progress_m: NonNegativeFloat | None = None
    route_length_m: NonNegativeFloat | None = None
    interventions: list[ControlIntervention] = Field(default_factory=list, max_length=200000)
    termination_reason: Literal["goal_reached", "duration_elapsed", "cancelled", "execution_failed"]

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.control_mode == "authoritative" and self.interventions:
            raise ValueError("Authoritative driving outcomes must not contain scripted interventions")
        if (self.status == "reached") != (self.reached_at_s is not None):
            raise ValueError("Reached driving outcomes require reached_at_s; other statuses must omit it")
        if self.termination_reason == "goal_reached" and self.status != "reached":
            raise ValueError("Goal-reached termination requires a reached driving outcome")
        if self.status == "reached" and self.termination_reason not in {"goal_reached", "duration_elapsed"}:
            raise ValueError("Reached driving outcomes require goal-reached or duration-elapsed termination")
        if self.status == "unavailable" and self.termination_reason != "duration_elapsed":
            raise ValueError("Unavailable driving outcomes require duration-elapsed termination")
        if self.status == "timed_out" and self.termination_reason != "duration_elapsed":
            raise ValueError("Timed-out driving outcomes require duration-elapsed termination")
        if self.status == "cancelled" and self.termination_reason != "cancelled":
            raise ValueError("Cancelled driving outcomes require cancelled termination")
        if self.status == "failed" and self.termination_reason != "execution_failed":
            raise ValueError("Failed driving outcomes require execution-failed termination")
        if self.route_owner == "agent" and self.resolved_route:
            raise ValueError("Agent-owned navigation must not contain a framework resolved route")
        if (self.route_progress_m is None) != (self.route_length_m is None):
            raise ValueError("Route progress and length must be present or unavailable together")
        if self.route_progress_m is not None and self.route_progress_m > self.route_length_m + 1e-6:
            raise ValueError("Route progress must not exceed route length")
        if self.route_owner == "agent" and self.route_progress_m is not None:
            raise ValueError("Agent-owned navigation cannot report framework route progress")
        previous = None
        for intervention in self.interventions:
            if previous is not None and (
                intervention.frame < previous.frame or intervention.time_s < previous.time_s
            ):
                raise ValueError("Control interventions must be ordered by frame and simulation time")
            previous = intervention
        return self


class SimulationResult(ContractModel):
    simulation: Simulation
    metrics: SafetyMetrics
    telemetry_frames: NonNegativeInt
    measured_duration_s: NonNegativeFloat
    artifacts: list[ArtifactReference] = Field(default_factory=list)
    runtime: SimulationRuntime | None = None
    driving_outcome: DrivingOutcome | None = None
    control_audit: ControlAudit | None = None
    agent_inputs: AgentInputInfo | None = None

    @model_validator(mode="after")
    def validate_terminal_run(self) -> Self:
        if self.simulation.status in {SimulationStatus.QUEUED, SimulationStatus.RUNNING}:
            raise ValueError("Results may only reference a terminal simulation")
        if self.runtime is not None and self.runtime.adapter != self.simulation.mode:
            raise ValueError("Runtime adapter must match the simulation mode")
        snapshot = self.simulation.agent_snapshot
        if snapshot is not None and self.control_audit is not None:
            if self.control_audit.controller != "sdk_agent" or self.control_audit.mode != snapshot.agent_control_mode:
                raise ValueError("Control audit must match the registered SDK agent and control mode")
        if self.agent_inputs is not None:
            if (self.simulation.agent_snapshot is not None
                    and self.agent_inputs.observation_mode != self.simulation.agent_snapshot.observation_mode):
                raise ValueError("Agent input mode must match the immutable registered agent snapshot")
            if snapshot is not None and self.agent_inputs.sensors != snapshot.manifest.sensors:
                raise ValueError("Agent input calibration must match the registered sensor rig")
            valid_sources = {"carla_ground_truth", "carla_sensors"} if self.simulation.mode == "carla" else {
                "synthetic_state", "deterministic_fixture",
            }
            if self.agent_inputs.source not in valid_sources:
                raise ValueError("Agent input provenance source must match the simulation mode")
            if self.agent_inputs.observation_mode == "sensors":
                task = self.simulation.scenario.driving_task
                if task is None:
                    raise ValueError("Sensor input results require an explicit driving task")
                framework_assistance = "framework_navigation" in self.agent_inputs.assistance
                if framework_assistance != (task.route_owner == "framework"):
                    raise ValueError("Sensor navigation assistance must match driving task route ownership")
                if self.control_audit is not None and self.control_audit.mode != "authoritative":
                    raise ValueError("Sensor input results require authoritative agent control")
        if self.driving_outcome is not None and self.control_audit is not None:
            if self.driving_outcome.interventions != self.control_audit.interventions:
                raise ValueError("Driving outcome interventions must match the control audit")
            if self.driving_outcome.control_mode != self.control_audit.mode:
                raise ValueError("Driving outcome control mode must match the control audit")
        return self


class FailureKind(StrEnum):
    COLLISION = "collision"
    NEAR_MISS = "near_miss"
    HARD_BRAKING = "hard_braking"
    TRAFFIC_VIOLATION = "traffic_violation"


class FailureReport(ContractModel):
    id: UUID = Field(default_factory=uuid4)
    simulation_id: UUID
    scenario: Scenario
    kind: FailureKind
    summary: NonEmptyString
    severity: Annotated[float, Field(ge=0, le=100, strict=True)]
    reproduced: StrictBool | None = None
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
