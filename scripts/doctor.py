"""Check a local installation without starting workers, inference or CARLA."""

import argparse
from datetime import UTC, datetime
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from backend.config import Settings

CORE = ("poltergeist", "fastapi", "uvicorn", "pydantic", "pydantic-settings", "PyYAML", "sqlalchemy")
CARLA_VERSION = "0.9.16"
_PROBE = """
import json,sys
import carla
c=carla.Client(sys.argv[1],int(sys.argv[2])); c.set_timeout(float(sys.argv[3]))
client=c.get_client_version(); server=c.get_server_version()
if client != '0.9.16' or server != '0.9.16':
    raise RuntimeError('Expected CARLA 0.9.16 client/server; received '+client+' / '+server)
w=c.get_world(); s=w.get_settings()
print('POLTERGEIST_PROBE='+json.dumps({'client_version':client,'server_version':server,'map':w.get_map().name,
    'synchronous_mode':s.synchronous_mode,'no_rendering_mode':s.no_rendering_mode,
    'fixed_delta_seconds':s.fixed_delta_seconds}))
"""


def carla_probe(python, host, port, timeout=5.0):
    """Keep a blocked native RPC/import outside the doctor process deadline."""
    completed = subprocess.run([str(python), "-c", _PROBE, host, str(port), str(timeout)],
        capture_output=True, text=True, timeout=timeout + 10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode:
        detail = (completed.stderr.strip().splitlines() or ["CARLA probe failed"])[-1]
        raise RuntimeError(detail[:500])
    # The native CARLA library can flush informational cache logs on shutdown.
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith("POLTERGEIST_PROBE="):
            return json.loads(line.removeprefix("POLTERGEIST_PROBE="))
    raise RuntimeError("CARLA probe returned no structured result; inspect the selected client environment")


def collect(*, frontend=False, carla=False, carla_python=None, timeout=5., hardware=False):
    checks = []

    def add(name, status, detail, remedy=None):
        checks.append({"name": name, "status": status, "detail": detail, "remedy": remedy})

    add("python", "pass" if sys.version_info >= (3, 12) else "fail", sys.version.split()[0],
        "Use Python 3.12 for the CARLA backend; a separate worker interpreter is supported.")
    versions = {}
    for name in CORE:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
        # Source module commands work without installing package metadata.
        add(f"package:{name}", "pass" if versions[name] else "warning" if name == "poltergeist" else "fail",
            versions[name], "Install requirements.txt from the checkout, or install the project package.")
    try:
        settings = Settings()
        add("configuration", "pass", {"database_url": settings.database_url, "storage_dir": str(settings.storage_dir),
            "api_host": settings.api_host, "api_port": settings.api_port, "cors_origins": settings.cors_origins})
        parent = settings.storage_dir
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        add("storage_parent", "pass" if parent.is_dir() and os.access(parent, os.W_OK) else "warning",
            f"Nearest existing parent: {parent}; permission estimate only, no files written.",
            "Choose a writable local storage directory and database path; avoid syncing active SQLite files.")
    except ValueError as error:
        settings = None
        add("configuration", "fail", str(error), "Correct root .env / process environment settings, then restart the backend.")
    node = shutil.which("node")
    if node:
        try:
            result = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5,
                check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            version = result.stdout.strip()
            match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version)
            numbers = tuple(map(int, match.groups())) if match else (0, 0, 0)
            supported = (numbers[0] == 20 and numbers >= (20, 19, 0)) or numbers >= (22, 12, 0)
            add("node", "pass" if supported else "fail" if frontend else "warning", version,
                "Use a Node version supported by frontend/package.json, then run npm ci in frontend/.")
        except (OSError, subprocess.SubprocessError) as error:
            add("node", "fail" if frontend else "warning", str(error), "Check node --version in a new terminal.")
    else:
        add("node", "fail" if frontend else "warning", "Node not found on PATH", "Install a supported Node runtime for the dashboard.")
    if carla:
        if settings is None:
            add("carla", "fail", "Configuration must be valid before probing CARLA.")
        else:
            try:
                info = carla_probe(carla_python or sys.executable, settings.carla_host, settings.carla_port, timeout)
                add("carla", "pass", info)
                if info["synchronous_mode"]:
                    add("carla_tick_owner", "warning", "World is synchronous; the probe does not tick or change it.",
                        "Use a dedicated simulator with one tick owner; stop other scenario runners before testing.")
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                add("carla", "fail", str(error),
                    "Use a Python environment with carla==0.9.16 and one matching running server. Check CARLA_HOST/PORT.")
    else:
        add("carla", "skipped", "Not requested; no simulator connection or CARLA import was made.")
    devices = None
    if hardware:
        from simulator.reference_evidence import hardware_info
        devices = hardware_info()
    return {"schema_version": "poltergeist-doctor-v1", "checked_at": datetime.now(UTC).isoformat(),
        "passed": not any(c["status"] == "fail" for c in checks), "python_executable": sys.executable,
        "versions": versions, "checks": checks, "hardware": devices,
        "scope": "Static installation checks and optional read-only CARLA RPC; no model inference, package installation, world changes or safety/performance certification."}


def positive(value):
    import math
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 30:
        raise argparse.ArgumentTypeError("must be finite, above zero and at most 30 seconds")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend", action="store_true", help="Require a supported Node runtime")
    parser.add_argument("--carla", action="store_true", help="Read versions/map/settings from an already running server")
    parser.add_argument("--carla-python", type=Path, help="Interpreter containing the optional CARLA client")
    parser.add_argument("--timeout", type=positive, default=5.)
    parser.add_argument("--hardware", action="store_true", help="Include OS/CPU and optional device-wide GPU snapshot")
    args = parser.parse_args(argv)
    if args.carla_python and not args.carla:
        parser.error("--carla-python requires --carla")
    report = collect(frontend=args.frontend, carla=args.carla, carla_python=args.carla_python,
                     timeout=args.timeout, hardware=args.hardware)
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
