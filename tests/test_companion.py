"""Desktop ownership and real durable execution, without a desktop or CARLA."""

import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
from time import monotonic

import pytest
from pydantic import ValidationError

from backend.dependencies import ApiRuntime
from backend.database import Database
from backend.persistence_errors import ConflictError
from companion.controller import CompanionController, MAX_IMPORT_BYTES, read_json
from companion.profile import CompanionConfig, configure_gui_environment, load_config, local_settings, profile_path
from scenarios import validate_scenario
from simulator import SimulationRunner
from simulator.test_adapter import TestSimulationAdapter


def scene():
    return validate_scenario({"name": "Desktop persistence check", "duration_s": .2,
                              "actors": [{"id": "ego", "type": "ego_vehicle", "speed_mps": 2}]})


def wait_for(predicate, timeout=30):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        value = predicate()
        if value:
            return value
        Event().wait(.01)
    raise AssertionError("Desktop workflow did not reach the expected state")


@pytest.fixture
def desktop(tmp_path):
    controller = CompanionController(tmp_path / "profile")
    controller.open()
    yield controller
    assert controller.close()


def completed(controller, id):
    return wait_for(lambda: (record if (record := controller._worker.get(id)).result is not None else None))


def test_construction_is_inert_and_single_instance_owns_stopped_profile(tmp_path):
    profile = tmp_path / "profile"
    first, second = CompanionController(profile), CompanionController(profile)
    assert not profile.exists()
    first.open()
    try:
        with pytest.raises(ConflictError, match="already open"):
            second.open()
        assert first.snapshot()["queue"]["state"] == "stopped"
        assert not (profile / "database").exists()
        first.start()
        assert first.stop()
        with pytest.raises(ConflictError):
            second.open()
    finally:
        assert first.close()
    second.open()  # A stale lock file does not prevent another launch.
    assert second.close()


def test_instance_lock_excludes_another_process(desktop):
    program = "from companion.controller import CompanionController; import sys; c=CompanionController(sys.argv[1]); c.open()"
    result = subprocess.run([sys.executable, "-c", program, str(desktop.profile)], capture_output=True, text=True, timeout=45)
    assert result.returncode != 0
    assert "already open" in result.stderr


def test_settings_ignore_all_ambient_configuration_without_changing_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///should-never-be-created.db")
    monkeypatch.setenv("CARLA_HOST", "outside.example")
    monkeypatch.setenv("CARLA_AUTO_START", "true")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "wrong-storage"))
    before = dict(os.environ)
    profile = tmp_path / "profile"
    settings = local_settings(profile, CompanionConfig())
    assert settings.carla_host == "127.0.0.1" and not settings.carla_auto_start
    assert settings.storage_dir == profile / "storage"
    assert Database(settings.database_url).file_path == profile / "database" / "poltergeist.db"
    assert dict(os.environ) == before
    assert not profile.exists()


def test_profile_is_absolute_and_outside_bundle(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="absolute"):
        profile_path("relative")
    with pytest.raises(ValueError, match="outside"):
        profile_path(tmp_path / "bundle" / "data", bundle=tmp_path / "bundle")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert profile_path() == tmp_path / "Poltergeist" / "CompanionPrototype"


def test_gui_uses_only_bundled_tcl_resources(tmp_path, monkeypatch):
    monkeypatch.setenv("TCL_LIBRARY", "ambient developer Tcl")
    monkeypatch.setenv("TK_LIBRARY", "ambient developer Tk")
    configure_gui_environment(tmp_path)
    assert Path(os.environ["TCL_LIBRARY"]) == tmp_path / "runtime" / "tcl" / "tcl8.6"
    assert Path(os.environ["TK_LIBRARY"]) == tmp_path / "runtime" / "tcl" / "tk8.6"


@pytest.mark.parametrize("folder", ["profile with spaces", "literal %20 profile"])
def test_sqlite_url_preserves_the_exact_profile_path(tmp_path, folder):
    profile = tmp_path / folder
    settings = local_settings(profile, CompanionConfig())
    assert Database(settings.database_url).file_path == profile / "database" / "poltergeist.db"


def test_settings_save_requires_stopped_worker_and_preserves_prior_file_on_invalid_update(desktop):
    desktop.configure(CompanionConfig(carla_port=2100, carla_quality="Low"))
    original = (desktop.profile / "companion.json").read_bytes()
    assert load_config(desktop.profile).carla_port == 2100
    with pytest.raises(ValueError, match="Select CARLA"):
        desktop.configure(CompanionConfig(carla_auto_start=True))
    assert (desktop.profile / "companion.json").read_bytes() == original
    desktop.start()
    with pytest.raises(ConflictError, match="Stop"):
        desktop.configure(CompanionConfig(carla_port=2200))
    assert list(desktop.profile.glob("*.tmp")) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows CARLA installation layout")
def test_windows_settings_reject_incomplete_shipping_copy(desktop, tmp_path):
    installation = tmp_path / "CARLA"
    shipping = installation / "CarlaUE4" / "Binaries" / "Win64" / "CarlaUE4-Win64-Shipping.exe"
    shipping.parent.mkdir(parents=True)
    shipping.write_bytes(b"layout fixture only")
    with pytest.raises(ValueError, match="complete CARLA"):
        desktop.configure(CompanionConfig(carla_executable=str(shipping), carla_auto_start=True))
    launcher = installation / "CarlaUE4.exe"
    launcher.write_bytes(b"layout fixture only")
    with pytest.raises(ValueError, match="complete CARLA"):
        desktop.configure(CompanionConfig(carla_executable=str(launcher), carla_auto_start=True))
    (installation / "Engine" / "Content" / "Internationalization").mkdir(parents=True)
    desktop.configure(CompanionConfig(carla_executable=str(launcher), carla_auto_start=True))
    assert desktop.config.carla_executable == str(launcher)


def test_corrupt_or_future_configuration_is_reported_instead_of_reset(tmp_path):
    tmp_path.joinpath("companion.json").write_text('{"schema_version":"future"}')
    with pytest.raises(ValidationError):
        CompanionController(tmp_path)
    assert "future" in tmp_path.joinpath("companion.json").read_text()


def test_real_synthetic_run_export_and_history_survive_full_controller_restart(desktop):
    desktop.start()
    accepted = desktop.submit(scene(), mode="test")
    record = completed(desktop, accepted.simulation.id)
    assert record.simulation.status.value == "completed"
    assert record.result.telemetry_frames == 5
    assert record.result.simulation.mode == "test"
    exported = desktop.export(record.simulation.id)
    report = json.loads(exported.read_bytes())
    assert report["record"]["simulation"]["id"] == str(record.simulation.id)
    assert b"<!doctype html>" in desktop.export(record.simulation.id, format="html").read_bytes().lower()
    assert desktop.close()
    reopened = CompanionController(desktop.profile)
    reopened.open()
    try:
        snapshot = reopened.start()
        assert snapshot["runs"][0]["id"] == str(record.simulation.id)
        assert snapshot["runs"][0]["status"] == "completed"
        assert reopened._worker.persistence.read_output(record.simulation.id).result == record.result
        assert "Unavailable" in snapshot["hosted"]
    finally:
        assert reopened.close()


def test_report_import_is_read_only_and_does_not_recreate_jobs_or_agent_trust(desktop, tmp_path):
    desktop.start()
    run = desktop.submit(scene(), mode="test")
    completed(desktop, run.simulation.id)
    source = desktop.export(run.simulation.id)
    before = source.read_bytes()
    assert desktop.stop()
    imported = desktop.import_report(source)
    assert imported.parent == desktop.profile / "imported-reports"
    assert json.loads(imported.read_bytes()) == json.loads(before)
    assert desktop.import_report(source) == imported
    assert source.read_bytes() == before
    assert not desktop.snapshot()["runs"]
    assert len(desktop.start()["runs"]) == 1
    invalid = tmp_path / "not-a-report.json"
    invalid.write_text('{"schema":"poltergeist-run-report-v1","record":{}}')
    with pytest.raises(ValidationError):
        desktop.import_report(invalid)
    assert len(list(imported.parent.glob("*.json"))) == 1


def test_import_is_bounded_and_rejects_nonfinite_json(tmp_path):
    oversized = tmp_path / "huge.json"
    oversized.write_bytes(b" " * (MAX_IMPORT_BYTES + 1))
    with pytest.raises(ValueError, match="10 MiB"):
        read_json(oversized)
    oversized.write_text('{"value":NaN}')
    with pytest.raises(ValueError, match="Nonfinite"):
        read_json(oversized)


def test_native_shutdown_timeout_keeps_profile_owned_until_cleanup(tmp_path):
    entered, release = Event(), Event()
    class BlockingOpen(TestSimulationAdapter):
        def open(self, scenario):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test did not release native call")
            return super().open(scenario)
    def factory(settings):
        settings.simulation_shutdown_timeout_s = .01
        return ApiRuntime(settings, runner_factory=lambda **kwargs: SimulationRunner(BlockingOpen(), **kwargs))
    desktop = CompanionController(tmp_path, runtime_factory=factory)
    desktop.open()
    desktop.start()
    run = desktop.submit(scene(), mode="test")
    try:
        assert entered.wait(5)
        assert not desktop.close()
        assert desktop.snapshot()["queue"]["state"] == "stopping"
        with pytest.raises(ConflictError, match="still stopping"):
            desktop.start()
        other = CompanionController(tmp_path)
        with pytest.raises(ConflictError):
            other.open()
        release.set()
        record = completed(desktop, run.simulation.id)
        assert record.simulation.status.value == "cancelled"
        assert wait_for(desktop.close)
        other.open()
        assert other.close()
    finally:
        release.set()
        wait_for(desktop.close)


def test_diagnostics_do_not_start_worker_or_create_database(desktop):
    report = desktop.diagnostics()
    assert not report["hosted_pairing_available"]
    assert report["runtime"]["state"] == "stopped"
    assert not report["readiness"]["carla_connection_requested"]
    assert not (desktop.profile / "database").exists()


def test_custom_agent_registration_requires_explicit_local_trust_and_runs_real_worker(desktop):
    package = Path(__file__).resolve().parents[1] / "examples" / "agents" / "starter"
    desktop.start()
    with pytest.raises(ValidationError, match="trusted_local_code"):
        desktop.register_agent(package)
    agent = desktop.register_agent(package, trusted_local_code=True)
    accepted = desktop.submit(scene(), mode="test", agent_id=agent.id)
    record = completed(desktop, accepted.simulation.id)
    assert record.simulation.status.value == "completed", record.simulation.error
    assert record.result.simulation.agent == "custom"
    assert record.result.simulation.agent_snapshot.registration_id == agent.id
    assert desktop.snapshot()["agents"][0]["id"] == str(agent.id)
