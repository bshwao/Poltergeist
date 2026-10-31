"""Durable experiments coordinated through the existing simulation worker."""

from collections import deque
from contextlib import nullcontext
import json
from math import isfinite
from datetime import UTC, datetime
from threading import Condition, Event, Lock, Thread, current_thread
from time import monotonic
from uuid import UUID

from pydantic import TypeAdapter

from backend.job_repository import JobRepository
from backend.optimization_repository import OptimizationRepository
from backend.persistence_errors import ConflictError, NotFoundError, PersistenceError
from backend.repositories import FailureRepository, SimulationRepository
from backend.services.simulation_service import QueueFullError, QueueUnavailableError
from backend.services.agent_registry_service import AgentRegistryService
from optimization.failure_minimizer import FailureMinimizer
from optimization.objective_function import assess_result
from optimization.reproduction import summarize_reproduction
from optimization.search_manager import SearchManager
from shared.models import FailureReport
from shared.models.optimization import (
    ComparisonAgent, ExecutionSpec, OptimizationOutcome, OptimizationRequest, OptimizationStatus,
    OptimizationTrial, OptimizationView,
)

_REQUEST = TypeAdapter(OptimizationRequest)
_OWNERS = set()
_OWNERS_LOCK = Lock()
_FAILURE_POLICY_VERSION = "failure-policy-v1"


def _engine_version(record):
    if record.request.kind == "discover":
        return "discovery-v1/frozen-candidates-v1"
    if record.request.kind == "suite":
        return "suite-v1"
    if record.request.kind == "compare":
        return "comparison-v1"
    if record.request.kind == "search":
        version = SearchManager(record.request).version
        return version + ("/agent-v1" if record.agent_snapshot is not None or record.request.driving_task is not None else "")
    return _FAILURE_POLICY_VERSION + ("/agent-v1" if record.agent_snapshot is not None else "")


class _Stop(Exception):
    def __init__(self, status, reason=None, *, budget_exhausted=False):
        self.status, self.reason, self.budget_exhausted = status, reason, budget_exhausted


def _key(model):
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False)


class OptimizationService:
    """One experiment controller; every native trial uses SimulationService.

    Restart pauses unfinished operations as interrupted. Explicit resume replays
    their durable journal and reuses already linked children, never resetting a
    trial budget or silently switching sampler versions.
    """

    def __init__(self, worker, *, max_pending=100, shutdown_timeout_s=15, now=None, monotonic_clock=monotonic):
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
        self._now = now or (lambda: datetime.now(UTC))
        self._monotonic = monotonic_clock
        self._deadline = self._deadline_monotonic = None
        self._admissions = {}
        self._active_admission = None

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
        snapshot, agents = None, []
        if request.kind in {"search", "compare", "suite", "discover"}:
            execution = ExecutionSpec(mode=request.mode, options=request.options, evaluation_settings=request.evaluation_settings)
            source = None
            if request.kind in {"search", "discover"}:
                search = request.search if request.kind == "discover" else request
                candidate = request.scenarios[0] if request.kind == "discover" else SearchManager(search).suggest()
                snapshot = self._freeze_agent(search.agent_id, candidate, request.options)
            else:
                scenarios = [case.scenario for case in request.cases] if request.kind == "suite" else [request.scenario]
                agents = [ComparisonAgent(agent_id=agent_id, snapshot=self._freeze_agent(agent_id, scenarios[0], request.options))
                          for agent_id in request.agents]
                for scenario in scenarios[1:]:
                    for agent in agents:
                        if self._freeze_agent(agent.agent_id, scenario, request.options) != agent.snapshot:
                            raise ConflictError("Suite agent identity changed while validating its cases")
        else:
            with self.persistence.database.session(write=False) as session:
                source = FailureRepository(session).get(request.failure_id)
                run = SimulationRepository(session).get(source.simulation_id)
                job = JobRepository(session).get(source.simulation_id, required=False)
            snapshot = run.simulation.agent_snapshot
            if run.simulation.agent != "baseline" and snapshot is None:
                raise ConflictError("Legacy custom-agent reproduction requires a frozen registered snapshot; record a new agent-aware run")
            if job is None:
                raise PersistenceError("Reproduction and minimization require recorded execution settings")
            execution = ExecutionSpec(mode=run.simulation.mode, options=job.options, evaluation_settings=job.evaluation_settings)
            if job.agent_snapshot != snapshot:
                raise ConflictError("Source job and simulation agent identity differ")
            if snapshot is not None and self._freeze_agent(snapshot.registration_id, source.scenario, job.options) != snapshot:
                raise ConflictError("Current registration differs from the source agent snapshot")
        if execution.mode == "test" and (execution.options.camera or execution.options.lidar):
            raise ValueError("Camera and LiDAR capture require CARLA mode")
        return request, execution, source, snapshot, agents

    def _freeze_agent(self, agent_id, scenario, options):
        if agent_id is None:
            return None
        snapshot = AgentRegistryService(self.persistence.database).resolve(agent_id)
        self.worker._validate_agent_launch(scenario, snapshot, options)
        return snapshot

    def submit(self, request, *, operation_id=None, on_created=None, approved_discovery=False, expected_agent_snapshot=None, admission=None):
        request = _REQUEST.validate_python(request.model_dump(mode="python") if hasattr(request, "model_dump") else request)
        if request.kind == "discover" and not approved_discovery:
            raise ConflictError("Review and approve a saved discovery plan before executing it")
        if request.kind == "suite" and request.suite_id is not None and on_created is None:
            raise ConflictError("Launch a saved suite through its revision-checked suite endpoint")
        if operation_id is not None:
            with self.persistence.database.session(write=False) as session:
                try:
                    existing = OptimizationRepository(session).get(operation_id)
                except NotFoundError:
                    existing = None
                if existing is not None:
                    if _key(existing.request) != _key(request):
                        raise ConflictError("This experiment request identity already has different settings")
                    return existing
        request, execution, source, snapshot, agents = self._prepare(request)
        if request.kind == "discover" and snapshot != expected_agent_snapshot:
            raise ConflictError("The discovery agent changed after plan review; create a new plan")
        with self._condition:
            if operation_id is not None:
                with self.persistence.database.session(write=False) as session:
                    try:
                        existing = OptimizationRepository(session).get(operation_id)
                    except NotFoundError:
                        existing = None
                    if existing is not None:
                        if _key(existing.request) != _key(request):
                            raise ConflictError("This experiment request identity already has different settings")
                        return existing
            self._available()
            if len(self._pending) >= self._max_pending:
                raise QueueFullError("Optimization waiting queue is full")
            with self.persistence.database.session() as session:
                record = OptimizationRepository(session).create(request, execution=execution, source_failure=source,
                    agent_snapshot=snapshot, comparison_agents=agents, id=operation_id)
                if on_created is not None:
                    on_created(session, record)
            self._pending.append(record.id)
            if admission is not None:
                self._admissions[record.id] = admission
            self._condition.notify()
            return record

    def get(self, operation_id):
        with self.persistence.database.session(write=False) as session:
            repository = OptimizationRepository(session)
            return OptimizationView(operation=repository.get(operation_id), trials=repository.trials(operation_id))

    def list(self, *, limit=50, offset=0):
        with self.persistence.database.session(write=False) as session:
            return OptimizationRepository(session).list(limit=limit, offset=offset)

    def resume(self, operation_id, *, expected_revision, admission=None, on_created=None):
        with self._condition:
            self._available()
            if len(self._pending) >= self._max_pending:
                raise QueueFullError("Optimization waiting queue is full")
            with self.persistence.database.session() as session:
                repository = OptimizationRepository(session)
                record = repository.get(operation_id)
                version = _engine_version(record)
                if record.engine_version is not None and version != record.engine_version:
                    raise ConflictError("Optimization engine version changed; recorded history cannot be resumed")
                record = repository.resume(operation_id, expected_revision=expected_revision)
                if on_created is not None:
                    on_created(session, record)
            self._pending.append(record.id)
            if admission is not None:
                self._admissions[record.id] = admission
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

    def cancel(self, operation_id, *, on_created=None):
        with self._condition:
            self._available()
            with self.persistence.database.session() as session:
                repository = OptimizationRepository(session)
                record = repository.request_cancel(operation_id)
                if on_created is not None:
                    on_created(session, record)
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
        if self._deadline is not None and (self._now() >= self._deadline or self._monotonic() >= self._deadline_monotonic):
            raise _Stop("completed", "Approved wall-time budget exhausted; cancellation requested for active work", budget_exhausted=True)

    def _work(self):
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._stopping or bool(self._pending))
                    if self._stopping:
                        return
                    operation_id = self._active_id = self._pending.popleft()
                    self._cancel = Event()
                    self._deadline = self._deadline_monotonic = None
                    self._active_admission = self._admissions.get(operation_id)
                try:
                    self._execute(operation_id)
                except _Stop as stop:
                    try:
                        self._cancel_children(operation_id)
                        self._finish(operation_id, stop.status, summary=stop.reason or "Stopped without a final reproduction decision",
                                     budget_exhausted=stop.budget_exhausted)
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
                        self._admissions.pop(operation_id, None)
                        self._active_admission = None
                        self._condition.notify_all()
        finally:
            with _OWNERS_LOCK:
                _OWNERS.discard(self.worker)

    def _fatal(self, error):
        with self._condition:
            self._error = f"{type(error).__name__}: {error}"
            self._stopping = True
            self._condition.notify_all()

    def _finish(self, operation_id, status, *, summary="", outcome=None, error=None, budget_exhausted=False):
        with self.persistence.database.session() as session:
            repository = OptimizationRepository(session)
            current = repository.get(operation_id)
            trials = repository.trials(operation_id)
            if current.cancel_requested and status in {"completed", "interrupted", "failed"}:
                status, outcome = "cancelled", None
            if outcome is None and current.request.kind == "discover":
                outcome = self._discovery_outcome(current, trials, summary or error or status, budget_exhausted=budget_exhausted)
            elif outcome is None:
                outcome = OptimizationOutcome(trials_used=len(trials), successes=sum(item.matches is True for item in trials),
                                              unknowns=sum(item.matches is None for item in trials), summary=summary or error or status)
            record = repository.transition(operation_id, status, expected_revision=current.revision,
                                           outcome=outcome, error=error if status == "failed" else None)
            if status == "completed" and current.request.kind == "reproduce":
                FailureRepository(session).set_reproduced(current.source_failure.id, outcome.reproduced)
            if current.request.kind == "discover":
                from optimization.discovery import candidates
                for candidate in candidates(current, trials):
                    if candidate.failure_id is not None and candidate.status in {"confirmed", "not_reproduced"}:
                        FailureRepository(session).set_reproduced(candidate.failure_id, candidate.status == "confirmed")
            return record

    def _execute(self, operation_id):
        record = self.get(operation_id).operation
        manager = SearchManager(record.request) if record.request.kind == "search" else None
        version = _engine_version(record)
        if record.engine_version is not None and record.engine_version != version:
            raise ConflictError("Optimization engine version changed; replay is unsafe")
        with self.persistence.database.session() as session:
            repository = OptimizationRepository(session)
            current = repository.get(operation_id)
            record = repository.transition(operation_id, "running", expected_revision=current.revision, engine_version=version)
        self._history = self.get(operation_id).trials
        self._cursor = 0
        if record.request.kind == "discover":
            self._deadline = record.request.deadline_at
            self._deadline_monotonic = self._monotonic() + min(record.request.budget.max_wall_time_s, max(0, (self._deadline - self._now()).total_seconds()))
            outcome = self._discover(record)
        elif record.request.kind == "suite":
            for case_index, case in enumerate(record.request.cases):
                for seed_index, seed in enumerate(record.request.seeds):
                    scenario = case.scenario.model_copy(update={"seed": seed})
                    for repeat in range(record.request.repeats):
                        for agent_index in range(len(record.comparison_agents)):
                            self._trial(record, scenario, "suite", case_index * len(record.request.seeds) + seed_index, repeat, agent_index)
            outcome = self._outcome(summary="Saved suite completed; compare matched agents separately for each scenario")
        elif manager is not None:
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
        elif record.request.kind == "compare":
            for seed_index, seed in enumerate(record.request.seeds):
                scenario = record.request.scenario.model_copy(update={"seed": seed})
                for repeat in range(record.request.repeats):
                    for agent_index in range(len(record.comparison_agents)):
                        self._trial(record, scenario, "compare", seed_index, repeat, agent_index)
            outcome = self._outcome(summary="Matched scenario/seed repetitions completed; inspect per-agent coverage and outcomes")
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

    def _discover(self, record):
        budget = record.request.budget
        checked = 0
        reason = "Approved search completed"
        exhausted = False
        for index in range(budget.max_search_trials):
            self._check()
            scenario = record.request.scenarios[index]
            if self._cursor >= len(self._history) and (
                len(self._history) + 1 + budget.confirmation_repeats > budget.max_executions
                or sum(t.scenario.duration_s for t in self._history) + scenario.duration_s * (1 + budget.confirmation_repeats) > budget.max_simulated_time_s + 1e-9
            ):
                reason, exhausted = "Remaining approved budget cannot cover another candidate and its confirmations", True
                break
            trial = self._trial(record, scenario, "discovery_search", index, 0)
            if trial.matches is True:
                for repeat in range(budget.confirmation_repeats):
                    self._trial(record, scenario, "discovery_confirm", index, repeat)
                checked += 1
                if checked >= budget.max_candidates:
                    reason = "Approved candidate confirmation limit reached"
                    break
        return self._discovery_outcome(record, self._history, reason, budget_exhausted=exhausted)

    @staticmethod
    def _discovery_outcome(record, trials, reason, *, budget_exhausted=False):
        from optimization.discovery import candidates
        findings = candidates(record, trials)
        confirmed = [item for item in findings if item.status == "confirmed"]
        best = confirmed[0] if confirmed else None
        summary = f"{len(confirmed)} failure(s) confirmed by independent repetitions" if confirmed else "No failure was confirmed within this approved plan"
        return OptimizationOutcome(trials_used=len(trials), successes=sum(t.matches is True for t in trials),
            unknowns=sum(t.matches is None for t in trials), confirmed_failures=len(confirmed),
            best_simulation_id=None if best is None else best.search_simulation_id,
            best_failure_id=None if best is None else best.failure_id, best_scenario=None if best is None else best.scenario,
            reproduced=True if confirmed else None, summary=summary + ". " + reason,
            budget_exhausted=budget_exhausted, stop_reason=reason)

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

    def _trial(self, record, scenario, role, candidate_index, repeat_index, agent_index=0):
        self._check()
        number = self._cursor
        if number < len(self._history):
            trial = self._history[number]
            if (trial.role != role or trial.candidate_index != candidate_index or trial.repeat_index != repeat_index or trial.agent_index != agent_index
                    or _key(trial.scenario) != _key(scenario)):
                raise ConflictError("Recorded optimization history does not match deterministic replay")
        else:
            def link(session, stored):
                self._check()
                if record.request.kind == "discover":
                    self._check_discovery_reservation(record, scenario)
                OptimizationRepository(session).add_trial(OptimizationTrial(
                    operation_id=record.id, number=number, simulation_id=stored.simulation.id, role=role,
                    candidate_index=candidate_index, repeat_index=repeat_index, scenario=scenario, agent_index=agent_index,
                ))
            while True:
                self._check()
                try:
                    # Remote admission owns its local approval lock before the
                    # worker condition/database, matching Stop/Unpair lock order.
                    with self._active_admission(record, scenario) if self._active_admission is not None else nullcontext():
                        self._check()
                        snapshot = record.comparison_agents[agent_index].snapshot if record.request.kind in {"compare", "suite"} else record.agent_snapshot
                        self.worker.submit(scenario, mode=record.execution.mode, options=record.execution.options,
                            evaluation_settings=record.execution.evaluation_settings, on_created=link,
                            agent_id=None if snapshot is None else snapshot.registration_id, expected_agent_snapshot=snapshot)
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
            kind = record.request.goal.failure_kind if record.request.kind == "discover" else record.request.failure_kind if record.request.kind in {"search", "compare", "suite"} else record.source_failure.kind
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

    def _check_discovery_reservation(self, record, scenario):
        budget = record.request.budget
        if len(self._history) >= budget.max_executions or sum(t.scenario.duration_s for t in self._history) + scenario.duration_s > budget.max_simulated_time_s + 1e-9:
            raise _Stop("completed", "Approved execution or simulated-time budget exhausted", budget_exhausted=True)
