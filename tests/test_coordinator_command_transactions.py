"""Browser authentication, command receipts and activity commit atomically."""

import sqlite3
from uuid import uuid4

import pytest
from starlette.requests import Request

from coordinator.app import COOKIE
from coordinator.relay import DeviceConnection
from shared.models.remote import StatusCommand
from test_coordinator_relay import web


@pytest.fixture
def active(web):
    client, app, device, credential, alice, bob = web
    clock = [1800000005.]
    app.state.auth.now = app.state.commands.now = lambda: clock[0]
    identity = app.state.commands.connect(credential)
    relay = app.state.relay
    relay.connections[str(device)] = DeviceConnection(None, credential, identity,
        ready=True, enabled=True, last_received=relay.monotonic())
    return client, app, device, alice, clock


def saved(app):
    with app.state.store.transaction(write=False) as connection:
        return (connection.execute("SELECT last_used_at FROM sessions ORDER BY created_at,id").fetchall(),
                connection.execute("SELECT request_id FROM commands").fetchall())


def fail_next_commit(app, monkeypatch):
    anchor = app.state.store._anchor
    class Connection:
        pending = True
        def __getattr__(self, name):
            return getattr(anchor, name)
        def commit(self):
            if self.pending:
                self.pending = False
                raise sqlite3.OperationalError("private commit failure detail")
            return anchor.commit()
    monkeypatch.setattr(app.state.store, "_anchor", Connection())


def test_admission_commit_failure_rolls_back_session_and_receipt_before_notification(active, monkeypatch):
    client, app, device, headers, clock = active
    before, _ = saved(app)
    notifications = []
    monkeypatch.setattr(app.state.relay, "notify", notifications.append)
    command = StatusCommand(request_id=uuid4()).model_dump(mode="json")
    url = f"/v1/devices/{device}/commands"
    fail_next_commit(app, monkeypatch)
    failed = client.post(url, json=command, headers=headers)
    assert failed.status_code == 503 and "private commit" not in failed.text
    sessions, commands = saved(app)
    assert sessions == before and commands == [] and notifications == []
    assert not app.state.store._anchor.in_transaction
    # Retry the same identity; the failed transaction left no partial receipt.
    assert client.post(url, json=command, headers=headers).status_code == 202
    sessions, commands = saved(app)
    assert sorted(row[0] for row in sessions) == [1800000000., clock[0]]
    assert [row[0] for row in commands] == [command["request_id"]]
    assert notifications == [device]


def test_readback_commit_failure_rolls_back_activity_and_success_touches_session(active, monkeypatch):
    client, app, device, headers, clock = active
    command = StatusCommand(request_id=uuid4()).model_dump(mode="json")
    url = f"/v1/devices/{device}/commands"
    assert client.post(url, json=command, headers=headers).status_code == 202
    before = saved(app)
    clock[0] += 5
    fail_next_commit(app, monkeypatch)
    failed = client.get(url + "/" + command["request_id"], headers=headers)
    assert failed.status_code == 503 and "private commit" not in failed.text
    assert saved(app) == before
    assert not app.state.store._anchor.in_transaction
    assert client.get(url + "/" + command["request_id"], headers=headers).status_code == 200
    sessions, commands = saved(app)
    assert sorted(row[0] for row in sessions) == [1800000000., clock[0]]
    assert commands == before[1]


def test_body_await_precedes_transaction_and_authentication_precedes_command_parser(active, monkeypatch):
    client, app, device, headers, clock = active
    original = Request.body
    observed = []
    async def body(request):
        observed.append(app.state.store._anchor.in_transaction)
        return await original(request)
    monkeypatch.setattr(Request, "body", body)
    url = f"/v1/devices/{device}/commands"
    assert client.post(url, json=StatusCommand(request_id=uuid4()).model_dump(mode="json"), headers=headers).status_code == 202
    invalid = {**headers, "Cookie": f"{COOKIE}=invalid", "Content-Type": "application/json"}
    assert client.post(url, content=b"not json", headers=invalid).status_code == 401
    assert observed and not any(observed)


@pytest.mark.parametrize("mutation", [
    "UPDATE sessions SET revoked_at=1",
    "UPDATE accounts SET disabled_at=1",
    "UPDATE accounts SET auth_revision=auth_revision+1",
    "UPDATE sessions SET expires_at=1800000005",
    "UPDATE sessions SET last_used_at=1799998205",
])
def test_each_command_request_rechecks_session_revocation_revision_and_deadlines(active, mutation):
    client, app, device, headers, clock = active
    command = StatusCommand(request_id=uuid4()).model_dump(mode="json")
    url = f"/v1/devices/{device}/commands"
    assert client.post(url, json=command, headers=headers).status_code == 202
    with app.state.store.transaction() as connection:
        connection.execute(mutation)
    before = saved(app)
    assert client.post(url, json=command, headers=headers).status_code == 401
    assert client.get(url + "/" + command["request_id"], headers=headers).status_code == 401
    assert saved(app) == before


@pytest.mark.parametrize("mutation", [
    "DELETE FROM device_grants WHERE scope='run:cancel'",
    "UPDATE devices SET policy_json=json_set(policy_json,'$.max_ticks',1)",
])
def test_admission_still_rechecks_full_live_policy_and_grants(active, mutation):
    client, app, device, headers, clock = active
    with app.state.store.transaction() as connection:
        connection.execute(mutation)
    before = saved(app)
    response = client.post(f"/v1/devices/{device}/commands",
        json=StatusCommand(request_id=uuid4()).model_dump(mode="json"), headers=headers)
    assert response.status_code == 503 and response.json()["error"]["code"] == "device_unavailable"
    assert saved(app) == before
