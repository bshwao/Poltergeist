"""Desktop model lifecycle with byte fixtures; these do not claim AI inference."""

import hashlib
import json
from pathlib import Path
import sqlite3
from threading import Event
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.persistence_errors import ConflictError
from companion.controller import CompanionController
from companion.model_assets import AssetStore, ModelAsset
from companion.model_catalog import load_bundled_capsule
from companion.model_capsule import CapsuleError
from companion.model_readiness import ModelReadiness, ModelReadinessError
from scripts import build_portable
from simulator.agents.runtime_lease import runtime_use_lease
from test_model_capsule import packed


def wait_for(check):
    deadline = monotonic() + 20
    while monotonic() < deadline:
        result = check()
        if result:
            return result
        Event().wait(.01)
    raise AssertionError("Owned environment fixture operation did not finish")


def test_missing_parent_road_raster_backend_fails_before_checkpoint_or_worker(monkeypatch, tmp_path):
    import builtins
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "cv2":
            raise ModuleNotFoundError("cv2")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    probe = ModelReadiness()
    with pytest.raises(ModelReadinessError, match="road rasters"):
        probe.run(SimpleNamespace(), tmp_path / "not-read.ckpt")
    assert probe.last_failure["stage"] == "road_raster"
    assert probe.last_failure["type"] == "ModuleNotFoundError"
    assert not probe.retained


@pytest.fixture
def bundled(tmp_path, packed):
    source, spec, _ = packed
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    build_portable.embed_model_capsule(source, bundle)
    inventory = {path.relative_to(bundle).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in bundle.rglob("*") if path.is_file()}
    (bundle / "bundle.json").write_text(json.dumps({"schema_version": "poltergeist-portable-v1", "files": inventory}))
    return bundle


@pytest.fixture
def managed(tmp_path, bundled, monkeypatch):
    controller = CompanionController(tmp_path / "profile", bundle=bundled)
    controller.open()
    manager = controller._model_manager()
    data = b"model byte fixture, not trained weights"
    asset = ModelAsset("fixture-weights", "weights.ckpt", "https://fixture.invalid/model", len(data), hashlib.sha256(data).hexdigest())
    store = AssetStore(controller.profile, asset)
    store._own()
    store.path.write_bytes(data)
    store._lease.release()
    manager._store = store
    class FixtureProbe:
        retained = False
        def close(self):
            return True
        def run(self, installed, checkpoint, **kwargs):
            return {"schema": "poltergeist-managed-model-readiness-v1", "passed": True,
                "generation": installed.generation, "checkpoint_sha256": asset.sha256,
                "scope": "Fixture receipt only; no code or model executed"}
    manager._probe = FixtureProbe()
    monkeypatch.setattr("companion.model_manager.sys", SimpleNamespace(platform="win32", maxsize=2**63-1))
    monkeypatch.setattr("companion.model_manager.platform", SimpleNamespace(machine=lambda: "AMD64"))
    yield controller, manager
    assert controller.close()


def settled(controller):
    return wait_for(lambda: controller.model_status() if not controller.model_status()["busy"] else None)


def test_catalogue_requires_its_bundle_pin_and_exact_capsule_size(bundled):
    selected = load_bundled_capsule(bundled)
    assert selected.spec.id == "plant2-cpu-win312-r1"
    assert load_bundled_capsule(None) is None
    catalogue = bundled / "modelpacks/catalog.json"
    original = catalogue.read_bytes()
    catalogue.write_bytes(original + b" ")
    with pytest.raises(CapsuleError, match="catalogue"):
        load_bundled_capsule(bundled)
    catalogue.write_bytes(original)
    with selected.source.open("ab") as output:
        output.write(b"changed")
    with pytest.raises(CapsuleError, match="changed"):
        load_bundled_capsule(bundled)


def test_environment_lifecycle_trust_fresh_registration_and_reopen(managed):
    controller, manager = managed
    with pytest.raises(ValueError, match="explicit trust"):
        controller.prepare_model("install_environment")
    assert not (controller.profile / "model-environments").exists()
    controller.prepare_model("install_environment", trusted_local_code=True)
    ready = settled(controller)
    assert ready["environment"]["state"] == "ready"
    request = manager.checked_registration()
    assert request.isolated_runtime.manifest_sha256 == manager._pack.spec.manifest_sha256
    assert request.python_executable == str(Path(request.isolated_runtime.prefix) / "python.exe")
    assert request.configuration["device"] == "cpu" and request.trusted_local_code
    assert manager.matches(request.isolated_runtime.prefix)
    first = manager._capsules.inspect()
    (first.root / "runtime/Lib/os.py").write_text("# corrupt fixture\n")
    with pytest.raises(CapsuleError, match="changed"):
        manager.checked_registration()
    assert not manager.matches(request.isolated_runtime.prefix)
    controller.prepare_model("install_environment", repair=True, trusted_local_code=True)
    assert settled(controller)["environment"]["state"] == "ready"
    second = manager._capsules.inspect()
    assert second.generation != first.generation and not first.root.exists()
    assert controller.close()
    controller.open()
    assert controller.model_status()["environment"]["state"] == "unchecked"
    with pytest.raises(ConflictError, match="Check the model environment"):
        controller._models.checked_registration()


def test_invalid_readiness_receipt_does_not_enable_agent(managed):
    controller, manager = managed
    manager._probe.run = lambda *args, **kwargs: {"passed": False}
    controller.prepare_model("install_environment", trusted_local_code=True)
    result = settled(controller)
    assert result["environment"]["state"] == "error" and manager._ready is None
    assert not controller.snapshot()["agents"]


def test_running_runtime_blocks_environment_mutations_and_checks(managed):
    controller, manager = managed
    controller.start()
    for action in ("install_environment", "inspect_environment", "remove_environment"):
        with pytest.raises(ConflictError, match="Stop the local runtime"):
            controller.prepare_model(action, trusted_local_code=True)
    assert not (controller.profile / "model-environments").exists()


@pytest.mark.parametrize("action,repair,blocked", [("install_environment", False, True),
    ("inspect_environment", False, True), ("remove_environment", False, True),
    ("download", True, True), ("remove", False, True), ("download", False, False)])
def test_runtime_admission_waits_for_model_mutations_but_allows_weight_download(managed, monkeypatch, action, repair, blocked):
    controller, manager = managed
    entered, release = Event(), Event()
    def work(*args):
        entered.set()
        assert release.wait(15)
    monkeypatch.setattr(manager, "_work", work)
    controller.prepare_model(action, repair=repair, trusted_local_code=True)
    assert entered.wait(5)
    try:
        assert controller.model_status()["execution_blocked"] is blocked
        if blocked:
            with pytest.raises(ConflictError, match="model preparation"):
                controller.start()
            assert controller._runtime is None
        else:
            controller.start()
            assert controller._runtime is not None
            assert controller.stop()
    finally:
        release.set()
        settled(controller)
    assert not controller.model_status()["execution_blocked"]


def test_weights_cannot_be_removed_while_another_process_owns_environment(managed):
    controller, manager = managed
    controller.prepare_model("install_environment", trusted_local_code=True)
    assert settled(controller)["environment"]["state"] == "ready"
    installed = manager._capsules.inspect()
    original = manager._store.path.read_bytes()
    with runtime_use_lease(installed.interpreter.parent):
        controller.prepare_model("remove")
        assert settled(controller)["state"] == "error"
        assert manager._store.path.read_bytes() == original
    controller.prepare_model("remove")
    assert settled(controller)["state"] == "absent"
    controller.prepare_model("remove_environment")
    assert settled(controller)["environment"]["state"] == "absent"


def test_retained_probe_keeps_profile_and_generation_owned_until_cleanup(managed):
    controller, manager = managed
    release = Event()
    class RetainedProbe:
        retained = False
        def run(self, installed, checkpoint, **kwargs):
            self.lease = runtime_use_lease(installed.interpreter.parent)
            self.lease.acquire()
            self.retained = True
            raise ModelReadinessError("Fixture process remains owned")
        def close(self):
            if not release.is_set():
                return False
            if self.retained:
                self.lease.release()
                self.retained = False
            return True
    manager._probe = RetainedProbe()
    try:
        controller.prepare_model("install_environment", trusted_local_code=True)
        wait_for(lambda: manager._future.done())
        assert controller.model_status()["model_process_retained"]
        assert not controller.close()
        other = CompanionController(controller.profile)
        with pytest.raises(ConflictError, match="already open"):
            other.open()
        with pytest.raises(ConflictError):
            manager._capsules.remove()
    finally:
        release.set()
        wait_for(lambda: manager._future.done())
        controller.pause_model_download()
        settled(controller)
    assert controller.close()


@pytest.mark.parametrize("kind", ["prefix", "weights"])
def test_saved_waiting_model_jobs_block_removal_without_database_writes(tmp_path, kind):
    controller = CompanionController(tmp_path / "profile")
    controller.open()
    database = controller.profile / "database/poltergeist.db"
    database.parent.mkdir()
    prefix = controller.profile / "model-environments/r1/generation-a/runtime"
    fingerprint = lambda path: {"path": str(path), "sha256": "a" * 64, "size_bytes": 0}
    snapshot = {"registration_id": str(uuid4()), "package_path": str(controller.profile / "agent"),
        "python_executable": str(prefix / "python.exe"), "python_sha256": "a" * 64, "python_size_bytes": 0,
        "manifest": {"agent_id": "guard-fixture", "name": "Guard fixture", "version": "1", "entry_point": "agent:Agent"},
        "configuration": {}, "observation_mode": "simulator_state", "files": [fingerprint("agent.py")],
        "package_sha256": "b" * 64}
    if kind == "prefix":
        snapshot.update(isolated_runtime={"prefix": str(prefix)},
            runtime_files=[fingerprint(prefix / "Lib/site-packages/simulator/__init__.py")],
            framework_runtime_root=str(Path(__file__).resolve().parents[1]),
            framework_runtime_files=[fingerprint(Path(__file__).resolve())])
    else:
        snapshot["model_files"] = [fingerprint(controller.profile / "models/model/weights.ckpt")]
    from shared.models.registry import AgentSnapshot
    AgentSnapshot.model_validate_json(json.dumps(snapshot))
    with sqlite3.connect(database) as connection:
        connection.executescript("CREATE TABLE simulations(id TEXT,status TEXT); CREATE TABLE simulation_jobs(simulation_id TEXT,agent_snapshot TEXT);")
        connection.execute("INSERT INTO simulations VALUES('fixture','queued')")
        connection.execute("INSERT INTO simulation_jobs VALUES('fixture',?)", (json.dumps(snapshot),))
    before = database.read_bytes()
    try:
        for action in ("remove", "install_environment", "remove_environment"):
            with pytest.raises(ConflictError, match="saved model jobs"):
                controller.prepare_model(action, trusted_local_code=True)
        assert database.read_bytes() == before
        assert not (controller.profile / "models").exists()
    finally:
        assert controller.close()
