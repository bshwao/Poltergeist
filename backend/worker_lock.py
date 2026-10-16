"""A nonblocking OS lease prevents concurrent workers sharing one SQLite file."""

import os
from threading import Lock

from .persistence_errors import ConflictError


_registry_lock = Lock()
_owners = set()


class WorkerLease:
    def __init__(self, database):
        path = database.file_path
        self._path = None if path is None else path.with_name(path.name + ".worker.lock")
        self._key = database if path is None else os.path.normcase(str(path))
        self._file = None
        self._owned = False

    def acquire(self):
        with _registry_lock:
            if self._key in _owners:
                raise ConflictError("A simulation worker already owns this database")
            _owners.add(self._key)
        try:
            if self._path is not None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._file = self._path.open("a+b")
                if os.name == "nt":
                    import msvcrt
                    self._file.seek(0, 2)
                    if self._file.tell() == 0:
                        self._file.write(b"0")
                        self._file.flush()
                    self._file.seek(0)
                    msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._owned = True
        except BaseException as error:
            if self._file is not None:
                self._file.close()
                self._file = None
            with _registry_lock:
                _owners.discard(self._key)
            if isinstance(error, OSError):
                raise ConflictError("Another simulation worker owns the database lease, or the lease is unavailable") from error
            raise

    def release(self):
        if not self._owned:
            return
        try:
            if self._file is not None:
                try:
                    if os.name == "nt":
                        import msvcrt
                        self._file.seek(0)
                        msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
                finally:
                    self._file.close()
                    self._file = None
        finally:
            with _registry_lock:
                _owners.discard(self._key)
            self._owned = False
