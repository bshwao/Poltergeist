"""Owned HTTPS/WSS companions for the opt-in Playwright hosted dashboard suite.

Fixture controls live on a separate loopback listener with an ephemeral token.
They are never installed on the coordinator application or in its public API.
"""

import json
import os
from pathlib import Path
import secrets
import signal
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Lock, Thread
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.dependencies import ApiRuntime
from companion.controller import CompanionController
from companion.pairing_client import JsonTransport, PairingClient
from companion.relay_client import RelayClient
from shared.models.coordination import DevicePolicy
from simulator import SimulationRunner
from simulator.test_adapter import TestSimulationAdapter
from test_hosted_execution import MemoryStore, wait_for
from test_relay_client import make_certificates


def main():
    root = Path(".pytest_cache/hosted-browser").resolve() / str(uuid4())
    root.mkdir(parents=True)
    files = make_certificates(root)
    context = ssl.create_default_context(cafile=str(files["ca"]))
    token = os.environ.get("POLTERGEIST_HOSTED_FIXTURE_TOKEN")
    if not token or len(token) < 32:
        raise ValueError("A private fixture control token is required")
    stop, release, entered, mutex = Event(), Event(), Event(), Lock()
    controllers, stores = {}, {}
    live_carla = os.environ.get("POLTERGEIST_HOSTED_LIVE_CARLA") == "1"
    scopes = ["status:read", "result:read", "run:execute:test", "run:cancel"]
    if live_carla:
        scopes.append("run:execute:carla")
    origin = "https://localhost:8058"

    class HeldAdapter(TestSimulationAdapter):
        def open(self, scenario):
            if scenario.name == "Cancel browser experiment":
                entered.set()
                if not release.wait(60):
                    raise RuntimeError("The browser cancellation fixture was not released")
            return super().open(scenario)

    def companion(name):
        def pairing(address, store):
            return PairingClient(address, store, _transport=JsonTransport(address, _ssl_context=context))
        def relay(*args, **kwargs):
            return RelayClient(*args, **kwargs, _ssl_context=context)
        def runtime(settings):
            if live_carla:
                return ApiRuntime(settings)
            return ApiRuntime(settings, runner_factory=lambda **kwargs: SimulationRunner(HeldAdapter(), **kwargs))
        controller = CompanionController(root / name, pairing_factory=pairing, relay_factory=relay,
            credential_store_factory=lambda profile: stores.setdefault(str(profile), MemoryStore()), runtime_factory=runtime)
        controller.open()
        controllers[name] = controller
        controller.configure_pairing(origin)
        controller.start()
        return controller

    def cleanup():
        release.set()
        for controller in controllers.values():
            assert controller.disconnect_relay()
            assert controller.close()

    # Extend the existing TLS fixture without changing coordinator production defaults.
    from coordinator.app import create_app
    from coordinator.settings import CoordinatorSettings
    import socket
    import uvicorn
    application = create_app(CoordinatorSettings(root / "coordinator.db", origin,
        hosted_assets_dir=Path("frontend/dist-hosted").resolve()))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 8058))
    server = uvicorn.Server(uvicorn.Config(application, log_level="critical", access_log=False,
        proxy_headers=False, ssl_certfile=str(files["cert"]), ssl_keyfile=str(files["key"]),
        ws_max_size=262144, ws_ping_interval=None, timeout_graceful_shutdown=5))
    server_thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)

    class Controls(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/healthz":
                self.reply(200, {"ready": server.started})
            else:
                self.reply(404, {})

        def reply(self, status, body):
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            if self.headers.get("Host") != "127.0.0.1:8059" or self.headers.get("Origin") or not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                self.reply(403, {})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024:
                    raise ValueError()
                body = json.loads(self.rfile.read(length))
                name = body.get("name")
                if name not in ("alice", "bob"):
                    raise ValueError()
                with mutex:
                    if self.path == "/new" and name not in controllers:
                        created = companion(name).begin_pairing(name.title() + " computer", scopes)
                        answer = {"code": created["code"]}
                    elif self.path == "/approve":
                        controller = controllers[name]
                        displayed = controller.pairing_status(refresh=True)
                        approved = controller.approve_pairing(displayed["claim"], DevicePolicy(scopes=scopes))
                        controller.connect_relay(allow_remote_execution=True, displayed_status=controller.pairing_status())
                        answer = {"device_id": approved["device_id"]}
                    elif self.path in ("/disconnect", "/pause", "/connect"):
                        controller = controllers[name]
                        assert controller.disconnect_relay()
                        if self.path != "/disconnect":
                            enabled = self.path == "/connect"
                            controller.connect_relay(allow_remote_execution=enabled,
                                displayed_status=controller.pairing_status() if enabled else None)
                        answer = {"ok": True}
                    elif self.path == "/release":
                        release.set()
                        answer = {"ok": True}
                    elif self.path == "/state":
                        controller = controllers[name]
                        snapshot = controller.snapshot()
                        answer = {"runs": len(snapshot["runs"]), "entered": entered.is_set(),
                            "statuses": [row["status"] for row in snapshot["runs"]]}
                    else:
                        self.reply(404, {})
                        return
                self.reply(200, answer)
            except Exception:
                self.reply(500, {"error": "Fixture operation failed"})

    controls = ThreadingHTTPServer(("127.0.0.1", 8059), Controls)
    control_thread = Thread(target=controls.serve_forever, daemon=True)
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    Thread(target=lambda: (sys.stdin.readline(), stop.set()), daemon=True).start()
    try:
        server_thread.start()
        wait_for(lambda: server.started)
        control_thread.start()
        stop.wait()
    finally:
        cleanup()
        server.should_exit = True
        server_thread.join(15)
        controls.shutdown()
        controls.server_close()
        listener.close()
        assert not server_thread.is_alive()


if __name__ == "__main__":
    main()
