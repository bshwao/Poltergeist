"""Two actual companions and accounts across HTTPS/WSS, including a lost ACK.

Windows uses current-user DPAPI. Other platforms use an explicit memory fixture;
they establish transport/queue behavior without claiming Windows protection.
"""

from contextlib import contextmanager
import copy
import json
import os
import socket
import ssl
from threading import Event, Thread
from time import monotonic
from uuid import UUID, uuid4

import httpx
import uvicorn

from backend.dependencies import ApiRuntime
from companion.controller import CompanionController
from companion.credential_store import WindowsCredentialStore
from companion.pairing_client import JsonTransport, PairingClient
from companion.relay_client import RelayClient
from coordinator.app import create_app
from coordinator.settings import CoordinatorSettings
from scenarios import validate_scenario
from shared.models.coordination import DevicePolicy
from shared.models.remote import (CancelCommand, ExecuteCommand, ResultCommand,
                                  canonical_json, parse_message)
from simulator import SimulationRunner
from simulator.test_adapter import TestSimulationAdapter
from test_relay_client import make_certificates

SCOPES = ["status:read", "run:execute:test", "run:cancel", "result:read"]


def wait_for(predicate, *, timeout=20):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        value = predicate()
        if value:
            return value
        Event().wait(.02)
    raise AssertionError("The hosted execution fixture did not reach its expected state")


class MemoryStore:
    def __init__(self):
        self.record = None
    def load(self):
        return copy.deepcopy(self.record)
    def save(self, value):
        self.record = copy.deepcopy(value)
    def clear(self):
        self.record = None


@contextmanager
def hosted_server(database, certificates, port=0, *, before_shutdown=None):
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    port = listener.getsockname()[1]
    origin = f"https://localhost:{port}"
    app = create_app(CoordinatorSettings(database, origin))
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False,
        proxy_headers=False, ssl_certfile=str(certificates["cert"]), ssl_keyfile=str(certificates["key"]),
        ws_max_size=262144, ws_ping_interval=None, lifespan="on", timeout_graceful_shutdown=5))
    thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        wait_for(lambda: server.started or (False if thread.is_alive() else (_ for _ in ()).throw(
            AssertionError("The fixture coordinator did not start"))))
        yield origin, app, port
    finally:
        try:
            if before_shutdown is not None:
                before_shutdown()
        finally:
            server.should_exit = True
            thread.join(15)
            listener.close()
            assert not thread.is_alive()


def login(client, username, origin):
    body = {"username": username, "password": "a private fixture account passphrase"}
    assert client.post("/v1/auth/register", json=body, headers={"Origin": origin}).status_code == 201
    response = client.post("/v1/auth/login", json=body, headers={"Origin": origin})
    assert response.status_code == 200
    return {"Origin": origin, "X-CSRF-Token": response.json()["csrf_token"],
            "X-Poltergeist-Client": "browser"}


def post_command(client, headers, device, command):
    return client.post(f"/v1/devices/{device}/commands", headers=headers,
                       content=canonical_json(command))


def complete_command(client, headers, device, command):
    response = post_command(client, headers, device, command)
    assert response.status_code == 202, response.text
    def done():
        result = client.get(f"/v1/devices/{device}/commands/{command.request_id}", headers=headers)
        assert result.status_code == 200
        body = result.json()
        return body if body["status"] != "queued" else None
    return wait_for(done)


def test_two_owned_companions_execute_read_cancel_revoke_and_restart_over_tls(tmp_path):
    certificates = make_certificates(tmp_path)
    context = ssl.create_default_context(cafile=str(certificates["ca"]))
    database = tmp_path / "hosted" / "coordinator.db"
    entered, release = Event(), Event()
    drops = []
    controllers = []
    stores = {}

    class BlockingAdapter(TestSimulationAdapter):
        def open(self, scenario):
            if scenario.name == "Cancel this owned run":
                entered.set()
                if not release.wait(20):
                    raise RuntimeError("The synthetic cancellation fixture was not released")
            return super().open(scenario)

    class DropFirstAck(RelayClient):
        def _serve(self, transport, binding, policy, credential):
            class Socket:
                def __getattr__(self, key):
                    return getattr(transport, key)
                def send(self, raw):
                    message = parse_message(raw)
                    if message.kind == "reply" and message.reply.kind == "accepted" and not drops:
                        drops.append(message.reply.simulation_id)
                        transport.close()
                        raise OSError("Injected lost acceptance acknowledgement")
                    return transport.send(raw)
            return super()._serve(Socket(), binding, policy, credential)

    def protected(profile):
        if os.name == "nt":
            return WindowsCredentialStore(profile)
        return stores.setdefault(str(profile), MemoryStore())

    def desktop(origin, name, *, drop=False):
        def pairing(address, store):
            return PairingClient(address, store, _transport=JsonTransport(address, _ssl_context=context))
        def relay(*args, **kwargs):
            return (DropFirstAck if drop else RelayClient)(*args, **kwargs, _ssl_context=context)
        def runtime(settings):
            return ApiRuntime(settings, runner_factory=lambda **kwargs: SimulationRunner(BlockingAdapter(), **kwargs))
        controller = CompanionController(tmp_path / name, pairing_factory=pairing, relay_factory=relay,
            credential_store_factory=protected, runtime_factory=runtime, _private_remote_execution=True)
        controller.open()
        controllers.append(controller)
        controller.configure_pairing(origin)
        controller.start()
        return controller

    def enroll(controller, client, headers):
        created = controller.begin_pairing("Fixture companion", SCOPES)
        assert client.post("/v1/pairing/claim", json={"code": created["code"]}, headers=headers).status_code == 200
        displayed = controller.pairing_status(refresh=True)
        saved = controller.approve_pairing(displayed["claim"], DevicePolicy(scopes=SCOPES))
        controller.connect_relay()
        return saved["device_id"]

    def disconnect():
        # Disable retrying clients before shutting down their server, including
        # assertion failures before the normal disconnect actions are reached.
        release.set()
        for controller in controllers:
            assert controller.disconnect_relay()

    try:
        with hosted_server(database, certificates, before_shutdown=disconnect) as (origin, app, port):
            with httpx.Client(base_url=origin, verify=context, trust_env=False, headers={"Content-Type": "application/json"}) as alice, \
                    httpx.Client(base_url=origin, verify=context, trust_env=False, headers={"Content-Type": "application/json"}) as bob:
                alice_headers, bob_headers = login(alice, "alice", origin), login(bob, "bob", origin)
                first, second = desktop(origin, "first", drop=True), desktop(origin, "second")
                alice_device = enroll(first, alice, alice_headers)
                bob_device = enroll(second, bob, bob_headers)
                wait_for(lambda: app.state.relay.available(alice_device) and app.state.relay.available(bob_device))
                recipe = validate_scenario({"name": "Owned TLS experiment", "duration_s": .2,
                    "actors": [{"type": "ego_vehicle", "speed_mps": 2}]})
                command = ExecuteCommand(request_id=uuid4(), scenario=recipe, mode="test")
                accepted = complete_command(alice, alice_headers, alice_device, command)
                assert accepted["status"] == "accepted" and len(drops) == 1
                simulation_id = UUID(accepted["simulation_id"])
                assert simulation_id == drops[0]
                assert complete_command(alice, alice_headers, alice_device, command) == accepted
                assert len(first.snapshot()["runs"]) == 1
                wait_for(lambda: first._worker.get(simulation_id).result)
                result_request = ResultCommand(request_id=uuid4(), simulation_id=simulation_id)
                result = complete_command(alice, alice_headers, alice_device, result_request)
                assert result["reply"]["summary"]["status"] == "completed"
                assert result["reply"]["summary"]["evidence"]["state"] == "local_only"
                assert result["reply"]["summary"]["metrics"]["coverage"]["total_frames"] > 0
                raw = json.dumps(result)
                for private in (str(first.profile), "agent_snapshot", "frames.ndjson",
                                protected(first.profile).load()["approval"]["credential"]):
                    assert private not in raw
                assert bob.get(f"/v1/devices/{alice_device}/commands/{command.request_id}", headers=bob_headers).status_code == 404
                assert post_command(bob, bob_headers, alice_device, command).status_code == 404
                assert post_command(alice, alice_headers, bob_device, result_request).status_code == 404
                assert post_command(bob, bob_headers, bob_device, result_request).status_code == 404
                assert len(second.snapshot()["runs"]) == 0
                hold = ExecuteCommand(request_id=uuid4(), scenario=recipe.model_copy(update={"name": "Cancel this owned run"}), mode="test")
                pending = complete_command(alice, alice_headers, alice_device, hold)
                held_id = UUID(pending["simulation_id"])
                assert entered.wait(5)
                cancel = complete_command(alice, alice_headers, alice_device,
                    CancelCommand(request_id=uuid4(), simulation_id=held_id))
                assert cancel["status"] == "completed" and cancel["reply"]["terminal"] is False
                release.set()
                wait_for(lambda: first._worker.get(held_id).result)
                assert first._worker.get(held_id).simulation.status.value == "cancelled"
                credential = protected(first.profile).load()["approval"]["credential"]
                assert alice.post(f"/v1/devices/{alice_device}/revoke", json={}, headers=alice_headers).status_code == 200
                assert post_command(alice, alice_headers, alice_device, command).status_code == 404
                assert alice.get(f"/v1/devices/{alice_device}/commands/{result_request.request_id}", headers=alice_headers).status_code == 404
                with httpx.Client(base_url=origin, verify=context, trust_env=False) as device_client:
                    assert device_client.get("/v1/device/session", headers={"Authorization": "Bearer " + credential}).status_code == 401
                wait_for(lambda: not app.state.relay.available(alice_device))
                assert first._worker.get(simulation_id).result is not None
                assert first.disconnect_relay() and second.disconnect_relay()
                saved_cookie = list(alice.cookies.items())
                assert first.stop() and second.stop()
        # New app/store and new connection attempts cannot restore the revoked credential.
        with hosted_server(database, certificates, port=port, before_shutdown=disconnect) as (same_origin, app, _):
            assert same_origin == origin
            with httpx.Client(base_url=origin, verify=context, trust_env=False) as client:
                assert client.get("/v1/device/session", headers={"Authorization": "Bearer " + credential}).status_code == 401
                for key, value in saved_cookie:
                    client.cookies.set(key, value)
                assert client.get(f"/v1/devices/{alice_device}/commands/{command.request_id}", headers=alice_headers).status_code == 404
            first.start()
            first.connect_relay()
            wait_for(lambda: first.relay_status()["state"] == "offline")
            assert not app.state.relay.available(alice_device)
            assert len(first.snapshot()["runs"]) == 2
            assert first.disconnect_relay()
    finally:
        release.set()
        for controller in controllers:
            assert controller.close()
