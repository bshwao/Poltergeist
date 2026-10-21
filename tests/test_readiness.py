"""Readiness is a non-executing snapshot, not simulator/agent certification."""

import builtins
import importlib.metadata
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

from backend import diagnostics
from backend.config import Settings
from backend.database import Database
from backend.main import create_app
from backend.services import readiness_service as readiness
from simulator.carla_launcher import LocalCarlaLauncher
from simulator.errors import SimulationError


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, storage_dir=tmp_path / "unused", database_url=f"sqlite:///{(tmp_path / 'unused' / 'db.sqlite').as_posix()}")


@pytest.fixture(autouse=True)
def no_native_execution(monkeypatch):
    original_import = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert name != "carla", "CARLA must not be imported in the API process"
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    monkeypatch.setattr(Database, "initialize", lambda *a: pytest.fail("Readiness initialized storage"))
    monkeypatch.setattr(LocalCarlaLauncher, "ensure_running", lambda *a, **k: pytest.fail("Readiness started CARLA"))


def client_version(monkeypatch, value="0.9.16"):
    original = importlib.metadata.version
    def version(name):
        if name == "carla":
            if value is None:
                raise importlib.metadata.PackageNotFoundError(name)
            return value
        return original(name)
    monkeypatch.setattr(readiness.importlib.metadata, "version", version)


def items(workflow):
    return {c.id: c for c in workflow.checks}


def missing_executable(*args):
    raise SimulationError("Missing simulator")


def test_static_api_is_inert_and_does_not_gate_core_on_developer_tools(settings, monkeypatch):
    monkeypatch.setattr(diagnostics.subprocess, "run", lambda *a, **k: pytest.fail("Automatic check started a subprocess"))
    monkeypatch.setattr(diagnostics.shutil, "which", lambda *a: pytest.fail("Automatic check inspected developer tools"))
    monkeypatch.setattr(readiness.carla_launcher, "find_executable", missing_executable)
    client_version(monkeypatch, None)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/readiness")
        assert client.get("/health").json() == {"status": "ok", "service": "poltergeist"}
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["schema_version"] == "poltergeist-readiness-v1"
    assert data["scope"] == "local_core" and not data["carla_connection_requested"]
    assert data["synthetic"]["status"] == "ready"
    carla = {c["id"]: c for c in data["carla"]["checks"]}
    assert carla["client"]["status"] == carla["installation"]["status"] == "missing"
    assert carla["connection"]["status"] == carla["server_version"]["status"] == "not_checked"
    assert app.state.runtime._persistence is None and app.state.runtime._worker is None
    assert not settings.storage_dir.exists()


def test_executable_name_and_client_metadata_never_establish_live_readiness(settings, monkeypatch):
    client_version(monkeypatch)
    monkeypatch.setattr(readiness.carla_launcher, "find_executable", lambda *a: Path("CARLA_0.9.16/CarlaUE4.exe" if sys.platform == "win32" else "CARLA_0.9.16/CarlaUE4.sh"))
    monkeypatch.setattr(diagnostics, "carla_probe", lambda *a, **k: pytest.fail("Static check contacted CARLA"))
    report = readiness.collect_readiness(settings)
    checks = items(report.carla)
    assert checks["installation"].status == checks["client"].status == "ready"
    assert report.carla.status == "not_checked"
    assert checks["server_version"].status == checks["execution"].status == "not_checked"


@pytest.mark.parametrize("failure,status", [
    (diagnostics.CarlaNotRunning("No listener"), "not_running"),
    (TimeoutError("Timed out"), "unknown"),
    (PermissionError("Socket forbidden"), "unknown"),
    (subprocess.TimeoutExpired("probe", 12), "unknown"),
    (diagnostics.CarlaListenerUnverified("TCP only"), "unknown"),
])
def test_failed_connection_does_not_invent_process_or_execution_state(settings, monkeypatch, failure, status):
    client_version(monkeypatch)
    monkeypatch.setattr(readiness.carla_launcher, "find_executable", lambda *a: Path("CarlaUE4.exe" if sys.platform == "win32" else "CarlaUE4.sh"))
    def probe(*args, **kwargs):
        assert kwargs == {"timeout": 2., "check_client": True, "include_map": False}
        raise failure
    monkeypatch.setattr(diagnostics, "carla_probe", probe)
    report = readiness.collect_readiness(settings, connection_requested=True)
    checks = items(report.carla)
    assert report.carla.status == checks["connection"].status == status
    assert checks["server_version"].status == checks["execution"].status == "not_checked"
    assert not settings.storage_dir.exists()


def test_explicit_rpc_checks_versions_without_requiring_discoverable_local_executable(settings, monkeypatch):
    client_version(monkeypatch)
    monkeypatch.setattr(readiness.carla_launcher, "find_executable", missing_executable)
    calls = []
    def probe(*args, **kwargs):
        calls.append((args, kwargs))
        return {"client_version": "0.9.16", "server_version": "0.9.16", "synchronous_mode": True}
    monkeypatch.setattr(diagnostics, "carla_probe", probe)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/readiness/carla-check")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    data = response.json()
    assert len(calls) == 1 and data["carla_connection_requested"]
    assert data["carla"]["status"] == "ready"
    checks = {c["id"]: c for c in data["carla"]["checks"]}
    assert checks["installation"]["status"] == "missing"
    assert checks["execution"]["status"] == "not_checked"
    assert "tick owner" in checks["connection"]["next_step"]
    assert app.state.runtime._persistence is None and not settings.storage_dir.exists()


def test_version_mismatch_is_unsupported_not_missing_or_not_running(settings, monkeypatch):
    client_version(monkeypatch)
    def probe(*a, **k):
        raise diagnostics.CarlaVersionMismatch({"client_version": "0.9.16", "server_version": "0.9.15"})
    monkeypatch.setattr(diagnostics, "carla_probe", probe)
    report = readiness.collect_readiness(settings, connection_requested=True)
    assert report.carla.status == "unsupported"
    assert items(report.carla)["connection"].status == "ready"
    assert items(report.carla)["server_version"].status == "unsupported"


def test_missing_client_does_not_get_imported_by_explicit_endpoint(settings, monkeypatch):
    client_version(monkeypatch, None)
    def probe(*a, **k):
        assert k["check_client"] is False
        raise diagnostics.CarlaListenerUnverified("Not an RPC check")
    monkeypatch.setattr(diagnostics, "carla_probe", probe)
    report = readiness.collect_readiness(settings, connection_requested=True)
    assert report.carla.status == "missing"
    assert items(report.carla)["connection"].status == "unknown"


def test_remote_host_does_not_require_local_installation(settings, monkeypatch):
    client_version(monkeypatch)
    settings.carla_host = "carla.example"
    monkeypatch.setattr(readiness.carla_launcher, "find_executable", lambda *a: pytest.fail("Remote config searched local installs"))
    report = readiness.collect_readiness(settings)
    assert report.carla.status == "not_checked"
    assert items(report.carla)["installation"].status == "not_checked"


def test_invalid_storage_configuration_blocks_core_without_creating_data(settings, monkeypatch):
    settings.database_url = "postgresql://localhost/db"
    client_version(monkeypatch, None)
    report = readiness.collect_readiness(settings)
    assert report.synthetic.status == "error"
    assert items(report.synthetic)["storage"].status == "unknown"
    assert report.carla.status == "error"
    assert not settings.storage_dir.exists()


def test_unwritable_location_is_unknown_not_a_proven_execution_failure(settings, monkeypatch):
    client_version(monkeypatch, None)
    monkeypatch.setattr(diagnostics.os, "access", lambda *a: False)
    report = readiness.collect_readiness(settings)
    assert report.synthetic.status == "unknown"
    assert items(report.synthetic)["execution"].status == "not_checked"


@pytest.mark.parametrize("database_directory", [False, True])
def test_wrong_storage_path_type_is_not_ready_and_existing_content_is_preserved(settings, monkeypatch, database_directory):
    client_version(monkeypatch, None)
    if database_directory:
        settings.storage_dir.mkdir()
        (settings.storage_dir / "db.sqlite").mkdir()
    else:
        settings.storage_dir.write_text("existing user file")
    report = readiness.collect_readiness(settings)
    assert report.synthetic.status == "unknown"
    if not database_directory:
        assert settings.storage_dir.read_text() == "existing user file"


def test_missing_core_dependency_blocks_both_workflows(settings, monkeypatch):
    original = importlib.metadata.version
    def version(name):
        if name in {"carla", "uvicorn"}:
            raise importlib.metadata.PackageNotFoundError(name)
        return original(name)
    monkeypatch.setattr(readiness.importlib.metadata, "version", version)
    report = readiness.collect_readiness(settings)
    assert report.synthetic.status == report.carla.status == "missing"
    assert "uvicorn" in items(report.synthetic)["packages"].detail


def test_unsupported_client_metadata_is_distinct_from_absent_client(settings, monkeypatch):
    client_version(monkeypatch, "0.9.15")
    report = readiness.collect_readiness(settings)
    assert report.carla.status == "unsupported"
    assert items(report.carla)["client"].status == "unsupported"
    assert items(report.carla)["connection"].status == "not_checked"


def test_probe_bounds_all_network_import_and_rpc_work_and_disables_map_downloads(monkeypatch):
    seen = []
    def run(command, **options):
        seen.append((command, options))
        return subprocess.CompletedProcess(command, 3, "POLTERGEIST_CONNECTION_REFUSED=1\n", "")
    monkeypatch.setattr(diagnostics.subprocess, "run", run)
    with pytest.raises(diagnostics.CarlaNotRunning):
        diagnostics.carla_probe("python", "localhost", 2000, timeout=2., include_map=False)
    command, options = seen[0]
    assert command[:3] == ["python", "-B", "-c"]
    assert command[-1] == "no-map" and options["timeout"] == 12
    assert "apply_settings" not in command[3] and ".tick(" not in command[3]


def test_doctor_cli_uses_the_shared_collector(monkeypatch, capsys):
    from scripts import doctor
    calls = []
    def collect(**kwargs):
        calls.append(kwargs)
        return {"passed": True, "schema_version": "poltergeist-doctor-v1"}
    monkeypatch.setattr(doctor, "collect", collect)
    assert doctor.main(["--frontend"]) == 0
    assert calls[0]["frontend"] and not calls[0]["carla"]
    assert "poltergeist-doctor-v1" in capsys.readouterr().out
