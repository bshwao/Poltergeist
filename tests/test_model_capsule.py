"""Capsule activation, interruption and owned generation/worker boundaries."""

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
import zipfile

import pytest

from backend.persistence_errors import ConflictError
from companion.model_assets import DownloadPaused
from companion.model_capsule import CapsuleError, CapsuleSpec, CapsuleStore, inspect_archive
from scripts import build_model_capsule as builder
from simulator.agents.runtime_lease import runtime_use_lease
from test_model_capsule_builder import runtime, wheel


@pytest.fixture
def packed(tmp_path):
    archive = tmp_path / "runtime.tar"
    runtime_spec = runtime(archive)
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    packages = []
    for name in ("poltergeist", "torch", "torchvision", "transformers", "timm", "omegaconf", "numpy", "scipy"):
        version = "1.0+cpu" if name in {"torch", "torchvision"} else "1.0"
        packages.append(wheel(wheels / f"{name}-{version}-py3-none-any.whl", name, version))
    manifest = tmp_path / "packages.json"
    manifest.write_text(json.dumps({"packages": packages}))
    output = tmp_path / "capsule"
    descriptor = builder.build(output, runtime_archive=archive, runtime_spec=runtime_spec, wheels_dir=wheels,
        packages_manifest=manifest, source_revision="d" * 40)
    source = output / "model-capsule.zip"
    spec = CapsuleSpec(descriptor["id"], descriptor["bytes"], descriptor["sha256"], descriptor["manifest_sha256"])
    return source, spec, descriptor["manifest"]


def test_activation_full_reinspection_repair_and_owned_removal(tmp_path, packed):
    source, spec, manifest = packed
    profile = tmp_path / "profile"
    store = CapsuleStore(profile, spec)
    assert store.inspect() is None and not profile.exists()
    assert inspect_archive(source, spec) == manifest
    first = store.install(source)
    assert first.interpreter.read_bytes() == b"MZfixture"
    assert first.root == store.inspect().root
    assert store.install(source).root == first.root
    corrupt = first.root / "runtime/Lib/os.py"
    corrupt.write_bytes(b"damaged")
    with pytest.raises(CapsuleError, match="changed"):
        store.inspect()
    with pytest.raises(CapsuleError, match="changed"):
        store.install(source)
    second = store.install(source, repair=True)
    assert second.root != first.root and not first.root.exists()
    assert store.inspect().generation == second.generation
    sentinel = profile / "imported-model.ckpt"
    sentinel.write_bytes(b"user bytes")
    store.remove()
    assert store.inspect() is None and not second.root.exists()
    assert sentinel.read_bytes() == b"user bytes"


def test_cancelled_staging_never_activates_and_retry_removes_only_owned_partial(tmp_path, packed):
    source, spec, _ = packed
    store = CapsuleStore(tmp_path / "profile", spec)
    pause = Event()
    def progress(state, count, total):
        if state == "installing_environment" and count:
            pause.set()
    with pytest.raises(DownloadPaused):
        store.install(source, cancelled=pause.is_set, progress=progress)
    assert not (store.directory / "active.json").exists() and store.inspect() is None
    partial = list(store.directory.glob("staging-*"))
    assert len(partial) == 1
    complete = store.install(source)
    assert not partial[0].exists() and store.inspect().root == complete.root


def test_failed_activation_preserves_previous_valid_generation(tmp_path, packed, monkeypatch):
    source, spec, _ = packed
    store = CapsuleStore(tmp_path / "profile", spec)
    first = store.install(source)
    original = store._replace_metadata
    def fail(name, data):
        if name == "active.json":
            raise OSError("fixture activation interruption")
        original(name, data)
    monkeypatch.setattr(store, "_replace_metadata", fail)
    with pytest.raises(OSError, match="interruption"):
        store.install(source, repair=True)
    assert store.inspect().root == first.root
    assert len(list(store.directory.glob("generation-*"))) == 2
    monkeypatch.setattr(store, "_replace_metadata", original)
    final = store.install(source, repair=True)
    assert not first.root.exists() and store.inspect().root == final.root
    assert len(list(store.directory.glob("generation-*"))) == 1


def test_runtime_lease_blocks_inspection_repair_removal_without_deactivating(tmp_path, packed):
    source, spec, _ = packed
    store = CapsuleStore(tmp_path / "profile", spec)
    installed = store.install(source)
    active = (store.directory / "active.json").read_bytes()
    with runtime_use_lease(installed.interpreter.parent):
        for action in (store.inspect, store.remove, lambda: store.install(source, repair=True)):
            with pytest.raises(ConflictError):
                action()
            assert (store.directory / "active.json").read_bytes() == active
            assert installed.interpreter.is_file()
    assert store.inspect().root == installed.root
    store.remove()
    assert not installed.root.exists()


def test_missing_runtime_lease_never_recreates_removed_generation(tmp_path):
    prefix = tmp_path / "absent/runtime"
    lease = runtime_use_lease(prefix)
    with pytest.raises(ConflictError, match="no longer installed"):
        lease.acquire()
    assert not prefix.parent.exists()
    lease = runtime_use_lease(prefix, allow_missing_prefix=True)
    with pytest.raises(ConflictError):
        lease.acquire()
    assert not prefix.parent.exists()


@pytest.mark.parametrize("change", ["unknown_file", "unknown_folder", "hardlink", "owner"])
def test_unrecognized_or_aliased_storage_is_preserved(tmp_path, packed, change):
    source, spec, _ = packed
    store = CapsuleStore(tmp_path / "profile", spec)
    installed = store.install(source)
    sentinel = tmp_path / "outside-data"
    sentinel.write_bytes(b"preserve")
    if change == "unknown_file":
        (installed.root / "user-notes.txt").write_bytes(b"preserve")
    elif change == "unknown_folder":
        (installed.root / "user-folder").mkdir()
    elif change == "hardlink":
        path = installed.root / "runtime/Lib/os.py"
        path.unlink()
        os.link(sentinel, path)
    elif change == "owner":
        (installed.root / ".installation.json").write_text('{}')
    before = {p.relative_to(installed.root).as_posix(): p.read_bytes() for p in installed.root.rglob("*") if p.is_file()}
    for action in (store.inspect, lambda: store.install(source, repair=True)):
        with pytest.raises(CapsuleError):
            action()
    assert before == {p.relative_to(installed.root).as_posix(): p.read_bytes() for p in installed.root.rglob("*") if p.is_file()}
    assert sentinel.read_bytes() == b"preserve"


def test_corrupt_installed_manifest_is_detected_and_rebuilt_from_trusted_archive(tmp_path, packed):
    source, spec, _ = packed
    store = CapsuleStore(tmp_path / "profile", spec)
    original = store.install(source)
    (original.root / "capsule.json").write_text('{}')
    with pytest.raises(CapsuleError, match="manifest changed"):
        store.inspect()
    repaired = store.install(source, repair=True)
    assert repaired.root != original.root and not original.root.exists()
    assert store.inspect().root == repaired.root


def test_archive_pins_cannot_be_supplied_by_its_own_modified_manifest(tmp_path, packed):
    source, spec, _ = packed
    with pytest.raises(CapsuleError, match="catalogue"):
        inspect_archive(source, replace(spec, sha256="0" * 64))
    with pytest.raises(CapsuleError, match="manifest"):
        inspect_archive(source, replace(spec, manifest_sha256="0" * 64))
    extra = tmp_path / "extra.zip"
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(extra, "w") as changed:
        for info in original.infolist():
            changed.writestr(info, original.read(info.filename))
        changed.writestr("runtime/extra.txt", b"not inventoried")
    forged = replace(spec, size=extra.stat().st_size, sha256=hashlib.sha256(extra.read_bytes()).hexdigest())
    with pytest.raises(CapsuleError):
        inspect_archive(extra, forged)


def test_real_process_exit_releases_generation_lease(tmp_path, packed):
    source, spec, _ = packed
    store = CapsuleStore(tmp_path / "profile", spec)
    installed = store.install(source)
    program = "from simulator.agents.runtime_lease import runtime_use_lease; import sys,time; lease=runtime_use_lease(sys.argv[1]); lease.acquire(); print('owned',flush=True); time.sleep(60)"
    child = subprocess.Popen([sys.executable, "-B", "-c", program, str(installed.interpreter.parent)],
        cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "owned"
        with pytest.raises(ConflictError):
            store.remove()
        child.kill()
        assert child.wait(10) != 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(10)
        child.stdout.close()
        child.stderr.close()
    store.remove()
    assert store.inspect() is None
