"""Exclusive use of an independent runtime, shared by installers and workers.

Acquisition never recreates a removed generation. A failed process cleanup must
retain this lease until its owner can confirm the whole process group exited.
"""

from pathlib import Path
import stat
from types import SimpleNamespace

from backend.persistence_errors import ConflictError
from backend.worker_lock import WorkerLease


class RuntimeUseLease:
    def __init__(self, prefix, *, allow_missing_prefix=False):
        self.prefix = Path(prefix)
        if not self.prefix.is_absolute() or str(self.prefix).startswith(("\\\\", "//")) or ".." in self.prefix.parts:
            raise ValueError("An independent runtime requires an absolute local prefix")
        self.path = self.prefix.parent / ".runtime-use.worker.lock"
        self._allow_missing_prefix = allow_missing_prefix
        self._lease = WorkerLease(SimpleNamespace(file_path=self.prefix.parent / ".runtime-use"), create_parents=False)

    def _plain(self):
        for path in (self.path, *self.path.parents, self.prefix):
            if path.is_symlink() or path.is_junction() or (path.exists() and getattr(path.stat(), "st_file_attributes", 0) & 0x400):
                raise ConflictError("Linked independent runtime storage was refused")
        if (not self.prefix.is_dir() and not self._allow_missing_prefix) or (self.prefix.exists() and not self.prefix.is_dir()):
            raise ConflictError("The independent runtime is no longer installed")
        if self.path.exists():
            info = self.path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ConflictError("The independent runtime lease is not an ordinary file")

    def acquire(self):
        self._plain()
        try:
            self._lease.acquire()
            self._plain()
        except ConflictError:
            self._lease.release()
            raise ConflictError("The independent runtime is in use or unavailable") from None
        except BaseException:
            self._lease.release()
            raise

    def release(self):
        self._lease.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


def runtime_use_lease(prefix, *, allow_missing_prefix=False):
    return RuntimeUseLease(prefix, allow_missing_prefix=allow_missing_prefix)
