"""SQLite lifecycle, transactional migrations, and native constraints."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text

from backend.database import Database
from backend.models import Base
from backend.persistence_errors import ConflictError, DatabaseInitializationError, PersistenceError
from backend.repositories import ScenarioRepository
from scenarios import validate_scenario


def scene():
    return validate_scenario({"name": "database-test", "actors": [{"type": "ego_vehicle"}]})


def file_database(path: Path) -> Database:
    return Database("sqlite:///" + path.as_posix())


def test_constructor_and_import_do_not_connect_or_create_directories(tmp_path):
    path = tmp_path / "not-created" / "runs.db"
    database = file_database(path)
    assert not path.parent.exists()
    with pytest.raises(PersistenceError, match="initialize"):
        with database.session():
            pass
    assert not path.parent.exists()
    database.dispose()


@pytest.mark.parametrize("url", ["postgresql://localhost/db", "sqlite+aiosqlite:///:memory:",
                                      "sqlite:///test.db?mode=ro", "invalid-url"])
def test_rejects_unsupported_database_and_driver(url):
    with pytest.raises(PersistenceError):
        Database(url)


def test_initialization_is_explicit_versioned_and_idempotent(tmp_path):
    path = tmp_path / "database" / "runs.db"
    database = file_database(path)
    database.initialize()
    database.initialize()
    assert path.is_file()
    with database.engine.connect() as connection:
        history = connection.execute(text("SELECT revision,name,checksum FROM schema_migrations ORDER BY revision")).all()
        assert [(row.revision, row.name) for row in history] == [(1, "initial_persistence"), (2, "simulation_jobs"), (3, "optimization_journal"), (4, "agent_registry"), (5, "remote_command_receipts"), (6, "cancellation_acceptance_snapshot"), (7, "guided_experiments_and_discovery")]
        assert all(len(row.checksum) == 64 for row in history)
        assert set(inspect(connection).get_table_names()) == set(Base.metadata.tables) | {"schema_migrations"}
    with database.session() as session:
        saved = ScenarioRepository(session).create(scene())
    database.dispose()
    database.initialize()
    with database.session() as session:
        assert ScenarioRepository(session).get(saved.id).scenario == scene()
    database.dispose()


def test_session_rolls_back_related_writes_on_interruption():
    database = Database("sqlite:///:memory:")
    database.initialize()
    with pytest.raises(KeyboardInterrupt):
        with database.session() as session:
            ScenarioRepository(session).create(scene())
            raise KeyboardInterrupt
    with database.session() as session:
        assert ScenarioRepository(session).list() == []
    database.dispose()


def test_memory_database_survives_sessions_and_serializes_threads():
    database = Database("sqlite:///:memory:")
    database.initialize()
    def save(_):
        with database.session() as session:
            return ScenarioRepository(session).create(scene()).id
    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(save, range(12)))
    with database.session() as session:
        assert {row.id for row in ScenarioRepository(session).list()} == set(ids)
    database.dispose()


def test_separate_instances_can_initialize_the_same_database_concurrently(tmp_path):
    path = tmp_path / "runs.db"
    barrier = Barrier(2)
    def initialize(_):
        database = file_database(path)
        barrier.wait()
        database.initialize()
        with database.session() as session:
            ScenarioRepository(session).create(scene())
        database.dispose()
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(initialize, range(2)))
    database = file_database(path)
    database.initialize()
    with database.session() as session:
        assert len(ScenarioRepository(session).list()) == 2
    database.dispose()


def test_concurrent_read_then_write_units_reserve_writer_before_reads(tmp_path):
    first = file_database(tmp_path / "serialized-writers.db")
    second = file_database(tmp_path / "serialized-writers.db")
    first.initialize()
    second.initialize()
    with first.session() as session:
        saved = ScenarioRepository(session).create(scene())
    first_read, release_first, second_attempted, second_entered = Event(), Event(), Event(), Event()

    def first_writer():
        with first.session() as session:
            previous = ScenarioRepository(session).get(saved.id)
            first_read.set()
            assert release_first.wait(3)
            return ScenarioRepository(session).update(saved.id, scene().model_copy(update={"name": "first-write"}), expected_revision=previous.revision)

    def second_writer():
        assert first_read.wait(3)
        second_attempted.set()
        with second.session() as session:
            second_entered.set()
            previous = ScenarioRepository(session).get(saved.id)
            return ScenarioRepository(session).update(saved.id, scene().model_copy(update={"name": "second-write"}), expected_revision=previous.revision)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(first_writer)
            second_future = executor.submit(second_writer)
            try:
                assert first_read.wait(3)
                assert second_attempted.wait(3)
                # The competing unit waits at BEGIN IMMEDIATE, before its read.
                assert not second_entered.wait(0.1)
            finally:
                release_first.set()
            assert first_future.result(timeout=3).revision == 2
            assert second_future.result(timeout=3).revision == 3
        with first.session(write=False) as session:
            assert ScenarioRepository(session).get(saved.id).scenario.name == "second-write"
    finally:
        first.dispose()
        second.dispose()


def test_readonly_session_does_not_inherit_writer_option_from_pooled_connection(tmp_path):
    database = file_database(tmp_path / "read-option.db")
    database.initialize()
    statements = []
    from sqlalchemy import event

    event.listen(database.engine, "before_cursor_execute", lambda connection, cursor, statement, parameters, context, many: statements.append(statement))
    try:
        with database.session() as session:
            session.execute(text("SELECT 1"))
        with database.session(write=False) as session:
            session.execute(text("SELECT 1"))
        with database.session() as session:
            session.execute(text("SELECT 1"))
        assert [sql for sql in statements if sql.startswith("BEGIN")] == ["BEGIN IMMEDIATE", "BEGIN", "BEGIN IMMEDIATE"]
    finally:
        database.dispose()


def test_refuses_unversioned_populated_database_without_modifying_it(tmp_path):
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE original(value TEXT)")
        connection.execute("INSERT INTO original VALUES ('preserve me')")
    with pytest.raises(DatabaseInitializationError, match="unversioned"):
        file_database(path).initialize()
    with closing(sqlite3.connect(path)) as connection, connection:
        assert connection.execute("SELECT * FROM original").fetchall() == [("preserve me",)]
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("original",)]


@pytest.mark.parametrize("mutation", [
    "UPDATE schema_migrations SET revision=999 WHERE revision=1",
    "UPDATE schema_migrations SET name='different_history'",
    "UPDATE schema_migrations SET checksum='tampered'",
    "DELETE FROM schema_migrations",
    "DROP INDEX ix_simulations_created_id",
    "ALTER TABLE scenarios ADD COLUMN unexpected TEXT",
    "DROP TABLE failures",
    "DROP INDEX ix_simulation_jobs_simulation_id",
])
def test_refuses_unknown_history_and_incompatible_schema(tmp_path, mutation):
    path = tmp_path / "runs.db"
    database = file_database(path)
    database.initialize()
    database.dispose()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(mutation)
    with pytest.raises(DatabaseInitializationError):
        file_database(path).initialize()


def test_refuses_same_column_names_with_missing_constraints(tmp_path):
    path = tmp_path / "runs.db"
    database = file_database(path)
    database.initialize()
    database.dispose()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DROP TABLE failures")
        connection.execute("CREATE TABLE failures (id TEXT,simulation_id TEXT,payload TEXT,reproduced BOOLEAN,created_at TEXT)")
    with pytest.raises(DatabaseInitializationError):
        file_database(path).initialize()


def test_failed_initial_migration_rolls_back_ddl_and_history(tmp_path, monkeypatch):
    import backend.migrations as migrations

    path = tmp_path / "runs.db"
    monkeypatch.setattr(migrations, "_INITIAL_DDL", migrations._INITIAL_DDL[:2] + ("THIS IS INVALID SQL",))
    with pytest.raises(DatabaseInitializationError, match="migration"):
        file_database(path).initialize()
    with closing(sqlite3.connect(path)) as connection, connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


def test_revision_one_upgrades_without_rewriting_existing_records(tmp_path):
    import backend.migrations as migrations

    path = tmp_path / "revision-one.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE schema_migrations (revision INTEGER PRIMARY KEY NOT NULL,name TEXT NOT NULL,checksum TEXT NOT NULL,applied_at TEXT NOT NULL)")
        for statement in migrations._INITIAL_DDL:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES (1,'initial_persistence',?,'2026-01-01')", (migrations._INITIAL_CHECKSUM,))
    database = file_database(path)
    database.initialize()
    try:
        with database.engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT revision FROM schema_migrations ORDER BY revision").scalars().all() == [1, 2, 3, 4, 5, 6, 7]
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM simulation_jobs").scalar() == 0
    finally:
        database.dispose()


def test_failed_upgrade_rolls_back_only_new_migration(tmp_path, monkeypatch):
    import backend.migrations as migrations

    path = tmp_path / "revision-one.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE schema_migrations (revision INTEGER PRIMARY KEY NOT NULL,name TEXT NOT NULL,checksum TEXT NOT NULL,applied_at TEXT NOT NULL)")
        for statement in migrations._INITIAL_DDL:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES (1,'initial_persistence',?,'2026-01-01')", (migrations._INITIAL_CHECKSUM,))
    monkeypatch.setattr(migrations, "_JOBS_DDL", migrations._JOBS_DDL + ("INVALID MIGRATION SQL",))
    with pytest.raises(DatabaseInitializationError):
        file_database(path).initialize()
    with closing(sqlite3.connect(path)) as connection, connection:
        assert connection.execute("SELECT revision FROM schema_migrations").fetchall() == [(1,)]
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='simulation_jobs'").fetchall() == []


def test_foreign_keys_and_busy_timeout_are_enabled_for_every_connection(tmp_path):
    database = file_database(tmp_path / "runs.db")
    database.initialize()
    with database.engine.connect() as first, database.engine.connect() as second:
        for connection in (first, second):
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
    with pytest.raises(ConflictError):
        with database.session() as session:
            session.execute(text("""INSERT INTO failures(id,simulation_id,payload,created_at)
                                   VALUES (:id,:run,'{}','2026-01-01')"""),
                            {"id": str(uuid4()), "run": str(uuid4())})
    database.dispose()


@pytest.mark.parametrize("status,mode,revision", [("invented", "test", 1), ("queued", "unknown", 1), ("queued", "test", 0)])
def test_native_run_constraints_reject_invalid_projection_values(status, mode, revision):
    database = Database("sqlite:///:memory:")
    database.initialize()
    with pytest.raises(ConflictError):
        with database.session() as session:
            session.execute(text("""INSERT INTO simulations(id,payload,status,mode,revision,created_at,updated_at)
                VALUES (:id,'{}',:status,:mode,:revision,'2026-01-01','2026-01-01')"""),
                {"id": str(uuid4()), "status": status, "mode": mode, "revision": revision})
    database.dispose()
