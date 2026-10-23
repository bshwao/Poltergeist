"""Real Tk callbacks and background actions, with an isolated durable worker."""

import logging
import os
from threading import Event
from time import monotonic

import pytest

tk = pytest.importorskip("tkinter")

from companion.controller import CompanionController
from companion.ui import CompanionWindow


def pump_until(root, predicate, timeout=30):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        root.update()
        if predicate():
            return
        Event().wait(.01)
    raise AssertionError("Desktop callbacks did not finish")


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
    pump_until(root, lambda: desktop._future is None and any(r["status"] == "completed" for r in (desktop._last_snapshot or {}).get("runs", [])))
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
    references = [weakref.ref(desktop.executable), weakref.ref(desktop.message), weakref.ref(desktop.setup.map_name)]
    desktop.request_close()
    pump_until(root, lambda: desktop._destroyed)
    assert all(reference() is None for reference in references)


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
