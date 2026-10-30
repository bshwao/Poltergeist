"""Locally approved relay actions over the existing transactional FIFO worker.

The supplied lock is also held by local Stop/Unpair actions. Admission holds it,
then the worker condition, then the database, matching the worker's lock order.
No network I/O or telemetry loading happens here.
"""

from math import ceil
from contextlib import nullcontext
from threading import RLock

from backend.job_repository import JobRepository
from backend.persistence_errors import ConflictError, NotFoundError, PersistenceError
from backend.remote_receipts import RemoteBinding, RemoteReceiptRepository
from backend.repositories import SimulationRepository
from backend.services.simulation_service import QueueFullError, QueueUnavailableError
from backend.storage import ArtifactError
from shared.models.coordination import DevicePolicy
from shared.models.remote import (CancelAccepted, CancelCommand, CommandRejected,
    DeviceStatusReply, ExecuteAccepted, ExecuteCommand, ResultCommand,
    RunResultReply, canonical_json, command_scope, parse_message, payload_digest)
from shared.models.runtime import SimulationOptions
from shared.models.evidence import EvidenceRequest
from shared.models.lab import LabCommand

from .hosted_projection import HostedProjectionError, project_hosted_run


class RemoteDenied(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__("The remote operation was refused")


class RemoteService:
    def __init__(self, worker, approved_binding, *, lock=None, optimizer_factory=None):
        self.worker = worker
        self.database = worker.persistence.database
        self.approved_binding = approved_binding
        self.lock = lock or RLock()
        self.lab_supported = optimizer_factory is not None
        self.lab = None
        if optimizer_factory is not None:
            from .remote_lab import RemoteLabService
            self.lab = RemoteLabService(self, optimizer_factory)

    def _approved(self, expected, command):
        try:
            binding, policy = self.approved_binding()
            if not isinstance(binding, RemoteBinding):
                raise ValueError
            policy = DevicePolicy.model_validate(policy.model_dump(mode="python"))
        except Exception:
            raise RemoteDenied("permission_denied") from None
        if binding != expected or command_scope(command) not in policy.scopes:
            raise RemoteDenied("permission_denied")
        if isinstance(command, EvidenceRequest) and not policy.allow_evidence_read:
            raise RemoteDenied("permission_denied")
        return policy

    def _lookup(self, binding, command, digest):
        from backend.experiment_repository import ExperimentRepository
        from backend.remote_receipts import experiment_scope
        with self.database.session(write=False) as session:
            receipt = RemoteReceiptRepository(session).lookup(binding, command.request_id)
            try:
                ExperimentRepository(session, "receipt", owner_scope=experiment_scope(binding)).get(command.request_id)
            except NotFoundError:
                pass
            else:
                raise RemoteDenied("request_conflict")
        if receipt is not None and (receipt.operation != command.kind or receipt.payload_sha256 != digest):
            raise RemoteDenied("request_conflict")
        return receipt

    @staticmethod
    def _ack(receipt):
        """Replay the durable acceptance snapshot, never today's lifecycle."""
        values = dict(request_id=receipt.request_id, simulation_id=receipt.simulation_id,
                      accepted_at=receipt.accepted_at)
        if receipt.operation == "execute":
            return ExecuteAccepted(**values)
        if receipt.cancel_terminal is None:
            # The original revision-five ACK bit was never stored. Keep the
            # receipt/run, but do not invent an acknowledgement from later state.
            raise RemoteDenied("device_unavailable")
        return CancelAccepted(**values, terminal=receipt.cancel_terminal)

    def dispatch(self, binding, command):
        """Return a closed reply; local exception text never crosses this boundary."""
        # Revalidate mutable Pydantic instances and enforce the same byte limit as
        # coordinator admission. A structurally invalid envelope closes the relay
        # before dispatch rather than inventing a request identity here.
        command = parse_message(canonical_json(command), relay=False)
        lab_read = isinstance(command, LabCommand) and self.lab is not None and self.lab.is_read(command)
        with nullcontext() if lab_read else self.lock:
            try:
                if lab_read:
                    with self.lock:
                        policy = self._approved(binding, command)
                else:
                    policy = self._approved(binding, command)
                if isinstance(command, LabCommand):
                    if self.lab is None:
                        raise RemoteDenied("invalid_request")
                    try:
                        reply = self.lab.dispatch(binding, command, policy)
                        if lab_read:
                            with self.lock:
                                self._approved(binding, command)
                        return reply
                    except ValueError as error:
                        if isinstance(error, PersistenceError):
                            raise
                        raise RemoteDenied("invalid_request") from None
                if isinstance(command, ExecuteCommand):
                    return self._execute(binding, command, policy)
                if isinstance(command, CancelCommand):
                    return self._cancel(binding, command)
                if isinstance(command, ResultCommand):
                    return self._result(binding, command)
                if isinstance(command, EvidenceRequest):
                    return self._evidence(binding, command)
                status = self.worker.status()
                with self.database.session(write=False) as session:
                    waiting = RemoteReceiptRepository(session).count_waiting(binding)
                return DeviceStatusReply(request_id=command.request_id, accepting=status["accepting"],
                    active=status["active_id"] is not None, waiting_remote_runs=waiting)
            except RemoteDenied as error:
                code = error.code
            except QueueFullError:
                code = "queue_full"
            except QueueUnavailableError:
                code = "device_unavailable"
            except NotFoundError:
                code = "run_not_found"
            except ConflictError:
                code = "request_conflict"
            except HostedProjectionError:
                code = "result_unavailable"
            except (PersistenceError, ValueError, TypeError, OSError):
                code = "device_unavailable"
            return CommandRejected(request_id=command.request_id, code=code)

    def _evidence(self, binding, command):
        from .evidence import read_evidence
        with self.database.session(write=False) as session:
            receipt = RemoteReceiptRepository(session).execution_for_run(binding, command.simulation_id)
            stored = SimulationRepository(session).get(command.simulation_id)
            if receipt is not None and receipt.scenario_sha256 != payload_digest(stored.simulation.scenario):
                raise RemoteDenied("result_unavailable")
        if receipt is None:
            if self.lab is None:
                raise RemoteDenied("run_not_found")
            stored = self.lab.owned_run(binding, command.simulation_id)
        return read_evidence(self.worker.persistence.artifacts, stored.result, command)

    def _execute(self, binding, command, policy, *, on_created=None):
        digest = payload_digest(command)
        existing = self._lookup(binding, command, digest)
        if existing is not None:
            return self._ack(existing)
        ticks = ceil(command.scenario.duration_s / command.scenario.fixed_delta_seconds - 1e-9)
        if (command.scenario.duration_s > policy.max_duration_s or ticks > policy.max_ticks
                or ticks > command.options.max_ticks or command.options.max_ticks > policy.max_ticks):
            raise RemoteDenied("policy_denied")
        if (command.options.camera or command.options.lidar) and (not policy.allow_raw_capture or command.mode != "carla"):
            raise RemoteDenied("policy_denied")
        scenario_digest = payload_digest(command.scenario)
        accepted = []

        def record(session, stored):
            current_policy = self._approved(binding, command)
            if current_policy != policy or stored.simulation.agent != "baseline" or stored.simulation.agent_snapshot is not None:
                raise RemoteDenied("policy_denied")
            if payload_digest(stored.simulation.scenario) != scenario_digest:
                raise RemoteDenied("policy_denied")
            repository = RemoteReceiptRepository(session)
            # Zero waiting means an idle worker slot, including no local waiting
            # work. It doesn't permit one additional waiter behind a running job.
            if policy.max_waiting_runs == 0:
                status = self.worker.status()  # already holds this condition
                if status["active_id"] is not None or status["pending"]:
                    raise QueueFullError("Remote idle slot is occupied")
            elif repository.count_waiting(binding) >= policy.max_waiting_runs:
                raise QueueFullError("Remote waiting queue is full")
            accepted.append(repository.create(binding, command.request_id, "execute", digest,
                stored.simulation.id, scenario_sha256=scenario_digest))
            if on_created is not None:
                on_created(session, stored)

        try:
            self.worker.submit(command.scenario, mode=command.mode,
                options=SimulationOptions(max_ticks=command.options.max_ticks,
                    camera=command.options.camera, lidar=command.options.lidar,
                    camera_width=320 if command.options.camera else 640,
                    camera_height=180 if command.options.camera else 360),
                evaluation_settings=command.evaluation_settings, on_created=record)
        except (ConflictError, RemoteDenied):
            # Queue checks precede the hook. A concurrent acceptance must still
            # resolve its committed original after the losing transaction rolls
            # back, including when the queue has since filled or stopped.
            existing = self._lookup(binding, command, digest)
            if existing is not None:
                return self._ack(existing)
            raise
        return self._ack(accepted[0])

    def _cancel(self, binding, command):
        digest = payload_digest(command)
        existing = self._lookup(binding, command, digest)
        if existing is not None:
            return self._ack(existing)
        with self.database.session(write=False) as session:
            execution = RemoteReceiptRepository(session).execution_for_run(binding, command.simulation_id)
        if execution is None:
            raise RemoteDenied("run_not_found")
        accepted = []

        def record(session, stored):
            self._approved(binding, command)
            accepted.append(RemoteReceiptRepository(session).create(binding, command.request_id,
                "cancel", digest, stored.simulation.id, cancel_terminal=stored.result is not None))

        try:
            self.worker.cancel(command.simulation_id, on_created=record)
        except ConflictError:
            existing = self._lookup(binding, command, digest)
            if existing is not None:
                return self._ack(existing)
            raise
        return self._ack(accepted[0])

    def _result(self, binding, command):
        # Take the worker condition first for one consistent lifecycle/position
        # snapshot. Database writes below only persist the public sequence hash.
        with self.worker.queue_snapshot() as waiting:
            with self.database.session() as session:
                repository = RemoteReceiptRepository(session)
                receipt = repository.execution_for_run(binding, command.simulation_id)
                if receipt is None:
                    raise RemoteDenied("run_not_found")
                stored = SimulationRepository(session).get(command.simulation_id)
                job = JobRepository(session).get(command.simulation_id, required=False)
                if receipt.scenario_sha256 != payload_digest(stored.simulation.scenario):
                    raise HostedProjectionError("The stored accepted scenario changed")
                position = waiting.index(command.simulation_id) + 1 if command.simulation_id in waiting else None
                evidence = "missing"
                if stored.result is not None and stored.result.artifacts:
                    try:
                        for reference in stored.result.artifacts:
                            self.worker.persistence.artifacts.validate_reference(reference, simulation_id=command.simulation_id)
                        evidence = "local_only"
                    except (ArtifactError, OSError, ValueError):
                        pass
                summary = project_hosted_run(stored, job, device_id=binding.device_id,
                    ownership_generation=binding.generation, execute_request_id=receipt.request_id,
                    receipt_revision=1, scenario_sha256=receipt.scenario_sha256,
                    queue_position=position, evidence_state=evidence)
                content = summary.model_dump(mode="json", exclude={"receipt_revision"})
                revision = repository.projection_revision(binding, command.simulation_id, payload_digest(content))
                summary = summary.model_copy(update={"receipt_revision": revision})
        return RunResultReply(request_id=command.request_id, summary=summary)
