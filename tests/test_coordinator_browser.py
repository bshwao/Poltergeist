"""Website projections retain current ownership, scopes and real device activity."""

import json
import secrets
from time import monotonic, sleep
from uuid import uuid4

from shared.models.coordination import PairingCreateInput
from shared.models.remote import ExecuteCommand, ResultCommand, StatusCommand
from scenarios.stress_presets import STRESS_PRESETS
from test_coordinator_relay import web, ready, accepted, execute


def devices(client, headers):
    response = client.get("/v1/browser/devices", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_device_list_is_owner_scoped_and_never_claims_carla_readiness(web):
    client, app, device, credential, alice, bob = web
    assert client.get("/v1/browser/devices").status_code == 403
    assert devices(client, bob) == []
    value = devices(client, alice)[0]
    assert value["device"]["device_id"] == str(device)
    assert value["connection"] == {"state": "offline", "last_seen_at": None,
        "protocol_version": None, "protocol_compatible": None, "remote_requests_enabled": False,
        "baseline_only": True, "carla_readiness": "unknown", "evidence_supported": False, "recordings_supported": False}
    assert credential not in json.dumps(value) and "token_hash" not in json.dumps(value)


def test_paused_live_revoked_and_expired_connections_are_truthful_without_fake_activity(web):
    client, app, device, credential, alice, bob = web
    with client.websocket_connect("wss://coordinator.test/v1/device/connect",
            headers={"Authorization": "Bearer " + credential}) as socket:
        hello = socket.receive_json()
        assert devices(client, alice)[0]["connection"]["state"] == "connecting"
        ready(socket, hello, enabled=False)
        deadline = monotonic() + 5
        while (state := devices(client, alice)[0]["connection"])["state"] != "paused" and monotonic() < deadline:
            sleep(.01)
        assert state["state"] == "paused" and state["protocol_compatible"] is True
        with app.state.store.transaction(write=False) as connection:
            before = connection.execute("SELECT last_seen_at FROM devices WHERE id=?", (str(device),)).fetchone()[0]
        for _ in range(3):
            devices(client, alice)
        with app.state.store.transaction() as connection:
            assert connection.execute("SELECT last_seen_at FROM devices WHERE id=?", (str(device),)).fetchone()[0] == before
            connection.execute("UPDATE device_credentials SET expires_at=0 WHERE device_id=?", (str(device),))
        assert devices(client, alice)[0]["connection"]["state"] == "offline"
        assert not app.state.relay.available(device)
    app.state.pairing.revoke(hello["owner_id"], device)
    assert devices(client, alice)[0]["connection"]["state"] == "revoked"


def test_current_connection_rechecks_grants_before_admission(web):
    client, app, device, credential, alice, bob = web
    with client.websocket_connect("wss://coordinator.test/v1/device/connect",
            headers={"Authorization": "Bearer " + credential}) as socket:
        hello = socket.receive_json()
        ready(socket, hello)
        deadline = monotonic() + 5
        while not app.state.relay.available(device) and monotonic() < deadline:
            sleep(.01)
        assert devices(client, alice)[0]["connection"]["state"] == "connected"
        with app.state.store.transaction() as connection:
            connection.execute("DELETE FROM device_grants WHERE device_id=? AND scope='result:read'", (str(device),))
        assert devices(client, alice)[0]["connection"]["state"] == "offline"
        assert not app.state.relay.available(device)


def test_claim_status_requires_claimant_session_and_reveals_no_secret(web):
    client, app, device, credential, alice, bob = web
    secret = "pa_" + secrets.token_urlsafe(32)
    created = app.state.pairing.create(PairingCreateInput(private_secret=secret,
        display_name="New computer", requested_scopes=["status:read"]), source="fixture")
    response = client.post("/v1/pairing/claim", json={"code": created.code}, headers=alice)
    assert response.status_code == 200
    url = f"/v1/browser/claims/{created.attempt_id}"
    assert client.get(url, headers=bob).status_code == 404
    result = client.get(url, headers=alice)
    assert result.status_code == 200 and result.json()["state"] == "claimed"
    assert secret not in result.text and created.code not in result.text and credential not in result.text
    app.state.pairing.deny(created.attempt_id, secret, result.json()["revision"])
    assert client.get(url, headers=alice).json()["state"] == "denied"


def test_history_paginates_equal_timestamps_without_payload_echo_or_cross_owner_access(web):
    client, app, device, credential, alice, bob = web
    owner = app.state.pairing.authenticate_device(credential)["owner_id"]
    commands = [execute() for _ in range(4)]
    for command in commands:
        app.state.commands.admit(owner, device, command, connected=True)
    url, seen, before = f"/v1/devices/{device}/commands", [], None
    for _ in commands:
        response = client.get(url, params={"limit": 1, **({"before": before} if before else {})}, headers=alice)
        assert response.status_code == 200, response.text
        page = response.json()
        assert "scenario" not in response.text and "payload_json" not in response.text
        seen.extend(row["request_id"] for row in page["items"])
        before = page["next_before"]
    assert before is None and set(seen) == {str(command.request_id) for command in commands}
    assert len(seen) == len(set(seen))
    assert client.get(url, headers=bob).status_code == 404
    assert client.get(url, params={"before": str(uuid4())}, headers=alice).status_code == 404
    assert client.get(url, params={"limit": 51}, headers=alice).status_code == 400


def test_history_applies_current_result_scope_before_reading_reply(web):
    client, app, device, credential, alice, bob = web
    owner = app.state.pairing.authenticate_device(credential)["owner_id"]
    identity = app.state.commands.connect(credential)
    command = execute()
    app.state.commands.admit(owner, device, command, connected=True)
    acceptance = accepted(command)
    app.state.commands.reply(credential, identity, acceptance)
    result = ResultCommand(request_id=uuid4(), simulation_id=acceptance.simulation_id)
    app.state.commands.admit(owner, device, result, connected=True)
    with app.state.store.transaction() as connection:
        connection.execute("UPDATE commands SET reply_json=? WHERE request_id=?",
            ('{"private":"private_browser_history_sentinel"}', str(result.request_id)))
        connection.execute("DELETE FROM device_grants WHERE device_id=? AND scope='result:read'", (str(device),))
    response = client.get(f"/v1/devices/{device}/commands", headers=alice)
    assert response.status_code == 200, response.text
    assert [item["request_id"] for item in response.json()["items"]] == [str(command.request_id)]
    assert str(result.request_id) not in response.text and "private_browser_history_sentinel" not in response.text
    assert client.get(f"/v1/devices/{device}/commands", params={"before": str(result.request_id)}, headers=alice).status_code == 404


def test_browser_catalogue_requires_a_session_and_preserves_calibrated_recipes(web):
    client, app, device, credential, alice, bob = web
    assert client.get("/v1/browser/scenarios").status_code == 403
    response = client.get("/v1/browser/scenarios", headers=alice)
    assert response.status_code == 200
    presets = response.json()
    assert len(presets) == 13 and len({preset["id"] for preset in presets}) == 13
    assert presets[0]["recipe"]["duration_s"] == .5
    lead = next(actor for actor in presets[0]["recipe"]["actors"] if actor["id"] == "lead")
    assert lead["spawn"] is None and lead["relative_spawn"]["anchor_actor_id"] == "ego"
    assert {preset["id"]: preset["recipe"] for preset in presets[1:]} == {
        preset.id: preset.scenario().model_dump(mode="json") for preset in STRESS_PRESETS}


def test_history_filters_execution_scope_and_generation_before_pagination(web):
    client, app, device, credential, alice, bob = web
    owner = app.state.pairing.authenticate_device(credential)["owner_id"]
    execution, status = execute(), StatusCommand(request_id=uuid4())
    for command in (execution, status):
        app.state.commands.admit(owner, device, command, connected=True)
    url = f"/v1/devices/{device}/commands"
    with app.state.store.transaction() as connection:
        connection.execute("DELETE FROM device_grants WHERE device_id=? AND scope='run:execute:test'", (str(device),))
    response = client.get(url, headers=alice)
    assert response.status_code == 200
    assert [row["request_id"] for row in response.json()["items"]] == [str(status.request_id)]
    assert client.get(url, params={"before": str(execution.request_id)}, headers=alice).status_code == 404
    with app.state.store.transaction() as connection:
        connection.execute("UPDATE commands SET ownership_generation=ownership_generation+1 WHERE request_id=?", (str(status.request_id),))
    assert client.get(url, headers=alice).json() == {"items": [], "next_before": None}
    assert client.get(url, params={"before": str(status.request_id)}, headers=alice).status_code == 404
