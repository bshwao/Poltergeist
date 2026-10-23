"""Read-only native bundle check used by Setup after extracting its files."""

import argparse
import importlib
import json
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.worker:
        try:
            completed = subprocess.run([sys.executable, "-I", "-B", "-m", "scripts.install_check",
                "--worker", "--output", str(args.output)], capture_output=True, text=True, timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if args.output.is_file():
                report = json.loads(args.output.read_bytes())
                return 0 if completed.returncode == 0 and report.get("passed") is True else 1
            raise RuntimeError("Native check returned no structured report")
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            args.output.write_text(json.dumps({"schema_version": "poltergeist-installed-native-v1",
                "passed": False, "error": f"{type(error).__name__}: {error}"}, indent=2) + "\n", encoding="utf-8")
            return 1
    report = {"schema_version": "poltergeist-installed-native-v1", "passed": False}
    try:
        from scripts.portable import bundle_root, verify_bundle
        from companion.profile import configure_gui_environment
        root = bundle_root()
        verify_bundle(root)
        configure_gui_environment(root)
        report["modules"] = {name: importlib.import_module(name).__file__ for name in
            ("companion", "companion.setup_ui", "sqlalchemy.orm", "carla", "numpy", "tkinter", "_tkinter")}
        if any(not Path(path).resolve().is_relative_to(root) for path in report["modules"].values()):
            raise RuntimeError("Installed module imported outside the private bundle")
        import tkinter as tk
        window = tk.Tk()
        try:
            window.withdraw()
            window.update_idletasks()
            report["tcl"] = window.tk.eval("info library")
            report["tk"] = window.tk.eval("set tk_library")
            if any(not Path(report[key]).resolve().is_relative_to(root) for key in ("tcl", "tk")):
                raise RuntimeError("GUI resources came from outside the installed bundle")
        finally:
            window.destroy()
        report["passed"] = True
        return 0
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        return 1
    finally:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
