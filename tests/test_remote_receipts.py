"""Real SQLite receipt identity, migration preservation, and worker atomicity."""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, timedelta
import sqlite3
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text

from backend.database import Database
from backend.job_repository import JobRepository
from backend.models import RemoteCommandReceiptRecord, RemoteProjectionRecord, SimulationJobRecord, SimulationRecord
from backend.persistence_errors import ConflictError, DatabaseInitializationError, PersistenceError
from backend.remote_receipts import RemoteBinding, RemoteReceiptReplay, RemoteReceiptRepository
from backend.repositories import SimulationRepository
from backend.services.persistence_service import PersistenceService
from backend.services.simulation_service import SimulationService
from backend.storage import ArtifactStore
from scenarios import validate_scenario
from shared.models import EvaluationSettings, SafetyMetrics, Simulation, SimulationResult
from simulator import SimulationRunner
from simulator.protocols import SimulationOptions
from simulator.test_adapter import TestSimulationAdapter


PAYLOAD = "a" * 64
SCENARIO = "b" * 64


def scene():
    return validate_scenario({"name": "remote-receipt-test", "duration_s": 0.1,
                              "actors": [{"type": "ego_vehicle"}]})


@pytest.fixture
def binding():
    return RemoteBinding("https://coordinator.example", uuid4(), uuid4(), 1)


@pytest.fixture
def database(tmp_path):
    value = Database(f"sqlite:///{(tmp_path / 'receipts.db').as_posix()}")
    value.initialize()
    yield value
    value.dispose()


def job(session):
    stored = SimulationRepository(session).create(Simulation(scenario=scene(), mode="test"))
    JobRepository(session).create(stored.simulation.id, SimulationOptions(), EvaluationSettings())
    return stored


def execute(repository, binding, run_id, request_id=None, **kwargs):
    return repository.create(binding, request_id or uuid4(), "execute", PAYLOAD, run_id,
                             scenario_sha256=SCENARIO, **kwargs)


def completed(stored):
    started = stored.simulation.created_at + timedelta(seconds=1)
    run = stored.simulation.model_copy(update={"status": "completed", "started_at": started,
        "finished_at": started + timedelta(seconds=1), "progress": 1.0})
    return SimulationResult(simulation=run, metrics=SafetyMetrics(), telemetry_frames=0, measured_duration_s=0.0)


def test_binding_is_canonical_immutable_and_has_strict_generation(binding):
    canonical = RemoteBinding("https://COORDINATOR.example:443/", str(binding.device_id), str(binding.owner_id), 1)
    assert canonical == binding
    with pytest.raises(FrozenInstanceError):
        canonical.generation = 2


@pytest.mark.parametrize("origin", ["http://localhost", "https://user:secret@example.com", "https://example.com/path",
                                    "https://example.com?token=secret", "https://example.com:bad", "not an origin"])
def test_binding_rejects_unsafe_origins_without_echoing_input(binding, origin):
    with pytest.raises(PersistenceError) as caught:
        replace(binding, origin=origin)
    assert "secret" not in str(caught.value) and origin not in str(caught.value)


@pytest.mark.parametrize("generation", [True, 0, -1, 1.0, "1", 2**63])
def test_binding_rejects_non_positive_or_non_integer_generations(binding, generation):
    with pytest.raises(PersistenceError):
        replace(binding, generation=generation)


@pytest.mark.parametrize("changes", [{"device_id": "invalid"}, {"owner_id": 1}])
def test_binding_rejects_invalid_uuids(binding, changes):
    with pytest.raises(PersistenceError):
        replace(binding, **changes)


def test_receipt_detached_retry_and_restart_preserve_original_identity(database, binding):
    with database.session() as session:
        run = job(session)
        repository = RemoteReceiptRepository(session)
        receipt = execute(repository, binding, run.simulation.id)
        assert repository.lookup(binding, str(receipt.request_id)) == receipt
        assert repository.execution_for_run(binding, run.simulation.id) == receipt
        assert execute(repository, binding, run.simulation.id, receipt.request_id) == receipt
        with pytest.raises(FrozenInstanceError):
            receipt.revision = 2
    database.dispose()
    database.initialize()
    with database.session(write=False) as session:
        restored = RemoteReceiptRepository(session).lookup(binding, receipt.request_id)
        assert restored == receipt
        assert restored.accepted_at.tzinfo == UTC and restored.revision == 1
        assert session.scalar(select(text("count(*)")).select_from(RemoteCommandReceiptRecord)) == 1


@pytest.mark.parametrize("operation,payload,scenario", [
    ("execute", "invalid", SCENARIO), ("execute", "A" * 64, SCENARIO),
    ("execute", PAYLOAD, None), ("execute", PAYLOAD, "invalid"),
    ("cancel", PAYLOAD, SCENARIO), ("unknown", PAYLOAD, None),
])
def test_invalid_command_fields_create_no_receipt(database, binding, operation, payload, scenario):
    with database.session() as session:
        run = job(session)
        with pytest.raises(PersistenceError):
            RemoteReceiptRepository(session).create(binding, uuid4(), operation, payload, run.simulation.id,
                                                     scenario_sha256=scenario)
        assert session.scalars(select(RemoteCommandReceiptRecord)).all() == []


@pytest.mark.parametrize("change", ["owner", "payload", "scenario", "operation", "cancel_target"])
def test_changed_request_conflicts_and_keeps_original(database, binding, change):
    with database.session() as session:
        run, other_run = job(session), job(session)
        repository = RemoteReceiptRepository(session)
        receipt = execute(repository, binding, run.simulation.id)
        cancel = repository.create(binding, uuid4(), "cancel", PAYLOAD, run.simulation.id)
        with pytest.raises(ConflictError):
            repository.create(replace(binding, owner_id=uuid4()) if change == "owner" else binding,
                cancel.request_id if change == "cancel_target" else receipt.request_id,
                "cancel" if change in ("operation", "cancel_target") else "execute",
                "c" * 64 if change == "payload" else PAYLOAD,
                other_run.simulation.id if change == "cancel_target" else run.simulation.id,
                scenario_sha256=None if change in ("operation", "cancel_target") else "c" * 64 if change == "scenario" else SCENARIO)
        assert repository.lookup(binding, receipt.request_id) == receipt
        assert repository.lookup(binding, cancel.request_id) == cancel


def test_forged_owner_never_receives_existing_receipts_or_admits_new_requests(database, binding):
    forged = replace(binding, owner_id=uuid4())
    with database.session() as session:
        run, other_run = job(session), job(session)
        repository = RemoteReceiptRepository(session)
        receipt = execute(repository, binding, run.simulation.id)
        for action in (
            lambda: repository.lookup(forged, receipt.request_id),
            lambda: repository.lookup(forged, uuid4()),
            lambda: repository.execution_for_run(forged, run.simulation.id),
            lambda: repository.count_waiting(forged),
            lambda: execute(repository, forged, other_run.simulation.id),
        ):
            with pytest.raises(ConflictError, match="ownership"):
                action()
        assert repository.count_waiting(binding) == 1


def test_origin_device_and_generation_isolate_request_keys_and_run_access(database, binding):
    with database.session() as session:
        repository = RemoteReceiptRepository(session)
        original = execute(repository, binding, job(session).simulation.id)
        for alternative in (replace(binding, origin="https://other.example"),
                            replace(binding, device_id=uuid4()), replace(binding, generation=2, owner_id=uuid4())):
            assert repository.lookup(alternative, original.request_id) is None
            assert repository.execution_for_run(alternative, original.simulation_id) is None
            assert repository.count_waiting(alternative) == 0
            with pytest.raises(ConflictError, match="owned execution"):
                repository.create(alternative, uuid4(), "cancel", PAYLOAD, original.simulation_id)
            accepted = execute(repository, alternative, job(session).simulation.id, original.request_id)
            assert repository.lookup(alternative, original.request_id) == accepted


def test_multiple_cancel_requests_are_allowed_but_unrelated_runs_are_denied(database, binding):
    with database.session() as session:
        repository = RemoteReceiptRepository(session)
        run = job(session)
        receipt = execute(repository, binding, run.simulation.id)
        cancellations = [repository.create(binding, uuid4(), "cancel", PAYLOAD, run.simulation.id) for _ in range(2)]
        assert cancellations[0].request_id != cancellations[1].request_id
        assert repository.execution_for_run(binding, run.simulation.id) == receipt
        assert repository.create(binding, cancellations[0].request_id, "cancel", PAYLOAD, run.simulation.id) == cancellations[0]
        with pytest.raises(ConflictError, match="owned execution"):
            repository.create(binding, uuid4(), "cancel", PAYLOAD, job(session).simulation.id)
        with pytest.raises(ConflictError, match="execution request"):
            execute(repository, binding, run.simulation.id)


def test_count_waiting_uses_jobs_and_lifecycle_without_counting_cancellations(database, binding):
    with database.session() as session:
        repository = RemoteReceiptRepository(session)
        waiting, claimed, cancelled, done, unmanaged = [job(session) for _ in range(5)]
        for run in (waiting, claimed, cancelled, done, unmanaged):
            execute(repository, binding, run.simulation.id)
        # Remove a job to prove a plain run projection is not a waiting worker job.
        session.execute(delete(SimulationJobRecord).where(SimulationJobRecord.simulation_id == str(unmanaged.simulation.id)))
        JobRepository(session).claim(claimed.simulation.id)
        JobRepository(session).request_cancel(cancelled.simulation.id)
        SimulationRepository(session).complete(completed(done), expected_revision=done.revision)
        for _ in range(2):
            repository.create(binding, uuid4(), "cancel", PAYLOAD, waiting.simulation.id)
        execute(repository, replace(binding, device_id=uuid4()), job(session).simulation.id)
        assert repository.count_waiting(binding) == 1


def test_caller_rollback_removes_receipt_simulation_and_job(database, binding):
    request_id = uuid4()
    with pytest.raises(RuntimeError, match="rollback"):
        with database.session() as session:
            execute(RemoteReceiptRepository(session), binding, job(session).simulation.id, request_id)
            raise RuntimeError("rollback")
    with database.session(write=False) as session:
        assert RemoteReceiptRepository(session).lookup(binding, request_id) is None
        assert session.scalars(select(SimulationJobRecord)).all() == []
        assert session.scalars(select(SimulationRecord)).all() == []


def test_projection_revision_tracks_changed_content_and_survives_restart(database, binding):
    with database.session() as session:
        repository = RemoteReceiptRepository(session)
        receipt = execute(repository, binding, job(session).simulation.id)
        assert repository.projection_revision(binding, receipt.simulation_id, PAYLOAD) == 1
        assert repository.projection_revision(binding, receipt.simulation_id, PAYLOAD) == 1
        assert repository.projection_revision(binding, receipt.simulation_id, SCENARIO) == 2
        assert repository.lookup(binding, receipt.request_id) == receipt
    database.dispose()
    database.initialize()
    with database.session() as session:
        repository = RemoteReceiptRepository(session)
        assert repository.projection_revision(binding, receipt.simulation_id, SCENARIO) == 2
        assert repository.projection_revision(binding, receipt.simulation_id, PAYLOAD) == 3
        assert repository.lookup(binding, receipt.request_id).revision == 1


def test_projection_requires_ownership_and_execution_receipt(database, binding):
    with database.session() as session:
        repository = RemoteReceiptRepository(session)
        receipt = execute(repository, binding, job(session).simulation.id)
        unowned = job(session)
        assert repository.projection_revision(binding, receipt.simulation_id, PAYLOAD) == 1
        for alternative in (replace(binding, owner_id=uuid4()), replace(binding, device_id=uuid4()),
                            replace(binding, generation=2), replace(binding, origin="https://other.example")):
            with pytest.raises(ConflictError):
                repository.projection_revision(alternative, receipt.simulation_id, SCENARIO)
        with pytest.raises(ConflictError, match="owned execution"):
            repository.projection_revision(binding, unowned.simulation.id, SCENARIO)
        with pytest.raises(PersistenceError, match="digest"):
            repository.projection_revision(binding, receipt.simulation_id, "invalid")
        assert session.scalar(select(RemoteProjectionRecord)).payload_sha256 == PAYLOAD


def test_projection_rollback_and_revision_overflow_preserve_previous_content(database, binding):
    with database.session() as session:
        repository = RemoteReceiptRepository(session)
        receipt = execute(repository, binding, job(session).simulation.id)
        repository.projection_revision(binding, receipt.simulation_id, PAYLOAD)
    with pytest.raises(RuntimeError):
        with database.session() as session:
            assert RemoteReceiptRepository(session).projection_revision(binding, receipt.simulation_id, SCENARIO) == 2
            raise RuntimeError("projection rollback")
    with database.session() as session:
        repository = RemoteReceiptRepository(session)
        assert repository.projection_revision(binding, receipt.simulation_id, PAYLOAD) == 1
        row = session.scalar(select(RemoteProjectionRecord))
        row.revision = 2**63 - 1
    with database.session() as session:
        repository = RemoteReceiptRepository(session)
        assert repository.projection_revision(binding, receipt.simulation_id, PAYLOAD) == 2**63 - 1
        with pytest.raises(ConflictError, match="limit"):
            repository.projection_revision(binding, receipt.simulation_id, SCENARIO)
        assert session.scalar(select(RemoteProjectionRecord)).payload_sha256 == PAYLOAD


def test_projection_writers_serialize_without_duplicate_or_lost_revisions(database, binding):
    with database.session() as session:
        receipt = execute(RemoteReceiptRepository(session), binding, job(session).simulation.id)

    def project(digest):
        with database.session() as session:
            return RemoteReceiptRepository(session).projection_revision(binding, receipt.simulation_id, digest)

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(project, [PAYLOAD] * 4)) == [1] * 4
        assert sorted(pool.map(project, [str(number) * 64 for number in range(4)])) == [2, 3, 4, 5]
    with database.session(write=False) as session:
        assert session.scalar(select(RemoteProjectionRecord)).revision == 5


def test_native_foreign_key_and_unique_scope_reject_invalid_rows(database, binding):
    with database.session() as session:
        # No job references this run, so the deletion failure proves the receipt FK.
        run = SimulationRepository(session).create(Simulation(scenario=scene(), mode="test"))
        receipt = execute(RemoteReceiptRepository(session), binding, run.simulation.id)
    with pytest.raises(ConflictError):
        with database.session() as session:
            session.execute(delete(SimulationRecord).where(SimulationRecord.id == str(run.simulation.id)))
    with pytest.raises(ConflictError):
        with database.session() as session:
            session.execute(text("INSERT INTO remote_command_receipts "
                "(origin,device_id,owner_id,generation,request_id,operation,payload_sha256,scenario_sha256,simulation_id,accepted_at,revision) "
                "VALUES (:origin,:device,:owner,:generation,:request,'execute',:payload,:scenario,:simulation,:accepted,1)"),
                {"origin": binding.origin, "device": str(binding.device_id), "owner": str(uuid4()),
                 "generation": binding.generation, "request": str(receipt.request_id), "payload": PAYLOAD,
                 "scenario": SCENARIO, "simulation": str(run.simulation.id), "accepted": receipt.accepted_at.isoformat()})
    with pytest.raises(ConflictError):
        with database.session() as session:
            execute(RemoteReceiptRepository(session), binding, uuid4())
    with database.session(write=False) as session:
        assert RemoteReceiptRepository(session).lookup(binding, receipt.request_id) == receipt


def revision_four(path):
    import backend.migrations as migrations

    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE schema_migrations (revision INTEGER PRIMARY KEY NOT NULL,name TEXT NOT NULL,checksum TEXT NOT NULL,applied_at TEXT NOT NULL)")
        for revision, name, statements, checksum in (
            (1, "initial_persistence", migrations._INITIAL_DDL, migrations._INITIAL_CHECKSUM),
            (2, "simulation_jobs", migrations._JOBS_DDL, migrations._JOBS_CHECKSUM),
            (3, "optimization_journal", migrations._OPTIMIZATION_DDL, migrations._OPTIMIZATION_CHECKSUM),
            (4, "agent_registry", migrations._AGENTS_DDL, migrations._AGENTS_CHECKSUM),
        ):
            for statement in statements:
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations VALUES (?,?,?,'2026-01-01')", (revision, name, checksum))
        scenario_id, run_id = str(uuid4()), str(uuid4())
        connection.execute("INSERT INTO scenarios VALUES (?,' original scenario bytes ',7,'2026-01-01','2026-01-02')", (scenario_id,))
        connection.execute("INSERT INTO simulations VALUES (?,?,' original run bytes ',NULL,'queued','test',3,'2026-01-01','2026-01-02')", (run_id, scenario_id))
        connection.execute("INSERT INTO simulation_jobs VALUES (1,?,' options ',' settings ',0,0,'2026-01-01',' snapshot ')", (run_id,))
        connection.execute("INSERT INTO agent_registrations VALUES (?,' agent bytes ',1,'2026-01-01')", (str(uuid4()),))
        return {table: connection.execute(f"SELECT * FROM {table}").fetchall()
                for table in ("schema_migrations", "scenarios", "simulations", "simulation_jobs", "agent_registrations")}


def test_revision_five_preserves_published_history_and_records_byte_for_byte(tmp_path):
    path = tmp_path / "revision-four.db"
    before = revision_four(path)
    database = Database(f"sqlite:///{path.as_posix()}")
    try:
        database.initialize()
        database.dispose()
        database.initialize()
        with database.engine.connect() as connection:
            for table, rows in before.items():
                query = f"SELECT * FROM {table}" + (" WHERE revision <= 4" if table == "schema_migrations" else "")
                assert [tuple(row) for row in connection.exec_driver_sql(query)] == rows
            assert connection.exec_driver_sql("SELECT revision,name FROM schema_migrations WHERE revision=5").one() == (5, "remote_command_receipts")
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM remote_command_receipts").scalar() == 0
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM remote_run_projections").scalar() == 0
    finally:
        database.dispose()


def test_failed_receipt_migration_rolls_back_new_table_indexes_and_history(tmp_path, monkeypatch):
    import backend.migrations as migrations

    path = tmp_path / "revision-four.db"
    before = revision_four(path)
    monkeypatch.setattr(migrations, "_REMOTE_RECEIPTS_DDL", (*migrations._REMOTE_RECEIPTS_DDL, "INVALID MIGRATION SQL"))
    with pytest.raises(DatabaseInitializationError):
        Database(f"sqlite:///{path.as_posix()}").initialize()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT * FROM schema_migrations").fetchall() == before["schema_migrations"]
        assert connection.execute("SELECT name FROM sqlite_master WHERE name LIKE '%remote_command_receipts%' OR name='remote_run_projections'").fetchall() == []
        assert connection.execute("SELECT * FROM simulation_jobs").fetchall() == before["simulation_jobs"]


@pytest.mark.parametrize("replacement", [
    "CREATE INDEX ix_remote_command_receipts_request ON remote_command_receipts (origin,device_id,generation,request_id)",
    "CREATE UNIQUE INDEX ix_remote_command_receipts_request ON remote_command_receipts (origin,device_id,request_id)",
    "CREATE UNIQUE INDEX ix_remote_command_receipts_request ON remote_command_receipts (origin,device_id,generation,request_id) WHERE owner_id='other'",
])
def test_exact_schema_verification_rejects_weakened_request_uniqueness(database, replacement):
    path = database.file_path
    database.dispose()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DROP INDEX ix_remote_command_receipts_request")
        connection.execute(replacement)
    with pytest.raises(DatabaseInitializationError, match="indexes"):
        database.initialize()


@pytest.mark.parametrize("column,value", [("payload_sha256", "invalid"), ("accepted_at", "2026-01-01"),
                                         ("device_id", "invalid"), ("scenario_sha256", "invalid")])
def test_malformed_stored_receipts_fail_closed_without_rewriting(database, binding, column, value):
    with database.session() as session:
        execute(RemoteReceiptRepository(session), binding, job(session).simulation.id)
    with database.engine.begin() as connection:
        connection.exec_driver_sql(f"UPDATE remote_command_receipts SET {column}=?", (value,))
    with database.session(write=False) as session:
        # A damaged indexed binding is no longer discoverable under its old key;
        # loading it directly still rejects it rather than normalizing storage.
        row = session.scalar(select(RemoteCommandReceiptRecord))
        with pytest.raises(PersistenceError):
            RemoteReceiptRepository._stored(row)
        assert getattr(row, column) == value


def wait_until(predicate):
    deadline = monotonic() + 10
    while monotonic() < deadline:
        if value := predicate():
            return value
        Event().wait(0.01)
    raise AssertionError("Receipt worker did not reach the expected state")


class BlockingOpen(TestSimulationAdapter):
    def __init__(self, entered, release):
        super().__init__()
        self.entered, self.release = entered, release

    def open(self, scenario):
        self.entered.set()
        if not self.release.wait(10):
            raise RuntimeError("Receipt test adapter was not released")
        return super().open(scenario)


def test_competing_submit_replay_rolls_back_losing_job_before_enqueue(database, binding, tmp_path):
    persistence = PersistenceService(database, ArtifactStore(tmp_path / "artifacts"))
    entered, release = Event(), Event()
    worker = SimulationService(persistence, runner_factory=lambda **kwargs: SimulationRunner(BlockingOpen(entered, release), **kwargs))
    worker.start()
    request_id = uuid4()

    def accept(session, stored):
        execute(RemoteReceiptRepository(session), binding, stored.simulation.id, request_id)

    try:
        original = worker.submit(scene(), mode="test", on_created=accept)
        assert entered.wait(5)
        with pytest.raises(RemoteReceiptReplay) as caught:
            worker.submit(scene(), mode="test", on_created=accept)
        assert caught.value.receipt.simulation_id == original.simulation.id
        assert worker.status()["pending"] == 0
        with database.session(write=False) as session:
            assert len(session.scalars(select(SimulationRecord)).all()) == 1
            assert len(session.scalars(select(SimulationJobRecord)).all()) == 1
            assert len(session.scalars(select(RemoteCommandReceiptRecord)).all()) == 1
    finally:
        release.set()
        assert worker.close()


def test_cancel_hook_failure_rolls_back_flag_and_receipt_before_any_signal(database, binding, tmp_path):
    persistence = PersistenceService(database, ArtifactStore(tmp_path / "artifacts"))
    entered, release, signalled = Event(), Event(), Event()

    class Runner(SimulationRunner):
        def cancel(self):
            signalled.set()
            super().cancel()

    worker = SimulationService(persistence, runner_factory=lambda **kwargs: Runner(BlockingOpen(entered, release), **kwargs))
    worker.start()
    try:
        run = worker.submit(scene(), mode="test", on_created=lambda session, stored:
            execute(RemoteReceiptRepository(session), binding, stored.simulation.id))
        assert entered.wait(5)
        request_id = uuid4()

        def reject(session, stored):
            assert JobRepository(session).get(stored.simulation.id).cancel_requested
            RemoteReceiptRepository(session).create(binding, request_id, "cancel", PAYLOAD, stored.simulation.id)
            assert not worker._active_cancel.is_set() and not signalled.is_set()
            raise RuntimeError("receipt transaction rejected")

        with pytest.raises(RuntimeError, match="receipt transaction"):
            worker.cancel(run.simulation.id, on_created=reject)
        assert not worker._active_cancel.is_set() and not signalled.is_set()
        assert not worker.job_info(run.simulation.id)["cancel_requested"]
        with database.session(write=False) as session:
            assert RemoteReceiptRepository(session).lookup(binding, request_id) is None
        assert worker.status()["accepting"]
    finally:
        release.set()
        assert worker.close()


def test_cancel_receipt_commits_before_signal_and_terminal_noop_has_receipt(database, binding, tmp_path):
    persistence = PersistenceService(database, ArtifactStore(tmp_path / "artifacts"))
    entered, release, signalled = Event(), Event(), Event()
    request_id = uuid4()

    class Runner(SimulationRunner):
        def cancel(self):
            # Separate read proves the hook's transaction committed before this
            # cooperative runner notification, rather than merely being flushed.
            with database.session(write=False) as session:
                assert RemoteReceiptRepository(session).lookup(binding, request_id) is not None
            signalled.set()
            super().cancel()

    worker = SimulationService(persistence, runner_factory=lambda **kwargs: Runner(BlockingOpen(entered, release), **kwargs))
    worker.start()
    try:
        run = worker.submit(scene(), mode="test", on_created=lambda session, stored:
            execute(RemoteReceiptRepository(session), binding, stored.simulation.id))
        assert entered.wait(5)

        def accept(session, stored):
            assert not worker._active_cancel.is_set()
            assert JobRepository(session).get(stored.simulation.id).cancel_requested
            return RemoteReceiptRepository(session).create(binding, request_id, "cancel", PAYLOAD, stored.simulation.id)

        worker.cancel(run.simulation.id, on_created=accept)
        assert signalled.is_set()
        release.set()
        terminal = wait_until(lambda: (item if (item := worker.get(run.simulation.id)).result is not None else None))
        assert terminal.simulation.status == "cancelled"
        noop_id = uuid4()
        assert worker.cancel(run.simulation.id, on_created=lambda session, stored:
            RemoteReceiptRepository(session).create(binding, noop_id, "cancel", PAYLOAD, stored.simulation.id)) == terminal
        rejected_id = uuid4()

        def reject_noop(session, stored):
            RemoteReceiptRepository(session).create(binding, rejected_id, "cancel", PAYLOAD, stored.simulation.id)
            raise RuntimeError("terminal receipt rejected")

        with pytest.raises(RuntimeError, match="terminal receipt"):
            worker.cancel(run.simulation.id, on_created=reject_noop)
        assert worker.get(run.simulation.id) == terminal
        with database.session(write=False) as session:
            assert RemoteReceiptRepository(session).lookup(binding, noop_id).simulation_id == run.simulation.id
            assert RemoteReceiptRepository(session).lookup(binding, rejected_id) is None
    finally:
        release.set()
        assert worker.close()


def test_accepted_queued_run_and_receipt_survive_database_worker_restart(database, binding, tmp_path):
    with database.session() as session:
        run = job(session)
        receipt = execute(RemoteReceiptRepository(session), binding, run.simulation.id)
    database.dispose()
    persistence = PersistenceService(database, ArtifactStore(tmp_path / "artifacts"))
    worker = SimulationService(persistence)
    worker.start()
    try:
        result = wait_until(lambda: (item if (item := worker.get(run.simulation.id)).result is not None else None))
        assert result.simulation.status == "completed"
        with database.session(write=False) as session:
            repository = RemoteReceiptRepository(session)
            assert repository.lookup(binding, receipt.request_id) == receipt
            assert repository.count_waiting(binding) == 0
    finally:
        assert worker.close()
