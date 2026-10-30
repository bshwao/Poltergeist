"""Durable PC-local remote command acceptance in the caller's transaction.

The request key excludes owner intentionally: presenting another owner for an
existing origin/device/generation/request conflicts instead of creating a second
identity. Such lookups raise a generic ConflictError without returning a receipt.
No method commits or opens another session. Receipt revisions describe immutable
acceptance, not the later simulation or hosted projection sequence.
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
import json
import re
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select, union
from sqlalchemy.orm import Session

from shared.origins import service_origin

from .models import (ExperimentDocumentRecord, OptimizationTrialRecord, RemoteCommandReceiptRecord,
    RemoteProjectionRecord, SimulationJobRecord, SimulationRecord)
from .persistence_errors import ConflictError, PersistenceError
from .repositories import _flush


def experiment_scope(binding):
    """Private lab documents follow the same account/device/pairing generation."""
    value = [binding.origin, str(binding.device_id), str(binding.owner_id), binding.generation]
    return sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _uuid(value) -> UUID:
    try:
        if not isinstance(value, (UUID, str)):
            raise ValueError
        return UUID(str(value))
    except ValueError:
        raise PersistenceError("Remote command identity must be a UUID") from None


def _positive(value, name):
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise PersistenceError(f"Remote command {name} must be a positive SQLite integer")


def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise PersistenceError("Remote command digest must be a lowercase SHA256 hex digest")
    return value


@dataclass(frozen=True)
class RemoteBinding:
    origin: str
    device_id: UUID
    owner_id: UUID
    generation: int

    def __post_init__(self):
        try:
            origin = service_origin(self.origin)
        except ValueError:
            raise PersistenceError("Remote binding requires a coordinator HTTPS origin") from None
        object.__setattr__(self, "origin", origin)
        object.__setattr__(self, "device_id", _uuid(self.device_id))
        object.__setattr__(self, "owner_id", _uuid(self.owner_id))
        _positive(self.generation, "generation")


@dataclass(frozen=True)
class RemoteReceipt:
    binding: RemoteBinding
    request_id: UUID
    operation: Literal["execute", "cancel"]
    payload_sha256: str
    simulation_id: UUID
    accepted_at: datetime
    revision: int
    scenario_sha256: str | None = None
    cancel_terminal: bool | None = None

    def __post_init__(self):
        if not isinstance(self.binding, RemoteBinding):
            raise PersistenceError("Remote command requires a validated binding")
        object.__setattr__(self, "request_id", _uuid(self.request_id))
        object.__setattr__(self, "simulation_id", _uuid(self.simulation_id))
        if self.operation not in ("execute", "cancel"):
            raise PersistenceError("Unsupported remote command operation")
        _digest(self.payload_sha256)
        if self.operation == "execute":
            _digest(self.scenario_sha256)
        elif self.scenario_sha256 is not None:
            raise PersistenceError("Cancellation receipts do not contain a scenario digest")
        if self.cancel_terminal is not None and (self.operation != "cancel" or type(self.cancel_terminal) is not bool):
            raise PersistenceError("Cancellation acceptance requires a boolean terminal snapshot")
        if (not isinstance(self.accepted_at, datetime) or self.accepted_at.tzinfo is None
                or self.accepted_at.utcoffset() is None):
            raise PersistenceError("Remote command acceptance time must be timezone aware")
        object.__setattr__(self, "accepted_at", self.accepted_at.astimezone(UTC))
        _positive(self.revision, "revision")


class RemoteReceiptReplay(ConflictError):
    """Roll back a competing new run, then resolve this original acceptance."""

    def __init__(self, receipt: RemoteReceipt):
        super().__init__("Remote command was already accepted for another submission")
        self.receipt = receipt


class RemoteReceiptRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _scope(binding, *, owner=True):
        if not isinstance(binding, RemoteBinding):
            raise PersistenceError("Remote command requires a validated binding")
        row = RemoteCommandReceiptRecord
        scope = [row.origin == binding.origin, row.device_id == str(binding.device_id),
                 row.generation == binding.generation]
        if owner:
            scope.append(row.owner_id == str(binding.owner_id))
        return scope

    @staticmethod
    def _stored(row):
        try:
            binding = RemoteBinding(row.origin, row.device_id, row.owner_id, row.generation)
            receipt = RemoteReceipt(binding, row.request_id, row.operation, row.payload_sha256,
                                    row.simulation_id, datetime.fromisoformat(row.accepted_at), row.revision,
                                    row.scenario_sha256,
                                    None if row.cancel_terminal is None else bool(row.cancel_terminal))
            if row.cancel_terminal is not None and (type(row.cancel_terminal) is not int or row.cancel_terminal not in (0, 1)):
                raise ValueError
            if (binding.origin != row.origin or str(binding.device_id) != row.device_id
                    or str(binding.owner_id) != row.owner_id or str(receipt.request_id) != row.request_id
                    or str(receipt.simulation_id) != row.simulation_id):
                raise ValueError
            return receipt
        except (ValueError, TypeError, AttributeError):
            raise PersistenceError("Stored remote command receipt is invalid") from None

    def _check_owner(self, binding):
        # A generation belongs to one owner even across different request IDs.
        # Database.session's writer reservation serializes admission checks.
        owners = self.session.scalars(select(RemoteCommandReceiptRecord.owner_id).where(
            *self._scope(binding, owner=False),
        ).distinct())
        if any(owner != str(binding.owner_id) for owner in owners):
            raise ConflictError("Remote command binding conflicts with stored ownership")

    def lookup(self, binding: RemoteBinding, request_id: UUID | str) -> RemoteReceipt | None:
        self._check_owner(binding)
        row = self.session.scalar(select(RemoteCommandReceiptRecord).where(
            *self._scope(binding, owner=False), RemoteCommandReceiptRecord.request_id == str(_uuid(request_id)),
        ).execution_options(populate_existing=True))
        if row is None:
            return None
        if row.owner_id != str(binding.owner_id):
            raise ConflictError("Remote command binding conflicts with stored ownership")
        return self._stored(row)

    def execution_for_run(self, binding: RemoteBinding, simulation_id: UUID | str) -> RemoteReceipt | None:
        self._check_owner(binding)
        rows = list(self.session.scalars(select(RemoteCommandReceiptRecord).where(
            *self._scope(binding, owner=False), RemoteCommandReceiptRecord.simulation_id == str(_uuid(simulation_id)),
            RemoteCommandReceiptRecord.operation == "execute",
        ).execution_options(populate_existing=True)))
        if not rows:
            return None
        if any(row.owner_id != str(binding.owner_id) for row in rows):
            raise ConflictError("Remote command binding conflicts with stored ownership")
        if len(rows) != 1:
            raise PersistenceError("Stored remote execution identity is ambiguous")
        return self._stored(rows[0])

    def create(self, binding: RemoteBinding, request_id: UUID | str, operation: Literal["execute", "cancel"],
               payload_sha256: str, simulation_id: UUID | str, *, scenario_sha256: str | None = None,
               cancel_terminal: bool | None = None) -> RemoteReceipt:
        receipt = RemoteReceipt(binding, request_id, operation, payload_sha256, simulation_id,
                                datetime.now(UTC), 1, scenario_sha256, cancel_terminal)
        existing = self.lookup(binding, receipt.request_id)
        if existing is not None:
            if (existing.operation != receipt.operation or existing.payload_sha256 != receipt.payload_sha256
                    or existing.scenario_sha256 != receipt.scenario_sha256):
                raise ConflictError("Remote request identity conflicts with its accepted command")
            if existing.simulation_id != receipt.simulation_id:
                if operation == "execute":
                    # Raising is essential: returning normally would commit the
                    # new simulation/job created before submit's callback.
                    raise RemoteReceiptReplay(existing)
                raise ConflictError("Remote cancellation target conflicts with its accepted command")
            return existing
        execution = self.execution_for_run(binding, receipt.simulation_id)
        if operation == "cancel" and execution is None:
            raise ConflictError("Remote cancellation requires an owned execution receipt")
        if operation == "execute" and execution is not None:
            raise ConflictError("Remote run already has an execution request")
        if operation == "cancel":
            # Read the authoritative snapshot in this same writer transaction;
            # an existing receipt above is never re-derived from later state.
            run = self.session.get(SimulationRecord, str(receipt.simulation_id))
            if run is None:
                raise PersistenceError("Remote cancellation run is unavailable")
            terminal = run.result is not None
            if receipt.cancel_terminal is not None and receipt.cancel_terminal != terminal:
                raise PersistenceError("Cancellation acceptance differs from the current stored result")
            receipt = replace(receipt, cancel_terminal=terminal)
        self.session.add(RemoteCommandReceiptRecord(
            origin=binding.origin, device_id=str(binding.device_id), owner_id=str(binding.owner_id),
            generation=binding.generation, request_id=str(receipt.request_id), operation=receipt.operation,
            payload_sha256=receipt.payload_sha256, scenario_sha256=receipt.scenario_sha256,
            simulation_id=str(receipt.simulation_id), accepted_at=receipt.accepted_at.isoformat(timespec="microseconds"),
            revision=receipt.revision,
            cancel_terminal=None if receipt.cancel_terminal is None else int(receipt.cancel_terminal),
        ))
        _flush(self.session)
        return receipt

    def count_waiting(self, binding: RemoteBinding) -> int:
        """Count owned unclaimed, uncancelled queued jobs, never cancel receipts."""
        self._check_owner(binding)
        row = RemoteCommandReceiptRecord
        owned = union(select(row.simulation_id).where(*self._scope(binding), row.operation == "execute"),
            select(OptimizationTrialRecord.simulation_id).join(ExperimentDocumentRecord,
                ExperimentDocumentRecord.id == OptimizationTrialRecord.operation_id).where(
                    ExperimentDocumentRecord.owner_scope == experiment_scope(binding),
                    ExperimentDocumentRecord.kind.in_(("discovery", "receipt")))).subquery()
        return self.session.scalar(select(func.count()).select_from(owned).join(
            SimulationRecord, SimulationRecord.id == owned.c.simulation_id,
        ).join(SimulationJobRecord, SimulationJobRecord.simulation_id == owned.c.simulation_id).where(
            SimulationRecord.status == "queued",
            SimulationJobRecord.claimed.is_(False), SimulationJobRecord.cancel_requested.is_(False),
        ))

    def projection_revision(self, binding: RemoteBinding, simulation_id: UUID | str, payload_sha256: str) -> int:
        """Persist a safe summary's content sequence in the supplied writer.

        The caller hashes only its explicit public projection, excluding this
        revision. Queue/cancel changes can then advance independently of both
        immutable acceptance and the simulation's lifecycle revision.
        """
        simulation_id = str(_uuid(simulation_id))
        payload_sha256 = _digest(payload_sha256)
        if self.execution_for_run(binding, simulation_id) is None:
            raise ConflictError("Remote projection requires an owned execution receipt")
        row = self.session.scalar(select(RemoteProjectionRecord).where(
            RemoteProjectionRecord.origin == binding.origin,
            RemoteProjectionRecord.device_id == str(binding.device_id),
            RemoteProjectionRecord.generation == binding.generation,
            RemoteProjectionRecord.simulation_id == simulation_id,
        ).execution_options(populate_existing=True))
        if row is None:
            row = RemoteProjectionRecord(origin=binding.origin, device_id=str(binding.device_id),
                generation=binding.generation, simulation_id=simulation_id, owner_id=str(binding.owner_id),
                revision=1, payload_sha256=payload_sha256)
            self.session.add(row)
        else:
            if row.owner_id != str(binding.owner_id):
                raise ConflictError("Remote projection binding conflicts with stored ownership")
            _positive(row.revision, "projection revision")
            _digest(row.payload_sha256)
            if row.payload_sha256 == payload_sha256:
                return row.revision
            if row.revision == 2**63 - 1:
                raise ConflictError("Remote projection revision limit reached")
            row.revision += 1
            row.payload_sha256 = payload_sha256
        _flush(self.session)
        return row.revision


__all__ = ["RemoteBinding", "RemoteReceipt", "RemoteReceiptReplay", "RemoteReceiptRepository"]
