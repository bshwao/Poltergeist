"""Transactional journal for bounded optimization work and child runs.

Repositories flush but never commit. In particular, ``add_trial`` belongs in
the simulation worker's ``on_created`` callback so a queued child and its
parent link are either both durable or neither is visible to the worker.
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, update

from shared.models.optimization import ExecutionSpec, OptimizationRecord, OptimizationTrial

from .job_repository import JobRepository
from .models import OptimizationOperationRecord, OptimizationTrialRecord
from .persistence_errors import ConflictError, NotFoundError, PersistenceError
from .repositories import FailureRepository, SimulationRepository, _canonical, _flush, _id, _now, _page, _revision, _timestamp


_UNSET = object()
_ACTIVE = {"queued", "running"}
_TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def _status(value):
    return value.value if hasattr(value, "value") else value


class OptimizationRepository:
    def __init__(self, session):
        self.session = session

    def _row(self, operation_id):
        row = self.session.scalar(select(OptimizationOperationRecord).where(
            OptimizationOperationRecord.id == _id(operation_id),
        ).execution_options(populate_existing=True))
        if row is None:
            raise NotFoundError(f"Optimization operation {operation_id} does not exist")
        return row

    @staticmethod
    def _stored(row):
        value = OptimizationRecord.model_validate_json(row.payload)
        if (str(value.id) != row.id or _status(value.status) != row.status
                or value.revision != row.revision or value.cancel_requested != row.cancel_requested
                or _timestamp(value.created_at) != row.created_at):
            raise PersistenceError("Stored optimization projections are inconsistent")
        return value

    def create(self, request, *, execution, source_failure=None, id=None, agent_snapshot=None, comparison_agents=()):
        try:
            value = OptimizationRecord(id=uuid4() if id is None else id, request=request,
                                       execution=execution, source_failure=source_failure,
                                       agent_snapshot=agent_snapshot, comparison_agents=list(comparison_agents))
            # A fresh validation also protects against mutated Pydantic inputs.
            value = OptimizationRecord.model_validate(value.model_dump(mode="python"))
        except (ValueError, TypeError) as error:
            raise PersistenceError(f"Invalid optimization operation: {error}") from error
        if value.request.kind in {"search", "compare"}:
            requested = ExecutionSpec(mode=value.request.mode, options=value.request.options,
                                      evaluation_settings=value.request.evaluation_settings)
            if _canonical(requested) != _canonical(value.execution):
                raise PersistenceError("Search execution must match its request settings")
        if value.source_failure is not None:
            stored = FailureRepository(self.session).get(value.source_failure.id)
            if _canonical(stored) != _canonical(value.source_failure):
                raise PersistenceError("Source failure must match its persisted snapshot")
            source_run = SimulationRepository(self.session).get(stored.simulation_id)
            source_job = JobRepository(self.session).get(stored.simulation_id, required=False)
            if source_job is None:
                raise PersistenceError("Source failure must reference a worker-managed simulation")
            source_execution = ExecutionSpec(mode=source_run.simulation.mode, options=source_job.options,
                                             evaluation_settings=source_job.evaluation_settings)
            if _canonical(value.execution) != _canonical(source_execution):
                raise PersistenceError("Optimization execution must match its source simulation settings")
            if value.agent_snapshot != source_run.simulation.agent_snapshot:
                raise PersistenceError("Optimization agent must match its source simulation snapshot")
            if source_run.simulation.agent == "custom" and value.agent_snapshot is None:
                raise PersistenceError("Legacy custom-agent sources have no reproducible registered snapshot")
        from backend.agent_repository import AgentRepository
        snapshots = [item.snapshot for item in value.comparison_agents] if value.request.kind == "compare" else [value.agent_snapshot]
        for snapshot in snapshots:
            if snapshot is not None:
                registered = AgentRepository(self.session).get(snapshot.registration_id)
                if not registered.active or registered.snapshot != snapshot:
                    raise PersistenceError("Experiment requires an active matching agent registration")
        row = OptimizationOperationRecord(
            id=_id(value.id), payload=value.model_dump_json(), status=_status(value.status),
            revision=value.revision, cancel_requested=value.cancel_requested,
            created_at=_timestamp(value.created_at), updated_at=_now(),
        )
        self.session.add(row)
        _flush(self.session)
        return self._stored(row)

    def get(self, operation_id):
        return self._stored(self._row(operation_id))

    def list(self, *, limit=50, offset=0):
        _page(limit, offset)
        rows = self.session.scalars(select(OptimizationOperationRecord).order_by(
            OptimizationOperationRecord.created_at.desc(), OptimizationOperationRecord.id.desc(),
        ).limit(limit).offset(offset))
        return [self._stored(row) for row in rows]

    def nonterminal(self):
        rows = self.session.scalars(select(OptimizationOperationRecord).where(
            OptimizationOperationRecord.status.in_(sorted(_ACTIVE)),
        ).order_by(OptimizationOperationRecord.created_at, OptimizationOperationRecord.id))
        return [self._stored(row) for row in rows]

    def _write(self, previous, changes, *, expected_revision):
        _revision(expected_revision)
        if previous.revision != expected_revision:
            raise ConflictError("Optimization revision changed; reload before updating")
        try:
            current = OptimizationRecord.model_validate(previous.model_dump(mode="python") | changes | {
                "revision": expected_revision + 1,
            })
        except (ValueError, TypeError) as error:
            raise PersistenceError(f"Invalid optimization lifecycle change: {error}") from error
        result = self.session.execute(update(OptimizationOperationRecord).where(
            OptimizationOperationRecord.id == _id(previous.id),
            OptimizationOperationRecord.revision == expected_revision,
        ).values(payload=current.model_dump_json(), status=_status(current.status), revision=current.revision,
                 cancel_requested=current.cancel_requested, updated_at=_now()))
        if result.rowcount != 1:
            raise ConflictError("Optimization revision changed; reload before updating")
        _flush(self.session)
        return self.get(previous.id)

    def transition(self, operation_id, status, *, expected_revision, engine_version=_UNSET,
                   outcome=_UNSET, error=_UNSET):
        previous = self.get(operation_id)
        status, old_status = _status(status), _status(previous.status)
        permitted = {"queued": {"running", "failed", "cancelled", "interrupted"},
                     "running": {"completed", "failed", "cancelled", "interrupted"},
                     "interrupted": {"cancelled"}}
        if status not in permitted.get(old_status, set()):
            raise ConflictError(f"Optimization lifecycle cannot change from {old_status} to {status}")
        changes = {"status": status}
        if status == "running" and previous.started_at is None:
            changes["started_at"] = datetime.now(UTC)
        if status in _TERMINAL:
            changes["finished_at"] = datetime.now(UTC)
        for key, value in (("engine_version", engine_version), ("outcome", outcome), ("error", error)):
            if value is not _UNSET:
                changes[key] = value
        return self._write(previous, changes, expected_revision=expected_revision)

    def request_cancel(self, operation_id):
        previous = self.get(operation_id)
        if _status(previous.status) not in _ACTIVE | {"interrupted"} or previous.cancel_requested:
            return previous
        return self._write(previous, {"cancel_requested": True}, expected_revision=previous.revision)

    def resume(self, operation_id, *, expected_revision):
        previous = self.get(operation_id)
        if _status(previous.status) != "interrupted":
            raise ConflictError("Only interrupted optimization operations can resume")
        if previous.cancel_requested:
            raise ConflictError("A cancelled optimization operation cannot resume")
        return self._write(previous, {"status": "queued", "finished_at": None, "error": None},
                           expected_revision=expected_revision)

    def recover(self):
        """Make interrupted ownership explicit; restart never silently relaunches."""
        return [self.transition(value.id, "interrupted", expected_revision=value.revision)
                for value in self.nonterminal()]

    def _trial_row(self, operation_id, number):
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise PersistenceError("Trial number must be a nonnegative integer")
        row = self.session.scalar(select(OptimizationTrialRecord).where(
            OptimizationTrialRecord.operation_id == _id(operation_id), OptimizationTrialRecord.number == number,
        ).execution_options(populate_existing=True))
        if row is None:
            raise NotFoundError(f"Optimization trial {operation_id}/{number} does not exist")
        return row

    @staticmethod
    def _stored_trial(row):
        value = OptimizationTrial.model_validate_json(row.payload)
        if (str(value.operation_id) != row.operation_id or value.number != row.number
                or str(value.simulation_id) != row.simulation_id or value.assessed != row.assessed
                or (None if value.failure_id is None else str(value.failure_id)) != row.failure_id):
            raise PersistenceError("Stored optimization trial projections are inconsistent")
        return value

    def trials(self, operation_id):
        self.get(operation_id)
        rows = self.session.scalars(select(OptimizationTrialRecord).where(
            OptimizationTrialRecord.operation_id == _id(operation_id),
        ).order_by(OptimizationTrialRecord.number))
        return [self._stored_trial(row) for row in rows]

    def add_trial(self, value):
        if not isinstance(value, OptimizationTrial):
            raise PersistenceError("An OptimizationTrial contract is required")
        try:
            value = OptimizationTrial.model_validate(value.model_dump(mode="python"))
        except (ValueError, TypeError) as error:
            raise PersistenceError(f"Invalid optimization trial: {error}") from error
        parent = self.get(value.operation_id)
        if _status(parent.status) not in _ACTIVE or parent.cancel_requested:
            raise ConflictError("Optimization operation is not accepting trials")
        if value.assessed or any(getattr(value, name) is not None for name in ("score", "matches", "reason", "failure_id")):
            raise PersistenceError("New optimization trials must be unassessed")
        existing = self.trials(parent.id)
        if value.number != len(existing):
            raise ConflictError("Optimization trial numbers must be consecutive")
        request = parent.request
        budget = request.trial_budget if request.kind == "compare" else request.trials if request.kind == "search" else request.repeats if request.kind == "reproduce" else request.max_trials
        if len(existing) >= budget:
            raise ConflictError("Optimization trial budget has been exhausted")
        roles = {"search": {"search"}, "compare": {"compare"}, "reproduce": {"reproduce"},
                 "minimize": {"minimize_baseline", "minimize_candidate"}}
        if value.role not in roles[request.kind]:
            raise PersistenceError("Optimization trial role must match its parent request")
        if request.kind == "search" and (value.repeat_index != 0 or value.candidate_index != value.number):
            raise PersistenceError("Search trials must enumerate one execution per candidate")
        if request.kind == "compare":
            agents = len(request.agents)
            if (value.agent_index != value.number % agents
                    or value.repeat_index != (value.number // agents) % request.repeats
                    or value.candidate_index != value.number // (agents * request.repeats)):
                raise PersistenceError("Comparison trials must preserve the seed/repeat/agent plan")
            expected = request.scenario.model_copy(update={"seed": request.seeds[value.candidate_index]})
            if _canonical(value.scenario) != _canonical(expected):
                raise PersistenceError("Comparison trial must preserve its paired scenario and seed")
        elif value.agent_index != 0:
            raise PersistenceError("Single-agent experiments require agent index zero")
        if request.kind == "reproduce":
            if value.repeat_index != value.number or value.candidate_index != 0:
                raise PersistenceError("Reproduction trials must enumerate the requested repetitions")
            if _canonical(value.scenario) != _canonical(parent.source_failure.scenario):
                raise PersistenceError("Reproduction must replay the exact source scenario")
        if request.kind == "minimize":
            if value.repeat_index >= request.repeats:
                raise PersistenceError("Minimization repeat index exceeds the requested repetitions")
            if (value.number < request.repeats) != (value.role == "minimize_baseline"):
                raise PersistenceError("Minimization must first confirm its baseline repetitions")
            if value.role == "minimize_baseline" and _canonical(value.scenario) != _canonical(parent.source_failure.scenario):
                raise PersistenceError("Minimization baseline must replay the exact source scenario")
        run = SimulationRepository(self.session).get(value.simulation_id)
        if _status(run.simulation.status) != "queued":
            raise PersistenceError("Optimization trials must link a newly queued simulation")
        if _canonical(value.scenario) != _canonical(run.simulation.scenario):
            raise PersistenceError("Optimization trial scenario must match its simulation snapshot")
        job = JobRepository(self.session).get(value.simulation_id)
        expected_agent = parent.comparison_agents[value.agent_index].snapshot if request.kind == "compare" else parent.agent_snapshot
        if (run.simulation.agent_snapshot != expected_agent or job.agent_snapshot != expected_agent
                or run.simulation.agent != ("baseline" if expected_agent is None else "custom")):
            raise PersistenceError("Optimization child must preserve its frozen agent identity")
        if (run.simulation.mode != parent.execution.mode or _canonical(job.options) != _canonical(parent.execution.options)
                or _canonical(job.evaluation_settings) != _canonical(parent.execution.evaluation_settings)):
            raise PersistenceError("Optimization trial execution must match its frozen parent settings")
        row = OptimizationTrialRecord(operation_id=_id(value.operation_id), number=value.number,
                                      simulation_id=_id(value.simulation_id), failure_id=None,
                                      payload=value.model_dump_json(), assessed=False)
        self.session.add(row)
        _flush(self.session)
        return self._stored_trial(row)

    def assess_trial(self, operation_id, number, *, score=None, matches=None, reason=None, failure_id=None):
        row = self._trial_row(operation_id, number)
        previous = self._stored_trial(row)
        if previous.assessed:
            raise ConflictError("Optimization trial assessment is immutable")
        run = SimulationRepository(self.session).get(previous.simulation_id)
        if run.result is None:
            raise ConflictError("Optimization trial cannot be assessed before its result is durable")
        if failure_id is not None:
            failure = FailureRepository(self.session).get(failure_id)
            if failure.simulation_id != previous.simulation_id:
                raise PersistenceError("Trial failure evidence must belong to its simulation")
        try:
            current = OptimizationTrial.model_validate(previous.model_dump(mode="python") | {
                "assessed": True, "score": score, "matches": matches, "reason": reason, "failure_id": failure_id,
            })
        except (ValueError, TypeError) as error:
            raise PersistenceError(f"Invalid trial assessment: {error}") from error
        changed = self.session.execute(update(OptimizationTrialRecord).where(
            OptimizationTrialRecord.operation_id == _id(operation_id), OptimizationTrialRecord.number == number,
            OptimizationTrialRecord.assessed.is_(False),
        ).values(payload=current.model_dump_json(), assessed=True,
                 failure_id=None if current.failure_id is None else _id(current.failure_id)))
        if changed.rowcount != 1:
            raise ConflictError("Optimization trial was already assessed")
        _flush(self.session)
        return self._stored_trial(self._trial_row(operation_id, number))


__all__ = ["OptimizationRepository"]
