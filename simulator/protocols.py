"""Adapter boundaries shared by the runner, CARLA, and deterministic test world."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from shared.models import ActorState, ControlInput, Scenario, TelemetryFrame, Transform
from shared.models.runtime import SimulationOptions


@dataclass(frozen=True)
class ActorCommand:
    target_speed_mps: float
    deceleration_mps2: float | None = None


@dataclass(frozen=True)
class DrivingObservation:
    ego: ActorState
    actors: tuple[ActorState, ...]
    dt_s: float
    target_speed_mps: float | None
    route_heading_deg: float | None = None
    traffic_light_red: bool | None = None


@dataclass(frozen=True)
class SensorSample:
    kind: Literal["camera", "lidar"]
    frame: int
    timestamp: float
    data: bytes
    metadata: Mapping[str, Any] = field(default_factory=dict)


class SimulationAdapter(Protocol):
    mode: Literal["carla", "test"]

    def open(self, scenario: Scenario) -> TelemetryFrame: ...

    def observation(self, frame: TelemetryFrame, target_speed_mps: float) -> DrivingObservation: ...

    def step(self, control: ControlInput, commands: Mapping[str, ActorCommand]) -> TelemetryFrame: ...

    def finalize(self, frames: list[TelemetryFrame]) -> list[TelemetryFrame]: ...

    def close(self) -> None: ...

    def sensor_samples(self) -> tuple[SensorSample, ...]: ...

    def runtime_info(self) -> dict[str, Any]: ...
