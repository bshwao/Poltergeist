"""One local model transfer at a time, independent of the simulation worker."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, RLock

from backend.persistence_errors import ConflictError
from .model_assets import AssetError, AssetStore, DownloadPaused


class ModelManager:
    def __init__(self, profile):
        self._store = AssetStore(profile)
        self._lock = RLock()
        self._pause = Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="companion-models")
        self._future = None
        self._closed = False
        self._status = {"state": "unchecked", "bytes": 0, "total": self._store.asset.size,
                        "message": "Saved weights have not been checked in this session."}

    def snapshot(self):
        with self._lock:
            return {**self._status, "busy": self._future is not None and not self._future.done()}

    def _progress(self, state, count, total):
        with self._lock:
            self._status = {"state": state, "bytes": count, "total": total, "message": ""}

    def start(self, action, *, repair=False):
        if action not in {"download", "inspect", "remove"} or (repair and action != "download"):
            raise ValueError("Unknown model action")
        with self._lock:
            if self._closed:
                raise ConflictError("The model manager is closed")
            if self._future is not None and not self._future.done():
                raise ConflictError("Pause the current model action and wait for it to finish")
            self._pause.clear()
            self._status.update(state="starting", message="")
            self._future = self._executor.submit(self._work, action, repair)

    def _work(self, action, repair):
        try:
            if action == "download":
                self._store.download(cancelled=self._pause.is_set, progress=self._progress, repair=repair)
                state, count = "weights_ready", self._store.asset.size
            elif action == "inspect":
                state, count = self._store.inspect(cancelled=self._pause.is_set, progress=self._progress)
            else:
                self._store.remove()
                state, count = "absent", 0
            message = ("Weights verified. The managed AI environment and inference check are still required."
                       if state == "weights_ready" else "No weights installed." if state == "absent"
                       else "A partial download is saved. Resume to continue.")
            with self._lock:
                self._status.update(state=state, bytes=count, message=message)
        except DownloadPaused:
            with self._lock:
                self._status.update(state="paused", message="Paused. Resume will check saved bytes and continue.")
        except Exception as error:
            with self._lock:
                self._status.update(state="error", message=str(error) if isinstance(error, (AssetError, ConflictError))
                                    else "Model preparation failed. Check saved weights or retry the download.")

    def pause(self):
        self._pause.set()

    def close(self):
        with self._lock:
            self._pause.set()
            if self._future is not None and not self._future.done():
                return False  # Keep the profile lease while I/O is still owned.
            if not self._closed:
                self._closed = True
                self._executor.shutdown(wait=True)
            return True
