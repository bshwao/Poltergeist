"""Explicit worker-prefix provenance and retained execution lease boundaries."""

import json
import hashlib
from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4

import pytest

from shared.models import AgentRegistration
from shared.models.registry import AgentExecutionOptions
from simulator.agents import fingerprints
from simulator.agents.fingerprints import AgentFingerprintError, build_agent_snapshot, verify_agent_snapshot
from simulator.agents.process import AgentProcessError, ProcessAgent

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("backend", "evaluation", "optimization", "scenarios", "shared", "simulator")


def copy_sdk(destination):
    for package in PACKAGES:
        for source in (ROOT / package).rglob("*.py"):
            target = destination / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


@pytest.fixture
def independent(tmp_path):
    prefix = tmp_path / "runtime"
    site = prefix / "Lib/site-packages"
    copy_sdk(site)
    (prefix / "python.exe").write_bytes(b"MZ static fixture; never executed")
    package = tmp_path / "agent"
    package.mkdir()
    (package / "agent.json").write_text(json.dumps({"agent_id": "isolated-fixture", "name": "Isolated fixture",
        "version": "1", "entry_point": "agent:Agent"}))
    (package / "agent.py").write_text("# Static fixture, never executed\n")
    request = AgentRegistration(package_path=str(package), python_executable=str(prefix / "python.exe"),
        isolated_runtime={"prefix": str(prefix)}, trusted_local_code=True)
    return request, prefix


def test_legacy_serialization_omits_new_optional_isolation_fields(independent):
    request, prefix = independent
    legacy = AgentRegistration(package_path=request.package_path, python_executable=request.python_executable,
                               trusted_local_code=True)
    assert "isolated_runtime" not in legacy.model_dump(mode="json")
    snapshot = build_agent_snapshot(legacy, uuid4())
    assert not {"isolated_runtime", "framework_runtime_root", "framework_runtime_files"} & snapshot.model_dump(mode="json").keys()
    verify_agent_snapshot(snapshot)


def test_snapshots_bind_distinct_worker_and_framework_source_sets(independent):
    request, prefix = independent
    snapshot = build_agent_snapshot(request, uuid4())
    assert snapshot.isolated_runtime.prefix == str(prefix)
    assert all(Path(item.path).is_relative_to(prefix / "Lib/site-packages") for item in snapshot.runtime_files)
    assert snapshot.framework_runtime_root == str(ROOT)
    assert all(Path(item.path).is_relative_to(ROOT) for item in snapshot.framework_runtime_files)
    assert len(snapshot.runtime_files) == len(snapshot.framework_runtime_files) > 100
    verify_agent_snapshot(snapshot)
    with (prefix / "Lib/site-packages/shared/models/base.py").open("a") as output:
        output.write("\n# changed SDK\n")
    with pytest.raises(AgentFingerprintError, match="runtime_files"):
        verify_agent_snapshot(snapshot)


def test_worker_rebuilds_declared_framework_provenance_without_importing_it(independent, tmp_path, monkeypatch):
    request, prefix = independent
    framework = tmp_path / "framework"
    copy_sdk(framework)
    (framework / "backend/__init__.py").write_text("raise AssertionError('Parent code must not be imported')\n")
    snapshot = build_agent_snapshot(request, uuid4(), _framework_root=framework)
    # Origin policy is tested separately; this case exercises the worker's
    # read-only reconstruction of a parent tree different from its own imports.
    monkeypatch.setattr(fingerprints, "_verify_worker_origin", lambda value: None)
    verify_agent_snapshot(snapshot, worker=True)
    with pytest.raises(AgentFingerprintError, match="framework_runtime"):
        verify_agent_snapshot(snapshot)
    (framework / "backend/__init__.py").write_text("# changed parent input\n")
    with pytest.raises(AgentFingerprintError, match="framework_runtime_files"):
        verify_agent_snapshot(snapshot, worker=True)


def test_worker_origin_policy_rejects_core_imports_even_with_correct_prefix(independent, monkeypatch):
    request, prefix = independent
    snapshot = build_agent_snapshot(request, uuid4())
    modules = {"simulator.agents.worker": SimpleNamespace(__file__=str(prefix / "Lib/site-packages/simulator/agents/worker.py"))}
    context = SimpleNamespace(prefix=str(prefix), base_prefix=str(prefix), executable=str(prefix / "python.exe"), version_info=(3, 12),
        flags=SimpleNamespace(isolated=1, ignore_environment=1), modules=modules)
    monkeypatch.setattr(fingerprints, "sys", context)
    monkeypatch.setattr(fingerprints, "os", SimpleNamespace(name="nt"))
    fingerprints._verify_worker_origin(snapshot)
    modules["shared.models"] = SimpleNamespace(__file__=str(ROOT / "shared/models/__init__.py"))
    with pytest.raises(AgentFingerprintError, match="outside"):
        fingerprints._verify_worker_origin(snapshot)
    modules.pop("shared.models")
    context.flags.isolated = 0
    with pytest.raises(AgentFingerprintError, match="declared isolated"):
        fingerprints._verify_worker_origin(snapshot)


def test_independent_prefix_requires_explicit_matching_interpreter(independent):
    request, prefix = independent
    for interpreter in (None, str(prefix.parent / "python.exe")):
        with pytest.raises(ValueError, match="isolated"):
            AgentRegistration(package_path=request.package_path, python_executable=interpreter,
                isolated_runtime={"prefix": str(prefix)}, trusted_local_code=True)


def test_process_cleanup_failure_keeps_runtime_lease_until_cleanup_retry(independent):
    request, prefix = independent
    agent = ProcessAgent(request.package_path, python_executable=request.python_executable)
    released = []
    agent._runtime_use = SimpleNamespace(release=lambda: released.append(True))
    agent._process = SimpleNamespace(poll=lambda: 0, wait=lambda **kwargs: None,
                                     stdin=None, stdout=None, stderr=None)
    class BrokenGroup:
        def terminate(self):
            raise RuntimeError("owned descendant still present")
    agent._group = BrokenGroup()
    with pytest.raises(AgentProcessError, match="cleanup failed"):
        agent._stop()
    assert not released and agent._runtime_use is not None
    agent._group = SimpleNamespace(terminate=lambda: None)
    agent._stop()
    assert released == [True] and agent._runtime_use is None


def test_sealed_snapshots_recheck_third_party_bytes_and_reject_extra_payload(independent):
    request, prefix = independent
    root = prefix.parent
    dependency = prefix / "Lib/site-packages/dependency.bin"
    dependency.write_bytes(b"pinned dependency fixture")
    notices = root / "notices"
    notices.mkdir()
    (notices / "LICENSE.txt").write_text("fixture notice\n")
    manifest = {"schema": "poltergeist-model-capsule-v1", "target": "windows-x86_64-cpython312",
        "files": {path.relative_to(root).as_posix(): {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size}
                  for path in root.rglob("*") if path.is_file()}}
    raw = json.dumps(manifest, sort_keys=True).encode()
    (root / "capsule.json").write_bytes(raw)
    sealed = AgentRegistration.model_validate(request.model_dump() | {"isolated_runtime": {
        "prefix": str(prefix), "manifest_sha256": hashlib.sha256(raw).hexdigest()}})
    snapshot = build_agent_snapshot(sealed, uuid4())
    verify_agent_snapshot(snapshot)
    dependency.write_bytes(b"changed dependency bytes")
    with pytest.raises(AgentFingerprintError, match="runtime seal"):
        verify_agent_snapshot(snapshot)
    dependency.write_bytes(b"pinned dependency fixture")
    extra = prefix / "Lib/site-packages/extra.py"
    extra.write_text("# not in the pinned capsule\n")
    with pytest.raises(AgentFingerprintError, match="runtime seal"):
        verify_agent_snapshot(snapshot)
    extra.unlink()
    (root / "capsule.json").write_bytes(raw + b" ")
    with pytest.raises(AgentFingerprintError, match="runtime seal"):
        verify_agent_snapshot(snapshot)
