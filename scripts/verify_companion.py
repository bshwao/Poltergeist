"""Isolated application-level desktop check using the packaged interpreter.

Tk callbacks are invoked directly; this does not claim hardware mouse input or
independent-machine validation. It creates a new private profile, executes two
synthetic runs, fully closes the window/controller, then reopens saved evidence.
"""

import argparse
from datetime import UTC, datetime
import importlib
import json
import logging
import os
from pathlib import Path
import sys
from threading import Event
from time import monotonic


def verify(profile, *, visible=False):
    from scripts.portable import bundle_root, verify_bundle
    from companion.controller import CompanionController
    from companion.profile import configure_gui_environment, profile_path
    from companion.ui import CompanionWindow
    import tkinter as tk

    bundle = bundle_root()
    configure_gui_environment(bundle)
    verify_bundle(bundle)
    profile = profile_path(profile, bundle=bundle)
    if profile.exists():
        raise ValueError("Choose a new profile directory for the companion check")
    profile.mkdir(parents=True)
    os.chdir(profile)
    report = {"schema_version": "poltergeist-companion-validation-v1", "checked_at": datetime.now(UTC).isoformat(),
              "passed": False, "scope": "same-machine packaged desktop callbacks and persistence",
              "hardware_input_verified": False, "external_clean_machine_verified": False,
              "python": sys.executable, "bundle": str(bundle), "profile": str(profile), "runs": []}
    evidence = profile / "companion-validation.json"
    host = root = controller = window = None
    errors = []
    try:
        def stage(name):
            report["stage"] = name
            evidence.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        stage("checking imports")
        host = tk.Tk()
        host.withdraw()
        host.report_callback_exception = lambda kind, error, trace: errors.append(f"{kind.__name__}: {error}")
        report["modules"] = {name: importlib.import_module(name).__file__ for name in ("companion", "backend", "simulator", "scenarios", "tkinter", "_tkinter")}
        if any(not Path(path).resolve().is_relative_to(bundle) for path in report["modules"].values()):
            raise RuntimeError("Desktop module imported outside the bundle")
        def pump(predicate, timeout=30):
            deadline = monotonic() + timeout
            while monotonic() < deadline:
                root.update()
                if predicate():
                    if errors:
                        raise RuntimeError(str(errors))
                    return
                Event().wait(.01)
            raise TimeoutError("Packaged desktop callback timed out")
        def open_window():
            nonlocal root, controller, window
            controller = CompanionController(profile, bundle=bundle)
            controller.open()
            root = tk.Toplevel(host)
            if not visible:
                root.withdraw()
            # Surface an error as a failed check instead of waiting in a modal.
            from companion import ui
            ui.messagebox.showerror = lambda title, message, **kwargs: errors.append(f"{title}: {message}")
            window = CompanionWindow(root, controller, logging.getLogger("companion-validation"))
            pump(lambda: window._future is None and window._last_snapshot is not None)
        def close_window():
            window.request_close()
            pump(lambda: window._destroyed)
            if not controller.close():
                raise RuntimeError("Desktop did not release its worker")
        def run_recipe():
            previous = {r["id"] for r in window._last_snapshot["runs"]}
            window.run_button.invoke()
            pump(lambda: window._future is None and any(r["id"] not in previous and r["has_result"] for r in window._last_snapshot["runs"]))
            run = next(r for r in window._last_snapshot["runs"] if r["id"] not in previous)
            if run["status"] != "completed" or run["mode"] != "test":
                raise RuntimeError(f"Synthetic desktop execution failed: {run}")
            report["runs"].append(run)
            return run
        open_window()
        stage("baseline execution")
        report["tk_libraries"] = {name: root.tk.eval(f"set {name}") for name in ("tcl_library", "tk_library")}
        if any(not Path(path).resolve().is_relative_to(bundle) for path in report["tk_libraries"].values()):
            raise RuntimeError("Tk/Tcl resources escaped the private bundle")
        baseline = run_recipe()
        stage("SDK execution")
        registered = []
        window._run(lambda: controller.register_agent(bundle / "examples" / "agents" / "starter", trusted_local_code=True), registered.append)
        pump(lambda: window._future is None)
        # Refresh registered choices on the background actions thread.
        window._run(controller.snapshot, window._apply_snapshot)
        pump(lambda: window._future is None)
        window.agent.set(next(name for name, value in window._agent_ids.items() if value == str(registered[0].id)))
        custom = run_recipe()
        window.runs.selection_set(baseline["id"])
        window._export("json")
        pump(lambda: window._future is None)
        exports = list((profile / "exports").glob("*.json"))
        if len(exports) != 1:
            raise RuntimeError("Desktop report export was not persisted")
        close_window()
        stage("reopening saved results")
        open_window()
        ids = {r["id"] for r in window._last_snapshot["runs"] if r["status"] == "completed"}
        if ids != {baseline["id"], custom["id"]}:
            raise RuntimeError("Finalized history changed after desktop restart")
        window._run(lambda: controller.import_report(exports[0]), lambda path: report.update(imported_report=str(path)))
        pump(lambda: window._future is None)
        window._diagnostics(False)
        pump(lambda: window._future is None)
        report["diagnostics"] = window._last_diagnostics
        close_window()
        stage("checking final bundle integrity")
        verify_bundle(bundle)
        report["stage"] = "complete"
        report["passed"] = True
    finally:
        if window is not None and not window._destroyed:
            window.request_close()
            try:
                pump(lambda: window._destroyed)
            except Exception as error:
                report["cleanup_error"] = f"{type(error).__name__}: {error}"
                report["passed"] = False
        if controller is not None:
            try:
                report["profile_released"] = controller.close()
                if not report["profile_released"]:
                    report["passed"] = False
            except Exception as error:
                report["profile_released"] = report["passed"] = False
                report["cleanup_error"] = f"{type(error).__name__}: {error}"
        if host is not None:
            host.destroy()
        evidence.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not report["passed"]:
        raise RuntimeError(f"Companion check did not pass; inspect {evidence}")
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="New absolute profile outside the installed bundle")
    parser.add_argument("--visible", action="store_true")
    args = parser.parse_args(argv)
    print(verify(args.data_dir, visible=args.visible))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
