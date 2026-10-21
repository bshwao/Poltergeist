"""Shared read-only installation diagnostics for the CLI and local API.

Default collection never imports CARLA or connects to storage/the simulator.
"""

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
from backend.database import Database

CORE = ("poltergeist", "fastapi", "uvicorn", "pydantic", "pydantic-settings", "PyYAML", "sqlalchemy")
CARLA_VERSION = "0.9.16"
_PROBE = """
import json,sys,socket
try:
    with socket.create_connection((sys.argv[1],int(sys.argv[2])), timeout=float(sys.argv[3])):
        pass
except ConnectionRefusedError:
    print('POLTERGEIST_CONNECTION_REFUSED=1')
    sys.exit(3)
if len(sys.argv)>4 and sys.argv[4]=='metadata-only':
    print('POLTERGEIST_LISTENER_UNVERIFIED=1')
    sys.exit(4)
import carla
c=carla.Client(sys.argv[1],int(sys.argv[2])); c.set_timeout(float(sys.argv[3]))
client=c.get_client_version(); server=c.get_server_version()
if client != '0.9.16' or server != '0.9.16':
    print('POLTERGEIST_VERSION_MISMATCH='+json.dumps({'client_version':client,'server_version':server}))
    sys.exit(2)
w=c.get_world(); s=w.get_settings()
print('POLTERGEIST_PROBE='+json.dumps({'client_version':client,'server_version':server,'map':None if len(sys.argv)>5 and sys.argv[5]=='no-map' else w.get_map().name,
    'synchronous_mode':s.synchronous_mode,'no_rendering_mode':s.no_rendering_mode,
    'fixed_delta_seconds':s.fixed_delta_seconds}))
"""


class CarlaVersionMismatch(RuntimeError):
    def __init__(self, versions):
        self.versions = versions
        super().__init__(f"Expected CARLA {CARLA_VERSION} client/server; received "
                         f"{versions['client_version']} / {versions['server_version']}")


class CarlaNotRunning(RuntimeError):
    """No listener accepted a connection; process existence is not known."""


class CarlaListenerUnverified(RuntimeError):
    """Listener found, but a supported client was not available for RPC."""


def carla_probe(python, host, port, timeout=5.0, *, check_client=True, include_map=True):
    """Keep a blocked native RPC/import outside the doctor process deadline."""
    completed = subprocess.run([str(python), "-B", "-c", _PROBE, host, str(port), str(timeout),
                                "rpc" if check_client else "metadata-only", "map" if include_map else "no-map"],
        capture_output=True, text=True, timeout=timeout + 10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for line in completed.stdout.splitlines():
        if line == "POLTERGEIST_CONNECTION_REFUSED=1":
            raise CarlaNotRunning("No listener accepted a connection at the configured CARLA endpoint")
        if line == "POLTERGEIST_LISTENER_UNVERIFIED=1":
            raise CarlaListenerUnverified("A listener exists but CARLA RPC was not verified")
        if line.startswith("POLTERGEIST_VERSION_MISMATCH="):
            raise CarlaVersionMismatch(json.loads(line.split("=", 1)[1]))
    if completed.returncode:
        detail = (completed.stderr.strip().splitlines() or ["CARLA probe failed"])[-1]
        raise RuntimeError(detail[:500])
    # The native CARLA library can flush informational cache logs on shutdown.
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith("POLTERGEIST_PROBE="):
            return json.loads(line.removeprefix("POLTERGEIST_PROBE="))
    raise RuntimeError("CARLA probe returned no structured result; inspect the selected client environment")


def writable_location(path, *, directory=False):
    """Permission estimate only. Never create a directory or write a probe file."""
    parent = Path(path)
    if parent.exists():
        if directory and not parent.is_dir() or not directory and not parent.is_file():
            return False, f"Expected {'directory' if directory else 'database file'}: {parent}"
        if not directory:
            return (os.access(parent, os.R_OK | os.W_OK) and os.access(parent.parent, os.W_OK | os.X_OK),
                    f"Existing file and journal directory: {parent}")
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    return parent.is_dir() and os.access(parent, os.W_OK | os.X_OK), f"Nearest existing parent: {parent}"


def collect(*, settings=None, development_tools=True, frontend=False, carla=False, carla_python=None, timeout=5., hardware=False):
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
        settings = settings if settings is not None else Settings()
        # Construction validates the supported driver/URL options without
        # connecting, migrating or creating any files.
        database = Database(settings.database_url)
        add("configuration", "pass", {"database_url": settings.database_url, "storage_dir": str(settings.storage_dir),
            "api_host": settings.api_host, "api_port": settings.api_port, "cors_origins": settings.cors_origins})
        writable, detail = writable_location(settings.storage_dir, directory=True)
        add("storage_parent", "pass" if writable else "warning",
            f"{detail}; permission estimate only, no files written.",
            "Choose a writable local storage directory and database path; avoid syncing active SQLite files.")
        if database.file_path is not None:
            writable, detail = writable_location(database.file_path)
            add("database_parent", "pass" if writable else "warning",
                f"{detail}; database is not opened or migrated.",
                "Choose a writable local database path and restart the backend.")
    except ValueError as error:
        settings = None
        add("configuration", "fail", str(error), "Correct root .env / process environment settings, then restart the backend.")
    if development_tools:
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
