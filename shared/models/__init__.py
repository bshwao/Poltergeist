"""Public, CARLA-free data contracts. Import models from this module."""

from .agent import (
    AGENT_PROTOCOL_VERSION,
    AgentContracts,
    AgentDiagnostics,
    AgentManifest,
    AgentObservation,
    AgentReset,
    AgentRoadRaster,
    AgentSceneContext,
    AgentSceneObject,
    AgentSensorSpec,
    AgentStepResult,
)
from .evaluation import EvaluationCoverage, EvaluationSettings
from .runtime import SimulationOptions

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
from .simulation import ArtifactReference, FailureKind, FailureReport, Simulation, SimulationResult, SimulationRuntime, SimulationStatus
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
    "AGENT_PROTOCOL_VERSION", "AgentContracts", "AgentDiagnostics", "AgentManifest", "AgentObservation",
    "AgentReset", "AgentSensorSpec", "AgentStepResult",
    "AgentRoadRaster", "AgentSceneContext", "AgentSceneObject",
    "ActorSpec", "ActorState", "ActorType", "ArtifactReference", "BoundingBoxExtent", "BrakingEvent",
    "CollisionEvent", "ControlInput", "EvaluationCoverage", "EvaluationSettings", "FailureKind", "FailureReport", "RelativeSpawn", "Rotation",
    "SafetyMetrics", "Scenario", "ScenarioEvent", "ScenarioTemplate", "Simulation", "SimulationResult",
    "SpeedChangeEvent",
    "SimulationOptions", "SimulationRuntime", "SimulationStatus", "TelemetryFrame", "TrafficRule", "TrafficViolation", "Transform",
    "Vector3", "Weather",
]
