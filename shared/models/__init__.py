"""Public, CARLA-free data contracts. Import models from this module."""

from .evaluation import EvaluationCoverage, EvaluationSettings

from .scenario import (
    ActorSpec,
    ActorType,
    BrakingEvent,
    RelativeSpawn,
    Rotation,
    Scenario,
    ScenarioEvent,
    ScenarioTemplate,
    SpeedChangeEvent,
    Transform,
    Vector3,
    Weather,
)
from .simulation import ArtifactReference, FailureKind, FailureReport, Simulation, SimulationResult, SimulationStatus
from .telemetry import (
    ActorState,
    BoundingBoxExtent,
    CollisionEvent,
    ControlInput,
    SafetyMetrics,
    TelemetryFrame,
    TrafficRule,
    TrafficViolation,
)

__all__ = [
    "ActorSpec", "ActorState", "ActorType", "ArtifactReference", "BoundingBoxExtent", "BrakingEvent",
    "CollisionEvent", "ControlInput", "EvaluationCoverage", "EvaluationSettings", "FailureKind", "FailureReport", "RelativeSpawn", "Rotation",
    "SafetyMetrics", "Scenario", "ScenarioEvent", "ScenarioTemplate", "Simulation", "SimulationResult",
    "SpeedChangeEvent",
    "SimulationStatus", "TelemetryFrame", "TrafficRule", "TrafficViolation", "Transform",
    "Vector3", "Weather",
]
