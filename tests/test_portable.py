"""Portable invariants independent of downloading/building a native runtime."""

import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from pydantic import ValidationError

from scripts import build_portable, portable
from shared.models.runner import RunnerCancelRequest, RunnerCapabilities, RunnerExecuteRequest
from simulator.agents.interpreter import worker_interpreter


def tiny_bundle(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "payload.txt").write_text("trusted input", encoding="utf-8")
    manifest = {"schema_version": "poltergeist-portable-v1", "files": {"payload.txt": portable.file_sha256(root / "payload.txt")}}
    (root / "bundle.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_integrity_is_read_only_and_detects_changed_missing_and_extra_files(tmp_path):
    root = tiny_bundle(tmp_path)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    assert portable.verify_bundle(root)["passed"]
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}
    (root / "payload.txt").write_text("changed")
    with pytest.raises(ValueError, match="integrity"):
        portable.verify_bundle(root)
    (root / "payload.txt").write_bytes(before["payload.txt"])
    (root / "unlisted.txt").write_text("extra")
    with pytest.raises(ValueError, match="unexpected"):
        portable.verify_bundle(root)


@pytest.mark.parametrize("relative", ["../outside", "/absolute", "C:/outside", "folder\\outside"])
def test_integrity_rejects_manifest_path_escape(tmp_path, relative):
    root = tiny_bundle(tmp_path)
    (root / "bundle.json").write_text(json.dumps({"schema_version": "poltergeist-portable-v1", "files": {relative: "a" * 64}}))
    with pytest.raises(ValueError, match="Unsafe"):
        portable.verify_bundle(root)


def test_profile_defaults_are_separate_and_do_not_create_storage(tmp_path, monkeypatch):
    bundle = tmp_path / "install"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    profile = portable.profile_path(None, bundle)
    assert profile == tmp_path / "local" / "Poltergeist" / "S1Prototype"
    assert not profile.exists()
    with pytest.raises(ValueError, match="absolute"):
        portable.profile_path("relative", bundle)
    with pytest.raises(ValueError, match="outside"):
        portable.profile_path(bundle / "data", bundle)


def test_private_configuration_ignores_developer_settings_without_opening_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "invalid database")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "developer-data"))
    monkeypatch.setenv("CARLA_AUTO_START", "true")
    settings = portable.local_settings(tmp_path / "profile")
    assert settings.api_host == settings.carla_host == "127.0.0.1"
    assert settings.carla_auto_start is False and settings.carla_quality == "Low"
    assert settings.storage_dir == tmp_path / "profile" / "storage"
    assert not settings.storage_dir.exists() and not (tmp_path / "developer-data").exists()


def test_private_configuration_does_not_accept_arbitrary_runtime_settings(tmp_path):
    (tmp_path / "config.json").write_text('{"api_host":"0.0.0.0"}')
    with pytest.raises(ValueError, match="accepts only"):
        portable.local_settings(tmp_path)


def test_worker_rejects_a_frozen_launcher_and_preserves_explicit_real_interpreter(tmp_path, monkeypatch):
    launcher = tmp_path / "launcher.exe"
    python = tmp_path / "runtime" / "python.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(launcher))
    for value in (None, launcher):
        with pytest.raises(ValueError, match="frozen launcher"):
            worker_interpreter(value)
    assert worker_interpreter(python) == python


def test_worker_default_uses_current_real_interpreter_without_path_lookup(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("PATH", "")
    assert worker_interpreter() == Path(sys.executable).absolute()


def test_builder_refuses_cache_corruption_and_offline_missing_inputs(tmp_path):
    entry = {"filename": "input.whl", "url": "https://example.invalid/input.whl", "sha256": hashlib.sha256(b"expected").hexdigest()}
    with pytest.raises(ValueError, match="Missing cached"):
        build_portable.fetch(entry, tmp_path, offline=True)
    (tmp_path / "input.whl").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        build_portable.fetch(entry, tmp_path, offline=True)


def test_builder_pruning_refuses_paths_outside_new_output(tmp_path):
    root = tmp_path / "output"
    root.mkdir()
    original = tmp_path / "existing-user-data"
    original.write_text("keep")
    for path in (original, root):
        with pytest.raises(ValueError, match="Refusing"):
            build_portable.remove_owned(path, root)
    assert original.read_text() == "keep"


def test_archive_bytes_are_reproducible_for_same_inputs(tmp_path):
    root = tiny_bundle(tmp_path)
    first, second = tmp_path / "one.zip", tmp_path / "two.zip"
    build_portable.deterministic_zip(root, first, 1700000000)
    build_portable.deterministic_zip(root, second, 1700000000)
    assert first.read_bytes() == second.read_bytes()


def test_installed_records_drop_build_specific_and_pruned_files(tmp_path):
    site = tmp_path / "runtime" / "Lib" / "site-packages"
    metadata = site / "example-1.dist-info"
    metadata.mkdir(parents=True)
    (site / "example.py").write_text("code")
    record = metadata / "RECORD"
    record.write_text("example.py,sha256=old,4\nexample-1.dist-info/direct_url.json,sha256=build-specific,20\nexample-1.dist-info/RECORD,,\n")
    build_portable.normalize_records(site, tmp_path)
    result = record.read_text()
    assert "direct_url" not in result and "build-specific" not in result
    assert "example.py,sha256=" in result and "example-1.dist-info/RECORD,," in result


def test_all_external_inputs_are_pinned_https_hashes_and_runtime_is_not_a_venv():
    inputs = json.loads(build_portable.INPUTS.read_text())
    assert inputs["runtime"]["version"] == "3.12.15"
    assert "full.tar.zst" in inputs["runtime"]["filename"]
    runtime = [e for e in inputs["wheels"] if e["role"] == "runtime"]
    assert next(e for e in runtime if e["name"] == "carla")["version"] == "0.9.16"
    assert not any(e["name"] in {"pip", "setuptools", "wheel", "pytest"} for e in runtime)
    for entry in [inputs["runtime"], *inputs["wheels"], *inputs["notices"]]:
        assert entry["url"].startswith("https://") and len(entry["sha256"]) == 64
        assert int(entry["sha256"], 16) >= 0


def test_core_lock_and_portable_inputs_cover_declared_runtime_dependencies():
    import tomllib
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    root = build_portable.ROOT
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {canonicalize_name(Requirement(value).name) for value in project["project"]["dependencies"]}
    locked = {canonicalize_name(Requirement(line).name) for line in
        (root / "requirements-core.lock").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")}
    inputs = json.loads(build_portable.INPUTS.read_text(encoding="utf-8"))
    bundled = {canonicalize_name(entry["name"]) for entry in inputs["wheels"] if entry["role"] == "runtime"}
    assert declared <= locked
    assert declared <= bundled


def test_runner_boundary_reuses_recipe_contracts_and_rejects_code_paths():
    recipe = {"name": "Boundary check", "actors": [{"id": "ego", "type": "ego_vehicle"}]}
    request = RunnerExecuteRequest(request_id=uuid4(), mode="test", scenario=recipe)
    assert request.agent_id is None
    for extra in ({"python_executable": "C:/python.exe"}, {"package_path": "C:/agent"}, {"command": "shell"}):
        with pytest.raises(ValidationError):
            RunnerExecuteRequest.model_validate(request.model_dump(mode="python") | extra)
    with pytest.raises(ValidationError):
        RunnerExecuteRequest(request_id=uuid4(), scenario=recipe)
    with pytest.raises(ValidationError):
        RunnerCancelRequest(protocol_version="unrecognized", request_id=uuid4(), simulation_id=uuid4())


def test_capabilities_never_enable_remote_execution_or_advertise_parallel_runs():
    from datetime import UTC, datetime
    value = RunnerCapabilities(checked_at=datetime.now(UTC), implementation_version="0.1.0", synthetic_status="ready", carla_status="not_checked")
    assert value.remote_execution_enabled is False and value.max_concurrent_runs == 1
    assert "carla" in value.execution_modes and value.carla_status == "not_checked"
    for field, invalid in (("remote_execution_enabled", True), ("max_concurrent_runs", 2)):
        with pytest.raises(ValidationError):
            RunnerCapabilities.model_validate(value.model_dump(mode="python") | {field: invalid})


def test_installation_validator_disables_simulator_autostart(tmp_path, monkeypatch):
    from argparse import Namespace
    from contextlib import contextmanager
    from scripts import validate_installation
    args = Namespace(examples_dir=build_portable.ROOT / "examples", mode="test", duration=.15,
                     sensors=False, carla_host="127.0.0.1", carla_port=2000)
    monkeypatch.setattr(validate_installation, "hardware_info", lambda: {})
    @contextmanager
    def inspect_settings(settings):
        assert settings.carla_auto_start is False
        assert settings.storage_dir.is_relative_to(tmp_path)
        raise RuntimeError("Stop before executing an experiment")
        yield
    monkeypatch.setattr(validate_installation, "isolated_api", inspect_settings)
    with pytest.raises(RuntimeError, match="Stop before"):
        validate_installation.validate(args, tmp_path)
