"""Bounded local capability inspection. No launch, writes or persistence access.

Ready means the reported prerequisites passed, never that an experiment or model
was executed. Only an explicit connection check opens sockets/imports CARLA in a
short-lived subprocess. Executable presence cannot establish its version.
"""

from datetime import UTC, datetime
import importlib.metadata
import subprocess
import sys

from backend import diagnostics
from shared.models.readiness import ReadinessCheck, ReadinessReport, WorkflowReadiness
from simulator import carla_launcher
from simulator.errors import SimulationError


def check(identifier, label, status, detail, next_step=None):
    return ReadinessCheck(id=identifier, label=label, status=status, detail=detail, next_step=next_step)


def first_problem(checks):
    return next((c.status for c in checks if c.status != "ready"), "ready")


def core_readiness(settings):
    report = diagnostics.collect(settings=settings, development_tools=False)
    checks = {c["name"]: c for c in report["checks"]}
    packages = [c for name, c in checks.items() if name.startswith("package:") and name != "package:poltergeist"]
    missing = [c["name"].split(":", 1)[1] for c in packages if c["status"] != "pass"]
    configuration_ok = checks["configuration"]["status"] == "pass"
    storage_ok = all(c["status"] == "pass" for name, c in checks.items() if name in {"storage_parent", "database_parent"})
    items = [
        check("runtime", "Core runtime", "ready" if checks["python"]["status"] == "pass" else "unsupported",
              f"Backend Python {checks['python']['detail']}; core requires 3.12 or newer.",
              None if checks["python"]["status"] == "pass" else "Use the supported backend environment from the First run guide."),
        check("packages", "Core dependencies", "missing" if missing else "ready",
              f"Missing packages: {', '.join(missing)}." if missing else "Required core package metadata is present in this backend environment.",
              "Complete the core installation in the First run guide." if missing else None),
        check("configuration", "Local configuration", "ready" if configuration_ok else "error",
              "Settings and SQLite URL are valid; the database was not opened." if configuration_ok else "The configured settings or database URL are unsupported.",
              None if configuration_ok else "Correct the backend configuration and restart it. See the First run guide."),
        check("storage", "Storage access estimate", "ready" if storage_ok and configuration_ok else "unknown",
              "Configured storage and database locations appear writable; no files were created." if storage_ok and configuration_ok else "Storage access could not be established by static inspection.",
              "A real run must still open storage and initialize its database." if storage_ok and configuration_ok else "Choose writable local storage and database locations, then restart the backend."),
    ]
    status = first_problem(items)
    items.append(check("execution", "Synthetic execution", "not_checked",
                       "No experiment, SDK worker or model was executed by this check.",
                       "Open Scenarios, validate a recipe, and launch with Synthetic test selected."))
    return WorkflowReadiness(status=status, checks=items, summary=(
        "Ready to try synthetic mode. Static prerequisites passed; execution is not tested here."
        if status == "ready" else "Resolve the core prerequisites below before trying synthetic mode."))


def carla_readiness(settings, synthetic, *, connection_requested=False):
    try:
        version = importlib.metadata.version("carla")
        client = check("client", "CARLA Python client", "ready" if version == diagnostics.CARLA_VERSION else "unsupported",
                       f"Installed package metadata: {version}. Required: {diagnostics.CARLA_VERSION}. Native import is not checked automatically.",
                       None if version == diagnostics.CARLA_VERSION else "Install the matching optional simulation dependencies from the First run guide.")
    except importlib.metadata.PackageNotFoundError:
        client = check("client", "CARLA Python client", "missing", "CARLA Python package metadata was not found in this backend environment.",
                       "Follow Add CARLA in the First run guide to install the optional simulation dependencies.")
    local = carla_launcher.local_host(settings.carla_host)
    installation = check("installation", "Simulator installation", "not_checked",
                         "A remote CARLA host is configured; a local installation is not required.")
    if local:
        try:
            executable = carla_launcher.find_executable(settings.carla_executable)
            expected_suffix = ".exe" if sys.platform == "win32" else ".sh"
            installation = check("installation", "Simulator installation", "ready" if executable.suffix.lower() == expected_suffix else "error",
                                 f"Executable found: {executable}. File presence does not verify its version or ability to run.",
                                 None if executable.suffix.lower() == expected_suffix else "Set CARLA_EXECUTABLE to the supported simulator launcher and restart the backend.")
        except SimulationError:
            installation = check("installation", "Simulator installation", "missing", "No configured or discoverable local simulator executable was found.",
                                 "Install CARLA 0.9.16 or set CARLA_EXECUTABLE to its launcher and restart the backend. A separately started server can also be checked.")
        except OSError:
            installation = check("installation", "Simulator installation", "unknown", "The simulator location could not be inspected.",
                                 "Check access to CARLA_EXECUTABLE and restart the backend.")
    connection = check("connection", "CARLA connection", "not_checked",
                       f"No connection attempted to {settings.carla_host}:{settings.carla_port}.",
                       "Use Check CARLA connection for a read-only check of an already running server.")
    server = check("server_version", "Client/server versions", "not_checked", "Executable names and folders do not establish the server version.",
                   "Start your matching CARLA server, then use Check CARLA connection.")
    if connection_requested:
        # DNS, socket access, native import and RPC all stay inside the same
        # bounded subprocess. Only a refused connection means not_running.
        try:
            info = diagnostics.carla_probe(sys.executable, settings.carla_host, settings.carla_port,
                                           timeout=2., check_client=client.status == "ready", include_map=False)
            connection = check("connection", "CARLA connection", "ready", "Read-only CARLA RPC succeeded; no ticks, actors or settings were changed.",
                               "Use one tick owner for live runs." if info["synchronous_mode"] else None)
            server = check("server_version", "Client/server versions", "ready", f"Client and server reported {diagnostics.CARLA_VERSION}.")
        except diagnostics.CarlaNotRunning:
            connection = check("connection", "CARLA connection", "not_running", "No RPC listener accepted a connection at the configured host and port.",
                               "Start CARLA and check its host/port, then check the connection again.")
        except diagnostics.CarlaListenerUnverified:
            connection = check("connection", "CARLA connection", "unknown", "A listener exists, but CARLA RPC has not been verified.",
                               "Install the matching client in this backend environment, then check again.")
        except diagnostics.CarlaVersionMismatch as error:
            connection = check("connection", "CARLA connection", "ready", "RPC responded, but the client/server pair is incompatible.")
            server = check("server_version", "Client/server versions", "unsupported", str(error), "Use the matching 0.9.16 client and server, then check again.")
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            connection = check("connection", "CARLA connection", "unknown", "Native import, network access or CARLA RPC failed or timed out; running state is unknown.",
                               "Check the backend client, matching server and network access, then retry. The simulator was not started or modified.")
    requirements = [client, connection, server]
    # Missing local files do not block a verified, separately started server.
    if local and connection.status != "ready":
        requirements.insert(1, installation)
    status = first_problem(requirements) if synthetic.status == "ready" else synthetic.status
    summary = ("Connection and versions verified. Ready to attempt a live run; execution remains untested."
               if status == "ready" else "CARLA prerequisites are incomplete or unchecked. Synthetic mode is separate.")
    if synthetic.status != "ready":
        summary = "Resolve the synthetic core prerequisites first, then check CARLA prerequisites."
    return WorkflowReadiness(status=status, summary=summary, checks=[client, installation, connection, server,
        check("execution", "Live execution", "not_checked", "No live experiment, driving policy or perception model was tested.",
              "After the connection check passes, validate a CARLA recipe and explicitly launch a live run.")])


def collect_readiness(settings, *, connection_requested=False):
    synthetic = core_readiness(settings)
    carla = carla_readiness(settings, synthetic, connection_requested=connection_requested)
    return ReadinessReport(checked_at=datetime.now(UTC), carla_connection_requested=connection_requested,
                           synthetic=synthetic, carla=carla)
