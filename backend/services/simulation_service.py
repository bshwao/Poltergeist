"""One durable FIFO worker, cooperative cancellation, and finalized persistence.

Imports and construction are inert. ``start`` explicitly initializes persistence
and takes an exclusive database worker lease. Queued jobs survive shutdown;
running jobs request cancellation. A native call cannot be forcibly interrupted:
``close`` returns False on timeout and the worker retains its lease until exit.
"""

from collections import deque
from collections.abc import Mapping
from datetime import UTC, datetime
from math import isfinite
from threading import Condition, Event, Thread, current_thread
from time import monotonic
from uuid import UUID

from evaluation import evaluate_safety
from scenarios import validate_scenario
from shared.models import EvaluationSettings, Scenario, Simulation, SimulationResult, SimulationStatus
from simulator import SimulationRunner
from simulator.protocols import SimulationOptions
from simulator.simulation_runner import RunOutput

from backend.job_repository import JobRepository
from backend.persistence_errors import ConflictError
from backend.repositories import ScenarioRepository, SimulationRepository
from backend.worker_lock import WorkerLease


class QueueUnavailableError(ConflictError):
    """The worker is stopped or needs an application restart after an error."""


class QueueFullError(ConflictError):
    """The bounded waiting queue has reached its configured capacity."""


class SimulationService:
    def __init__(self, persistence, *, runner_factory=None, max_pending=100,
                 progress_interval_s=0.25, shutdown_timeout_s=15):
        if isinstance(max_pending, bool) or not isinstance(max_pending, int) or max_pending < 1:
            raise ValueError("max_pending must be a positive integer")
        for name, value in (("progress_interval_s", progress_interval_s), ("shutdown_timeout_s", shutdown_timeout_s)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite nonnegative number")
        self.persistence = persistence
        self._factory = runner_factory or SimulationRunner
        self._max_pending = max_pending
        self._progress_interval = float(progress_interval_s)
        self._shutdown_timeout = float(shutdown_timeout_s)
        self._condition = Condition()
        self._pending = deque()
        self._thread = None
        self._lease = WorkerLease(persistence.database)
        self._started = False
        self._closed = False
        self._stopping = False
        self._error = None
        self._active_id = None
        self._active_cancel = None
        self._active_runner = None

    def start(self):
        with self._condition:
            if self._started:
                self._require_available()
                return
            if self._closed:
                raise QueueUnavailableError("Create a new service after closing the worker")
            self._lease.acquire()
            try:
                self.persistence.initialize()
                with self.persistence.database.session(write=False) as session:
                    pending = JobRepository(session).pending()
                for stored, job in pending:
                    if stored.simulation.status == SimulationStatus.RUNNING or job.claimed:
                        self._persist_empty(stored, job.evaluation_settings, SimulationStatus.FAILED,
                                            "Simulation worker was interrupted before finalized output was saved")
                    elif job.cancel_requested:
                        self._persist_empty(stored, job.evaluation_settings, SimulationStatus.CANCELLED)
                    else:
                        self._pending.append(stored.simulation.id)
                self._started = True
                self._thread = Thread(target=self._work, name="poltergeist-simulation-worker", daemon=True)
                self._thread.start()
            except BaseException:
                self._started = False
                self._thread = None
                self._pending.clear()
                self._lease.release()
                raise

    def _require_available(self):
        if (not self._started or self._closed or self._stopping or self._error is not None
                or self._thread is None or not self._thread.is_alive()):
            raise QueueUnavailableError(self._error or "Simulation worker is not accepting jobs")

    def submit(self, scenario: Scenario | Mapping | UUID, *, mode="carla", options=None, evaluation_settings=None):
        options = SimulationOptions.model_validate({} if options is None else options.model_dump(mode="python"))
        settings = EvaluationSettings.model_validate({} if evaluation_settings is None else evaluation_settings.model_dump(mode="python"))
        with self._condition:
            self._require_available()
            if len(self._pending) >= self._max_pending:
                raise QueueFullError("Simulation waiting queue is full")
            with self.persistence.database.session() as session:
                scenario_id = scenario if isinstance(scenario, UUID) else None
                recipe = ScenarioRepository(session).get(scenario_id).scenario if scenario_id is not None else validate_scenario(scenario)
                simulation = Simulation(scenario=recipe, mode=mode, agent="baseline")
                stored = SimulationRepository(session).create(simulation, scenario_id=scenario_id)
                JobRepository(session).create(simulation.id, options, settings)
            self._pending.append(simulation.id)
            self._condition.notify()
            return stored

    def get(self, simulation_id):
        return self.persistence.get_simulation(simulation_id)

    def job_info(self, simulation_id):
        with self._condition:
            # Verify the run exists even when it is a manually stored run.
            self.persistence.get_simulation(simulation_id)
            with self.persistence.database.session(write=False) as session:
                job = JobRepository(session).get(simulation_id, required=False)
            if job is None:
                return None
            waiting = list(self._pending)
            return {
                "cancel_requested": job.cancel_requested,
                "queue_position": waiting.index(job.simulation_id) + 1 if job.simulation_id in waiting else None,
                "options": job.options, "evaluation_settings": job.evaluation_settings,
            }

    def cancel(self, simulation_id):
        simulation_id = UUID(str(simulation_id))
        with self._condition:
            self._require_available()
            stored = self.persistence.get_simulation(simulation_id)
            with self.persistence.database.session() as session:
                job = JobRepository(session).get(simulation_id)
                if stored.result is not None:
                    return stored
                JobRepository(session).request_cancel(simulation_id)
            if simulation_id == self._active_id:
                self._active_cancel.set()
                if self._active_runner is not None:
                    self._active_runner.cancel()
                return stored
            try:
                self._persist_empty(stored, job.evaluation_settings, SimulationStatus.CANCELLED)
            except BaseException as error:
                self._fatal(error)
                raise
            if simulation_id in self._pending:
                self._pending.remove(simulation_id)
            return self.persistence.get_simulation(simulation_id)

    def status(self):
        with self._condition:
            alive = self._thread is not None and self._thread.is_alive()
            return {"started": self._started, "accepting": self._started and not self._closed and not self._stopping and self._error is None and alive,
                    "worker_alive": alive, "error": self._error, "pending": len(self._pending), "active_id": self._active_id}

    def close(self):
        with self._condition:
            self._closed = True
            self._stopping = True
            if self._active_cancel is not None:
                self._active_cancel.set()
            if self._active_runner is not None:
                self._active_runner.cancel()
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(self._shutdown_timeout)
        return thread is None or not thread.is_alive()

    def _fatal(self, error):
        with self._condition:
            self._error = f"{type(error).__name__}: {error}"
            self._stopping = True
            self._condition.notify_all()

    def _persist_empty(self, stored, settings, status, error=None):
        simulation = Simulation.model_validate(stored.simulation.model_dump() | {
            "status": status, "finished_at": datetime.now(UTC), "error": error,
        })
        result = SimulationResult(simulation=simulation, metrics=evaluate_safety([], settings=settings),
                                  telemetry_frames=0, measured_duration_s=0.0)
        self.persistence.persist_output(RunOutput(result, ()), expected_revision=stored.revision)

    def _work(self):
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._stopping or bool(self._pending))
                    if self._stopping:
                        return
                    self._active_id = self._pending.popleft()
                    self._active_cancel = Event()
                    simulation_id = self._active_id
                try:
                    self._execute(simulation_id)
                except BaseException as error:
                    self._fatal(error)
                finally:
                    with self._condition:
                        self._active_id = self._active_cancel = self._active_runner = None
                        self._condition.notify_all()
        finally:
            self._lease.release()

    def _execute(self, simulation_id):
        stored = self.persistence.get_simulation(simulation_id)
        with self.persistence.database.session(write=False) as session:
            job = JobRepository(session).get(simulation_id)
        if stored.result is not None:
            return
        if job.cancel_requested:
            self._persist_empty(stored, job.evaluation_settings, SimulationStatus.CANCELLED)
            return
        # Parent started_at belongs to the runner and follows successful native
        # setup. A separate claim covers process loss during factory/open so it
        # cannot be mistaken for a waiting job and silently executed again.
        with self.persistence.database.session() as session:
            JobRepository(session).claim(simulation_id)
        revision = stored.revision
        last_progress = None
        progress_error = None
        capture = None

        def progress(simulation):
            nonlocal revision, last_progress, progress_error
            if simulation.status != SimulationStatus.RUNNING:
                return
            now = monotonic()
            if last_progress is not None and now - last_progress < self._progress_interval:
                return
            try:
                with self.persistence.database.session() as session:
                    saved = SimulationRepository(session).update(simulation, expected_revision=revision)
                revision, last_progress = saved.revision, now
            except BaseException as error:
                progress_error = error
                self._fatal(error)
                raise

        try:
            if job.options.camera or job.options.lidar:
                capture = self.persistence.artifacts.capture(simulation_id)
            try:
                runner = self._factory(options=job.options, evaluation_settings=job.evaluation_settings)
                with self._condition:
                    self._active_runner = runner
                    if self._active_cancel.is_set():
                        runner.cancel()
                output = runner.run(stored.simulation, cancellation=self._active_cancel, on_progress=progress,
                                    on_sensor=None if capture is None else capture.write)
            except BaseException as error:
                if progress_error is not None:
                    raise progress_error
                # Factory and pre-run validation failures still receive an
                # evaluated failed result under the original queued identity.
                current = self.persistence.get_simulation(simulation_id)
                self._persist_empty(current, job.evaluation_settings, SimulationStatus.FAILED,
                                    f"Simulation launch failed: {type(error).__name__}: {error}")
                return
            if progress_error is not None:
                raise progress_error
            self.persistence.persist_output(output, expected_revision=revision, capture=capture)
            capture = None
        finally:
            if capture is not None:
                capture.abort()
