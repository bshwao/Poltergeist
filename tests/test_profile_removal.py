"""Uninstall may erase only its explicitly owned, inactive plain profile."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion.profile import instance_lease
from backend.persistence_errors import ConflictError
from backend.worker_lock import WorkerLease
from scripts.remove_owned_profile import remove_owned_profile

OWNER = "Poltergeist.Private.Companion.Validation"


def profile(root, *, encoding="utf-8"):
    path = root / "CompanionPrototype"
    path.mkdir(parents=True)
    text = f"[Owner]\nAppId={OWNER}\nProfile={path}\n"
    (path / "installation-owner.ini").write_text(text, encoding=encoding)
    (path / "database").mkdir()
    (path / "database" / "poltergeist.db").write_text("disposable fixture")
    (path / "storage" / "recordings").mkdir(parents=True)
    (path / "storage" / "recordings" / "sample.bin").write_bytes(b"disposable recording")
    return path


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_owned_profile_removal_preserves_its_parent_and_other_profiles(tmp_path, encoding):
    target = profile(tmp_path / "literal %20 directory", encoding=encoding)
    other = tmp_path / "unrelated-user-data.txt"
    other.write_text("keep me")
    result = remove_owned_profile(target, OWNER)
    assert result["removed"] and not target.exists()
    assert target.parent.exists() and other.read_text() == "keep me"


def test_active_companion_profile_is_preserved(tmp_path):
    target = profile(tmp_path)
    lease = instance_lease(target)
    lease.acquire()
    try:
        with pytest.raises(ConflictError):
            remove_owned_profile(target, OWNER)
        assert (target / "storage" / "recordings" / "sample.bin").read_bytes() == b"disposable recording"
    finally:
        lease.release()


def test_active_database_worker_preserves_profile_when_companion_is_closed(tmp_path):
    target = profile(tmp_path)
    (target / "companion.json").write_text(json.dumps({"carla_port": 2100}))
    (target / "logs").mkdir()
    (target / "logs" / "companion.log").write_text("retain diagnostics")
    user_files = {path.relative_to(target): path.read_bytes()
                  for path in target.rglob("*") if path.is_file()}
    worker = WorkerLease(SimpleNamespace(file_path=target / "database" / "poltergeist.db"))
    desktop = instance_lease(target)
    worker.acquire()
    try:
        # The database worker alone must prevent removal; no desktop lease is held.
        desktop.acquire()
        desktop.release()
        with pytest.raises(ConflictError, match="database"):
            remove_owned_profile(target, OWNER)
        assert target.is_dir()
        for relative, content in user_files.items():
            assert (target / relative).read_bytes() == content
        # Cleanup must release the desktop lease acquired before the DB refusal.
        desktop.acquire()
        desktop.release()
    finally:
        desktop.release()
        worker.release()


def test_foreign_or_unowned_profile_is_preserved(tmp_path):
    target = profile(tmp_path)
    with pytest.raises(ValueError, match="does not own"):
        remove_owned_profile(target, "another-application")
    assert (target / "database" / "poltergeist.db").exists()
    (target / "installation-owner.ini").unlink()
    with pytest.raises(FileNotFoundError):
        remove_owned_profile(target, OWNER)
    assert (target / "storage" / "recordings" / "sample.bin").exists()


def test_copied_ownership_marker_cannot_authorize_another_location(tmp_path):
    target = profile(tmp_path / "first")
    other = profile(tmp_path / "second")
    (other / "installation-owner.ini").write_bytes((target / "installation-owner.ini").read_bytes())
    with pytest.raises(ValueError, match="does not own"):
        remove_owned_profile(other, OWNER)
    assert (other / "database" / "poltergeist.db").exists()


def test_linked_content_is_refused_before_any_data_deletion(tmp_path):
    target = profile(tmp_path)
    outside = tmp_path / "private-original.txt"
    outside.write_text("keep original")
    try:
        (target / "linked.txt").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this test machine")
    with pytest.raises(ValueError, match="link or junction"):
        remove_owned_profile(target, OWNER)
    assert outside.read_text() == "keep original"
    assert (target / "database" / "poltergeist.db").exists()


def test_relative_or_wrongly_named_target_is_refused(tmp_path):
    for path in (Path("relative/CompanionPrototype"), tmp_path, tmp_path.parent):
        with pytest.raises(ValueError, match="explicitly named"):
            remove_owned_profile(path, OWNER)
