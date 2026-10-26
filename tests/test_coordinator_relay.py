"""Coordinator relay ownership, durable retries, fencing and browser isolation."""

from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sqlite3
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from coordinator.app import COOKIE, create_app
from coordinator.auth import AuthError, AuthService
from coordinator.commands import CommandService
from coordinator.pairing import PairingService
from coordinator.settings import CoordinatorSettings
from coordinator.store import APPLICATION_ID, SCHEMA, Store, StoreError
from shared.models.coordination import PairingCreateInput, PairingApproveInput
from shared.models.remote import (CancelCommand, ClientReady, CommandRejected, DeviceStatusReply,
    ExecuteAccepted, ExecuteCommand, RelayReply, ResultCommand, RunResultReply, StatusCommand,
    canonical_json, parse_message, payload_digest)

ORIGIN = "https://coordinator.test"
SCOPES = ["status:read", "run:execute:test", "run:cancel", "result:read"]


def enroll(pairing, owner, scopes=SCOPES):
    secret = "pa_" + secrets.token_urlsafe(32)
    created = pairing.create(PairingCreateInput(private_secret=secret, display_name="Fixture companion", requested_scopes=scopes), source="fixture")
    claimed = pairing.claim(owner, created.code, source="fixture")
    credential = "pd_" + secrets.token_urlsafe(32)
    device = pairing.approve(created.attempt_id, secret, PairingApproveInput(request_id=uuid4(), expected_account_id=owner,
        expected_revision=claimed.revision, credential=credential, policy={"scopes": scopes}))
    return device, credential


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "coordinator.db")
    store.initialize()
    now = [1800000000.]
    auth = AuthService(store, now=lambda: now[0])
    alice = auth.register("alice", "a long private fixture passphrase")["account_id"]
    bob = auth.register("bob", "another private fixture passphrase")["account_id"]
    pairing = PairingService(store, auth, now=lambda: now[0])
    device, credential = enroll(pairing, alice)
    service = CommandService(store, now=lambda: now[0])
    identity = service.connect(credential)
    yield service, pairing, store, now, alice, bob, device.device_id, credential, identity
    store.close()


def execute(**updates):
    data = {"request_id": uuid4(), "scenario": {"name": "Owner experiment", "duration_s": 1.,
        "actors": [{"type": "ego_vehicle"}]}, "mode": "test"}
    data.update(updates)
    return ExecuteCommand.model_validate(data)


def accepted(command, simulation_id=None):
    return ExecuteAccepted(request_id=command.request_id, simulation_id=simulation_id or uuid4(),
        accepted_at=datetime.fromtimestamp(1800000000., timezone.utc))


def test_schema_one_upgrades_atomically_and_drift_is_rejected(tmp_path):
    target = tmp_path / "legacy.db"
    connection = sqlite3.connect(target)
    connection.executescript(SCHEMA)
    connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
    connection.execute("INSERT INTO accounts(id,username,password_hash,created_at) VALUES ('a','alice','opaque',1)")
    connection.commit()
    connection.close()
    store = Store(target)
    store.initialize()
    with store.transaction(write=False) as connection:
        assert connection.execute("SELECT version FROM coordinator_meta").fetchone()[0] == 2
        assert connection.execute("SELECT password_hash FROM accounts").fetchone()[0] == "opaque"
    store.close()
    connection = sqlite3.connect(target)
    connection.execute("CREATE INDEX unexpected_index ON commands(status)")
    connection.commit()
    connection.close()
    with pytest.raises(StoreError, match="Unsupported"):
        Store(target).initialize()


def test_owner_scope_canonical_retry_and_restart_are_durable(setup):
    service, pairing, store, now, alice, bob, device, credential, identity = setup
    command = execute()
    with pytest.raises(AuthError) as error:
        service.admit(bob, device, command, connected=True)
    assert error.value.status == 404
    first = service.admit(alice, device, command, connected=True)
    assert first.status == "queued"
    assert service.admit(alice, device, command, connected=False) == first
    with pytest.raises(AuthError) as error:
        service.admit(alice, device, command.model_copy(update={"mode": "carla"}), connected=True)
    assert error.value.status == 403
    changed = execute(request_id=command.request_id, scenario={"name": "Changed", "actors": [{"type": "ego_vehicle"}]})
    with pytest.raises(AuthError) as error:
        service.admit(alice, device, changed, connected=True)
    assert error.value.status == 409
    reply = accepted(command)
    final = service.reply(credential, identity, reply)
    assert final.status == "accepted" and final.reply == reply
    restarted = CommandService(store, now=lambda: now[0])
    assert restarted.admit(alice, device, command, connected=False) == final
    assert restarted.reply(credential, identity, reply) == final
    assert "scenario" not in canonical_json(final) and "Owner experiment" not in canonical_json(final)
    with pytest.raises(AuthError):
        restarted.get(bob, device, command.request_id)
    with store.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM commands").fetchone()[0] == 1


def test_queue_bound_offline_and_scoped_local_run_denials(setup):
    service, pairing, store, now, alice, bob, device, credential, identity = setup
    with pytest.raises(AuthError) as error:
        service.admit(alice, device, execute(), connected=False)
    assert error.value.code == "device_unavailable"
    with pytest.raises(AuthError) as error:
        service.admit(alice, device, execute(options={"max_ticks": 1}), connected=True)
    assert error.value.code == "policy_denied"
    for kind in (CancelCommand, ResultCommand):
        with pytest.raises(AuthError) as error:
            service.admit(alice, device, kind(request_id=uuid4(), simulation_id=uuid4()), connected=True)
        assert error.value.status == 404
    commands = [StatusCommand(request_id=uuid4()) for _ in range(32)]
    for command in commands:
        service.admit(alice, device, command, connected=True)
    assert service.admit(alice, device, commands[0], connected=True).status == "queued"
    with pytest.raises(AuthError) as error:
        service.admit(alice, device, StatusCommand(request_id=uuid4()), connected=True)
    assert error.value.status == 429
    assert len(service.pending(credential, identity)) == 32


def test_grants_rechecked_for_delivery_read_reply_and_revocation(setup):
    service, pairing, store, now, alice, bob, device, credential, identity = setup
    command = execute()
    service.admit(alice, device, command, connected=True)
    with store.transaction() as connection:
        connection.execute("DELETE FROM device_grants WHERE device_id=? AND scope='run:execute:test'", (str(device),))
    with pytest.raises(AuthError) as error:
        service.get(alice, device, command.request_id)
    assert error.value.status == 403
    with pytest.raises(AuthError):
        service.reply(credential, identity, accepted(command))
    assert service.pending(credential, identity) == []
    with store.transaction(write=False) as connection:
        assert connection.execute("SELECT status FROM commands").fetchone()[0] == "rejected"
    pairing.revoke(alice, device)
    with pytest.raises(AuthError):
        service.authenticate(credential, identity)
    with pytest.raises(AuthError):
        service.connect(credential)


def test_connection_generation_fences_old_socket_and_queued_delivery_replays(setup):
    service, pairing, store, now, alice, bob, device, credential, identity = setup
    command = execute()
    service.admit(alice, device, command, connected=True)
    assert service.pending(credential, identity) == [command]
    newer = service.connect(credential)
    assert newer["connection_generation"] == identity["connection_generation"] + 1
    with pytest.raises(AuthError) as error:
        service.reply(credential, identity, accepted(command))
    assert error.value.code == "device_connection_fenced"
    assert service.pending(credential, newer) == [command]


def queued_summary(command, reply, device):
    when = datetime.fromtimestamp(1800000000., timezone.utc).isoformat()
    return {"device_id": str(device), "ownership_generation": 1, "execute_request_id": str(command.request_id),
        "receipt_revision": 1, "revision": 1, "simulation_id": str(reply.simulation_id), "mode": "test", "status": "queued",
        "progress": 0., "cancel_requested": False, "queue_position": 1, "created_at": when, "started_at": None,
        "finished_at": None, "updated_at": when, "scenario": {"name": command.scenario.name, "sha256": payload_digest(command.scenario)},
        "result_available": False, "measured_duration_s": None, "metrics": None, "runtime": None, "provenance": None,
        "error": None, "evidence": {"state": "missing", "telemetry_frames": None, "artifact_reference_count": None}}


def test_results_match_accepted_binding_scenario_and_monotonic_projection(setup):
    service, pairing, store, now, alice, bob, device, credential, identity = setup
    command = execute()
    service.admit(alice, device, command, connected=True)
    acceptance = accepted(command)
    service.reply(credential, identity, acceptance)
    result = ResultCommand(request_id=uuid4(), simulation_id=acceptance.simulation_id)
    service.admit(alice, device, result, connected=True)
    summary = queued_summary(command, acceptance, device)
    def response(data, request=result):
        return RunResultReply.model_validate_json(json.dumps({"request_id": str(request.request_id), "summary": data}))
    with pytest.raises(AuthError):
        service.reply(credential, identity, response(summary | {"device_id": str(uuid4())}))
    with pytest.raises(AuthError):
        service.reply(credential, identity, response(summary | {"scenario": {"name": "Wrong", "sha256": "0" * 64}}))
    assert service.reply(credential, identity, response(summary)).status == "completed"
    newer = result.model_copy(update={"request_id": uuid4()})
    service.admit(alice, device, newer, connected=True)
    summary["receipt_revision"] = 2
    service.reply(credential, identity, response(summary, newer))
    stale = result.model_copy(update={"request_id": uuid4()})
    service.admit(alice, device, stale, connected=True)
    summary["receipt_revision"] = 1
    with pytest.raises(AuthError) as error:
        service.reply(credential, identity, response(summary, stale))
    assert error.value.code == "stale_projection"
    with store.transaction() as connection:
        connection.execute("DELETE FROM device_grants WHERE device_id=? AND scope='result:read'", (str(device),))
    with pytest.raises(AuthError) as error:
        service.get(alice, device, result.request_id)
    assert error.value.status == 403


@pytest.fixture
def web(tmp_path):
    app = create_app(CoordinatorSettings(tmp_path / "coordinator.db", ORIGIN), now=lambda: 1800000000.)
    with TestClient(app, base_url=ORIGIN) as client:
        alice = app.state.auth.register("alice", "private fixture password long")["account_id"]
        bob = app.state.auth.register("bob", "other fixture password long")["account_id"]
        login = app.state.auth.login("alice", "private fixture password long")
        other = app.state.auth.login("bob", "other fixture password long")
        def headers(value):
            return {"Origin": ORIGIN, "X-Poltergeist-Client": "browser", "X-CSRF-Token": value["csrf_token"],
                "Cookie": f"{COOKIE}={value['session_token']}"}
        device, credential = enroll(app.state.pairing, alice)
        yield client, app, device.device_id, credential, headers(login), headers(other)


def ready(socket, hello, *, enabled=True):
    envelope = {key: hello[key] for key in ("device_id", "ownership_generation", "connection_generation")}
    socket.send_text(canonical_json(ClientReady(**envelope, remote_execution_enabled=enabled)))
    return envelope


def await_available(app, device, expected=True):
    deadline = time.monotonic() + 3
    while app.state.relay.available(device) != expected and time.monotonic() < deadline:
        time.sleep(.005)
    assert app.state.relay.available(device) == expected


def test_websocket_ready_gate_owner_http_reply_and_revoke_close(web):
    client, app, device, credential, alice, bob = web
    command = execute()
    url = f"/v1/devices/{device}/commands"
    with client.websocket_connect("wss://coordinator.test/v1/device/connect", headers={"Authorization": "Bearer " + credential}) as socket:
        hello = socket.receive_json()
        assert hello["kind"] == "hello" and credential not in json.dumps(hello)
        assert client.post(url, content=canonical_json(command), headers={**alice, "Content-Type": "application/json"}).status_code == 503
        envelope = ready(socket, hello)
        await_available(app, device)
        assert client.post(url, json=command.model_dump(mode="json"), headers=bob).status_code == 404
        response = client.post(url, json=command.model_dump(mode="json"), headers=alice)
        assert response.status_code == 202, response.text
        delivered = parse_message(socket.receive_text())
        assert delivered.command == command
        reply = accepted(command)
        socket.send_text(canonical_json(RelayReply(**envelope, reply=reply)))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            read = client.get(url + "/" + str(command.request_id), headers=alice)
            if read.json()["status"] == "accepted":
                break
        assert read.json()["simulation_id"] == str(reply.simulation_id)
        assert client.get(url + "/" + str(command.request_id), headers=bob).status_code == 404
        assert client.post(f"/v1/devices/{device}/revoke", json={}, headers=alice).status_code == 200
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
    assert client.get(url + "/" + str(command.request_id), headers=alice).status_code == 404
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("wss://coordinator.test/v1/device/connect", headers={"Authorization": "Bearer " + credential}):
            pass


def test_disabled_ready_never_admits_and_duplicate_connection_fences(web):
    client, app, device, credential, alice, bob = web
    auth = {"Authorization": "Bearer " + credential}
    with client.websocket_connect("wss://coordinator.test/v1/device/connect", headers=auth) as first:
        old = first.receive_json()
        ready(first, old, enabled=False)
        assert client.post(f"/v1/devices/{device}/commands", json=execute().model_dump(mode="json"), headers=alice).status_code == 503
        with client.websocket_connect("wss://coordinator.test/v1/device/connect", headers=auth) as second:
            current = second.receive_json()
            assert current["connection_generation"] == old["connection_generation"] + 1
            with pytest.raises(WebSocketDisconnect):
                first.receive_json()
            ready(second, current)
            await_available(app, device)


@pytest.mark.parametrize("suffix,headers,subprotocols", [
    ("?credential=pd_secret", {}, None), ("", {"Origin": ORIGIN}, None), ("", {"Cookie": "private=secret"}, None),
    ("", {"Host": "wrong.test"}, None), ("", {}, ["pd_secret"]), ("", {"Authorization": "Bearer invalid"}, None),
])
def test_websocket_rejects_browser_url_subprotocol_and_invalid_host(web, suffix, headers, subprotocols):
    client, app, device, credential, alice, bob = web
    supplied = {"Authorization": "Bearer " + credential, **headers}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("wss://coordinator.test/v1/device/connect" + suffix, headers=supplied, subprotocols=subprotocols):
            pass


@pytest.mark.parametrize("payload", ['{"kind":"ready","kind":"ready"}', "{}", "NaN", "x" * 262145],
    ids=["duplicate", "empty", "nonfinite", "oversized"])
def test_websocket_malformed_oversized_message_closes_without_admission(web, payload):
    client, app, device, credential, alice, bob = web
    with client.websocket_connect("wss://coordinator.test/v1/device/connect", headers={"Authorization": "Bearer " + credential}) as socket:
        socket.receive_json()
        socket.send_text(payload)
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
    with app.state.store.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM commands").fetchone()[0] == 0


def test_websocket_disconnect_replays_same_durable_request(web):
    client, app, device, credential, alice, bob = web
    command = execute()
    auth = {"Authorization": "Bearer " + credential}
    with client.websocket_connect("wss://coordinator.test/v1/device/connect", headers=auth) as socket:
        first = socket.receive_json()
        ready(socket, first)
        await_available(app, device)
        assert client.post(f"/v1/devices/{device}/commands", json=command.model_dump(mode="json"), headers=alice).status_code == 202
        assert parse_message(socket.receive_text()).command == command
        # Deliberately lose the acknowledgement after local acceptance would be
        # possible. The coordinator reuses this exact durable command on retry.
    with client.websocket_connect("wss://coordinator.test/v1/device/connect", headers=auth) as socket:
        newer = socket.receive_json()
        ready(socket, newer)
        assert parse_message(socket.receive_text()).command == command
    with app.state.store.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM commands").fetchone()[0] == 1


def test_heartbeat_requires_current_grants_and_expires_without_pong():
    import asyncio
    from types import SimpleNamespace
    from coordinator.relay import RelayHub
    calls, clock, sent = [], [0.], []
    class Commands:
        def connection_current(self, credential, identity):
            calls.append(clock[0])
    async def sleep(duration):
        assert duration == 30
        clock[0] += duration
        # Even application traffic cannot satisfy an unanswered server ping.
        current.last_received = clock[0]
    async def send(message):
        sent.append(message)
    current = SimpleNamespace(credential="not-logged", identity={}, last_received=0., ping=None, send=send,
        envelope=lambda: {"device_id": uuid4(), "ownership_generation": 1, "connection_generation": 1})
    hub = RelayHub(Commands(), monotonic=lambda: clock[0], sleep=sleep)
    with pytest.raises(TimeoutError, match="heartbeat expired"):
        asyncio.run(hub._heartbeat(current))
    assert calls == [30., 60., 90.]
    assert len(sent) == 1 and sent[0].kind == "ping"


def test_server_heartbeats_do_not_manufacture_device_last_seen_activity(setup):
    import asyncio
    from types import SimpleNamespace
    from coordinator.relay import RelayHub
    service, pairing, store, now, owner, bob, device, credential, identity = setup
    clock, sent = [0.], []
    before = service.last_seen(owner, device)
    async def sleep(duration):
        clock[0] += duration
        now[0] += duration
    async def send(message):
        sent.append(message)
    current = SimpleNamespace(credential=credential, identity=identity, last_received=0., ping=None, send=send,
        envelope=lambda: {"device_id": device, "ownership_generation": 1,
                          "connection_generation": identity["connection_generation"]})
    hub = RelayHub(service, monotonic=lambda: clock[0], sleep=sleep)
    with pytest.raises(TimeoutError, match="heartbeat expired"):
        asyncio.run(hub._heartbeat(current))
    assert service.last_seen(owner, device) == before
    assert len(sent) == 1


def test_duplicate_websocket_authorization_is_rejected_before_authentication(web):
    client, app, device, credential, alice, bob = web
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("wss://coordinator.test/v1/device/connect",
                headers={"Authorization": "Bearer " + credential, "authorization": "Bearer " + credential}):
            pass
    with app.state.store.transaction(write=False) as connection:
        assert connection.execute("SELECT connection_generation FROM devices").fetchone()[0] == 0


def test_corrupt_stored_command_returns_fixed_failure_without_payload(web):
    client, app, device, credential, alice, bob = web
    command = execute()
    app.state.commands.admit(app.state.pairing.authenticate_device(credential)["owner_id"], device, command, connected=True)
    sentinel = "C:/private/secret-token-pd_redaction-fixture"
    with app.state.store.transaction() as connection:
        connection.execute("UPDATE commands SET payload_json=?", (json.dumps({"secret": sentinel}),))
    response = client.get(f"/v1/devices/{device}/commands/{command.request_id}", headers=alice)
    assert response.status_code == 503
    assert sentinel not in response.text and "pd_" not in response.text
