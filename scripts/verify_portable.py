"""Prove same-machine relocation with isolated data and no developer search paths.

This does not claim a clean second computer. Live checks reuse a separately
started dedicated idle CARLA server; this tool never starts or resets CARLA.
"""

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

from scripts.build_portable import ROOT, sha256, write_json

IMPORT_PROBE = """
import importlib,json,sys
import carla,numpy,ssl,sqlite3
from importlib.metadata import version
print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,'sys_path':sys.path,
 'modules':{name:importlib.import_module(name).__file__ for name in ['backend','shared','simulator','scenarios','evaluation','optimization','scripts']},
 'python':sys.version,'carla':version('carla'),'numpy':numpy.__version__}))
"""


def verify(archive, workdir, *, live=False, carla_executable=None, carla_port=2000):
    if sys.platform != "win32":
        raise ValueError("This portable prototype verifier targets Windows x64 only")
    archive, workdir = archive.resolve(strict=True), workdir.resolve()
    if workdir.is_relative_to(ROOT) or ROOT.is_relative_to(workdir):
        raise ValueError("Relocation evidence must use a new directory outside the source checkout")
    workdir.mkdir(parents=True, exist_ok=False)
    staging = workdir / "initial-extraction"
    staging.mkdir()
    with zipfile.ZipFile(archive) as zipped:
        for name in zipped.namelist():
            if not (staging / name).resolve().is_relative_to(staging):
                raise ValueError("Unsafe portable archive path")
        zipped.extractall(staging)
    source = staging / "Poltergeist-S1-windows-x64"
    relocated = workdir / "relocated prototype with spaces"
    relocated.mkdir()
    bundle = relocated / source.name
    if not source.resolve().is_relative_to(workdir) or not bundle.resolve().is_relative_to(workdir):
        raise ValueError("Relocation escaped verification directory")
    source.rename(bundle)
    cwd = workdir / "unrelated working directory"
    cwd.mkdir()
    profile = workdir / "fresh-user-profile"
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("PYTHON", "VIRTUAL_ENV", "CONDA", "PIP_"))}
    environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    # Also challenge isolated mode with a hostile Python search path. It must
    # have no effect; no source-tree files may appear in the recorded imports.
    environment["PYTHONPATH"] = str(ROOT)
    environment["PYTHONHOME"] = str(cwd / "nonexistent-system-python")
    python = bundle / "runtime" / "python.exe"
    report = {"schema_version": "poltergeist-portable-validation-v1", "checked_at": datetime.now(UTC).isoformat(),
        "scope": "same-machine relocated Windows x64 prototype", "external_clean_machine_verified": False,
        "archive_sha256": sha256(archive), "bundle": str(bundle), "profile": str(profile),
        "live_requested": live, "commands": [], "passed": False}

    def run(name, arguments, timeout=180):
        completed = subprocess.run(arguments, cwd=cwd, env=environment, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        (workdir / f"{name}.stdout.log").write_text(completed.stdout, encoding="utf-8")
        (workdir / f"{name}.stderr.log").write_text(completed.stderr, encoding="utf-8")
        report["commands"].append({"name": name, "exit_code": completed.returncode})
        write_json(workdir / "portable-validation.json", report)
        if completed.returncode:
            raise RuntimeError(f"Portable {name} failed; inspect {workdir / (name + '.stderr.log')}")
        return completed.stdout

    command = [str(python), "-I", "-B", "-m", "scripts.portable", "--data-dir", str(profile)]
    try:
        run("integrity-before", [*command, "verify"])
        imported = json.loads(run("imports", [str(python), "-I", "-B", "-c", IMPORT_PROBE]))
        report["imports"] = imported
        if Path(imported["executable"]).resolve() != python.resolve() or imported["carla"] != "0.9.16":
            raise RuntimeError("Private interpreter/client identity mismatch")
        if any(not Path(path).resolve().is_relative_to(bundle) for path in imported["modules"].values()):
            raise RuntimeError("First-party import escaped the relocated bundle")
        if any(not Path(path).resolve().is_relative_to(bundle) for path in imported["sys_path"]):
            raise RuntimeError("Python search path escaped the relocated bundle")
        run("doctor", [*command, "doctor"])
        run("capabilities", [*command, "capabilities"])
        run("worker-smoke", [*command, "worker-smoke"])
        run("synthetic", [*command, "validate", "--sensors"])
        # Exercise the actual Windows user entry point as well as the interpreter.
        wrapper = str(bundle / "Poltergeist.cmd")
        if any(character in wrapper for character in '&|<>^"%\r\n'):
            raise ValueError("Use a verification path without shell metacharacters for the .cmd smoke check")
        # Pass the cmd command line verbatim: list2cmdline backslash escaping is
        # for Windows argv parsing, whereas cmd.exe has its own quote grammar.
        cmd = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
        run("cmd-entry-point", f'"{cmd}" /d /s /c ""{wrapper}" verify"')
        if live:
            if carla_executable:
                run("configure", [*command, "configure", "--carla-executable", str(carla_executable.resolve(strict=True)),
                                  "--carla-port", str(carla_port)])
            elif carla_port != 2000:
                raise ValueError("A non-default live port requires --carla-executable to write an isolated configuration")
            run("live", [*command, "validate", "--mode", "carla", "--duration", "2"], timeout=240)
        run("integrity-after", [*command, "verify"])
        report["passed"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        write_json(workdir / "portable-validation.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True, help="New absolute evidence directory outside the checkout")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--carla-executable", type=Path)
    parser.add_argument("--carla-port", type=int, default=2000)
    args = parser.parse_args(argv)
    result = verify(args.archive, args.workdir, live=args.live, carla_executable=args.carla_executable, carla_port=args.carla_port)
    print(json.dumps({"passed": result["passed"], "evidence": str(args.workdir / "portable-validation.json"),
                      "external_clean_machine_verified": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
