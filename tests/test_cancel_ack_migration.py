"""Revision six preserves unknown historical ACKs and published payload bytes."""

from contextlib import closing
import sqlite3
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.database import Database
from backend.models import RemoteCommandReceiptRecord
from backend.persistence_errors import ConflictError, DatabaseInitializationError, PersistenceError
from backend.remote_receipts import RemoteReceiptRepository
from test_remote_receipts import PAYLOAD, binding, database, execute, job, revision_four


def revision_five(path):
    import backend.migrations as migrations
    revision_four(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        for statement in migrations._REMOTE_RECEIPTS_DDL:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES (5,'remote_command_receipts',?,'2026-01-01')", (migrations._REMOTE_RECEIPTS_CHECKSUM,))
        run = connection.execute("SELECT id FROM simulations LIMIT 1").fetchone()[0]
        device, owner = str(uuid4()), str(uuid4())
        for operation in ("execute", "cancel"):
            connection.execute("INSERT INTO remote_command_receipts "
                "(origin,device_id,owner_id,generation,request_id,operation,payload_sha256,scenario_sha256,simulation_id,accepted_at,revision) "
                "VALUES ('https://coordinator.example',?,?,1,?,?,?,?,?,'2026-01-01T00:00:00+00:00',1)",
                (device, owner, str(uuid4()), operation, PAYLOAD, PAYLOAD if operation == "execute" else None, run))
        connection.execute("INSERT INTO remote_run_projections VALUES ('https://coordinator.example',?,1,?,?,3,?)", (device, run, owner, PAYLOAD))
        names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {name: {"columns": [row[1] for row in connection.execute(f"PRAGMA table_info({name})")],
                      "rows": connection.execute(f"SELECT * FROM {name}").fetchall()} for name in names}


def test_revision_six_preserves_legacy_payloads_and_unknown_terminal_snapshot(tmp_path):
    path = tmp_path / "legacy-five.db"
    before = revision_five(path)
    current = Database(f"sqlite:///{path.as_posix()}")
    try:
        current.initialize()
        current.dispose()
        current.initialize()
        with current.engine.connect() as connection:
            for name, snapshot in before.items():
                query = f"SELECT {','.join(snapshot['columns'])} FROM {name}" + (" WHERE revision<=5" if name == "schema_migrations" else "")
                assert [tuple(row) for row in connection.exec_driver_sql(query)] == snapshot["rows"]
            assert connection.exec_driver_sql("SELECT cancel_terminal FROM remote_command_receipts ORDER BY id").scalars().all() == [None, None]
            assert connection.exec_driver_sql("SELECT revision,name FROM schema_migrations WHERE revision=6").one() == (6, "cancellation_acceptance_snapshot")
    finally:
        current.dispose()


def test_failed_revision_six_rolls_back_column_constraint_and_history(tmp_path, monkeypatch):
    import backend.migrations as migrations
    path = tmp_path / "legacy-five.db"
    before = revision_five(path)
    monkeypatch.setattr(migrations, "_CANCEL_ACK_DDL", (*migrations._CANCEL_ACK_DDL, "INVALID MIGRATION SQL"))
    with pytest.raises(DatabaseInitializationError):
        Database(f"sqlite:///{path.as_posix()}").initialize()
    with closing(sqlite3.connect(path)) as connection:
        for name, snapshot in before.items():
            assert [row[1] for row in connection.execute(f"PRAGMA table_info({name})")] == snapshot["columns"]
            assert connection.execute(f"SELECT * FROM {name}").fetchall() == snapshot["rows"]


@pytest.mark.parametrize("value", [0, 1, 2, "false"])
def test_cancel_snapshot_rejects_coerced_non_boolean_values(database, binding, value):
    with database.session() as session:
        run = job(session)
        repository = RemoteReceiptRepository(session)
        execute(repository, binding, run.simulation.id)
        with pytest.raises(PersistenceError):
            repository.create(binding, uuid4(), "cancel", PAYLOAD, run.simulation.id, cancel_terminal=value)


@pytest.mark.parametrize("operation,value", [("execute", 0), ("cancel", 2)])
def test_native_constraint_rejects_invalid_snapshot_bindings(database, binding, operation, value):
    with database.session() as session:
        run = job(session)
        repository = RemoteReceiptRepository(session)
        receipt = execute(repository, binding, run.simulation.id)
        if operation == "cancel":
            receipt = repository.create(binding, uuid4(), "cancel", PAYLOAD, run.simulation.id)
    with pytest.raises(ConflictError):
        with database.session() as session:
            row = session.scalar(select(RemoteCommandReceiptRecord).where(RemoteCommandReceiptRecord.request_id == str(receipt.request_id)))
            row.cancel_terminal = value


def test_supplied_snapshot_cannot_predict_result_before_it_is_stored(database, binding):
    with database.session() as session:
        run = job(session)
        repository = RemoteReceiptRepository(session)
        execute(repository, binding, run.simulation.id)
        with pytest.raises(PersistenceError, match="current stored result"):
            repository.create(binding, uuid4(), "cancel", PAYLOAD, run.simulation.id, cancel_terminal=True)
