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


@pytest.fixture
def window(tmp_path, monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError as error:
        if os.name != "nt" and ("display" in str(error).lower()):
            pytest.skip(f"Desktop display is unavailable: {error}")
        raise
    root.withdraw()
    controller = CompanionController(tmp_path / "profile")
    controller.open()
    errors = []
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
