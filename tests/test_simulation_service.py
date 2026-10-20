"""Exercise the durable worker with real SQLite, artifacts, and the test runner."""

from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
from threading import Event
from time import monotonic

import pytest

from backend.database import Database
from backend.job_repository import JobRepository
from backend.persistence_errors import ConflictError
from backend.repositories import SimulationRepository
from backend.services.persistence_service import PersistenceService
from backend.services.simulation_service import QueueFullError, QueueUnavailableError, SimulationService
from backend.storage import ArtifactStore
from scenarios import validate_scenario
from shared.models import EvaluationSettings, Simulation
from simulator import SimulationRunner
from simulator.protocols import SensorSample, SimulationOptions
from simulator.test_adapter import TestSimulationAdapter


def scene(name="worker-test", duration=0.2):
    return validate_scenario({"name": name, "duration_s": duration,
                              "actors": [{"type": "ego_vehicle", "speed_mps": 2, "spawn_point_index": 0}]})


def wait_until(predicate, timeout=5):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        value = predicate()
        if value:
            return value
        Event().wait(0.01)
    raise AssertionError("Worker did not reach the expected state")


@pytest.fixture
def persistence(tmp_path):
    result = PersistenceService(Database(f"sqlite:///{(tmp_path / 'database' / 'runs.db').as_posix()}"), ArtifactStore(tmp_path / "artifacts"))
    yield result
    result.database.dispose()


@pytest.fixture
def workers(persistence):
    created = []

    def make(**kwargs):
        worker = SimulationService(persistence, **kwargs)
        worker.start()
        created.append(worker)
        return worker

    yield make
    for worker in created:
        assert worker.close()


def terminal(worker, id):
    return wait_until(lambda: (stored if (stored := worker.get(id)).result is not None else None))


class BlockingOpen(TestSimulationAdapter):
    def __init__(self, entered, release, starts=None):
        super().__init__()
        self.entered, self.release, self.starts = entered, release, starts

    def open(self, scenario):
        if self.starts is not None:
            self.starts.append(scenario.name)
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("test open was never released")
        return super().open(scenario)


def blocking_factory(entered, release, starts=None):
    return lambda **kwargs: SimulationRunner(BlockingOpen(entered, release, starts), **kwargs)


def durable_job(persistence, recipe=None, *, cancel=False, running=False, claimed=False, options=None, settings=None, agent="baseline"):
    with persistence.database.session() as session:
        stored = SimulationRepository(session).create(Simulation(scenario=scene() if recipe is None else recipe, mode="test", agent=agent))
        JobRepository(session).create(stored.simulation.id, options or SimulationOptions(), settings or EvaluationSettings())
        if cancel:
            JobRepository(session).request_cancel(stored.simulation.id)
        if claimed or running:
            JobRepository(session).claim(stored.simulation.id)
        if running:
            current = Simulation.model_validate(stored.simulation.model_dump() | {"status": "running", "started_at": datetime.now(UTC), "progress": 0.2})
            stored = SimulationRepository(session).update(current, expected_revision=stored.revision)
        return stored


def test_constructor_is_inert_and_start_close_are_explicit(persistence, tmp_path):
    worker = SimulationService(persistence)
    assert list(tmp_path.iterdir()) == []
    assert worker.status() == {"started": False, "accepting": False, "worker_alive": False, "error": None, "pending": 0, "active_id": None}
    with pytest.raises(QueueUnavailableError):
        worker.submit(scene(), mode="test")
    worker.start()
    worker.start()
    assert worker.status()["accepting"]
    assert worker.close()
    assert not worker.status()["worker_alive"]
    with pytest.raises(QueueUnavailableError):
        worker.start()


@pytest.mark.parametrize("kwargs", [{"max_pending": 0}, {"max_pending": True}, {"progress_interval_s": float("nan")}, {"shutdown_timeout_s": -1}])
def test_invalid_worker_configuration_is_rejected_without_side_effects(persistence, tmp_path, kwargs):
    with pytest.raises(ValueError):
        SimulationService(persistence, **kwargs)
    assert list(tmp_path.iterdir()) == []


def test_real_test_run_persists_finalized_output_and_launch_settings(workers, persistence):
    worker = workers(progress_interval_s=100)
    options = SimulationOptions(max_ticks=20)
    settings = EvaluationSettings(speeding_tolerance_mps=0.7)
    queued = worker.submit(scene(), mode="test", options=options, evaluation_settings=settings)
    options.max_ticks = 1
    settings.speeding_tolerance_mps = 999
    stored = terminal(worker, queued.simulation.id)
    assert stored.simulation.status == "completed"
    assert stored.revision == 3  # queued, first running snapshot, finalized result
    assert stored.simulation.id == queued.simulation.id
    assert persistence.read_output(queued.simulation.id).result == stored.result
    assert stored.result.metrics.evaluation_settings.speeding_tolerance_mps == 0.7
    assert worker.job_info(queued.simulation.id)["options"].max_ticks == 20
    assert worker.job_info(queued.simulation.id)["queue_position"] is None


def test_single_worker_fifo_and_queue_bound_leave_rejected_submissions_unstored(workers, persistence):
    entered, release, starts = Event(), Event(), []
    worker = workers(runner_factory=blocking_factory(entered, release, starts), max_pending=2)
    try:
        first = worker.submit(scene("first"), mode="test")
        assert entered.wait(2)
        second = worker.submit(scene("second"), mode="test")
        third = worker.submit(scene("third"), mode="test")
        assert worker.job_info(second.simulation.id)["queue_position"] == 1
        assert worker.job_info(third.simulation.id)["queue_position"] == 2
        with pytest.raises(QueueFullError):
            worker.submit(scene("rejected"), mode="test")
        assert starts == ["first"]
        with persistence.database.session() as session:
            assert len(SimulationRepository(session).list()) == 3
    finally:
        release.set()
    terminal(worker, third.simulation.id)
    assert starts == ["first", "second", "third"]
    assert terminal(worker, first.simulation.id).simulation.status == "completed"


def test_queued_cancellation_never_constructs_a_runner_and_is_idempotent(workers):
    entered, release, starts = Event(), Event(), []
    worker = workers(runner_factory=blocking_factory(entered, release, starts))
    try:
        first = worker.submit(scene("active"), mode="test")
        assert entered.wait(2)
        queued = worker.submit(scene("cancel-me"), mode="test")
        cancelled = worker.cancel(queued.simulation.id)
        assert cancelled.simulation.status == "cancelled"
        assert cancelled.simulation.started_at is None
        assert cancelled.result.telemetry_frames == 0
        assert cancelled.result.metrics.severity is None
        assert worker.job_info(queued.simulation.id)["cancel_requested"]
        assert worker.cancel(queued.simulation.id) == cancelled
    finally:
        release.set()
    terminal(worker, first.simulation.id)
    assert starts == ["active"]


def test_cancellation_during_adapter_open_is_persisted_and_cooperative(workers):
    entered, release = Event(), Event()
    worker = workers(runner_factory=blocking_factory(entered, release))
    queued = worker.submit(scene(), mode="test")
    try:
        assert entered.wait(2)
        assert worker.cancel(queued.simulation.id).simulation.status == "queued"
        assert worker.job_info(queued.simulation.id)["cancel_requested"]
    finally:
        release.set()
    stopped = terminal(worker, queued.simulation.id)
    assert stopped.simulation.status == "cancelled"
    assert stopped.result.telemetry_frames == 1
    assert stopped.result.metrics.severity is None


def test_cancellation_while_running_keeps_finalized_partial_trace(workers):
    entered, release = Event(), Event()

    class BlockingStep(TestSimulationAdapter):
        def step(self, *args):
            entered.set()
            assert release.wait(5)
            return super().step(*args)

    worker = workers(runner_factory=lambda **kwargs: SimulationRunner(BlockingStep(), **kwargs))
    queued = worker.submit(scene(), mode="test")
    try:
        assert entered.wait(2)
        assert worker.cancel(queued.simulation.id).simulation.status == "running"
    finally:
        release.set()
    stopped = terminal(worker, queued.simulation.id)
    assert stopped.simulation.status == "cancelled"
    assert stopped.result.telemetry_frames == 2


def test_progress_and_cancellation_writes_serialize_without_halting_worker(workers, persistence, monkeypatch):
    progress_read, release_progress, cancel_attempted, release_step = Event(), Event(), Event(), Event()
    original_update = SimulationRepository.update
    paused = False

    def paused_update(repository, simulation, **kwargs):
        nonlocal paused
        if not paused:
            paused = True
            # A read before the write used to hold a deferred shared lock and
            # deadlock a cancellation writer upgrading its own transaction.
            repository.get(simulation.id)
            progress_read.set()
            assert release_progress.wait(3)
        return original_update(repository, simulation, **kwargs)

    monkeypatch.setattr(SimulationRepository, "update", paused_update)
    class WaitingStep(TestSimulationAdapter):
        def step(self, *args):
            assert release_step.wait(3)
            return super().step(*args)

    worker = workers(progress_interval_s=0, runner_factory=lambda **kwargs: SimulationRunner(WaitingStep(), **kwargs))
    queued = worker.submit(scene(duration=0.5), mode="test")

    def cancel():
        cancel_attempted.set()
        return worker.cancel(queued.simulation.id)

    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            assert progress_read.wait(3)
            cancellation = executor.submit(cancel)
            assert cancel_attempted.wait(3)
            # Cancellation waits for the existing writer's transaction. Release
            # it before awaiting cancellation, avoiding a test-created deadlock.
        finally:
            release_progress.set()
        try:
            cancellation.result(timeout=3)
        finally:
            release_step.set()
    stopped = terminal(worker, queued.simulation.id)
    assert stopped.simulation.status == "cancelled"
    assert worker.job_info(queued.simulation.id)["cancel_requested"]
    assert worker.status()["error"] is None
    assert worker.status()["accepting"]
    assert terminal(worker, worker.submit(scene(), mode="test").simulation.id).simulation.status == "completed"


def test_saved_scenario_submission_snapshots_recipe_and_preserves_reference(workers, persistence):
    worker = workers()
    saved = persistence.save_scenario(scene())
    queued = worker.submit(saved.id, mode="test")
    assert terminal(worker, queued.simulation.id).scenario_id == saved.id
    assert queued.simulation.scenario == saved.scenario


def test_submission_creates_run_and_job_in_one_transaction(workers, persistence, monkeypatch):
    worker = workers()

    def fail(*args, **kwargs):
        raise RuntimeError("job insert failed")

    monkeypatch.setattr(JobRepository, "create", fail)
    with pytest.raises(RuntimeError, match="job insert failed"):
        worker.submit(scene(), mode="test")
    with persistence.database.session() as session:
        assert SimulationRepository(session).list() == []
        assert JobRepository(session).pending() == []
    assert worker.status()["pending"] == 0
    assert worker.status()["accepting"]


def test_manually_queued_records_are_visible_but_never_adopted_or_cancelled(workers, persistence):
    worker = workers()
    manual = persistence.create_simulation(scene(), mode="test")
    assert worker.job_info(manual.simulation.id) is None
    with pytest.raises(ValueError, match="not managed"):
        worker.cancel(manual.simulation.id)
    assert worker.get(manual.simulation.id).simulation.status == "queued"


def test_restart_resumes_fifo_settings_and_recovers_interrupted_running(persistence):
    persistence.initialize()
    interrupted = durable_job(persistence, running=True)
    cancelled = durable_job(persistence, cancel=True)
    queued = durable_job(persistence, scene("resumed"), options=SimulationOptions(max_ticks=10), settings=EvaluationSettings(speeding_tolerance_mps=0.9))
    manual = persistence.create_simulation(scene("manual"), mode="test")
    captured = []

    def factory(**kwargs):
        captured.append(kwargs)
        return SimulationRunner(**kwargs)

    worker = SimulationService(persistence, runner_factory=factory)
    worker.start()
    try:
        recovered = worker.get(interrupted.simulation.id)
        assert recovered.simulation.status == "failed"
        assert recovered.simulation.started_at == interrupted.simulation.started_at
        assert "interrupted" in recovered.simulation.error
        assert recovered.result.metrics.severity is None
        assert recovered.result.telemetry_frames == 0
        assert worker.get(cancelled.simulation.id).simulation.status == "cancelled"
        assert terminal(worker, queued.simulation.id).simulation.status == "completed"
        assert len(captured) == 1
        assert captured[0]["options"].max_ticks == 10
        assert captured[0]["evaluation_settings"].speeding_tolerance_mps == 0.9
        assert worker.get(manual.simulation.id).simulation.status == "queued"
    finally:
        assert worker.close()


def test_restart_preserves_terminal_results_and_artifacts(persistence):
    first = SimulationService(persistence)
    first.start()
    try:
        saved = terminal(first, first.submit(scene(), mode="test").simulation.id)
        output = persistence.read_output(saved.simulation.id)
    finally:
        assert first.close()

    def reject(**kwargs):
        raise AssertionError("terminal job was restarted")

    second = SimulationService(persistence, runner_factory=reject)
    second.start()
    try:
        assert second.get(saved.simulation.id) == saved
        assert persistence.read_output(saved.simulation.id) == output
        assert second.status()["pending"] == 0
    finally:
        assert second.close()


def test_restart_does_not_repeat_native_setup_after_queued_job_was_claimed(persistence):
    persistence.initialize()
    interrupted = durable_job(persistence, claimed=True)
    called = []

    def reject(**kwargs):
        called.append(True)
        raise AssertionError("claimed native setup must not be replayed")

    worker = SimulationService(persistence, runner_factory=reject)
    worker.start()
    try:
        recovered = worker.get(interrupted.simulation.id)
        assert recovered.simulation.status == "failed"
        assert recovered.simulation.started_at is None
        assert "interrupted" in recovered.simulation.error
        assert recovered.result.telemetry_frames == 0
        assert recovered.result.metrics.severity is None
        assert called == []
    finally:
        assert worker.close()


def test_execution_claim_commits_before_factory_or_native_open(workers, persistence):
    claimed = []

    def factory(**kwargs):
        with persistence.database.session() as session:
            pending = JobRepository(session).pending()
            claimed.append(pending[0][1].claimed)
            assert pending[0][0].simulation.status == "queued"
        return SimulationRunner(**kwargs)

    worker = workers(runner_factory=factory)
    assert terminal(worker, worker.submit(scene(), mode="test").simulation.id).simulation.status == "completed"
    assert claimed == [True]


def test_failed_start_releases_lease_and_can_retry(persistence, monkeypatch):
    original = persistence.initialize

    def fail():
        raise OSError("storage unavailable")

    monkeypatch.setattr(persistence, "initialize", fail)
    worker = SimulationService(persistence)
    with pytest.raises(OSError, match="storage unavailable"):
        worker.start()
    assert not worker.status()["started"]
    monkeypatch.setattr(persistence, "initialize", original)
    worker.start()
    try:
        assert terminal(worker, worker.submit(scene(), mode="test").simulation.id).simulation.status == "completed"
    finally:
        assert worker.close()


def test_memory_database_lease_is_unique_per_database_instance(tmp_path):
    database = Database("sqlite:///:memory:")
    persistence = PersistenceService(database, ArtifactStore(tmp_path / "memory-artifacts"))
    first, second = SimulationService(persistence), SimulationService(persistence)
    first.start()
    try:
        with pytest.raises(ConflictError, match="owns"):
            second.start()
    finally:
        assert first.close()
    second.start()
    try:
        assert terminal(second, second.submit(scene(), mode="test").simulation.id).simulation.status == "completed"
    finally:
        assert second.close()
        database.dispose()


@pytest.mark.parametrize("failure", ["factory", "pre_run", "unsupported_custom"])
def test_launch_failures_become_unknown_failed_results_and_worker_continues(persistence, failure):
    persistence.initialize()
    calls = 0

    def factory(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1 and failure == "factory":
            raise RuntimeError("factory failed")
        return SimulationRunner(**kwargs)

    if failure == "unsupported_custom":
        first = durable_job(persistence, agent="custom")
    else:
        first = durable_job(persistence, options=SimulationOptions(max_ticks=1 if failure == "pre_run" else 100))
    worker = SimulationService(persistence, runner_factory=factory)
    worker.start()
    try:
        failed = terminal(worker, first.simulation.id)
        assert failed.simulation.status == "failed"
        assert failed.result.telemetry_frames == 0
        assert failed.result.metrics.collision_count is None
        assert failed.result.metrics.severity is None
        assert failed.simulation.id == first.simulation.id
        assert terminal(worker, worker.submit(scene(), mode="test").simulation.id).simulation.status == "completed"
        assert worker.status()["error"] is None
    finally:
        assert worker.close()


def test_terminal_progress_is_never_saved_before_sensor_finalization(workers, persistence):
    statuses = []

    class Checking(TestSimulationAdapter):
        def finalize(self, frames):
            with persistence.database.session() as session:
                row = SimulationRepository(session).list()[0]
                statuses.append((row.simulation.status, row.result))
            return super().finalize(frames)

    worker = workers(runner_factory=lambda **kwargs: SimulationRunner(Checking(), **kwargs), progress_interval_s=0)
    finished = terminal(worker, worker.submit(scene(), mode="test").simulation.id)
    assert statuses == [("running", None)]
    assert finished.revision == 7  # initial running plus four tick snapshots plus terminal


def test_progress_cas_failure_stops_worker_and_new_submissions(workers, monkeypatch):
    def fail(*args, **kwargs):
        raise ConflictError("external progress update won")

    monkeypatch.setattr(SimulationRepository, "update", fail)
    worker = workers()
    queued = worker.submit(scene(), mode="test")
    wait_until(lambda: worker.status()["error"])
    assert "external progress update won" in worker.status()["error"]
    wait_until(lambda: not worker.status()["worker_alive"])
    assert worker.get(queued.simulation.id).result is None
    with pytest.raises(QueueUnavailableError):
        worker.submit(scene(), mode="test")


def test_final_persistence_failure_never_reports_success_and_halts_worker(workers, persistence, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(persistence, "persist_output", fail)
    worker = workers()
    queued = worker.submit(scene(), mode="test")
    wait_until(lambda: worker.status()["error"])
    wait_until(lambda: not worker.status()["worker_alive"])
    assert worker.get(queued.simulation.id).simulation.status == "running"
    assert worker.get(queued.simulation.id).result is None
    assert "disk full" in worker.status()["error"]


def test_shutdown_cancels_active_retains_pending_and_keeps_lease_while_blocked(persistence):
    entered, release = Event(), Event()
    worker = SimulationService(persistence, runner_factory=blocking_factory(entered, release), shutdown_timeout_s=0.01)
    worker.start()
    active = worker.submit(scene("active"), mode="test")
    try:
        assert entered.wait(2)
        pending = worker.submit(scene("pending"), mode="test")
        assert not worker.close()
        assert worker.get(pending.simulation.id).simulation.status == "queued"
        other = SimulationService(persistence)
        with pytest.raises(ConflictError, match="owns"):
            other.start()
    finally:
        release.set()
        wait_until(lambda: not worker.status()["worker_alive"])
        assert worker.close()
    assert worker.get(active.simulation.id).simulation.status == "cancelled"
    resumed = SimulationService(persistence)
    resumed.start()
    try:
        assert terminal(resumed, pending.simulation.id).simulation.status == "completed"
    finally:
        assert resumed.close()


def test_os_lease_excludes_another_process_and_is_released_after_close(workers, persistence):
    worker = workers()
    script = """
import sys
from backend.database import Database
from backend.worker_lock import WorkerLease
from backend.persistence_errors import ConflictError
lease = WorkerLease(Database('sqlite:///' + sys.argv[1]))
try:
    lease.acquire()
except ConflictError:
    sys.exit(23)
lease.release()
"""
    command = [sys.executable, "-c", script, persistence.database.file_path.as_posix()]
    assert subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10).returncode == 23
    assert worker.close()
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode()


def test_raw_capture_callbacks_publish_only_after_successful_finalization(workers, persistence):
    class Capturing(TestSimulationAdapter):
        def __init__(self):
            super().__init__()
            self.last = -1

        def sensor_samples(self):
            if not self._active or self._frame_index == self.last:
                return ()
            self.last = self._frame_index
            return (SensorSample("camera", self._frame_index, self._frame_index * 0.05, b"abcd", {"encoding": "bgra8", "width": 1, "height": 1}),)

    worker = workers(runner_factory=lambda **kwargs: SimulationRunner(Capturing(), **kwargs))
    stored = terminal(worker, worker.submit(scene(), mode="test", options=SimulationOptions(camera=True)).simulation.id)
    assert stored.simulation.status == "completed"
    assert [artifact.kind for artifact in stored.result.artifacts] == ["recording", "telemetry", "decisions"]
    assert persistence.artifacts.read_capture_manifest(stored.result.artifacts[0])["sample_count"] == 5


def test_failed_capture_write_aborts_recording_but_keeps_failed_trace(workers, persistence, monkeypatch):
    from backend.storage import CaptureWriter

    def fail(*args):
        raise OSError("capture write failed")

    class Capturing(TestSimulationAdapter):
        def sensor_samples(self):
            return (SensorSample("camera", 0, 0, b"abcd", {"encoding": "bgra8", "width": 1, "height": 1}),) if self._active else ()

    monkeypatch.setattr(CaptureWriter, "write", fail)
    worker = workers(runner_factory=lambda **kwargs: SimulationRunner(Capturing(), **kwargs))
    stored = terminal(worker, worker.submit(scene(), mode="test", options=SimulationOptions(camera=True)).simulation.id)
    assert stored.simulation.status == "failed"
    assert "capture write failed" in stored.simulation.error
    assert [artifact.kind for artifact in stored.result.artifacts] == ["telemetry", "decisions"]
    assert persistence.read_decisions(stored.simulation.id).coverage == "empty"
    assert not list(persistence.artifacts.root.rglob("*.bin"))
