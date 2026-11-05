"""Delivery transaction consolidation retains complete live authorization."""

import asyncio
import sqlite3
from uuid import uuid4

import pytest

from coordinator.auth import AuthError
from coordinator.relay import DeviceConnection, RelayHub
from shared.models.remote import StatusCommand, parse_message
from test_coordinator_relay import setup


MUTATIONS = ["DELETE FROM device_grants WHERE scope='run:cancel'",
    "UPDATE devices SET policy_json=json_set(policy_json,'$.max_ticks',1)"]


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_pending_delivery_checks_the_entire_connection_before_marking_work(setup, mutation):
    service, pairing, store, now, alice, bob, device, credential, identity = setup
    command = StatusCommand(request_id=uuid4())
    service.admit(alice, device, command, connected=True)
    with store.transaction() as connection:
        connection.execute(mutation)
    with pytest.raises(AuthError):
        service.pending_for_delivery(credential, identity)
    with store.transaction(write=False) as connection:
        row = connection.execute("SELECT status,delivered_at FROM commands").fetchone()
        assert tuple(row) == ("queued", None)


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_final_delivery_check_includes_other_grants_and_full_policy(setup, mutation):
    service, pairing, store, now, alice, bob, device, credential, identity = setup
    command = StatusCommand(request_id=uuid4())
    service.admit(alice, device, command, connected=True)
    assert service.pending_for_delivery(credential, identity) == [command]
    with store.transaction() as connection:
        connection.execute(mutation)
    with pytest.raises(AuthError):
        service.delivery_allowed(credential, identity, command.request_id)


def test_policy_is_rechecked_after_each_awaited_send(setup):
    service, pairing, store, now, alice, bob, device, credential, identity = setup
    requests = [StatusCommand(request_id=uuid4()) for _ in range(2)]
    for command in requests:
        service.admit(alice, device, command, connected=True)
    delivered = []

    async def run():
        hub = RelayHub(service)

        class Socket:
            async def send_text(self, text):
                delivered.append(parse_message(text).command.request_id)
                with store.transaction() as connection:
                    connection.execute(MUTATIONS[0])

        current = DeviceConnection(Socket(), credential, identity, ready=True, enabled=True,
            last_received=hub.monotonic())
        current.wake.set()
        with pytest.raises(AuthError):
            await hub._deliver(current)

    asyncio.run(run())
    assert len(delivered) == 1


@pytest.mark.parametrize("fault, error, message", [
    ("revocation", AuthError, "device_authentication_required"),
    ("reconnect", AuthError, "device_connection_fenced"),
    ("expiry", TimeoutError, "heartbeat expired"),
])
def test_pending_socket_send_cannot_cross_a_connection_fault(setup, fault, error, message):
    service, pairing, store, now, alice, bob, device, credential, identity = setup
    command = StatusCommand(request_id=uuid4())
    service.admit(alice, device, command, connected=True)

    async def run():
        clock = [0.]
        hub = RelayHub(service, monotonic=lambda: clock[0])
        send_called = asyncio.Event()

        class Socket:
            async def send_text(self, text):
                pytest.fail("The old connection sent work after the fault")

        current = DeviceConnection(Socket(), credential, identity, ready=True, enabled=True)
        send = current.send

        async def observe_send(*args, **kwargs):
            send_called.set()
            await send(*args, **kwargs)

        current.send = observe_send
        current.wake.set()
        async with current.send_lock:
            delivery = asyncio.create_task(hub._deliver(current))
            await asyncio.wait_for(send_called.wait(), timeout=2)
            if fault == "revocation":
                pairing.revoke(alice, device)
            elif fault == "reconnect":
                replacement = service.connect(credential)
            else:
                clock[0] = 90.
        with pytest.raises(error, match=message):
            await asyncio.wait_for(delivery, timeout=2)
        assert not current.sent
        if fault == "reconnect":
            assert service.pending_for_delivery(credential, replacement) == [command]

    asyncio.run(run())


def test_failed_pending_commit_cannot_return_work_and_rolls_back_delivery_time(setup, monkeypatch):
    service, pairing, store, now, alice, bob, device, credential, identity = setup
    command = StatusCommand(request_id=uuid4())
    service.admit(alice, device, command, connected=True)
    anchor = store._anchor

    class FailedCommit:
        fail = True

        def __getattr__(self, name):
            return getattr(anchor, name)

        def commit(self):
            if self.fail:
                self.fail = False
                raise sqlite3.OperationalError("fixture commit failure")
            return anchor.commit()

    monkeypatch.setattr(store, "_anchor", FailedCommit())
    with pytest.raises(sqlite3.OperationalError):
        service.pending_for_delivery(credential, identity)
    assert not anchor.in_transaction
    with store.transaction(write=False) as connection:
        assert connection.execute("SELECT delivered_at FROM commands").fetchone()[0] is None
