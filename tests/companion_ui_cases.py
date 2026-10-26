"""Real Tk callbacks and background actions, with an isolated durable worker."""

import logging
import os
from threading import Event
from time import monotonic

import pytest

tk = pytest.importorskip("tkinter")

from companion.controller import CompanionController
from companion.ui import CompanionWindow


def pump_until(root, predicate, timeout=90, *, stage="desktop callback", desktop=None, errors=None):
    import faulthandler
    import json
    import math
    import sys

    deadline = monotonic() + timeout
    # Arm the dump before entering Tcl: a stall inside root.update() must also
    # leave worker/import/filesystem stacks before the outer process guard fires.
    faulthandler.dump_traceback_later(timeout, file=sys.stderr)
    try:
        while monotonic() < deadline:
            root.update()
            if predicate():
                return
            Event().wait(.01)
    finally:
        faulthandler.cancel_dump_traceback_later()
    diagnostic = {"stage": stage, "timeout_s": timeout, "callback_error_count": len(errors or [])}
    if desktop is not None:
        future = desktop._future
        diagnostic["action"] = "none" if future is None else "done" if future.done() else "pending"
        snapshot = desktop._last_snapshot or {}
        queue = snapshot.get("queue", {})
        pending = queue.get("pending")
        diagnostic["queue"] = {
            "state": queue.get("state") if queue.get("state") in {"stopped", "running", "stopping", "error"} else "unknown",
            "pending": pending if type(pending) is int and pending >= 0 else None,
            "active": queue.get("active_id") is not None,
            "accepting": queue.get("accepting") if type(queue.get("accepting")) is bool else None,
        }
        diagnostic["runs"] = []
        for run in snapshot.get("runs", [])[:5]:
            progress = run.get("progress")
            if type(progress) not in (int, float) or not math.isfinite(progress) or not 0 <= progress <= 1:
                progress = None
            diagnostic["runs"].append({
                "mode": run.get("mode") if run.get("mode") in {"test", "carla"} else "unknown",
                "status": run.get("status") if run.get("status") in {"queued", "running", "completed", "failed", "cancelled"} else "unknown",
                "progress": progress,
                "has_result": run.get("has_result") if type(run.get("has_result")) is bool else None,
            })
    # Omit arbitrary errors, scenario/agent names, configuration and paths.
    message = "Desktop callbacks did not finish: " + json.dumps(diagnostic, sort_keys=True, allow_nan=False)
    print(message, file=sys.stderr, flush=True)
    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
    raise AssertionError(message)


@pytest.fixture(scope="module")
def tk_host():
    try:
        root = tk.Tk()
    except tk.TclError as error:
        if os.name != "nt" and ("display" in str(error).lower()):
            pytest.skip(f"Desktop display is unavailable: {error}")
        raise
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def window(tmp_path, monkeypatch, tk_host):
    root = tk.Toplevel(tk_host)
    root.withdraw()
    controller = CompanionController(tmp_path / "profile")
    controller.open()
    errors = []
    tk_host.report_callback_exception = lambda kind, error, trace: errors.append((kind.__name__, str(error)))
    monkeypatch.setattr("companion.ui.messagebox.showerror", lambda title, message, **kwargs: errors.append((title, message)))
    desktop = CompanionWindow(root, controller, logging.getLogger("test-companion"))
    yield root, controller, desktop, errors
    if not desktop._destroyed:
        desktop.request_close()
        pump_until(root, lambda: desktop._destroyed)
    assert controller.close()
    assert not errors, errors


def test_run_button_persisted_history_export_stop_and_restart(window):
    root, controller, desktop, errors = window
    pump_until(root, lambda: desktop._future is None and desktop._last_snapshot is not None)
    desktop.run_button.invoke()
    pump_until(root, lambda: desktop._future is None and any(r["status"] == "completed" for r in (desktop._last_snapshot or {}).get("runs", [])),
               stage="synthetic run completion", desktop=desktop, errors=errors)
    run = desktop._last_snapshot["runs"][0]
    assert run["mode"] == "test"
    assert desktop.runs.item(run["id"], "values")[2] == "completed"
    desktop.runs.selection_set(run["id"])
    desktop._export("json")
    pump_until(root, lambda: desktop._future is None)
    assert len(list((controller.profile / "exports").glob("*.json"))) == 1
    desktop.stop_button.invoke()
    pump_until(root, lambda: desktop._future is None)
    assert "stopped" in desktop.runtime_text.get()
    desktop.start_button.invoke()
    pump_until(root, lambda: desktop._future is None and bool(desktop._last_snapshot["runs"]))
    assert desktop._last_snapshot["runs"][0]["id"] == run["id"]


def test_background_action_keeps_tk_responsive_and_close_waits_for_action(window):
    root, controller, desktop, errors = window
    pump_until(root, lambda: desktop._future is None)
    entered, release, heartbeat = Event(), Event(), Event()
    desktop._run(lambda: (entered.set(), release.wait(5)))
    assert entered.wait(5)
    root.after(20, heartbeat.set)
    pump_until(root, heartbeat.is_set)
    desktop.request_close()
    assert not desktop._destroyed
    release.set()
    pump_until(root, lambda: desktop._destroyed)


def test_closed_window_releases_tk_variables_on_gui_thread(window):
    import weakref
    root, controller, desktop, errors = window
    pump_until(root, lambda: desktop._future is None)
    references = [weakref.ref(desktop.executable), weakref.ref(desktop.message), weakref.ref(desktop.setup.map_name),
        weakref.ref(desktop.pairing.origin), weakref.ref(desktop.pairing.allow_remote_execution),
        *[weakref.ref(value) for value in desktop.pairing.permissions.values()]]
    desktop.request_close()
    pump_until(root, lambda: desktop._destroyed)
    assert all(reference() is None for reference in references)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows protected enrollment")
def test_pairing_controls_review_real_account_preserve_origin_and_revoke(window):
    from fastapi.testclient import TestClient
    from companion.pairing_client import PairingClient, PairingClientError
    from coordinator.app import COOKIE, create_app
    from coordinator.settings import CoordinatorSettings
    from shared.models.coordination import PermissionScope
    root, controller, desktop, errors = window
    pump_until(root, lambda: desktop._future is None)
    origin = "https://coordinator.test"
    with TestClient(create_app(CoordinatorSettings(controller.profile.parent / "coordinator.db", origin)), base_url=origin) as api:
        class Transport:
            def request(self, method, path, *, body=None, authorization=None):
                response = api.request(method, path, json=body,
                    headers={"Authorization": authorization} if authorization else {})
                if response.status_code >= 400:
                    raise PairingClientError(f"Test coordinator returned HTTP {response.status_code}")
                return response.json()
        controller._pairing_factory = lambda address, store: PairingClient(address, store, _transport=Transport())
        headers = {"Origin": origin, "X-Poltergeist-Client": "browser"}
        credentials = {"username": "alice", "password": "a private test account passphrase"}
        assert api.post("/v1/auth/register", json=credentials, headers=headers).status_code == 201
        login = api.post("/v1/auth/login", json=credentials, headers=headers)
        headers.update({"Cookie": f"{COOKIE}={login.cookies[COOKIE]}", "X-CSRF-Token": login.json()["csrf_token"]})
        api.cookies.clear()
        panel = desktop.pairing
        panel.origin.set(origin)
        panel.save_origin()
        pump_until(root, lambda: desktop._future is None)
        assert panel._displayed["state"] == "unpaired"
        panel.permissions[PermissionScope.EXECUTE_TEST].set(True)
        panel.begin_button.invoke()
        pump_until(root, lambda: desktop._future is None)
        code = panel.code.get()
        assert len(code) == 10
        assert api.post("/v1/pairing/claim", json={"code": code}, headers=headers).status_code == 200
        assert api.get("/v1/devices", headers=headers).json() == []
        panel.refresh_button.invoke()
        pump_until(root, lambda: desktop._future is None)
        assert "alice" in panel.account.get() and origin in panel.saved_origin.get()
        # The editable input may change; consent still displays the bound server.
        panel.origin.set("https://another.test")
        assert origin in panel.saved_origin.get()
        panel.permissions[PermissionScope.RESULT].set(False)
        panel.approve_button.invoke()
        pump_until(root, lambda: desktop._future is None)
        assert panel._displayed["state"] == "paired" and not panel.code.get()
        devices = api.get("/v1/devices", headers=headers).json()
        assert len(devices) == 1 and set(devices[0]["policy"]["scopes"]) == {"status:read", "run:execute:test"}
        assert "Run synthetic experiments" in panel.approved_permissions.get()
        assert "Read experiment results" not in panel.approved_permissions.get()
        assert controller.config.coordinator_origin == origin
        desktop.stop_button.invoke()
        pump_until(root, lambda: desktop._future is None)
        desktop.port.set("2100")
        desktop._save_settings()
        pump_until(root, lambda: desktop._future is None)
        assert controller.config.coordinator_origin == origin
        assert desktop.setup._config().coordinator_origin == origin
        panel.revoke_button.invoke()
        pump_until(root, lambda: desktop._future is None)
        assert panel._displayed["state"] == "unpaired"
        assert api.get("/v1/devices", headers=headers).json()[0]["revoked_at"] is not None


@pytest.mark.skipif(os.name != "nt", reason="Native Windows protected enrollment")
def test_pending_approval_cancel_button_recovers_lost_approval_and_cancel_replies(window):
    from fastapi.testclient import TestClient
    from companion.credential_store import WindowsCredentialStore
    from companion.pairing_client import PairingClient, PairingClientError
    from coordinator.app import COOKIE, create_app
    from coordinator.settings import CoordinatorSettings

    root, controller, desktop, errors = window
    pump_until(root, lambda: desktop._future is None)
    origin = "https://coordinator.test"
    with TestClient(create_app(CoordinatorSettings(controller.profile.parent / "pending-coordinator.db", origin)), base_url=origin) as api:
        class Transport:
            approval_reply = True
            cancellation_reply = True
            def request(self, method, path, *, body=None, authorization=None):
                response = api.request(method, path, json=body,
                    headers={"Authorization": authorization} if authorization else {})
                if response.status_code >= 400:
                    raise PairingClientError("The fixture coordinator refused the operation")
                if path.endswith("/approve") and self.approval_reply:
                    self.approval_reply = False
                    raise PairingClientError("The approval reply was lost")
                if path.endswith("/cancel-pending-approval") and self.cancellation_reply:
                    self.cancellation_reply = False
                    raise PairingClientError("The cancellation reply was lost")
                return response.json()
        transport = Transport()
        controller._pairing_factory = lambda address, store: PairingClient(address, store, _transport=transport)
        headers = {"Origin": origin, "X-Poltergeist-Client": "browser"}
        credentials = {"username": "alice", "password": "a private test account passphrase"}
        assert api.post("/v1/auth/register", json=credentials, headers=headers).status_code == 201
        login = api.post("/v1/auth/login", json=credentials, headers=headers)
        headers.update({"Cookie": f"{COOKIE}={login.cookies[COOKIE]}", "X-CSRF-Token": login.json()["csrf_token"]})
        api.cookies.clear()
        panel = desktop.pairing
        panel.origin.set(origin)
        panel.save_origin()
        pump_until(root, lambda: desktop._future is None)
        panel.begin_button.invoke()
        pump_until(root, lambda: desktop._future is None)
        assert api.post("/v1/pairing/claim", json={"code": panel.code.get()}, headers=headers).status_code == 200
        panel.refresh_button.invoke()
        pump_until(root, lambda: desktop._future is None)
        panel.approve_button.invoke()
        pump_until(root, lambda: desktop._future is None and panel._displayed["state"] == "pending_approval")
        assert len(errors) == 1 and "approval reply was lost" in errors[0][1]
        errors.clear()
        store = WindowsCredentialStore(controller.profile)
        pending = store.load()
        assert pending["state"] == "pending_approval"
        credential = pending["approval"]["credential"]
        assert credential.encode() not in store.path.read_bytes()
        assert "revoked" in panel.summary.get() and not panel.cancel_approval_button.instate(["disabled"])
        assert not panel.allow_remote_execution.get()
        panel.cancel_approval_button.invoke()
        pump_until(root, lambda: desktop._future is None)
        assert len(errors) == 1 and "cancellation reply was lost" in errors[0][1]
        errors.clear()
        assert store.load() == pending and panel._displayed["state"] == "pending_approval"
        assert api.get("/v1/device/session", headers={"Authorization": "Bearer " + credential}).status_code == 401
        panel.cancel_approval_button.invoke()
        pump_until(root, lambda: desktop._future is None)
        assert panel._displayed["state"] == "unpaired" and store.load() is None
        assert panel.cancel_approval_button.instate(["disabled"])
        assert not panel.allow_remote_execution.get()
        devices = api.get("/v1/devices", headers=headers).json()
        assert len(devices) == 1 and devices[0]["revoked_at"] is not None
        panel.begin_button.invoke()
        pump_until(root, lambda: desktop._future is None)
        assert panel._displayed["state"] == "attempt" and len(panel.code.get()) == 10


def test_invalid_pairing_address_is_not_echoed_in_messages_or_logs(window, caplog):
    root, controller, desktop, errors = window
    pump_until(root, lambda: desktop._future is None)
    desktop.pairing.origin.set("https://user:private_address_sentinel@coordinator.test")
    with caplog.at_level(logging.ERROR):
        desktop.pairing.save_origin()
        pump_until(root, lambda: desktop._future is None)
    assert errors and "HTTPS origin" in errors[0][1]
    assert "private_address_sentinel" not in str(errors) + caplog.text + desktop.message.get()
    assert controller.config.coordinator_origin is None
    errors.clear()


def test_imported_recipe_and_settings_apply_through_desktop_callbacks(window):
    root, controller, desktop, errors = window
    pump_until(root, lambda: desktop._future is None)
    desktop.preset.set("sudden_braking-low")
    desktop._load_preset()
    assert desktop._recipe.name == "sudden_braking-low"
    desktop.stop_button.invoke()
    pump_until(root, lambda: desktop._future is None)
    desktop.port.set("2100")
    desktop._save_settings()
    pump_until(root, lambda: desktop._future is None)
    assert controller.config.carla_port == 2100
    desktop._diagnostics(False)
    pump_until(root, lambda: desktop._future is None)
    assert not desktop._last_diagnostics["readiness"]["carla_connection_requested"]
    assert "CARLA prerequisites" in desktop.diagnostic_text.get("1.0", "end")


def test_close_during_integrity_check_prevents_worker_restart(window):
    root, controller, desktop, errors = window
    pump_until(root, lambda: desktop._future is None)
    desktop.stop_button.invoke()
    pump_until(root, lambda: desktop._future is None)
    entered, release = Event(), Event()
    def check():
        entered.set()
        assert release.wait(10)
    desktop._startup_check, desktop._files_checked = check, False
    desktop.start_button.invoke()
    assert entered.wait(5)
    desktop.request_close()
    assert not desktop._destroyed
    release.set()
    pump_until(root, lambda: desktop._destroyed)
    assert controller._runtime is None


def test_failed_integrity_check_is_required_again_before_start(window):
    root, controller, desktop, errors = window
    pump_until(root, lambda: desktop._future is None)
    desktop.stop_button.invoke()
    pump_until(root, lambda: desktop._future is None)
    attempts = []
    def check():
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError("damaged test bundle")
    desktop._startup_check, desktop._files_checked = check, False
    desktop.start_button.invoke()
    pump_until(root, lambda: desktop._future is None)
    assert errors and "damaged test bundle" in errors[0][1]
    errors.clear()
    assert not desktop._files_checked and controller._runtime is None
    desktop.start_button.invoke()
    pump_until(root, lambda: desktop._future is None)
    assert len(attempts) == 2 and desktop._files_checked
    assert controller.snapshot()["queue"]["accepting"]


def test_first_run_scan_reports_hardware_and_rejected_copies_without_claiming_rpc(window, monkeypatch):
    root, controller, desktop, errors = window
    pump_until(root, lambda: desktop._future is None)
    from companion import onboarding
    monkeypatch.setattr(onboarding, "detect_carla", lambda: {
        "installations": [{"executable": "C:/CARLA/CarlaUE4.exe", "version_verified": False}],
        "rejected": [{"path": "C:/Incomplete/Shipping.exe", "reason": "Missing Engine"}],
        "search_limited": True, "inaccessible": 0})
    monkeypatch.setattr(onboarding, "inspect_system", lambda profile: {
        "platform": "Windows", "machine": "AMD64", "physical_memory_bytes": 16 * 1024 ** 3,
        "logical_cpus": 8, "disk_free_bytes": 50 * 1024 ** 3, "display_adapters": ["Example GPU"]})
    desktop.setup.scan()
    pump_until(root, lambda: desktop._future is None)
    assert desktop.executable.get() == "C:/CARLA/CarlaUE4.exe"
    text = desktop.setup.details.get("1.0", "end")
    assert "Missing Engine" in text and "16.0 GiB" in text and "Version remains unverified" in text
    assert not controller.config.setup_completed
    assert not (controller.profile / "companion.json").exists()


def test_synthetic_onboarding_is_explicit_and_persists_without_carla(window):
    root, controller, desktop, errors = window
    pump_until(root, lambda: desktop._future is None)
    desktop.stop_button.invoke()
    pump_until(root, lambda: desktop._future is None)
    desktop.setup.synthetic()
    pump_until(root, lambda: desktop._future is None)
    from companion.profile import load_config
    config = load_config(controller.profile)
    assert config.setup_completed and config.carla_executable is None and not config.carla_auto_start
    assert controller.snapshot()["queue"]["state"] == "stopped"
    assert "Synthetic setup saved" in desktop.setup.summary.get()


def test_onboarding_connection_reports_actual_map_and_synchronous_owner(window, monkeypatch):
    root, controller, desktop, errors = window
    pump_until(root, lambda: desktop._future is None)
    from companion import onboarding
    monkeypatch.setattr(onboarding, "check_connection", lambda config: {
        "server_version": "0.9.16", "client_version": "0.9.16", "map": "Town03",
        "available_maps": ["/Game/Carla/Maps/Town03"], "synchronous_mode": True})
    desktop.setup.check_connection()
    pump_until(root, lambda: desktop._future is None)
    text = desktop.setup.connection.get()
    assert "0.9.16 verified" in text and "Town03" in text and "synchronous" in text
    assert desktop.setup.maps.cget("values") == ("Town03",)
    assert "No simulation or model was executed" in desktop.setup.summary.get()


def connection_fixture(window, monkeypatch):
    """Real controller/worker and Tk, with explicit memory enrollment/relay fixtures.

    These cases check UI consent and ownership, not TLS or Windows protection.
    Separate transport and packaged acceptance exercise those boundaries.
    """
    from copy import deepcopy
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4
    from shared.models.coordination import DevicePolicy

    root, controller, desktop, errors = window
    pump_until(root, lambda: desktop._future is None)
    class Store:
        record = None
        def load(self):
            return deepcopy(self.record)
        def clear(self):
            self.record = None
    store = Store()
    created = []
    class Relay:
        def __init__(self, origin, enrollment, service, *, remote_execution_enabled):
            self.origin, self.enrollment, self.service = origin, enrollment, service
            self.enabled = remote_execution_enabled
            self.running, self.clean = False, True
            self.state = "disconnected"
            created.append(self)
        def start(self):
            self.running = True
            self.state = "connected"
        def stop(self):
            if self.clean:
                self.running = False
                self.state = "disconnected"
            return self.clean
        def snapshot(self):
            return {"state": self.state, "running": self.running,
                    "remote_execution_enabled": self.enabled}
    monkeypatch.setattr(controller, "_credential_store_factory", lambda _: store)
    monkeypatch.setattr(controller, "_relay_factory", Relay)
    controller.configure_pairing("https://coordinator.test")
    policy = DevicePolicy(scopes=["status:read", "result:read", "run:execute:test", "run:cancel"],
                          max_duration_s=12., max_ticks=240, max_waiting_runs=2)
    store.record = {"origin": "https://coordinator.test", "state": "paired",
        "created": {"attempt_id": str(uuid4()), "device_id": str(uuid4()), "code": "ABCDEFGHJK",
            "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()},
        "private_secret": "pa_" + "a" * 43, "requested_scopes": list(policy.scopes),
        "approval": {"request_id": str(uuid4()), "expected_account_id": str(uuid4()),
            "expected_revision": 2, "credential": "pd_" + "b" * 43,
            "policy": policy.model_dump(mode="json")}}
    desktop._apply_snapshot(controller.snapshot())
    return store, created


def test_connection_buttons_require_fresh_consent_and_keep_tk_reads_on_gui_thread(window, monkeypatch):
    from copy import deepcopy
    from threading import get_ident

    root, controller, desktop, errors = window
    store, relays = connection_fixture(window, monkeypatch)
    panel = desktop.pairing
    saved = deepcopy(store.record)
    saved_config = (controller.profile / "companion.json").read_bytes()
    main_thread = get_ident()
    original_get = panel.allow_remote_execution.get
    reads, calls = [], []
    def read_consent():
        assert get_ident() == main_thread
        reads.append(get_ident())
        return original_get()
    monkeypatch.setattr(panel.allow_remote_execution, "get", read_consent)
    original_connect = controller.connect_relay
    def connect(*, allow_remote_execution=False, displayed_status=None):
        assert get_ident() != main_thread
        assert type(allow_remote_execution) is bool
        assert displayed_status is not panel._displayed
        calls.append((allow_remote_execution, deepcopy(displayed_status)))
        return original_connect(allow_remote_execution=allow_remote_execution, displayed_status=displayed_status)
    monkeypatch.setattr(controller, "connect_relay", connect)
    assert not relays and panel.allow_remote_execution.get() is False
    assert not panel.connect_button.instate(["disabled"])
    assert panel.disconnect_button.instate(["disabled"])
    assert all(control.instate(["disabled"]) for control in panel.permission_controls.values())
    assert "12 simulated seconds" in panel.approved_limits.get() and "2 waiting" in panel.approved_limits.get()
    # Editing the address cannot change the reviewed coordinator binding.
    panel.origin.set("https://unsaved.test")
    panel.connect_button.invoke()
    pump_until(root, lambda: desktop._future is None)
    assert len(relays) == 1 and relays[0].enabled is False and calls[0][0] is False
    assert calls[0][1]["origin"] == relays[0].origin == "https://coordinator.test"
    assert "requests are paused" in panel.connection.get()
    assert panel.connect_button.instate(["disabled"]) and panel.consent_control.instate(["disabled"])
    panel.disconnect_button.invoke()
    pump_until(root, lambda: desktop._future is None)
    assert panel.allow_remote_execution.get() is False and not relays[0].running
    panel.consent_control.invoke()
    assert panel.allow_remote_execution.get() is True
    panel.connect_button.invoke()
    pump_until(root, lambda: desktop._future is None)
    assert len(relays) == 2 and relays[1].enabled is True and calls[1][0] is True
    assert "requests are allowed" in panel.connection.get()
    assert store.record == saved and reads
    assert (controller.profile / "companion.json").read_bytes() == saved_config
    relays[1].state = "offline"
    desktop._apply_snapshot(controller.snapshot())
    assert panel.allow_remote_execution.get() is True and "offline" in panel.connection.get()
    assert panel.consent_control.instate(["disabled"])
    relays[1].state = "connected"
    desktop._apply_snapshot(controller.snapshot())
    assert "requests are allowed" in panel.connection.get()
    desktop.stop_button.invoke()
    pump_until(root, lambda: desktop._future is None)
    assert panel.allow_remote_execution.get() is False and not relays[1].running
    assert panel.connect_button.instate(["disabled"])
    desktop.start_button.invoke()
    pump_until(root, lambda: desktop._future is None)
    assert len(relays) == 2 and not panel.connect_button.instate(["disabled"])
    assert panel.allow_remote_execution.get() is False


def test_disconnect_pending_cleanup_blocks_requests_and_retains_controls_for_retry(window, monkeypatch):
    from uuid import uuid4
    from shared.models.remote import StatusCommand

    root, controller, desktop, errors = window
    store, relays = connection_fixture(window, monkeypatch)
    panel = desktop.pairing
    panel.consent_control.invoke()
    panel.connect_button.invoke()
    pump_until(root, lambda: desktop._future is None)
    relay = relays[0]
    binding, _, _ = relay.enrollment()
    relay.clean = False
    panel.disconnect_button.invoke()
    pump_until(root, lambda: desktop._future is None)
    assert controller._runtime is not None and controller._owned
    assert panel.allow_remote_execution.get() is False
    assert controller.snapshot()["relay"]["remote_execution_enabled"] is False
    assert "New remote requests are blocked" in panel.connection.get()
    assert panel.connect_button.instate(["disabled"])
    assert not panel.disconnect_button.instate(["disabled"])
    assert relay.service.dispatch(binding, StatusCommand(request_id=uuid4())).code == "permission_denied"
    relay.clean = True
    panel.disconnect_button.invoke()
    pump_until(root, lambda: desktop._future is None)
    assert not relay.running and not controller.snapshot()["relay"]["owned"]
    assert not panel.connect_button.instate(["disabled"])
    assert panel.disconnect_button.instate(["disabled"])
    # A partially started relay may survive the controller's failed start.
    # The error path must refresh ownership before enabling Connect again.
    original_start = type(relay).start
    def failed_start(self):
        original_start(self)
        self.clean = False
        raise RuntimeError("Injected partial relay start")
    monkeypatch.setattr(type(relay), "start", failed_start)
    panel.consent_control.invoke()
    panel.connect_button.invoke()
    pump_until(root, lambda: desktop._future is None)
    assert errors and relays[-1].running and controller.snapshot()["relay"]["owned"]
    assert panel.allow_remote_execution.get() is False
    assert panel.connect_button.instate(["disabled"])
    assert not panel.disconnect_button.instate(["disabled"])
    assert "New remote requests are blocked" in panel.connection.get()
    errors.clear()
    relays[-1].clean = True
    panel.disconnect_button.invoke()
    pump_until(root, lambda: desktop._future is None)
    assert not controller.snapshot()["relay"]["owned"]


def test_stale_connection_consent_is_refused_and_error_restores_eligible_controls(window, monkeypatch):
    from uuid import uuid4

    root, controller, desktop, errors = window
    store, relays = connection_fixture(window, monkeypatch)
    panel = desktop.pairing
    panel.consent_control.invoke()
    entered, release, responsive = Event(), Event(), Event()
    def check():
        entered.set()
        assert release.wait(10)
    desktop._startup_check, desktop._files_checked = check, False
    panel.connect_button.invoke()
    assert entered.wait(5)
    assert panel.consent_control.instate(["disabled"])
    root.after(20, responsive.set)
    pump_until(root, responsive.is_set)
    # The failed action's immediate status refresh can also fail. Until a real
    # snapshot succeeds, the old empty relay display must not enable Connect.
    unavailable = Event()
    unavailable.set()
    original_snapshot = controller.snapshot
    def snapshot():
        if unavailable.is_set():
            raise RuntimeError("Injected connection status read failure")
        return original_snapshot()
    monkeypatch.setattr(controller, "snapshot", snapshot)
    # Change the protected binding after the GUI captured its reviewed display.
    store.record["approval"]["expected_account_id"] = str(uuid4())
    release.set()
    pump_until(root, lambda: desktop._future is None)
    assert len(errors) >= 2 and not relays and panel.allow_remote_execution.get() is False
    assert panel.connect_button.instate(["disabled"]) and panel.consent_control.instate(["disabled"])
    assert not panel.disconnect_button.instate(["disabled"])
    assert all(control.instate(["disabled"]) for control in panel.permission_controls.values())
    assert store.record["approval"]["credential"] not in str(errors) + desktop.message.get()
    unavailable.clear()
    errors.clear()
    desktop._run(controller.snapshot, desktop._apply_snapshot)
    pump_until(root, lambda: desktop._future is None)
    assert not panel.connect_button.instate(["disabled"])
    assert panel.disconnect_button.instate(["disabled"])
    assert store.record["approval"]["expected_account_id"] in panel.account.get()
    panel.consent_control.invoke()
    store.record["approval"]["policy"]["max_waiting_runs"] = 1
    desktop._apply_snapshot(controller.snapshot())
    assert panel.allow_remote_execution.get() is False and "1 waiting" in panel.approved_limits.get()
