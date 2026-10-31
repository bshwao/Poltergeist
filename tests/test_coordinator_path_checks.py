"""Reusing path names must never reuse filesystem authorization results."""

from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from coordinator.store import Store, StoreError


@pytest.mark.parametrize("location", ["database", "parent", "ancestor"])
@pytest.mark.parametrize("kind", ["symlink", "junction", "reparse_symlink"])
@pytest.mark.parametrize("write", [False, True])
def test_each_transaction_detects_a_new_link_after_a_successful_check(tmp_path, monkeypatch, location, kind, write):
    store = Store(tmp_path / "service" / "state" / "coordinator.db")
    store.initialize()
    target = {"database": store.path, "parent": store.path.parent, "ancestor": store.path.parent.parent}[location]
    original, linked = Path.lstat, [False]
    monkeypatch.setattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C, raising=False)
    monkeypatch.setattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003, raising=False)

    def lstat(path, *args, **kwargs):
        if linked[0] and path == target:
            return SimpleNamespace(st_mode=stat.S_IFLNK if kind == "symlink" else stat.S_IFDIR,
                st_reparse_tag={"symlink": 0, "junction": 0xA0000003, "reparse_symlink": 0xA000000C}[kind])
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    try:
        with store.transaction(write=write) as connection:
            assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        linked[0] = True
        with pytest.raises(StoreError, match="ordinary local path"):
            with store.transaction(write=write):
                pytest.fail("Changed filesystem path reached SQLite")
        assert not store._anchor.in_transaction
        linked[0] = False
        with store.transaction(write=write) as connection:
            assert connection.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0
    finally:
        store.close()


def test_a_different_configured_path_does_not_reuse_the_original_parent_chain(tmp_path, monkeypatch):
    store = Store(tmp_path / "original" / "coordinator.db")
    store.initialize()
    original, database = Path.lstat, store.path
    changed = tmp_path / "changed" / "coordinator.db"
    monkeypatch.setattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003, raising=False)

    def lstat(path, *args, **kwargs):
        if path == changed.parent:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=0xA0000003)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    try:
        with store.transaction(write=False):
            pass
        store.path = changed
        with pytest.raises(StoreError, match="ordinary local path"):
            with store.transaction(write=False):
                pytest.fail("Changed path reused stale components")
    finally:
        store.path = database
        store.close()
