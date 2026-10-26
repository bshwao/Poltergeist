"""Durable optimization identity, bounded trial links, and atomic child writes."""

from contextlib import closing
from datetime import timedelta
import sqlite3
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.database import Database
from backend.job_repository import JobRepository
from backend.optimization_repository import OptimizationRepository
from backend.persistence_errors import ConflictError, DatabaseInitializationError, NotFoundError, PersistenceError
from backend.repositories import FailureRepository, SimulationRepository
from backend.services.persistence_service import PersistenceService
from backend.services.simulation_service import SimulationService
from backend.storage import ArtifactStore
from scenarios import validate_scenario
from shared.models import FailureReport, SafetyMetrics, Simulation, SimulationResult
from shared.models.optimization import ExecutionSpec, MinimizeRequest, OptimizationOutcome, OptimizationTrial, ReproductionRequest, SearchRequest
from simulator.protocols import SimulationOptions


def scene(name="journal-test"):
    return validate_scenario({"name": name, "duration_s": 0.1, "actors": [{"type": "ego_vehicle"}]})


@pytest.fixture
def database():
    result = Database("sqlite:///:memory:")
    result.initialize()
    yield result
    result.dispose()


def search(repository, **kwargs):
    request = SearchRequest(template="sudden_braking", mode="test", **kwargs)
    return repository.create(request, execution=ExecutionSpec(mode="test"))


def terminal(queued, **metric_values):
    started = queued.created_at + timedelta(seconds=1)
    simulation = queued.model_copy(update={"status": "completed", "started_at": started,
                                           "finished_at": started + timedelta(seconds=1), "progress": 1.0})
    return SimulationResult(simulation=simulation, metrics=SafetyMetrics(**metric_values),
                            telemetry_frames=0, measured_duration_s=0.0)


def source_failure(session):
    runs = SimulationRepository(session)
    queued = runs.create(Simulation(scenario=scene(), mode="test"))
    execution = ExecutionSpec(mode="test")
    JobRepository(session).create(queued.simulation.id, execution.options, execution.evaluation_settings)
    result = terminal(queued.simulation, severity=100.0, collision_count=1)
    runs.complete(result, expected_revision=queued.revision)
    return FailureRepository(session).create(FailureReport(simulation_id=queued.simulation.id,
        scenario=queued.simulation.scenario, kind="collision", severity=100.0, summary="Confirmed collision"))


def child(session, operation, *, number=0, scenario=None, role="search", candidate_index=None,
          repeat_index=0, options=None, mode=None):
    recipe = scene() if scenario is None else scenario
    run = SimulationRepository(session).create(Simulation(scenario=recipe, mode=mode or operation.execution.mode))
    JobRepository(session).create(run.simulation.id, options or operation.execution.options,
                                  operation.execution.evaluation_settings)
    trial = OptimizationTrial(operation_id=operation.id, number=number, simulation_id=run.simulation.id,
        scenario=recipe, role=role, candidate_index=number if candidate_index is None else candidate_index,
        repeat_index=repeat_index)
    return run, trial


def test_operation_roundtrip_snapshots_and_revision_transitions(database):
    with database.session() as session:
        repository = OptimizationRepository(session)
        operation = search(repository)
        operation.request.parameters["seed"] = 999
        assert repository.get(operation.id).request.parameters == {}
        running = repository.transition(operation.id, "running", expected_revision=1, engine_version="test-v1")
        assert running.revision == 2 and running.started_at is not None
        complete = repository.transition(operation.id, "completed", expected_revision=2,
                                         outcome=OptimizationOutcome(summary="No qualifying failure"))
        assert complete.finished_at >= complete.started_at
        assert complete.engine_version == "test-v1"
        assert repository.list() == [complete]
        with pytest.raises(ConflictError):
            repository.transition(operation.id, "running", expected_revision=3)


@pytest.mark.parametrize("revision", [True, 0, -1, 1.0])
def test_revision_validation_and_stale_writes(database, revision):
    with database.session() as session:
        repository = OptimizationRepository(session)
        operation = search(repository)
        with pytest.raises(PersistenceError):
            repository.transition(operation.id, "running", expected_revision=revision)
        assert repository.get(operation.id).revision == 1


def test_stale_revision_and_illegal_regressions_do_not_mutate(database):
    with database.session() as session:
        repository = OptimizationRepository(session)
        operation = search(repository)
        running = repository.transition(operation.id, "running", expected_revision=1)
        with pytest.raises(ConflictError, match="revision"):
            repository.transition(operation.id, "completed", expected_revision=1)
        with pytest.raises(ConflictError, match="lifecycle"):
            repository.transition(operation.id, "queued", expected_revision=2)
        assert repository.get(operation.id) == running


def test_cancel_is_idempotent_and_recovery_does_not_restart_work(database):
    with database.session() as session:
        repository = OptimizationRepository(session)
        first, second = search(repository), search(repository)
        repository.transition(second.id, "running", expected_revision=1)
        cancelled = repository.request_cancel(first.id)
        assert cancelled.revision == 2
        assert repository.request_cancel(first.id) == cancelled
        recovered = repository.recover()
        assert {item.id for item in recovered} == {first.id, second.id}
        assert all(item.status == "interrupted" and item.finished_at is not None for item in recovered)
        assert repository.nonterminal() == []
        with pytest.raises(ConflictError, match="cancelled"):
            repository.resume(first.id, expected_revision=3)
        final = repository.transition(first.id, "cancelled", expected_revision=3)
        assert repository.request_cancel(first.id) == final
        resumed = repository.resume(second.id, expected_revision=3)
        assert resumed.status == "queued" and resumed.finished_at is None
        # Timestamp ties are ordered by UUID; match the recovered identity rather
        # than assuming the second-created operation occupies the second slot.
        prior = next(item for item in recovered if item.id == second.id)
        assert resumed.started_at == prior.started_at


def test_failed_operations_need_error_and_only_interrupted_can_resume(database):
    with database.session() as session:
        repository = OptimizationRepository(session)
        operation = search(repository)
        with pytest.raises(PersistenceError):
            repository.transition(operation.id, "failed", expected_revision=1)
        with pytest.raises(ConflictError):
            repository.resume(operation.id, expected_revision=1)
        failed = repository.transition(operation.id, "failed", expected_revision=1, error="Invalid native setup")
        assert failed.error == "Invalid native setup"


def test_interrupted_operation_can_be_cancelled_and_cannot_resume_after_request(database):
    with database.session() as session:
        repository = OptimizationRepository(session)
        operation = search(repository)
        interrupted = repository.transition(operation.id, "interrupted", expected_revision=1)
        requested = repository.request_cancel(operation.id)
        assert requested.revision == interrupted.revision + 1 and requested.cancel_requested
        assert repository.request_cancel(operation.id) == requested
        with pytest.raises(ConflictError, match="cancelled"):
            repository.resume(operation.id, expected_revision=requested.revision)
        final = repository.transition(operation.id, "cancelled", expected_revision=requested.revision)
        assert final.cancel_requested and final.status == "cancelled"


def test_search_execution_and_source_failure_snapshot_are_checked(database):
    with database.session() as session:
        repository = OptimizationRepository(session)
        with pytest.raises(PersistenceError, match="execution"):
            repository.create(SearchRequest(template="sudden_braking", mode="test"), execution=ExecutionSpec(mode="carla"))
        report = source_failure(session)
        request = ReproductionRequest(failure_id=report.id)
        created = repository.create(request, execution=ExecutionSpec(mode="test"), source_failure=report)
        report.summary = "Changed after saving"
        assert repository.get(created.id).source_failure.summary == "Confirmed collision"
        with pytest.raises(PersistenceError, match="persisted snapshot"):
            repository.create(request, execution=ExecutionSpec(mode="test"), source_failure=report)


@pytest.mark.parametrize("change", [{"mode": "carla"}, {"options": SimulationOptions(max_ticks=2)}])
def test_reproduction_freezes_source_worker_execution_settings(database, change):
    with database.session() as session:
        report = source_failure(session)
        with pytest.raises(PersistenceError, match="source simulation settings"):
            OptimizationRepository(session).create(ReproductionRequest(failure_id=report.id),
                execution=ExecutionSpec.model_validate(ExecutionSpec(mode="test").model_dump() | change),
                source_failure=report)


def test_manual_failure_without_launch_metadata_cannot_invent_reproduction_settings(database):
    with database.session() as session:
        runs = SimulationRepository(session)
        saved = runs.create(Simulation(scenario=scene(), mode="test"))
        runs.complete(terminal(saved.simulation, severity=100.0, collision_count=1), expected_revision=1)
        report = FailureRepository(session).create(FailureReport(simulation_id=saved.simulation.id,
            scenario=saved.simulation.scenario, kind="collision", severity=100.0, summary="Manual failure"))
        with pytest.raises(PersistenceError, match="worker-managed"):
            OptimizationRepository(session).create(ReproductionRequest(failure_id=report.id),
                execution=ExecutionSpec(mode="test"), source_failure=report)


def test_trials_link_normalized_child_and_can_only_assess_a_durable_result_once(database):
    with database.session() as session:
        repository = OptimizationRepository(session)
        operation = search(repository)
        run, trial = child(session, operation)
        assert repository.add_trial(trial) == trial
        with pytest.raises(ConflictError, match="before its result"):
            repository.assess_trial(operation.id, 0)
        SimulationRepository(session).complete(terminal(run.simulation, severity=0.0), expected_revision=1)
        saved = repository.assess_trial(operation.id, 0, score=0.0, matches=False, reason="No evidence")
        assert saved.assessed is True and saved.score == 0.0 and saved.matches is False
        assert repository.trials(operation.id) == [saved]
        with pytest.raises(ConflictError, match="immutable"):
            repository.assess_trial(operation.id, 0, score=100.0, matches=True)


@pytest.mark.parametrize("mutation,match", [
    ({"scenario": scene("different")}, "scenario"),
    ({"number": 1}, "consecutive"),
    ({"role": "reproduce"}, "role"),
    ({"candidate_index": 2}, "candidate"),
    ({"assessed": True}, "unassessed"),
    ({"score": 20.0}, "unassessed"),
])
def test_invalid_child_links_are_rejected(database, mutation, match):
    with database.session() as session:
        repository = OptimizationRepository(session)
        operation = search(repository)
        _, trial = child(session, operation)
        with pytest.raises(PersistenceError, match=match):
            repository.add_trial(trial.model_copy(update=mutation))
        assert repository.trials(operation.id) == []


@pytest.mark.parametrize("mode,options", [("carla", None), (None, SimulationOptions(max_ticks=100))])
def test_child_execution_must_match_the_frozen_parent(database, mode, options):
    with database.session() as session:
        repository = OptimizationRepository(session)
        operation = search(repository)
        _, trial = child(session, operation, mode=mode, options=options)
        with pytest.raises(PersistenceError, match="execution"):
            repository.add_trial(trial)


def test_trial_budget_and_cancellation_stop_new_links(database):
    with database.session() as session:
        repository = OptimizationRepository(session)
        operation = search(repository, trials=1)
        _, first = child(session, operation)
        repository.add_trial(first)
        _, next_trial = child(session, operation, number=1)
        with pytest.raises(ConflictError, match="budget"):
            repository.add_trial(next_trial)
        other = search(repository)
        repository.request_cancel(other.id)
        _, blocked = child(session, other)
        with pytest.raises(ConflictError, match="accepting"):
            repository.add_trial(blocked)


def test_reproduction_and_minimization_validate_baseline_and_repeats(database):
    with database.session() as session:
        repository = OptimizationRepository(session)
        report = source_failure(session)
        replay = repository.create(ReproductionRequest(failure_id=report.id, repeats=1),
                                   execution=ExecutionSpec(mode="test"), source_failure=report)
        _, invalid = child(session, replay, role="reproduce", scenario=scene("different"), candidate_index=0)
        with pytest.raises(PersistenceError, match="exact source"):
            repository.add_trial(invalid)
        _, valid = child(session, replay, role="reproduce", scenario=report.scenario, candidate_index=0)
        repository.add_trial(valid)
        minimum = repository.create(MinimizeRequest(failure_id=report.id, max_trials=3, repeats=2),
                                    execution=ExecutionSpec(mode="test"), source_failure=report)
        _, wrong_role = child(session, minimum, role="minimize_candidate", candidate_index=0)
        with pytest.raises(PersistenceError, match="baseline"):
            repository.add_trial(wrong_role)
        _, baseline = child(session, minimum, role="minimize_baseline", scenario=report.scenario, candidate_index=0)
        repository.add_trial(baseline)
        _, invalid_repeat = child(session, minimum, number=1, role="minimize_baseline", repeat_index=2, candidate_index=0)
        with pytest.raises(PersistenceError, match="repeat index"):
            repository.add_trial(invalid_repeat)


def test_trial_evidence_must_belong_to_the_same_completed_child(database):
    with database.session() as session:
        repository = OptimizationRepository(session)
        report = source_failure(session)
        operation = search(repository)
        run, trial = child(session, operation)
        repository.add_trial(trial)
        SimulationRepository(session).complete(terminal(run.simulation, severity=100.0, collision_count=1), expected_revision=1)
        with pytest.raises(PersistenceError, match="belong"):
            repository.assess_trial(operation.id, 0, score=100.0, failure_id=report.id)
        evidence = FailureRepository(session).create(FailureReport(simulation_id=run.simulation.id,
            scenario=trial.scenario, severity=100.0, kind="collision", summary="Child collided"))
        assessed = repository.assess_trial(operation.id, 0, score=100.0, matches=True, failure_id=evidence.id)
        assert assessed.failure_id == evidence.id


def test_projection_corruption_is_detected_and_missing_ids_remain_distinct(database):
    with database.session() as session:
        repository = OptimizationRepository(session)
        with pytest.raises(NotFoundError):
            repository.get(uuid4())
        operation = search(repository)
        session.execute(text("UPDATE optimization_operations SET revision=99 WHERE id=:id"), {"id": str(operation.id)})
        with pytest.raises(PersistenceError, match="projections"):
            repository.get(operation.id)


def test_atomic_worker_callback_failure_rolls_back_child_job_and_trial(tmp_path):
    persistence = PersistenceService(Database("sqlite:///:memory:"), ArtifactStore(tmp_path / "artifacts"))
    worker = SimulationService(persistence)
    worker.start()
    try:
        with persistence.database.session() as session:
            operation = search(OptimizationRepository(session))

        def link_then_fail(session, stored):
            OptimizationRepository(session).add_trial(OptimizationTrial(operation_id=operation.id, number=0,
                simulation_id=stored.simulation.id, scenario=stored.simulation.scenario, role="search"))
            raise RuntimeError("Link transaction interrupted")

        with pytest.raises(RuntimeError, match="interrupted"):
            worker.submit(scene(), mode="test", on_created=link_then_fail)
        with persistence.database.session() as session:
            assert SimulationRepository(session).list() == []
            assert JobRepository(session).pending() == []
            assert OptimizationRepository(session).trials(operation.id) == []
        assert worker.status()["pending"] == 0
    finally:
        assert worker.close()
        persistence.database.dispose()


def historical_database(path):
    import backend.migrations as migrations

    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE schema_migrations (revision INTEGER PRIMARY KEY NOT NULL,name TEXT NOT NULL,checksum TEXT NOT NULL,applied_at TEXT NOT NULL)")
        for statement in (*migrations._INITIAL_DDL, *migrations._JOBS_DDL):
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES (1,'initial_persistence',?,'2026-01-01')", (migrations._INITIAL_CHECKSUM,))
        connection.execute("INSERT INTO schema_migrations VALUES (2,'simulation_jobs',?,'2026-01-01')", (migrations._JOBS_CHECKSUM,))
        connection.execute("INSERT INTO scenarios VALUES (?,'preserve original bytes',7,'2026-01-01','2026-01-02')", (str(uuid4()),))


def test_revision_two_upgrade_preserves_historical_records_byte_for_byte(tmp_path):
    path = tmp_path / "revision-two.db"
    historical_database(path)
    database = Database(f"sqlite:///{path.as_posix()}")
    database.initialize()
    try:
        with database.engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT revision FROM schema_migrations ORDER BY revision").scalars().all() == [1, 2, 3, 4, 5, 6]
            assert tuple(connection.exec_driver_sql("SELECT payload,revision,created_at,updated_at FROM scenarios").one()) == ("preserve original bytes", 7, "2026-01-01", "2026-01-02")
    finally:
        database.dispose()


def test_failed_revision_three_rolls_back_new_ddl_and_preserves_history(tmp_path, monkeypatch):
    import backend.migrations as migrations

    path = tmp_path / "failed-upgrade.db"
    historical_database(path)
    monkeypatch.setattr(migrations, "_OPTIMIZATION_DDL", migrations._OPTIMIZATION_DDL + ("INVALID OPTIMIZATION SQL",))
    with pytest.raises(DatabaseInitializationError):
        Database(f"sqlite:///{path.as_posix()}").initialize()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT revision FROM schema_migrations ORDER BY revision").fetchall() == [(1,), (2,)]
        assert connection.execute("SELECT payload,revision FROM scenarios").fetchall() == [("preserve original bytes", 7)]
        assert connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'optimization_%'").fetchall() == []
