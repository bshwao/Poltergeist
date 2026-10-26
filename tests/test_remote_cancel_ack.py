"""Cancellation acceptance is immutable; final outcomes are read separately."""

from uuid import uuid4
from contextlib import closing
import sqlite3
from threading import Event

import pytest

from backend.models import SimulationJobRecord, SimulationRecord
from backend.remote_receipts import RemoteReceiptRepository
from shared.models.remote import CancelCommand, ResultCommand
from test_remote_service import count, execute, harness, wait_for


@pytest.mark.parametrize("target", ["active", "queued"])
def test_cancel_ack_survives_offline_finalization_before_first_redelivery_and_restart(harness, target):
    binding, current, persistence, make, release = harness
    worker, service, entered = make(block=True)
    accepted = service.dispatch(binding, execute())
    assert entered.wait(5)
    if target == "queued":
        accepted = service.dispatch(binding, execute())
    command = CancelCommand(request_id=uuid4(), simulation_id=accepted.simulation_id)
    original = service.dispatch(binding, command)
    assert original.kind == "cancel_requested" and original.terminal is False
    # Lose this ACK, then let the work finalize while no replay is delivered.
    release.set()
    wait_for(lambda: worker.get(accepted.simulation_id).result)
    assert service.dispatch(binding, command) == original
    assert worker.close()
    restarted, service, _ = make()
    assert service.dispatch(binding, command) == original
    result = service.dispatch(binding, ResultCommand(request_id=uuid4(), simulation_id=accepted.simulation_id))
    assert result.summary.status == "cancelled" and result.summary.cancel_requested
    assert count(persistence, SimulationRecord) == count(persistence, SimulationJobRecord) == (2 if target == "queued" else 1)


def test_cancel_of_already_finalized_run_preserves_terminal_ack_after_restart(harness):
    binding, current, persistence, make, release = harness
    worker, service, _ = make()
    accepted = service.dispatch(binding, execute())
    wait_for(lambda: worker.get(accepted.simulation_id).result)
    command = CancelCommand(request_id=uuid4(), simulation_id=accepted.simulation_id)
    original = service.dispatch(binding, command)
    assert original.kind == "cancel_requested" and original.terminal is True
    assert worker.close()
    restarted, service, _ = make()
    assert service.dispatch(binding, command) == original
    result = service.dispatch(binding, ResultCommand(request_id=uuid4(), simulation_id=accepted.simulation_id))
    assert result.summary.status == "completed" and not result.summary.cancel_requested


def test_legacy_unknown_cancel_ack_is_retained_without_inventing_terminal_or_resignalling(harness, monkeypatch):
    binding, current, persistence, make, release = harness
    worker, service, entered = make(block=True)
    execute_command = execute()
    accepted = service.dispatch(binding, execute_command)
    assert entered.wait(5)
    command = CancelCommand(request_id=uuid4(), simulation_id=accepted.simulation_id)
    original = service.dispatch(binding, command)
    release.set()
    wait_for(lambda: worker.get(accepted.simulation_id).result)
    assert worker.close()
    path = persistence.database.file_path
    persistence.database.dispose()
    # Recreate revision five's actual lack of the ACK snapshot, without changing
    # any existing acceptance identity, timestamp or payload field.
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("ALTER TABLE remote_command_receipts DROP COLUMN cancel_terminal")
        connection.execute("DELETE FROM schema_migrations WHERE revision=6")
        before = connection.execute("SELECT * FROM remote_command_receipts ORDER BY id").fetchall()
    restarted, service, _ = make()
    def unexpected_cancel(*args, **kwargs):
        raise AssertionError("A legacy replay must not signal cancellation again")
    monkeypatch.setattr(restarted, "cancel", unexpected_cancel)
    reply = service.dispatch(binding, command)
    assert reply.kind == "rejected" and reply.code == "device_unavailable"
    assert service.dispatch(binding, execute_command) == accepted
    result = service.dispatch(binding, ResultCommand(request_id=uuid4(), simulation_id=accepted.simulation_id))
    assert result.summary.status == "cancelled" and result.summary.cancel_requested
    with persistence.database.session(write=False) as session:
        unknown = RemoteReceiptRepository(session).lookup(binding, command.request_id)
        assert unknown.cancel_terminal is None
        assert unknown.accepted_at == original.accepted_at
    with closing(sqlite3.connect(path)) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(remote_command_receipts)") if row[1] != "cancel_terminal"]
        assert connection.execute("SELECT " + ",".join(columns) + " FROM remote_command_receipts ORDER BY id").fetchall() == before


def test_cancel_after_runner_finished_before_persistence_keeps_completed_outcome_and_original_ack(harness, monkeypatch):
    binding, current, persistence, make, release = harness
    at_finalization, allow_finalization = Event(), Event()
    persist = persistence.persist_output
    def held_finalization(*args, **kwargs):
        at_finalization.set()
        assert allow_finalization.wait(10), "The finalization fixture was not released"
        return persist(*args, **kwargs)
    monkeypatch.setattr(persistence, "persist_output", held_finalization)
    worker, service, _ = make()
    try:
        accepted = service.dispatch(binding, execute())
        assert at_finalization.wait(5)
        command = CancelCommand(request_id=uuid4(), simulation_id=accepted.simulation_id)
        original = service.dispatch(binding, command)
        assert original.kind == "cancel_requested" and original.terminal is False
        allow_finalization.set()
        wait_for(lambda: worker.get(accepted.simulation_id).result)
        assert service.dispatch(binding, command) == original
        result = service.dispatch(binding, ResultCommand(request_id=uuid4(), simulation_id=accepted.simulation_id))
        # A cancellation accepted after driving finished cannot retroactively
        # change a completed output into a successful cancellation.
        assert result.summary.status == "completed" and result.summary.cancel_requested
        assert count(persistence, SimulationRecord) == count(persistence, SimulationJobRecord) == 1
    finally:
        allow_finalization.set()
