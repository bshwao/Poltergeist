"""Simulation lifecycle, results, artifacts, and reproducible failure records."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, StrictBool, model_validator

from .base import ContractModel, NonEmptyString, NonNegativeFloat, NonNegativeInt, PositiveFloat, Seed
from .scenario import Scenario, Transform
from .telemetry import SafetyMetrics


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
    mode: Literal["carla", "test"] = "carla"
    status: SimulationStatus = SimulationStatus.QUEUED
    progress: Annotated[float, Field(ge=0, le=1, strict=True)] = 0.0
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    error: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
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
    kind: Literal["telemetry", "recording", "report"]
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


class SimulationResult(ContractModel):
    simulation: Simulation
    metrics: SafetyMetrics
    telemetry_frames: NonNegativeInt
    measured_duration_s: NonNegativeFloat
    artifacts: list[ArtifactReference] = Field(default_factory=list)
    runtime: SimulationRuntime | None = None

    @model_validator(mode="after")
    def validate_terminal_run(self) -> Self:
        if self.simulation.status in {SimulationStatus.QUEUED, SimulationStatus.RUNNING}:
            raise ValueError("Results may only reference a terminal simulation")
        if self.runtime is not None and self.runtime.adapter != self.simulation.mode:
            raise ValueError("Runtime adapter must match the simulation mode")
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
