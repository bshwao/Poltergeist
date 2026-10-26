"""Real HTTPS/WSS coordinator process loss at durable execute/cancel boundaries."""

from collections import deque
from contextlib import closing
import json
import os
from pathlib import Path
from queue import Empty, Queue
import socket
import sqlite3
import ssl
import subprocess
import sys
from threading import Event, Thread
from uuid import UUID, uuid4

import httpx
import pytest

from backend.dependencies import ApiRuntime
from companion.controller import CompanionController
from companion.credential_store import WindowsCredentialStore
from companion.pairing_client import JsonTransport, PairingClient
from companion.relay_client import RelayClient
from enrollment_diagnostics import approve_for_crash_fixture, diagnostic_worker
from shared.models.coordination import DevicePolicy
from shared.models.remote import CancelCommand, ExecuteCommand, ResultCommand
from simulator import SimulationRunner
from simulator.test_adapter import TestSimulationAdapter
from test_hosted_execution import MemoryStore, SCOPES, complete_command, login, post_command, wait_for
from test_relay_client import make_certificates


@pytest.fixture(autouse=True)
def record_private_enrollment_failures(monkeypatch, tmp_path, record_property):
    """Keep bounded error categories when CI fails before the injected crash."""
    from companion.enrollment_process import EnrollmentProcess

    directory = Path(__file__).resolve().parents[1] / "validation-evidence/coordinator-crash-enrollment" / (tmp_path.name + "-" + uuid4().hex[:8])
    worker, _ = diagnostic_worker(directory)
    record_property("enrollment_diagnostic_directory", directory.relative_to(Path(__file__).resolve().parents[1]).as_posix())
    original = EnrollmentProcess.__init__
    source = Path(__file__).resolve().parents[1] / "companion/enrollment_worker.py"

    def initialize(self, worker_path, **options):
        return original(self, worker if Path(worker_path).resolve() == source else worker_path, **options)

    monkeypatch.setattr(EnrollmentProcess, "__init__", initialize)
    recoveries = []
    yield recoveries
    record_property("enrollment_recoveries", json.dumps(recoveries))
    (directory / "fixture-recovery.json").write_text(json.dumps({"recoveries": recoveries}) + "\n", encoding="utf-8")


class CoordinatorProcess:
    def __init__(self, root, files, port, *, kind=None, fault=None):
        self.origin = f"https://localhost:{port}"
        self.events, self.errors = Queue(), deque(maxlen=20)
        self.process = subprocess.Popen([sys.executable, "-u", str(Path(__file__).with_name("coordinator_crash_server.py"))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")

        def output():
            for line in self.process.stdout:
                try:
                    self.events.put(json.loads(line))
                except ValueError:
                    self.events.put({"event": "invalid_output"})
        def errors():
            for line in self.process.stderr:
                self.errors.append(line[:500])
        self.readers = [Thread(target=output, daemon=True), Thread(target=errors, daemon=True)]
        for thread in self.readers:
            thread.start()
        self.send({"database": str(root / "coordinator.db"), "origin": self.origin, "port": port,
                   "cert": str(files["cert"]), "key": str(files["key"]), "kind": kind, "fault": fault})
        try:
            assert self.event()["event"] == "ready"
        except BaseException:
            self.stop()
            raise

    def send(self, value):
        self.process.stdin.write(json.dumps(value) + "\n")
        self.process.stdin.flush()

    def event(self):
        try:
            return self.events.get(timeout=35)
        except Empty:
            raise AssertionError("No fixture coordinator event; process status: " + str(self.process.poll())) from None

    def assert_crashed(self):
        event = self.event()
        assert event["event"] == "fault"
        assert self.process.wait(timeout=20) == 79
        return event

    def stop(self):
        if self.process.poll() is None:
            try:
                self.send({"action": "stop"})
            except (BrokenPipeError, OSError):
                pass
            try:
                self.process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                # The owned process's stdin control requests a hard exit in the
                # actual child; do not target a PID from a saved state file.
                self.send({"action": "crash"})
                self.process.wait(timeout=10)
                raise AssertionError("Fixture coordinator required hard-stop cleanup")
        self.process.stdin.close()
        for thread in self.readers:
            thread.join(5)
            assert not thread.is_alive()
        self.process.stdout.close()
        self.process.stderr.close()


def stored(root, request):
    with closing(sqlite3.connect(f"file:{(root / 'coordinator.db').as_posix()}?mode=ro", uri=True)) as connection:
        return connection.execute("SELECT status,reply_json FROM commands WHERE request_id=?", (str(request),)).fetchone()


def _check_coordinator_process_loss(tmp_path, boundary, operation, *, finish_before_replay=False, enrollment_recoveries=None):
    files = make_certificates(tmp_path)
    context = ssl.create_default_context(cafile=str(files["ca"]))
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    first_server = CoordinatorProcess(tmp_path, files, port,
        kind="accepted" if operation == "execute" else "cancel_requested", fault=boundary)
    origin, release, entered = first_server.origin, Event(), Event()
    controllers, stores, servers = [], {}, [first_server]
    enrollment_recoveries = [] if enrollment_recoveries is None else enrollment_recoveries

    class HeldAdapter(TestSimulationAdapter):
        def open(self, scenario):
            if scenario.name == "Hold for lost cancel acknowledgement":
                entered.set()
                # The repeated-outage case deliberately spans two restarts.
                if not release.wait(180):
                    raise RuntimeError("The cancellation fault fixture was not released")
            return super().open(scenario)

    def desktop(name):
        def pairing(address, store):
            return PairingClient(address, store, _transport=JsonTransport(address, _ssl_context=context))
        def relay(*args, **kwargs):
            return RelayClient(*args, **kwargs, _ssl_context=context)
        def protected(profile):
            return WindowsCredentialStore(profile) if os.name == "nt" else stores.setdefault(str(profile), MemoryStore())
        def runtime(settings):
            return ApiRuntime(settings, runner_factory=lambda **kwargs: SimulationRunner(HeldAdapter(), **kwargs))
        controller = CompanionController(tmp_path / name, pairing_factory=pairing, relay_factory=relay,
            credential_store_factory=protected, runtime_factory=runtime)
        controller.open()
        controllers.append(controller)
        controller.configure_pairing(origin)
        controller.start()
        return controller

    def enroll(controller, client, headers, name):
        created = controller.begin_pairing(name, SCOPES)
        assert client.post("/v1/pairing/claim", json={"code": created["code"]}, headers=headers).status_code == 200
        approved = approve_for_crash_fixture(controller, DevicePolicy(scopes=SCOPES), enrollment_recoveries)
        controller.connect_relay(allow_remote_execution=True, displayed_status=controller.pairing_status())
        return approved["device_id"]

    def connected(client, headers, device):
        try:
            response = client.get("/v1/browser/devices", headers=headers)
        except httpx.TransportError:
            return False
        assert response.status_code == 200
        return any(row["device"]["device_id"] == str(device) and row["connection"]["remote_requests_enabled"]
                   for row in response.json())

    try:
        with httpx.Client(base_url=origin, verify=context, trust_env=False, headers={"Content-Type": "application/json"}) as alice, \
                httpx.Client(base_url=origin, verify=context, trust_env=False, headers={"Content-Type": "application/json"}) as bob:
            alice_headers, bob_headers = login(alice, "alice", origin), login(bob, "bob", origin)
            first, second = desktop("alice"), desktop("bob")
            device = enroll(first, alice, alice_headers, "Alice computer")
            other = enroll(second, bob, bob_headers, "Bob computer")
            wait_for(lambda: connected(alice, alice_headers, device) and connected(bob, bob_headers, other), timeout=30)
            command = ExecuteCommand(request_id=uuid4(), mode="test", scenario={
                "name": "Hold for lost cancel acknowledgement" if operation == "cancel" else "Survives coordinator crash",
                "duration_s": .2, "actors": [{"id": "ego", "type": "ego_vehicle", "speed_mps": 2}]})
            if operation == "cancel":
                acceptance = complete_command(alice, alice_headers, device, command)
                assert entered.wait(15)
                target = CancelCommand(request_id=uuid4(), simulation_id=acceptance["simulation_id"])
            else:
                target = command
            # The browser response can be lost too. Keep the exact original
            # request identity; process state and durable rows prove the fault.
            try:
                response = post_command(alice, alice_headers, device, target)
                assert response.status_code == 202
            except httpx.TransportError:
                pass
            fault = first_server.assert_crashed()
            assert fault["boundary"] == boundary and fault["reply"]["request_id"] == str(target.request_id)
            expected = "queued" if boundary == "before_commit" else "accepted" if operation == "execute" else "completed"
            assert stored(tmp_path, target.request_id)[0] == expected
            assert len(first.snapshot()["runs"]) == 1 and second.snapshot()["runs"] == []
            if finish_before_replay:
                assert operation == "cancel" and boundary == "before_commit"
                # The coordinator has no committed ACK yet. Finish cleanup while
                # it remains down, before the first possible redelivery.
                release.set()
                interrupted_id = UUID(fault["reply"]["simulation_id"])
                wait_for(lambda: first._worker.get(interrupted_id).result, timeout=30)
                assert first._worker.get(interrupted_id).simulation.status == "cancelled"
            # A replacement starts only after the original process has proven
            # exit. Device connections and cookies reconcile against the same DB.
            replacement = CoordinatorProcess(tmp_path, files, port)
            servers.append(replacement)
            wait_for(lambda: connected(alice, alice_headers, device) and connected(bob, bob_headers, other), timeout=40)
            reconciled = complete_command(alice, alice_headers, device, target)
            assert reconciled["reply"] == fault["reply"]
            assert complete_command(alice, alice_headers, device, target) == reconciled
            simulation = UUID(fault["reply"]["simulation_id"])
            finished_offline = operation == "cancel" and boundary == "before_commit"
            if finished_offline and not finish_before_replay:
                # Lose the coordinator again after reconciliation. Local cleanup
                # must finish independently, and its outcome must survive a
                # second reconnect while the original cancel ACK stays unchanged.
                replacement.send({"action": "crash"})
                assert replacement.process.wait(timeout=20) == 79
                release.set()
                wait_for(lambda: first._worker.get(simulation).result, timeout=30)
                assert first._worker.get(simulation).simulation.status == "cancelled"
                later = CoordinatorProcess(tmp_path, files, port)
                servers.append(later)
                wait_for(lambda: connected(alice, alice_headers, device) and connected(bob, bob_headers, other), timeout=40)
                assert complete_command(alice, alice_headers, device, target) == reconciled
            if operation == "cancel":
                assert finished_offline or first._worker.get(simulation).result is None
                release.set()
            wait_for(lambda: first._worker.get(simulation).result, timeout=30)
            result = complete_command(alice, alice_headers, device, ResultCommand(request_id=uuid4(), simulation_id=simulation))
            summary = result["reply"]["summary"]
            assert summary["status"] == ("cancelled" if operation == "cancel" else "completed")
            assert summary["execute_request_id"] == str(command.request_id)
            assert summary["device_id"] == str(device) and summary["ownership_generation"] == 1
            assert summary["cancel_requested"] is (operation == "cancel")
            assert len(first.snapshot()["runs"]) == 1 and second.snapshot()["runs"] == []
            assert bob.get(f"/v1/devices/{device}/commands/{target.request_id}", headers=bob_headers).status_code == 404
            assert alice.get(f"/v1/devices/{other}/commands", headers=alice_headers).status_code == 404
    finally:
        release.set()
        for controller in controllers:
            assert controller.close()
        for server in reversed(servers):
            server.stop()


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
@pytest.mark.parametrize("operation", ["execute", "cancel"])
def test_coordinator_process_loss_reconciles_original_request_and_owned_work(tmp_path, boundary, operation, record_private_enrollment_failures):
    _check_coordinator_process_loss(tmp_path, boundary, operation, enrollment_recoveries=record_private_enrollment_failures)


def test_cancel_finishes_offline_before_first_lost_ack_redelivery(tmp_path, record_private_enrollment_failures):
    _check_coordinator_process_loss(tmp_path, "before_commit", "cancel", finish_before_replay=True,
        enrollment_recoveries=record_private_enrollment_failures)
