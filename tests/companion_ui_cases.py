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
        weakref.ref(desktop.pairing.origin), *[weakref.ref(value) for value in desktop.pairing.permissions.values()]]
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
