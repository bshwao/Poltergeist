"""Owned HTTPS/WSS companions for the opt-in Playwright hosted dashboard suite.

Fixture controls live on a separate loopback listener with an ephemeral token.
They are never installed on the coordinator application or in its public API.
"""

from http.client import HTTPSConnection
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Lock, Thread
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.dependencies import ApiRuntime
from companion.controller import CompanionController
from companion.pairing_client import JsonTransport, PairingClient
from companion.relay_client import RelayClient
from companion.relay_transport import RelayConnector
from shared.models.coordination import DevicePolicy
from simulator import SimulationRunner
from simulator.test_adapter import TestSimulationAdapter
from test_hosted_execution import MemoryStore, wait_for
from test_relay_client import make_certificates


class RelayLink:
    """Cut only fixture-owned TCP sockets, without a WebSocket close frame."""

    def __init__(self):
        self._lock = Lock()
        self._blocked = False
        self._sockets = []

    def connect(self, uri, **options):
        with self._lock:
            if self._blocked:
                raise OSError("The fixture relay link is unavailable")
            transport = socket.create_connection(("127.0.0.1", 8058), timeout=2)
            self._sockets = [item for item in self._sockets if item.fileno() >= 0]
            self._sockets.append(transport)
        return RelayConnector(uri, sock=transport, **options)

    def set_blocked(self, blocked):
        with self._lock:
            self._blocked = blocked
            cut = 0
            if blocked:
                for transport in self._sockets:
                    try:
                        transport.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        continue
                    cut += 1
            return cut


def main():
    live_carla = os.environ.get("POLTERGEIST_HOSTED_LIVE_CARLA") == "1"
    validate_retention = live_carla and os.environ.get("POLTERGEIST_HOSTED_VALIDATE_RETENTION") == "1"
    if live_carla:
        try:
            import carla  # noqa: F401
        except ImportError as error:
            raise RuntimeError("Live hosted acceptance requires CARLA 0.9.16. Set POLTERGEIST_TEST_PYTHON to the Python 3.12 environment with the CARLA client installed.") from error
    root = Path(".pytest_cache/hosted-browser").resolve() / str(uuid4())
    root.mkdir(parents=True)
    external_ca = os.environ.get("POLTERGEIST_HOSTED_EXTERNAL_CA")
    files = None if external_ca else make_certificates(root)
    context = ssl.create_default_context(cafile=external_ca or str(files["ca"]))
    token = os.environ.get("POLTERGEIST_HOSTED_FIXTURE_TOKEN")
    if not token or len(token) < 32:
        raise ValueError("A private fixture control token is required")
    stop, release, entered, mutex = Event(), Event(), Event(), Lock()
    controllers, stores, links = {}, {}, {}
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
        link = links[name] = RelayLink()

        def pairing(address, store):
            return PairingClient(address, store, _transport=JsonTransport(address, _ssl_context=context))
        def relay(*args, **kwargs):
            return RelayClient(*args, **kwargs, _ssl_context=context, _connector=link.connect)
        def runtime(settings):
            if live_carla:
                service = ApiRuntime(settings)
                normal_factory = service.runner_factory

                def witnessed_runner(**kwargs):
                    runner = normal_factory(**kwargs)
                    run = runner.run

                    def witnessed_run(*args, **options):
                        on_progress = options["on_progress"]

                        def progress(simulation):
                            on_progress(simulation)
                            if simulation.status == "running" and simulation.progress > 0 and not entered.is_set():
                                entered.set()
                                if not release.wait(120):
                                    raise RuntimeError("The native browser fixture was not released")

                        options["on_progress"] = progress
                        return run(*args, **options)

                    runner.run = witnessed_run
                    return runner

                service.runner_factory = witnessed_runner
                return service
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
    import uvicorn
    server = server_thread = listener = None
    if files is not None:
        application = create_app(CoordinatorSettings(root / "coordinator.db", origin,
            hosted_assets_dir=Path("frontend/dist-hosted").resolve()))
        listener = socket.socket()
        listener.bind(("127.0.0.1", 8058))
        server = uvicorn.Server(uvicorn.Config(application, log_level="critical", access_log=False,
            proxy_headers=False, ssl_certfile=str(files["cert"]), ssl_keyfile=str(files["key"]),
            ws_max_size=262144, ws_ping_interval=None, timeout_graceful_shutdown=5))
        server_thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    else:
        # Accept only the fixed loopback origin and verify its certificate before
        # opening the fixture controls or creating any test accounts.
        connection = HTTPSConnection("localhost", 8058, context=context, timeout=5)
        try:
            connection.request("GET", "/healthz")
            response = connection.getresponse()
            if response.status != 200 or json.loads(response.read(4096)) != {
                "status": "ok", "protocol_version": "poltergeist-coordinator-v1",
            }:
                raise ValueError("The external loopback coordinator did not pass its HTTPS health check")
        finally:
            connection.close()

    class Controls(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/healthz":
                self.reply(200, {"ready": server is None or server.started})
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
                if name not in ("alice", "bob", "lab"):
                    raise ValueError()
                with mutex:
                    if self.path == "/new" and name not in controllers:
                        created = companion(name).begin_pairing(name.title() + " computer", scopes)
                        answer = {"code": created["code"]}
                    elif self.path == "/approve":
                        controller = controllers[name]
                        displayed = controller.pairing_status(refresh=True)
                        approved = controller.approve_pairing(displayed["claim"], DevicePolicy(scopes=scopes,
                            allow_evidence_read=True, allow_raw_capture=live_carla))
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
                    elif self.path in ("/cut-relay-link", "/restore-relay-link"):
                        answer = {"cut_sockets": links[name].set_blocked(self.path == "/cut-relay-link")}
                    elif self.path == "/state":
                        controller = controllers[name]
                        snapshot = controller.snapshot()
                        answer = {"runs": len(snapshot["runs"]), "entered": entered.is_set(),
                            "statuses": [row["status"] for row in snapshot["runs"]],
                            "run_ids": [row["id"] for row in snapshot["runs"]]}
                    elif self.path == "/export-and-remove-evidence" and validate_retention:
                        controller = controllers[name]
                        run, = controller.snapshot()["runs"]
                        assert run["status"] == "completed" and run["mode"] == "carla"
                        simulation_id = UUID(run["id"])
                        export = root / f"{name}-capture.zip"
                        before = controller.maintain_evidence(simulation_id, export=export)
                        after = controller.maintain_evidence(simulation_id, delete=True)
                        answer = {"exported_bytes": export.stat().st_size,
                                  "reclaimed_bytes": before["used_bytes"] - after["used_bytes"]}
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
        if server_thread is not None:
            server_thread.start()
            wait_for(lambda: server.started)
        control_thread.start()
        stop.wait()
    finally:
        cleanup()
        controls.shutdown()
        controls.server_close()
        if server_thread is not None:
            server.should_exit = True
            server_thread.join(15)
            listener.close()
            assert not server_thread.is_alive()


if __name__ == "__main__":
    main()
