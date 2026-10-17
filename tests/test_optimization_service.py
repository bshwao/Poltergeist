"""Experiment coordination against real SQLite, artifacts and native-run boundaries."""

from threading import Event, Thread
from time import monotonic

import pytest

from backend.database import Database
from backend.job_repository import JobRepository
from backend.optimization_repository import OptimizationRepository
from backend.persistence_errors import ConflictError
from backend.repositories import FailureRepository, SimulationRepository
from backend.services.optimization_service import OptimizationService
from backend.services.persistence_service import PersistenceService
from backend.services.simulation_service import QueueFullError, QueueUnavailableError, SimulationService
from backend.storage import ArtifactStore
from optimization.failure_minimizer import FailureMinimizer
from optimization.search_manager import SearchManager
from scenarios import get_parameter_specs, validate_scenario
from shared.models import CollisionEvent, EvaluationSettings, FailureReport, Simulation
from shared.models.optimization import ExecutionSpec, MinimizeRequest, OptimizationTrial, ReproductionRequest, SearchRequest
from simulator import SimulationRunner
from simulator.protocols import SimulationOptions
from simulator.test_adapter import TestSimulationAdapter


def wait_until(predicate, timeout=30):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        value = predicate()
        if value:
            return value
        Event().wait(0.01)
    raise AssertionError("Experiment did not reach the expected state")


def scene():
    return validate_scenario({
        "name": "reduction-source", "seed": 817, "duration_s": 0.2,
        "actors": [
            {"id": "ego", "type": "ego_vehicle", "speed_mps": 2, "spawn_point_index": 0},
            {"id": "removable", "type": "vehicle", "speed_mps": 1, "spawn_point_index": 1},
        ],
    })


def search(**kwargs):
    return SearchRequest(template="sudden_braking", mode="test", trials=2,
                         seed=57, failure_kind="collision",
                         parameters={name: spec.default for name, spec in get_parameter_specs("sudden_braking").items()},
                         **kwargs)


class CollisionAdapter(TestSimulationAdapter):
    def open(self, recipe):
        frame = super().open(recipe)
        frame.collisions = [CollisionEvent(other_actor_id="world:barrier", event_id="observed-contact")]
        return frame


class FailedAdapter(TestSimulationAdapter):
    def open(self, recipe):
        raise RuntimeError("injected unavailable native world")


class UnknownCollisionAdapter(TestSimulationAdapter):
    def open(self, recipe):
        frame = super().open(recipe)
        frame.collisions = None
        return frame

    def step(self, *args):
        frame = super().step(*args)
        frame.collisions = None
        return frame


class BlockedCollisionAdapter(CollisionAdapter):
    def __init__(self, entered, release):
        super().__init__()
        self.entered, self.release = entered, release

    def open(self, recipe):
        self.entered.set()
        if not self.release.wait(3):
            raise RuntimeError("Test did not release the native call")
        return super().open(recipe)


class Harness:
    def __init__(self, tmp_path):
        self.persistence = PersistenceService(Database(f"sqlite:///{(tmp_path / 'runs.db').as_posix()}"),
                                              ArtifactStore(tmp_path / "artifacts"))
        self.adapter_factory = CollisionAdapter
        self.controllers, self.releases = [], []
        self.worker = SimulationService(self.persistence, runner_factory=self.runner,
                                        progress_interval_s=100, shutdown_timeout_s=3)

    def runner(self, **kwargs):
        return SimulationRunner(self.adapter_factory(), **kwargs)

    def controller(self, **kwargs):
        service = OptimizationService(self.worker, shutdown_timeout_s=3, **kwargs)
        self.controllers.append(service)
        service.start()
        return service

    def blocker(self):
        entered, release = Event(), Event()
        self.releases.append(release)
        return entered, release

    def source(self, *, reproduced=None):
        self.worker.start()
        queued = self.worker.submit(scene(), mode="test", options=SimulationOptions(max_ticks=20),
                                    evaluation_settings=EvaluationSettings(speeding_tolerance_mps=0.7))
        stored = wait_until(lambda: value if (value := self.worker.get(queued.simulation.id)).result else None)
        assert stored.simulation.status == "completed"
        with self.persistence.database.session() as session:
            return FailureRepository(session).create(FailureReport(
                simulation_id=stored.simulation.id, scenario=stored.simulation.scenario,
                kind="collision", severity=stored.result.metrics.severity,
                summary="Observed source contact", reproduced=reproduced,
            ))

    def failure(self, failure_id):
        with self.persistence.database.session(write=False) as session:
            return FailureRepository(session).get(failure_id)

    def close(self):
        for release in self.releases:
            release.set()
        for controller in self.controllers:
            assert controller.close()
        assert self.worker.close()
        self.persistence.database.dispose()


@pytest.fixture
def harness(tmp_path):
    result = Harness(tmp_path)
    yield result
    result.close()


def terminal(controller, operation_id):
    return wait_until(lambda: view if (view := controller.get(operation_id)).operation.status
                      in {"completed", "failed", "cancelled", "interrupted"} else None)


def test_constructor_is_inert_and_service_lifecycle_is_explicit(harness, tmp_path):
    controller = OptimizationService(harness.worker)
    harness.controllers.append(controller)
    assert list(tmp_path.iterdir()) == []
    assert not controller.status()["accepting"]
    with pytest.raises(QueueUnavailableError):
        controller.submit(search())
    controller.start()
    controller.start()
    assert controller.status()["accepting"]
    duplicate = OptimizationService(harness.worker)
    with pytest.raises(ConflictError, match="already owns"):
        duplicate.start()
    assert controller.close()
    with pytest.raises(QueueUnavailableError):
        controller.start()


@pytest.mark.parametrize("kwargs", [{"max_pending": 0}, {"max_pending": True},
                                    {"shutdown_timeout_s": -1}, {"shutdown_timeout_s": float("nan")}])
def test_invalid_controller_configuration_has_no_side_effects(harness, tmp_path, kwargs):
    with pytest.raises(ValueError):
        OptimizationService(harness.worker, **kwargs)
    assert list(tmp_path.iterdir()) == []


def test_search_persists_budget_journal_failure_evidence_and_frozen_execution(harness):
    controller = harness.controller()
    request = search(options=SimulationOptions(max_ticks=1000),
                     evaluation_settings=EvaluationSettings(speeding_tolerance_mps=0.8))
    operation = controller.submit(request)
    request.seed = 999
    request.options.max_ticks = 1
    request.evaluation_settings.speeding_tolerance_mps = 999
    view = terminal(controller, operation.id)
    assert view.operation.status == "completed"
    assert view.operation.request.seed == 57
    assert view.operation.engine_version
    assert view.operation.outcome.trials_used == view.operation.outcome.successes == 2
    assert [trial.number for trial in view.trials] == [0, 1]
    assert [trial.candidate_index for trial in view.trials] == [0, 1]
    assert len({trial.simulation_id for trial in view.trials}) == 2
    assert view.operation.outcome.best_simulation_id == view.trials[0].simulation_id
    for trial in view.trials:
        assert trial.assessed and trial.matches is True and trial.score == 100
        failure = harness.failure(trial.failure_id)
        assert failure.simulation_id == trial.simulation_id and failure.scenario == trial.scenario
        output = harness.persistence.read_output(trial.simulation_id)
        assert output.result.telemetry_frames == 601
        assert output.result.simulation.scenario == trial.scenario
        job = harness.worker.job_info(trial.simulation_id)
        assert job["options"].max_ticks == 1000
        assert job["evaluation_settings"].speeding_tolerance_mps == 0.8
    assert controller.list(limit=1)[0].id == operation.id


@pytest.mark.parametrize("adapter", [FailedAdapter, UnknownCollisionAdapter])
def test_search_unknown_or_failed_objective_is_not_ranked_as_zero(harness, adapter):
    harness.adapter_factory = adapter
    controller = harness.controller()
    view = terminal(controller, controller.submit(search()).id)
    assert view.operation.status == "completed"
    assert view.operation.outcome.trials_used == view.operation.outcome.unknowns == 2
    assert view.operation.outcome.best_simulation_id is None
    assert all(item.assessed and item.matches is None and item.score is None and item.failure_id is None
               for item in view.trials)


@pytest.mark.parametrize("adapter, expected", [(CollisionAdapter, True), (TestSimulationAdapter, False),
                                               (UnknownCollisionAdapter, None), (FailedAdapter, None)])
def test_reproduction_replays_exact_source_and_commits_three_valued_decision(harness, adapter, expected):
    source = harness.source(reproduced=True)
    harness.adapter_factory = adapter
    controller = harness.controller()
    view = terminal(controller, controller.submit(ReproductionRequest(failure_id=source.id, repeats=2)).id)
    assert view.operation.status == "completed"
    assert view.operation.outcome.reproduced is expected
    assert harness.failure(source.id).reproduced is expected
    assert len(view.trials) == view.operation.outcome.trials_used == 2
    for repeat, trial in enumerate(view.trials):
        assert trial.scenario == source.scenario
        assert trial.scenario.seed == 817
        assert trial.repeat_index == repeat and trial.role == "reproduce"
        job = harness.worker.job_info(trial.simulation_id)
        assert job["options"].max_ticks == 20
        assert job["evaluation_settings"].speeding_tolerance_mps == 0.7


def test_cancelled_reproduction_never_changes_source_reproduction_flag(harness):
    source = harness.source(reproduced=True)
    entered, release = harness.blocker()
    harness.adapter_factory = lambda: BlockedCollisionAdapter(entered, release)
    controller = harness.controller()
    operation = controller.submit(ReproductionRequest(failure_id=source.id, repeats=2))
    assert entered.wait(2)
    assert harness.failure(source.id).reproduced is True
    controller.cancel(operation.id)
    view = terminal(controller, operation.id)
    assert view.operation.status == "cancelled"
    assert view.operation.outcome.reproduced is None
    assert harness.failure(source.id).reproduced is True
    assert len(view.trials) == 1
    release.set()


def test_reproduction_completion_and_source_flag_share_one_transaction(harness, monkeypatch):
    source = harness.source(reproduced=True)
    harness.adapter_factory = TestSimulationAdapter

    def unavailable_flag_write(self, failure_id, value):
        assert failure_id == source.id and value is False
        raise RuntimeError("source decision write failed")

    monkeypatch.setattr(FailureRepository, "set_reproduced", unavailable_flag_write)
    controller = harness.controller()
    view = terminal(controller, controller.submit(ReproductionRequest(failure_id=source.id, repeats=1)).id)
    assert view.operation.status == "failed"
    assert "source decision write failed" in view.operation.error
    assert harness.failure(source.id).reproduced is True
    assert len(view.trials) == 1 and view.trials[0].matches is False


def test_minimization_verifies_baseline_and_counts_every_native_repeat_in_budget(harness):
    source = harness.source()
    controller = harness.controller()
    view = terminal(controller, controller.submit(MinimizeRequest(failure_id=source.id, repeats=2, max_trials=5)).id)
    assert view.operation.status == "completed"
    assert view.operation.outcome.reproduced is True
    assert len(view.trials) == view.operation.outcome.trials_used == 4
    assert [item.role for item in view.trials] == ["minimize_baseline"] * 2 + ["minimize_candidate"] * 2
    assert [item.repeat_index for item in view.trials] == [0, 1, 0, 1]
    assert all(item.scenario == source.scenario for item in view.trials[:2])
    assert view.trials[2].scenario == view.trials[3].scenario
    assert FailureMinimizer.complexity(view.operation.outcome.best_scenario) < FailureMinimizer.complexity(source.scenario)
    assert len(view.operation.outcome.best_scenario.actors) == 1
    assert harness.failure(source.id).reproduced is None  # Only reproduction updates this flag.


def test_unconfirmed_minimization_baseline_prevents_any_reduction(harness):
    source = harness.source()
    harness.adapter_factory = TestSimulationAdapter
    controller = harness.controller()
    view = terminal(controller, controller.submit(MinimizeRequest(failure_id=source.id, repeats=2, max_trials=6)).id)
    assert view.operation.status == "completed"
    assert view.operation.outcome.reproduced is False
    assert view.operation.outcome.best_scenario is None
    assert len(view.trials) == 2
    assert all(item.role == "minimize_baseline" and item.matches is False for item in view.trials)


def test_queued_cancel_and_queue_capacity_never_schedule_rejected_children(harness):
    entered, release = harness.blocker()
    harness.adapter_factory = lambda: BlockedCollisionAdapter(entered, release)
    controller = harness.controller(max_pending=1)
    active = controller.submit(search())
    assert entered.wait(2)
    queued = controller.submit(search())
    with pytest.raises(QueueFullError):
        controller.submit(search())
    assert len(controller.list()) == 2
    cancelled = controller.cancel(queued.id)
    assert cancelled.status == "cancelled" and cancelled.started_at is None
    assert controller.get(queued.id).trials == []
    assert controller.cancel(queued.id) == cancelled
    controller.cancel(active.id)
    assert terminal(controller, active.id).operation.status == "cancelled"
    assert len(controller.get(active.id).trials) == 1
    release.set()


def test_shutdown_requires_explicit_resume_and_reuses_linked_child_without_budget_reset(harness):
    entered, release = harness.blocker()
    harness.adapter_factory = lambda: BlockedCollisionAdapter(entered, release)
    first = harness.controller()
    operation = first.submit(search())
    assert entered.wait(2)
    original_child = first.get(operation.id).trials[0].simulation_id
    assert first.close()
    interrupted = first.get(operation.id)
    assert interrupted.operation.status == "interrupted"
    harness.adapter_factory = CollisionAdapter
    second = harness.controller()
    assert second.get(operation.id).operation.status == "interrupted"
    assert second.get(operation.id).trials == interrupted.trials
    with harness.persistence.database.session(write=False) as session:
        assert len(SimulationRepository(session).list()) == 1
    with pytest.raises(ConflictError, match="revision"):
        second.resume(operation.id, expected_revision=operation.revision)
    second.resume(operation.id, expected_revision=interrupted.operation.revision)
    release.set()
    view = terminal(second, operation.id)
    assert view.operation.status == "completed"
    assert len(view.trials) == view.operation.outcome.trials_used == 2
    assert view.trials[0].simulation_id == original_child
    assert view.trials[0].matches is None  # Its already requested native cancellation is retained.
    assert view.trials[1].matches is True
    with harness.persistence.database.session(write=False) as session:
        assert len(SimulationRepository(session).list()) == 2


def test_interrupted_operation_can_be_cancelled_without_resuming(harness):
    harness.worker.start()
    request = search()
    with harness.persistence.database.session() as session:
        operation = OptimizationRepository(session).create(request, execution=request)
    controller = harness.controller()
    assert controller.get(operation.id).operation.status == "interrupted"
    cancelled = controller.cancel(operation.id)
    assert cancelled.status == "cancelled" and cancelled.cancel_requested
    assert controller.get(operation.id).trials == []
    with pytest.raises(ConflictError):
        controller.resume(operation.id, expected_revision=cancelled.revision)


def test_resume_finishes_partially_executed_minimization_group_without_duplicate_repeats(harness):
    source = harness.source()
    entered, release = harness.blocker()
    starts = []

    def adapters():
        starts.append(len(starts))
        return BlockedCollisionAdapter(entered, release) if len(starts) == 4 else CollisionAdapter()

    harness.adapter_factory = adapters
    first = harness.controller()
    operation = first.submit(MinimizeRequest(failure_id=source.id, repeats=2, max_trials=6))
    assert entered.wait(2)
    assert first.close()
    interrupted = first.get(operation.id)
    assert interrupted.operation.status == "interrupted"
    assert len(interrupted.trials) == 4
    old_ids = [item.simulation_id for item in interrupted.trials]
    second = harness.controller()
    second.resume(operation.id, expected_revision=interrupted.operation.revision)
    release.set()
    view = terminal(second, operation.id)
    assert view.operation.status == "completed"
    assert len(view.trials) == view.operation.outcome.trials_used == 6
    assert [item.simulation_id for item in view.trials[:4]] == old_ids
    assert [item.repeat_index for item in view.trials] == [0, 1, 0, 1, 0, 1]
    assert [item.candidate_index for item in view.trials] == [0, 0, 1, 1, 2, 2]
    assert view.trials[2].matches is True and view.trials[3].matches is None
    assert view.trials[2].scenario == view.trials[3].scenario
    assert all(item.matches is True for item in view.trials[4:])
    assert view.operation.outcome.best_scenario == view.trials[5].scenario
    assert len(starts) == 6  # Every journal entry owns exactly one native invocation.


def test_sampler_version_change_refuses_resume_without_creating_another_child(harness):
    harness.worker.start()
    request = search()
    with harness.persistence.database.session() as session:
        repository = OptimizationRepository(session)
        record = repository.create(request, execution=request)
        repository.transition(record.id, "running", expected_revision=record.revision,
                              engine_version="incompatible-sampler-version")
    controller = harness.controller()
    interrupted = controller.get(record.id).operation
    assert interrupted.status == "interrupted"
    with pytest.raises(ConflictError, match="version changed"):
        controller.resume(record.id, expected_revision=interrupted.revision)
    assert controller.get(record.id).operation == interrupted
    assert controller.get(record.id).trials == []


@pytest.mark.parametrize("request_type", [ReproductionRequest, MinimizeRequest])
def test_failure_policy_change_refuses_resume_without_mutating_state_or_journal(harness, request_type):
    source = harness.source()
    job = harness.worker.job_info(source.simulation_id)
    execution = ExecutionSpec(mode="test", options=job["options"],
                              evaluation_settings=job["evaluation_settings"])
    with harness.persistence.database.session() as session:
        repository = OptimizationRepository(session)
        record = repository.create(request_type(failure_id=source.id), execution=execution, source_failure=source)
        repository.transition(record.id, "running", expected_revision=record.revision,
                              engine_version="failure-policy-obsolete")
    controller = harness.controller()
    interrupted = controller.get(record.id)
    assert interrupted.operation.status == "interrupted"
    with pytest.raises(ConflictError, match="version changed"):
        controller.resume(record.id, expected_revision=interrupted.operation.revision)
    assert controller.get(record.id) == interrupted
    assert interrupted.trials == []
    assert harness.failure(source.id) == source
    with harness.persistence.database.session(write=False) as session:
        assert len(SimulationRepository(session).list()) == 1


def test_restart_may_finish_linked_queued_child_but_never_schedules_new_trial_before_resume(harness):
    harness.persistence.initialize()
    request = search(options=SimulationOptions(max_ticks=1))
    manager = SearchManager(request)
    recipe = manager.suggest()
    with harness.persistence.database.session() as session:
        repository = OptimizationRepository(session)
        record = repository.create(request, execution=request)
        record = repository.transition(record.id, "running", expected_revision=record.revision,
                                       engine_version=manager.version)
        stored = SimulationRepository(session).create(Simulation(scenario=recipe, mode="test"))
        JobRepository(session).create(stored.simulation.id, request.options, request.evaluation_settings)
        repository.add_trial(OptimizationTrial(operation_id=record.id, number=0, candidate_index=0,
                                                simulation_id=stored.simulation.id, role="search", scenario=recipe))
    controller = harness.controller()
    child = wait_until(lambda: value if (value := harness.worker.get(stored.simulation.id)).result else None)
    assert child.simulation.status == "failed"  # Native budget guard, not synthetic objective zero.
    interrupted = controller.get(record.id)
    assert interrupted.operation.status == "interrupted"
    assert len(interrupted.trials) == 1 and not interrupted.trials[0].assessed
    with harness.persistence.database.session(write=False) as session:
        assert len(SimulationRepository(session).list()) == 1
    controller.resume(record.id, expected_revision=interrupted.operation.revision)
    view = terminal(controller, record.id)
    assert view.operation.status == "completed"
    assert len(view.trials) == 2 and view.trials[0].simulation_id == stored.simulation.id
    assert view.operation.outcome.trials_used == view.operation.outcome.unknowns == 2
    assert all(item.assessed and item.score is None for item in view.trials)


def test_cancellation_between_trial_check_and_transaction_link_finishes_cancelled(harness, monkeypatch):
    controller = harness.controller()
    entered, release = harness.blocker()
    original_submit = harness.worker.submit

    def delayed_submit(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original_submit(*args, **kwargs)

    monkeypatch.setattr(harness.worker, "submit", delayed_submit)
    operation = controller.submit(search())
    assert entered.wait(2)
    controller.cancel(operation.id)
    release.set()
    view = terminal(controller, operation.id)
    assert view.operation.status == "cancelled" and view.operation.error is None
    assert view.trials == []
    with harness.persistence.database.session(write=False) as session:
        assert SimulationRepository(session).list() == []  # Failed link rolls back the child and job together.


def test_shutdown_during_pending_child_submit_retains_resumable_interruption(harness, monkeypatch):
    controller = harness.controller()
    entered, release = harness.blocker()
    original_submit = harness.worker.submit

    def delayed_submit(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original_submit(*args, **kwargs)

    monkeypatch.setattr(harness.worker, "submit", delayed_submit)
    operation = controller.submit(search())
    assert entered.wait(2)
    close_results = []
    closing = Thread(target=lambda: close_results.append(controller.close()))
    closing.start()
    try:
        wait_until(lambda: not controller.status()["accepting"])
        assert harness.worker.close()  # API shutdown proceeds while no native child has been enqueued.
    finally:
        release.set()
        closing.join(3)
    assert not closing.is_alive() and close_results == [True]
    assert not controller.status()["worker_alive"]
    view = terminal(controller, operation.id)
    assert view.operation.status == "interrupted" and view.operation.error is None
    assert not view.operation.cancel_requested and view.trials == []
    with harness.persistence.database.session() as session:
        assert SimulationRepository(session).list() == []
        resumed = OptimizationRepository(session).resume(operation.id, expected_revision=view.operation.revision)
        assert resumed.status == "queued"  # The durable operation remains eligible for explicit resume.


def test_optuna_search_records_sampler_version_and_durable_trials(harness):
    optuna = pytest.importorskip("optuna")
    controller = harness.controller()
    view = terminal(controller, controller.submit(search(strategy="optuna")).id)
    assert view.operation.status == "completed"
    assert optuna.__version__ in view.operation.engine_version
    assert len(view.trials) == 2 and all(item.assessed for item in view.trials)
