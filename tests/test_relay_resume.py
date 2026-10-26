"""Deadline checks must survive scheduling gaps inside asynchronous receives."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from coordinator.relay import RelayHub
from shared.models.remote import ClientReady, ExecuteAccepted, RelayPong, RelayReply, StatusCommand, canonical_json


@pytest.mark.parametrize("kind", ["ready", "pong", "reply"])
def test_server_late_frame_does_not_authenticate_update_activity_or_clear_ping(kind):
    clock, authenticated, writes = [0.], [], []
    identity = {"id": str(uuid4()), "ownership_generation": 1, "connection_generation": 1}
    fields = dict(device_id=identity["id"], ownership_generation=1, connection_generation=1)
    nonce = uuid4()
    request = uuid4()
    if kind == "ready":
        message = ClientReady(**fields, remote_execution_enabled=True)
    elif kind == "pong":
        message = RelayPong(**fields, nonce=nonce)
    else:
        message = RelayReply(**fields, reply=ExecuteAccepted(request_id=request,
            simulation_id=uuid4(), accepted_at=datetime.now(UTC)))
    class Socket:
        calls = 0
        async def receive(self):
            self.calls += 1
            if self.calls > 1:
                return {"type": "websocket.disconnect"}
            clock[0] = 90.
            return {"type": "websocket.receive", "text": canonical_json(message)}
    commands = NS(authenticate=lambda *args: authenticated.append(clock[0]), reply=lambda *args: writes.append(True))
    current = NS(socket=Socket(), credential="private-fixture", identity=identity, last_received=0.,
        ready=kind != "ready", enabled=kind == "reply", sent={request}, ping=(nonce, 0.) if kind == "pong" else None,
        wake=asyncio.Event())
    hub = RelayHub(commands, monotonic=lambda: clock[0])
    with pytest.raises(TimeoutError, match="heartbeat expired"):
        asyncio.run(hub._receive(current))
    assert authenticated == [] and current.last_received == 0.
    assert writes == [] and current.sent == {request}
    assert current.enabled is (kind == "reply")
    if kind == "pong":
        assert current.ping == (nonce, 0.)


def test_server_does_not_deliver_queued_work_after_a_scheduling_gap():
    clock, touched = [90.], []
    class Wake:
        async def wait(self):
            if touched:
                raise AssertionError("Expired delivery loop continued")
        def clear(self): pass
    commands = NS(pending=lambda *args: touched.append("pending") or [],
                  connection_current=lambda *args: touched.append("auth"))
    current = NS(wake=Wake(), ready=True, enabled=True, last_received=0., ping=None,
                 credential="private-fixture", identity={})
    hub = RelayHub(commands, monotonic=lambda: clock[0])
    with pytest.raises(TimeoutError, match="heartbeat expired"):
        asyncio.run(hub._deliver(current))
    assert touched == []


def test_server_rechecks_deadline_after_pending_read_before_sending_a_command():
    clock, sent = [0.], []
    command = StatusCommand(request_id=uuid4())
    def pending(*args):
        clock[0] = 90.
        return [command]
    commands = NS(pending=pending, connection_current=lambda *args: None,
                  delivery_allowed=lambda *args: True)
    class Wake:
        async def wait(self): pass
        def clear(self): pass
    async def send(message):
        sent.append(message)
    current = NS(wake=Wake(), ready=True, enabled=True, last_received=0., ping=None, sent=set(),
        credential="private-fixture", identity={}, send=send)
    hub = RelayHub(commands, monotonic=lambda: clock[0])
    with pytest.raises(TimeoutError, match="heartbeat expired"):
        asyncio.run(hub._deliver(current))
    assert sent == [] and current.sent == set()


def test_an_overdue_ping_makes_availability_false_even_with_recent_other_frames():
    clock, touched = [120.], []
    hub = RelayHub(NS(connection_current=lambda *args: touched.append("auth")), monotonic=lambda: clock[0])
    hub.connections["device"] = NS(ready=True, enabled=True, last_received=119., ping=(uuid4(), 30.),
                                   credential="private-fixture", identity={})
    assert hub.available("device") is False
    assert touched == []
