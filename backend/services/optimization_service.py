"""Durable experiments coordinated through the existing simulation worker."""

from collections import deque
import json
from math import isfinite
from threading import Condition, Event, Lock, Thread, current_thread
from uuid import UUID

from pydantic import TypeAdapter

from backend.job_repository import JobRepository
from backend.optimization_repository import OptimizationRepository
from backend.persistence_errors import ConflictError, PersistenceError
from backend.repositories import FailureRepository, SimulationRepository
from backend.services.simulation_service import QueueFullError, QueueUnavailableError
from optimization.failure_minimizer import FailureMinimizer
from optimization.objective_function import assess_result
from optimization.reproduction import summarize_reproduction
from optimization.search_manager import SearchManager
from shared.models import FailureReport
from shared.models.optimization import (
    ExecutionSpec, OptimizationOutcome, OptimizationRequest, OptimizationStatus,
    OptimizationTrial, OptimizationView,
)

_REQUEST = TypeAdapter(OptimizationRequest)
_OWNERS = set()
_OWNERS_LOCK = Lock()
_FAILURE_POLICY_VERSION = "failure-policy-v1"


class _Stop(Exception):
    def __init__(self, status):
        self.status = status


def _key(model):
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False)


class OptimizationService:
    """One experiment controller; every native trial uses SimulationService.

    Restart pauses unfinished operations as interrupted. Explicit resume replays
    their durable journal and reuses already linked children, never resetting a
    trial budget or silently switching sampler versions.
    """

    def __init__(self, worker, *, max_pending=100, shutdown_timeout_s=15):
        if type(max_pending) is not int or max_pending < 1:
            raise ValueError("max_pending must be a positive integer")
        if isinstance(shutdown_timeout_s, bool) or not isinstance(shutdown_timeout_s, (int, float)) or not isfinite(shutdown_timeout_s) or shutdown_timeout_s < 0:
            raise ValueError("shutdown_timeout_s must be finite and nonnegative")
        self.worker = worker
        self.persistence = worker.persistence
        self._max_pending = max_pending
        self._timeout = shutdown_timeout_s
        self._condition = Condition()
        self._pending = deque()
        self._thread = None
        self._started = self._closed = self._stopping = False
        self._active_id = self._active_child = None
        self._cancel = Event()
        self._error = None

    def start(self):
        with self._condition:
            if self._started:
                self._available()
                return
            if self._closed:
                raise QueueUnavailableError("Create a new optimization service after shutdown")
            self.worker.start()
            with _OWNERS_LOCK:
                if self.worker in _OWNERS:
                    raise ConflictError("An experiment controller already owns this simulation worker")
                _OWNERS.add(self.worker)
            try:
                with self.persistence.database.session() as session:
                    recovered = OptimizationRepository(session).recover()
                    for record in recovered:
                        if record.cancel_requested:
                            OptimizationRepository(session).transition(record.id, "cancelled", expected_revision=record.revision)
                for record in recovered:
                    if record.cancel_requested:
                        self._cancel_children(record.id)
                self._started = True
                self._thread = Thread(target=self._work, name="poltergeist-optimization", daemon=True)
                self._thread.start()
            except BaseException:
                self._started = False
                with _OWNERS_LOCK:
                    _OWNERS.discard(self.worker)
                raise

    def _available(self):
        if (not self._started or self._closed or self._stopping or self._error is not None
                or self._thread is None or not self._thread.is_alive()):
            raise QueueUnavailableError(self._error or "Optimization controller is not accepting work")
        if not self.worker.status()["accepting"]:
            raise QueueUnavailableError(self.worker.status()["error"] or "Simulation worker is unavailable")

    def _prepare(self, request):
        request = _REQUEST.validate_python(request.model_dump(mode="python") if hasattr(request, "model_dump") else request)
        if request.kind == "search":
            SearchManager(request)  # Validate domain/dependency before saving work.
            execution = ExecutionSpec(mode=request.mode, options=request.options, evaluation_settings=request.evaluation_settings)
            source = None
        else:
            with self.persistence.database.session(write=False) as session:
                source = FailureRepository(session).get(request.failure_id)
                run = SimulationRepository(session).get(source.simulation_id)
                job = JobRepository(session).get(source.simulation_id, required=False)
            if job is None:
                raise PersistenceError("Reproduction and minimization require recorded execution settings")
            execution = ExecutionSpec(mode=run.simulation.mode, options=job.options, evaluation_settings=job.evaluation_settings)
        if execution.mode == "test" and (execution.options.camera or execution.options.lidar):
            raise ValueError("Camera and LiDAR capture require CARLA mode")
        return request, execution, source

    def submit(self, request):
        request, execution, source = self._prepare(request)
        with self._condition:
            self._available()
            if len(self._pending) >= self._max_pending:
                raise QueueFullError("Optimization waiting queue is full")
            with self.persistence.database.session() as session:
                record = OptimizationRepository(session).create(request, execution=execution, source_failure=source)
            self._pending.append(record.id)
            self._condition.notify()
            return record

    def get(self, operation_id):
        with self.persistence.database.session(write=False) as session:
            repository = OptimizationRepository(session)
            return OptimizationView(operation=repository.get(operation_id), trials=repository.trials(operation_id))

    def list(self, *, limit=50, offset=0):
        with self.persistence.database.session(write=False) as session:
            return OptimizationRepository(session).list(limit=limit, offset=offset)

    def resume(self, operation_id, *, expected_revision):
        with self._condition:
            self._available()
            if len(self._pending) >= self._max_pending:
                raise QueueFullError("Optimization waiting queue is full")
            with self.persistence.database.session() as session:
                repository = OptimizationRepository(session)
                record = repository.get(operation_id)
                version = SearchManager(record.request).version if record.request.kind == "search" else _FAILURE_POLICY_VERSION
                if record.engine_version is not None and version != record.engine_version:
                    raise ConflictError("Optimization engine version changed; recorded history cannot be resumed")
                record = repository.resume(operation_id, expected_revision=expected_revision)
            self._pending.append(record.id)
            self._condition.notify()
            return record

    def _cancel_children(self, operation_id):
        for trial in self.get(operation_id).trials:
            try:
                if self.worker.get(trial.simulation_id).result is None:
                    self.worker.cancel(trial.simulation_id)
            except QueueUnavailableError:
                # Keep the cancellation durable for restart even if the native
                # worker has stopped accepting control requests after an error.
                with self.persistence.database.session() as session:
                    JobRepository(session).request_cancel(trial.simulation_id)

    def cancel(self, operation_id):
        with self._condition:
            self._available()
            with self.persistence.database.session() as session:
                repository = OptimizationRepository(session)
                record = repository.request_cancel(operation_id)
            if record.status in {"completed", "failed", "cancelled"}:
                return record
            if record.id == self._active_id:
                self._cancel.set()
            else:
                if record.id in self._pending:
                    self._pending.remove(record.id)
                record = self._finish(record.id, "cancelled", summary="Cancelled before further trials")
            self._cancel_children(record.id)
            return record

    def status(self):
        with self._condition:
            alive = self._thread is not None and self._thread.is_alive()
            return {"started": self._started, "accepting": self._started and alive and not self._closed and not self._stopping and self._error is None and self.worker.status()["accepting"],
                    "worker_alive": alive, "error": self._error, "pending": len(self._pending), "active_id": self._active_id}

    def close(self):
        with self._condition:
            self._closed = self._stopping = True
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(self._timeout)
        return thread is None or not thread.is_alive()

    def _check(self):
        if self._stopping:
            raise _Stop("interrupted")
        if self._cancel.is_set():
            raise _Stop("cancelled")

    def _work(self):
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._stopping or bool(self._pending))
                    if self._stopping:
                        return
                    operation_id = self._active_id = self._pending.popleft()
                    self._cancel = Event()
                try:
                    self._execute(operation_id)
                except _Stop as stop:
                    try:
                        self._cancel_children(operation_id)
                        self._finish(operation_id, stop.status, summary="Stopped without a final reproduction decision")
                    except Exception as error:
                        self._fatal(error)
                except Exception as error:
                    try:
                        # A bounded app shutdown can close the native worker
                        # between a stop check and child submission. Preserve
                        # resumability when that submission is interrupted.
                        if self._stopping:
                            self._finish(operation_id, "interrupted", summary="Controller stopped before further trials")
                        else:
                            self._finish(operation_id, "failed", error=f"{type(error).__name__}: {error}")
                    except Exception as persistence_error:
                        self._fatal(persistence_error)
                    if self.worker.status()["error"] is not None:
                        self._fatal(error)
                finally:
                    with self._condition:
                        self._active_id = self._active_child = None
                        self._condition.notify_all()
        finally:
            with _OWNERS_LOCK:
                _OWNERS.discard(self.worker)

    def _fatal(self, error):
        with self._condition:
            self._error = f"{type(error).__name__}: {error}"
            self._stopping = True
            self._condition.notify_all()

    def _finish(self, operation_id, status, *, summary="", outcome=None, error=None):
        with self.persistence.database.session() as session:
            repository = OptimizationRepository(session)
            current = repository.get(operation_id)
            trials = repository.trials(operation_id)
            if current.cancel_requested and status in {"completed", "interrupted", "failed"}:
                status, outcome = "cancelled", None
            if outcome is None:
                outcome = OptimizationOutcome(trials_used=len(trials), successes=sum(item.matches is True for item in trials),
                                              unknowns=sum(item.matches is None for item in trials), summary=summary or error or status)
            record = repository.transition(operation_id, status, expected_revision=current.revision,
                                           outcome=outcome, error=error if status == "failed" else None)
            if status == "completed" and current.request.kind == "reproduce":
                FailureRepository(session).set_reproduced(current.source_failure.id, outcome.reproduced)
            return record

    def _execute(self, operation_id):
        record = self.get(operation_id).operation
        manager = SearchManager(record.request) if record.request.kind == "search" else None
        version = manager.version if manager is not None else _FAILURE_POLICY_VERSION
        if record.engine_version is not None and record.engine_version != version:
            raise ConflictError("Optimization engine version changed; replay is unsafe")
        with self.persistence.database.session() as session:
            repository = OptimizationRepository(session)
            current = repository.get(operation_id)
            record = repository.transition(operation_id, "running", expected_revision=current.revision, engine_version=version)
        self._history = self.get(operation_id).trials
        self._cursor = 0
        if manager is not None:
            best = None
            for index in range(record.request.trials):
                self._check()
                scenario = manager.suggest()
                trial = self._trial(record, scenario, "search", index, 0)
                score = trial.score if record.request.failure_kind is None or trial.matches is True else None
                manager.observe(score)
                if score is not None and (best is None or score > best.score):
                    best = trial
            outcome = self._outcome(best, summary="Search budget completed; unknown objectives were excluded")
        elif record.request.kind == "reproduce":
            results = [self._trial(record, record.source_failure.scenario, "reproduce", 0, repeat)
                       for repeat in range(record.request.repeats)]
            decision = summarize_reproduction([item.matches for item in results], total_repeats=record.request.repeats,
                                              required_successes=record.request.required_successes or record.request.repeats)
            best = next((item for item in results if item.matches is True), None)
            outcome = self._outcome(best, reproduced=decision, summary="Reproduction policy evaluated all requested repetitions")
        else:
            outcome = self._minimize(record)
        if self._cursor != len(self._history):
            raise ConflictError("Recorded trials extend beyond the replayed optimization decision")
        self._check()
        self._finish(operation_id, "completed", outcome=outcome)

    def _outcome(self, best=None, *, reproduced=None, summary=""):
        trials = self._history[:self._cursor]
        return OptimizationOutcome(trials_used=len(trials), successes=sum(item.matches is True for item in trials),
            unknowns=sum(item.matches is None for item in trials), best_simulation_id=None if best is None else best.simulation_id,
            best_failure_id=None if best is None else best.failure_id, best_scenario=None if best is None else best.scenario,
            reproduced=reproduced, summary=summary)

    def _minimize(self, record):
        request = record.request
        baseline = [self._trial(record, record.source_failure.scenario, "minimize_baseline", 0, repeat)
                    for repeat in range(request.repeats)]
        decision = summarize_reproduction([item.matches for item in baseline], total_repeats=request.repeats,
                                          required_successes=request.repeats)
        if decision is not True:
            return self._outcome(reproduced=decision, summary="Baseline failure was not confirmed; no reduction accepted")
        minimizer = FailureMinimizer(record.source_failure.scenario)
        best = baseline[-1]
        candidate_index = 1
        while self._cursor + request.repeats <= request.max_trials:
            self._check()
            candidate = minimizer.propose()
            if candidate is None:
                return self._outcome(best, reproduced=True, summary="No further valid local reduction preserves the failure")
            trials = [self._trial(record, candidate, "minimize_candidate", candidate_index, repeat) for repeat in range(request.repeats)]
            accepted = all(item.matches is True for item in trials)
            minimizer.observe(accepted)
            if accepted:
                best = trials[-1]
            candidate_index += 1
        return self._outcome(best, reproduced=True, summary="Minimization execution budget exhausted; result is a local reduction")

    def _trial(self, record, scenario, role, candidate_index, repeat_index):
        self._check()
        number = self._cursor
        if number < len(self._history):
            trial = self._history[number]
            if (trial.role != role or trial.candidate_index != candidate_index or trial.repeat_index != repeat_index
                    or _key(trial.scenario) != _key(scenario)):
                raise ConflictError("Recorded optimization history does not match deterministic replay")
        else:
            def link(session, stored):
                OptimizationRepository(session).add_trial(OptimizationTrial(
                    operation_id=record.id, number=number, simulation_id=stored.simulation.id, role=role,
                    candidate_index=candidate_index, repeat_index=repeat_index, scenario=scenario,
                ))
            while True:
                self._check()
                try:
                    self.worker.submit(scenario, mode=record.execution.mode, options=record.execution.options,
                                       evaluation_settings=record.execution.evaluation_settings, on_created=link)
                    break
                except QueueFullError:
                    self._cancel.wait(0.05)
            trial = self.get(record.id).trials[number]
            self._history.append(trial)
        self._active_child = trial.simulation_id
        if not trial.assessed:
            while True:
                self._check()
                child = self.worker.get(trial.simulation_id)
                if child.result is not None:
                    break
                worker_status = self.worker.status()
                if not worker_status["worker_alive"] or worker_status["error"] is not None:
                    raise QueueUnavailableError(worker_status["error"] or "Simulation worker stopped before persisting its result")
                self._cancel.wait(0.05)
            kind = record.request.failure_kind if record.request.kind == "search" else record.source_failure.kind
            assessment = assess_result(child.result, kind=kind, minimum_severity=record.request.minimum_severity)
            with self.persistence.database.session() as session:
                failure_id = None
                if assessment.matches is True and assessment.failure_kind is not None:
                    report = FailureRepository(session).create(FailureReport(
                        simulation_id=trial.simulation_id, scenario=child.simulation.scenario,
                        kind=assessment.failure_kind, severity=child.result.metrics.severity,
                        summary=f"Observed {assessment.failure_kind.value} during {record.request.kind} trial {number + 1}",
                    ))
                    failure_id = report.id
                trial = OptimizationRepository(session).assess_trial(record.id, number, score=assessment.score,
                    matches=assessment.matches, reason=assessment.reason, failure_id=failure_id)
            self._history[number] = trial
        self._cursor += 1
        self._active_child = None
        return trial
