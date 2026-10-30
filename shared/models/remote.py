"""Closed, bounded relay messages. Only approved baseline experiments are exposed."""

from hashlib import sha256
import json
from math import ceil, isfinite
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, StrictBool, TypeAdapter, field_validator

from .base import ContractModel
from .coordination import DevicePolicy, PermissionScope
from .evaluation import EvaluationSettings
from .evidence import EvidenceRequest, EvidenceReply
from .hosted import HostedRunSummaryV1
from .scenario import Scenario
from .lab import LabCommand, LabReply

MAX_COMMAND_BYTES = 32768
MAX_RELAY_BYTES = 262144
PositiveRevision = Annotated[int, Field(ge=1, le=2**63 - 1, strict=True)]


class _RemoteModel(ContractModel):
    model_config = ConfigDict(hide_input_in_errors=True)


class RemoteRunOptions(_RemoteModel):
    # Unsafe local options aren't part of this wire contract.
    max_ticks: Annotated[int, Field(ge=1, le=10000, strict=True)] = 10000
    camera: StrictBool = Field(default=False, exclude_if=lambda value: not value)
    lidar: StrictBool = Field(default=False, exclude_if=lambda value: not value)


class ExecuteCommand(_RemoteModel):
    kind: Literal["execute"] = "execute"
    request_id: UUID
    scenario: Scenario
    mode: Literal["test", "carla"]
    agent_id: None = None
    options: RemoteRunOptions = Field(default_factory=RemoteRunOptions)
    evaluation_settings: EvaluationSettings = Field(default_factory=EvaluationSettings)

    @field_validator("scenario")
    @classmethod
    def bounded_scenario(cls, value):
        # Browser JSON has one number type: 2.0 arrives as 2. Normalize through
        # the worker's scenario validator before hashing or admitting a request,
        # so template parameter coercion cannot change its frozen recipe later.
        from scenarios import validate_scenario
        value = validate_scenario(value)
        if len(value.name) > 128:
            raise ValueError("Remote scenario names are limited to 128 characters")
        tick_ratio = value.duration_s / value.fixed_delta_seconds
        if value.duration_s > 120 or not isfinite(tick_ratio) or ceil(tick_ratio - 1e-9) > 10000:
            raise ValueError("Remote scenario exceeds the prototype execution limits")
        task = value.driving_task
        if task is not None and (task.route_owner == "agent" or task.target_speed_mps is None):
            raise ValueError("Remote baseline tasks require a framework route and target speed")
        return value


class CancelCommand(_RemoteModel):
    kind: Literal["cancel"] = "cancel"
    request_id: UUID
    simulation_id: UUID


class StatusCommand(_RemoteModel):
    kind: Literal["status"] = "status"
    request_id: UUID


class ResultCommand(_RemoteModel):
    kind: Literal["result"] = "result"
    request_id: UUID
    simulation_id: UUID


RemoteCommand = Annotated[ExecuteCommand | CancelCommand | StatusCommand | ResultCommand | EvidenceRequest | LabCommand,
                          Field(discriminator="kind")]
COMMAND_ADAPTER = TypeAdapter(RemoteCommand)


class ExecuteAccepted(_RemoteModel):
    kind: Literal["accepted"] = "accepted"
    request_id: UUID
    simulation_id: UUID
    accepted_at: AwareDatetime


class CancelAccepted(_RemoteModel):
    kind: Literal["cancel_requested"] = "cancel_requested"
    request_id: UUID
    simulation_id: UUID
    accepted_at: AwareDatetime
    # Immutable snapshot at durable acceptance; use ResultCommand for final state.
    terminal: bool


class DeviceStatusReply(_RemoteModel):
    kind: Literal["status"] = "status"
    request_id: UUID
    accepting: bool
    active: bool
    waiting_remote_runs: Annotated[int, Field(ge=0, le=10, strict=True)]


class RunResultReply(_RemoteModel):
    kind: Literal["result"] = "result"
    request_id: UUID
    summary: HostedRunSummaryV1


RemoteErrorCode = Literal["permission_denied", "invalid_request", "request_conflict", "queue_full",
    "device_unavailable", "run_not_found", "policy_denied", "result_unavailable"]


class CommandRejected(_RemoteModel):
    kind: Literal["rejected"] = "rejected"
    request_id: UUID
    code: RemoteErrorCode


RemoteReply = Annotated[ExecuteAccepted | CancelAccepted | DeviceStatusReply | RunResultReply | CommandRejected | EvidenceReply | LabReply,
                       Field(discriminator="kind")]
REPLY_ADAPTER = TypeAdapter(RemoteReply)


class _Envelope(_RemoteModel):
    protocol_version: Literal["poltergeist-relay-v1"] = "poltergeist-relay-v1"
    device_id: UUID
    ownership_generation: PositiveRevision
    connection_generation: PositiveRevision


class ServerHello(_Envelope):
    kind: Literal["hello"] = "hello"
    owner_id: UUID
    policy_revision: PositiveRevision
    policy: DevicePolicy
    heartbeat_s: Literal[30] = 30
    dead_timeout_s: Literal[90] = 90
    evidence_supported: bool = Field(default=False, exclude_if=lambda value: not value)
    recordings_supported: bool = Field(default=False, exclude_if=lambda value: not value)
    lab_supported: bool = Field(default=False, exclude_if=lambda value: not value)


class ClientReady(_Envelope):
    kind: Literal["ready"] = "ready"
    remote_execution_enabled: bool = False
    evidence_supported: bool = Field(default=False, exclude_if=lambda value: not value)
    recordings_supported: bool = Field(default=False, exclude_if=lambda value: not value)
    lab_supported: bool = Field(default=False, exclude_if=lambda value: not value)


class RelayCommand(_Envelope):
    kind: Literal["command"] = "command"
    command: RemoteCommand


class RelayReply(_Envelope):
    kind: Literal["reply"] = "reply"
    reply: RemoteReply


class RelayPing(_Envelope):
    kind: Literal["ping"] = "ping"
    nonce: UUID


class RelayPong(_Envelope):
    kind: Literal["pong"] = "pong"
    nonce: UUID


RelayMessage = Annotated[ServerHello | ClientReady | RelayCommand | RelayReply | RelayPing | RelayPong,
                         Field(discriminator="kind")]
RELAY_ADAPTER = TypeAdapter(RelayMessage)


def canonical_json(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def payload_digest(value) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate protocol field")
        result[key] = value
    return result


def parse_message(value: str | bytes, *, relay=True):
    limit = MAX_RELAY_BYTES if relay else MAX_COMMAND_BYTES
    try:
        encoded = value.encode("utf-8") if isinstance(value, str) else value
        if not isinstance(encoded, bytes) or len(encoded) > limit:
            raise ValueError
        # Reject duplicate JSON keys and nonfinite constants before typed parsing.
        def invalid_constant(value):
            raise ValueError
        raw = json.loads(encoded, object_pairs_hook=_unique_pairs, parse_constant=invalid_constant)
        adapter = RELAY_ADAPTER if relay else COMMAND_ADAPTER
        # JSON validation supports the strict UUID/datetime types in hosted DTOs.
        result = adapter.validate_json(canonical_json(raw))
        if len(canonical_json(result).encode("utf-8")) > limit:
            raise ValueError
        return result
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ValueError("Invalid remote protocol message") from None


def command_scope(command: RemoteCommand) -> PermissionScope:
    if isinstance(command, LabCommand):
        if command.action.kind in {"operation_cancel", "run_cancel"}:
            return PermissionScope.CANCEL
        if command.action.kind in {"suite_run", "plan_approve", "run_launch", "operation_resume"}:
            return PermissionScope.EXECUTE_TEST if command.mode == "test" else PermissionScope.EXECUTE_CARLA
        return PermissionScope.RESULT
    if isinstance(command, ExecuteCommand):
        return PermissionScope.EXECUTE_TEST if command.mode == "test" else PermissionScope.EXECUTE_CARLA
    return {"cancel": PermissionScope.CANCEL, "status": PermissionScope.STATUS,
            "result": PermissionScope.RESULT, "evidence": PermissionScope.RESULT}[command.kind]


class RemoteCommandView(_RemoteModel):
    request_id: UUID
    operation: Literal["execute", "cancel", "status", "result"]
    status: Literal["queued", "accepted", "completed", "rejected"]
    created_at: AwareDatetime
    updated_at: AwareDatetime
    simulation_id: UUID | None = None
    reply: RemoteReply | None = None


class RemoteContracts(_RemoteModel):
    command: RemoteCommand
    reply: RemoteReply
    envelope: RelayMessage
    view: RemoteCommandView
