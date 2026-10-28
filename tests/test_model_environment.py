"""Integrity, ownership and execution gates for the managed model capsule."""

import io
import json
import os
from pathlib import Path
import tarfile
import zipfile

import pytest

from backend.persistence_errors import ConflictError
from companion.controller import CompanionController
from companion.model_assets import AssetError, DownloadPaused
from companion.model_environment import ModelEnvironment, digest, unpack


def prepared(tmp_path):
    env = ModelEnvironment(tmp_path)
    with env.owned():
        env.runtime.mkdir()
        env.python.write_bytes(b"fixture interpreter; never execute")
        value = {"identity": env.identity, "app_root": str(Path(__file__).resolve().parents[1]),
                 "files": {"python.exe": digest(env.python)}, "app_files": {}}
        env.receipt.write_text(json.dumps(value), encoding="utf-8")
    return env


@pytest.mark.parametrize("name", ["../escape", "/escape", "C:/escape", "dir./file"])
def test_archive_escape_is_rejected_without_writing_outside(tmp_path, name):
    archive = tmp_path / "fixture.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr(name, b"bad")
    target = tmp_path / "capsule"
    target.mkdir()
    with pytest.raises(AssetError, match="Unsafe"):
        unpack(archive, target, "wheel")
    assert list(target.iterdir()) == []


def test_backslash_paths_are_rejected_before_archive_extraction():
    from companion.model_environment import _relative
    with pytest.raises(AssetError, match="Unsafe"):
        _relative("bad\\path")


def test_archive_symlinks_and_duplicate_files_are_rejected(tmp_path):
    archive = tmp_path / "fixture.zip"
    link = zipfile.ZipInfo("link")
    link.external_attr = 0o120777 << 16
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr(link, b"outside")
    with pytest.raises(AssetError, match="Linked"):
        unpack(archive, tmp_path / "target", "runtime")


def test_pure_source_dependency_installs_sources_and_original_notices_without_running_setup(tmp_path):
    archive = tmp_path / "fixture.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        for name, data in {"src/antlr4/__init__.py": b"x=1", "PKG-INFO": b"original", "src/pkg.egg-info/PKG-INFO": b"duplicate",
                           "LICENSE.txt": b"BSD license", "setup.py": b"raise RuntimeError('must not execute')"}.items():
            member = tarfile.TarInfo("antlr4-4.9.3/" + name)
            member.size = len(data)
            stream.addfile(member, io.BytesIO(data))
    target = tmp_path / "site"
    unpack(archive, target, "antlr-source")
    assert (target / "antlr4/__init__.py").read_bytes() == b"x=1"
    assert not (target / "setup.py").exists()
    assert (target / "antlr4_python3_runtime-4.9.3.dist-info/PKG-INFO").read_bytes() == b"original"


@pytest.mark.parametrize("change", ["corrupt", "extra", "missing", "foreign-app"])
def test_environment_detects_changed_files_and_wrong_application_before_use(tmp_path, change):
    env = prepared(tmp_path)
    assert env.verify()["files"]
    if change == "corrupt": env.python.write_bytes(b"changed")
    elif change == "extra": (env.runtime / "unexpected.pth").write_text("import bad")
    elif change == "missing": env.python.unlink()
    else:
        value = json.loads(env.receipt.read_bytes())
        value["app_root"] = "another app"
        env.receipt.write_text(json.dumps(value))
    with pytest.raises(AssetError):
        with env.use():
            pytest.fail("Damaged capsule became executable")


def test_use_lease_excludes_repair_or_removal_then_releases_and_preserves_other_data(tmp_path):
    env = prepared(tmp_path)
    sentinel = tmp_path / "imported-model.txt"
    sentinel.write_text("user data")
    with env.use():
        with pytest.raises(ConflictError):
            ModelEnvironment(tmp_path).remove()
    env.remove()
    assert not env.python.exists() and not env.receipt.exists()
    assert sentinel.read_text() == "user data"


def test_cancelled_integrity_check_never_grants_use_and_releases_lease(tmp_path):
    env = prepared(tmp_path)
    with pytest.raises(DownloadPaused):
        with env.use(lambda: True):
            pytest.fail("Cancelled check became executable")
    env.verify()


def test_unknown_and_unowned_directories_are_preserved(tmp_path):
    env = ModelEnvironment(tmp_path)
    env.directory.mkdir(parents=True)
    sentinel = env.directory / "user-data"
    sentinel.write_text("keep")
    with pytest.raises(AssetError, match="ownership"):
        env.remove()
    assert sentinel.read_text() == "keep"


def test_capsule_hardlinks_are_refused_before_integrity_or_removal(tmp_path):
    env = prepared(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"preserve")
    os.link(outside, env.runtime / "linked-file")
    with pytest.raises(AssetError, match="unlinked"):
        env.verify()
    with pytest.raises(AssetError, match="unlinked"):
        env.remove()
    assert outside.read_bytes() == b"preserve"


def test_controller_requires_inference_and_explicit_trust_before_enabling(tmp_path):
    controller = CompanionController(tmp_path)
    controller.open()
    try:
        with pytest.raises(ValueError, match="trust"):
            controller.enable_managed_model()
        with pytest.raises(ConflictError, match="inference"):
            controller.enable_managed_model(trusted_local_code=True)
        assert controller._runtime is None
    finally:
        assert controller.close()


def test_included_starter_registers_once_without_model_download_and_executes(tmp_path):
    from companion.ui import installation_recipe
    from threading import Event
    from time import monotonic
    controller = CompanionController(tmp_path)
    controller.open()
    try:
        with pytest.raises(ValueError, match="trust"):
            controller.enable_starter()
        starter = controller.enable_starter(trusted_local_code=True)
        assert controller.enable_starter(trusted_local_code=True).id == starter.id
        run = controller.submit(installation_recipe(), mode="test", agent_id=starter.id)
        deadline = monotonic() + 30
        while monotonic() < deadline:
            record = controller._worker.get(run.simulation.id)
            if record.result is not None:
                break
            Event().wait(.05)
        assert record.simulation.status.value == "completed", record.simulation.error
        assert not (tmp_path / "models").exists()
        assert not (tmp_path / "environments").exists()
    finally:
        assert controller.close()


def test_supported_pack_has_complete_unique_https_pins_and_cpu_choice(tmp_path):
    env = ModelEnvironment(tmp_path)
    lock = env.lock
    assert lock["device"] == "cpu"
    entries = [lock["runtime"], *lock["packages"]]
    assert len({e["filename"].lower() for e in entries}) == len(entries)
    from companion.model_assets import ModelAsset
    for entry in entries:
        ModelAsset("test-asset", entry["filename"], entry["url"], entry["size"], entry["sha256"])
    assert next(e for e in entries if e["name"] == "torch")["version"] == "2.6.0+cpu"
