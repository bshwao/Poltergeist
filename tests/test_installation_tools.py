"""Installation diagnostics are inert; opt-in acceptance uses real isolated HTTP/workers."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import doctor, validate_installation


def test_doctor_default_never_contacts_carla_or_imports_agent_code(monkeypatch):
    before = sys.modules.get("carla")
    monkeypatch.setattr(doctor, "carla_probe", lambda *a: pytest.fail("Unexpected simulator connection"))
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    report = doctor.collect()
    assert report["passed"]
    assert next(c for c in report["checks"] if c["name"] == "carla")["status"] == "skipped"
    assert report["hardware"] is None
    assert sys.modules.get("carla") is before


def test_doctor_node_is_required_only_for_explicit_frontend_check(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    assert doctor.collect()["passed"]
    assert not doctor.collect(frontend=True)["passed"]


@pytest.mark.parametrize("value, supported", [("v20.19.0", True), ("v20.18.9", False), ("v21.0.0", False), ("v22.11.0", False), ("v22.12.0", True), ("v24.19.0", True)])
def test_doctor_node_engine_matches_dashboard_constraints(monkeypatch, value, supported):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/node")
    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, value, ""))
    assert doctor.collect(frontend=True)["passed"] is supported


def test_carla_probe_handles_native_logs_after_structured_output(monkeypatch):
    payload = {"client_version": "0.9.16", "server_version": "0.9.16", "map": "Town10HD_Opt"}
    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0,
        "INFO native startup\nPOLTERGEIST_PROBE=" + json.dumps(payload) + "\nINFO cache message\n", ""))
    assert doctor.carla_probe("python", "localhost", 2000) == payload


def test_carla_probe_failure_is_explicit_and_does_not_report_readiness(monkeypatch):
    monkeypatch.setattr(doctor, "carla_probe", lambda *a: (_ for _ in ()).throw(TimeoutError("No running server")))
    report = doctor.collect(carla=True)
    assert not report["passed"]
    check = next(c for c in report["checks"] if c["name"] == "carla")
    assert check["status"] == "fail" and "No running server" in check["detail"]


def test_doctor_invalid_configuration_never_attempts_probe(monkeypatch):
    monkeypatch.setenv("CARLA_PORT", "invalid")
    monkeypatch.setattr(doctor, "carla_probe", lambda *a: pytest.fail("Invalid settings reached CARLA"))
    report = doctor.collect(carla=True)
    assert not report["passed"]
    assert any(c["name"] == "configuration" and c["status"] == "fail" for c in report["checks"])


def test_validation_refuses_existing_output_without_touching_it(tmp_path, capsys):
    marker = tmp_path / "keep.txt"
    marker.write_text("existing user data")
    assert validate_installation.main(["--output", str(tmp_path)]) == 1
    assert marker.read_text() == "existing user data"
    assert not (tmp_path / "validation.json").exists()


@pytest.mark.parametrize("arguments", [["--seeds", "7", "7"], ["--repeats", "0"], ["--wait-timeout", "nan"],
    ["--duration", "inf"], ["--duration", ".01"], ["--agent-python", "python"],
    ["--agent-load-timeout", "nan"], ["--agent-load-timeout", "120"]])
def test_invalid_acceptance_plans_fail_before_creating_storage(tmp_path, arguments):
    directory = tmp_path / "unused"
    with pytest.raises(SystemExit):
        validate_installation.main(["--output", str(directory), *arguments])
    assert not directory.exists()


def test_failed_validation_is_retained_and_never_claims_success(tmp_path, monkeypatch):
    monkeypatch.setattr(validate_installation, "validate", lambda *a: (_ for _ in ()).throw(RuntimeError("injected failure")))
    target = tmp_path / "failed"
    assert validate_installation.main(["--output", str(target)]) == 1
    report = json.loads((target / "validation.json").read_text())
    assert report["passed"] is False and "injected failure" in report["error"]


def test_full_installation_path_registers_bundled_examples_exports_and_reopens(tmp_path, monkeypatch):
    # Prove the default user database/storage are not used by the private API.
    outside = tmp_path / "user-storage-must-not-exist"
    monkeypatch.setenv("STORAGE_DIR", str(outside))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(outside / 'user.db').as_posix()}")
    monkeypatch.setattr(validate_installation, "hardware_info", lambda: {"fixture": "integration test"})
    output = tmp_path / "acceptance"
    result = validate_installation.main(["--output", str(output), "--mode", "test", "--sensors",
        "--duration", ".15", "--seeds", "7", "--wait-timeout", "90"])
    assert result == 0, (output / "validation.json").read_text()
    report = json.loads((output / "validation.json").read_text())
    assert report["passed"] and len(report["runs"]) == 3
    assert {r["agent_inputs"]["source"] for r in report["runs"] if r["agent_inputs"]} == {"synthetic_state", "deterministic_fixture"}
    assert "persistence_reopen" in report["checks"]
    assert all(r["decisions"] == 3 for r in report["runs"])
    assert all(len(r["reports"]) == 3 for r in report["runs"])
    assert not outside.exists()


def test_bundled_starter_supports_current_goal_protocol():
    from scripts.check_agent import check_package
    directory = Path(__file__).resolve().parents[1] / "examples" / "agents" / "starter"
    report = check_package(directory, smoke_test=True)
    assert report["protocol_version"] == "1.3"
    assert report["smoke_test"]["passed"]
    assert all(r["protocol_version"] == "1.3" for r in report["smoke_test"]["results"])
