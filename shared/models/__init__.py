"""Public, CARLA-free data contracts. Import models from this module."""

from .agent import (
    AGENT_PROTOCOL_VERSION,
    AgentContracts,
    AgentDiagnostics,
    AgentManifest,
    AgentInputInfo,
    AgentObservation,
    AgentReset,
    AgentRoadRaster,
    AgentSceneContext,
    AgentSceneObject,
    AgentSensorSpec,
    AgentSensorFrame,
    AgentStepResult,
)
from .evaluation import EvaluationCoverage, EvaluationSettings
from .coordination import (
    AccountIdentity, AccountInput, ApprovedAgent, BrowserSession, CoordinationContracts,
    DeviceCredentialStatus, DevicePolicy, DeviceView, EmptyInput, PairingApproveInput,
    PairingClaimInput, PairingCreateInput, PairingCreated, PairingDenyInput, PairingStatus,
    PasswordChangeInput, PermissionScope,
)
from .runtime import SimulationOptions
from .hosted import HostedRunSummaryV1
from .remote import (CancelCommand, CommandRejected, ExecuteCommand, RemoteCommandView,
                     RemoteContracts, ResultCommand, StatusCommand)
from .readiness import ReadinessCheck, ReadinessReport, ReadinessStatus, WorkflowReadiness
from .registry import (
    AgentExecutionOptions, AgentExternalFileFingerprint, AgentFileFingerprint,
    AgentReadiness, AgentRegistration, AgentSnapshot, RegisteredAgent,
)

from .scenario import (
    ActorSpec,
    ActorType,
    BrakingEvent,
    DrivingTask,
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
from .simulation import (
    ArtifactReference, ControlAudit, ControlIntervention, DrivingOutcome, FailureKind, FailureReport,
    Simulation, SimulationResult, SimulationRuntime, SimulationStatus,
)
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
    "HostedRunSummaryV1", "CancelCommand", "CommandRejected", "ExecuteCommand",
    "RemoteCommandView", "RemoteContracts", "ResultCommand", "StatusCommand",
    "AccountIdentity", "AccountInput", "ApprovedAgent", "BrowserSession", "CoordinationContracts",
    "DeviceCredentialStatus", "DevicePolicy", "DeviceView", "EmptyInput", "PairingApproveInput",
    "PairingClaimInput", "PairingCreateInput", "PairingCreated", "PairingDenyInput", "PairingStatus",
    "PasswordChangeInput", "PermissionScope",
    "ReadinessCheck", "ReadinessReport", "ReadinessStatus", "WorkflowReadiness",
    "AgentExecutionOptions", "AgentExternalFileFingerprint", "AgentFileFingerprint",
    "AgentReadiness", "AgentRegistration", "AgentSnapshot", "RegisteredAgent",
    "AGENT_PROTOCOL_VERSION", "AgentContracts", "AgentDiagnostics", "AgentManifest", "AgentInputInfo", "AgentObservation",
    "AgentReset", "AgentSensorSpec", "AgentSensorFrame", "AgentStepResult",
    "AgentRoadRaster", "AgentSceneContext", "AgentSceneObject",
    "ActorSpec", "ActorState", "ActorType", "ArtifactReference", "BoundingBoxExtent", "BrakingEvent",
    "ControlAudit", "ControlIntervention", "DrivingOutcome", "DrivingTask",
    "CollisionEvent", "ControlInput", "EvaluationCoverage", "EvaluationSettings", "FailureKind", "FailureReport", "RelativeSpawn", "Rotation",
    "SafetyMetrics", "Scenario", "ScenarioEvent", "ScenarioTemplate", "Simulation", "SimulationResult",
    "SpeedChangeEvent",
    "SimulationOptions", "SimulationRuntime", "SimulationStatus", "TelemetryFrame", "TrafficRule", "TrafficViolation", "Transform",
    "Vector3", "Weather",
]
