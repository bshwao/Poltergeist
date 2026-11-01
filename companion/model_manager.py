"""One local model transfer at a time, independent of the simulation worker."""

from concurrent.futures import ThreadPoolExecutor
import logging
from threading import Event, RLock

from backend.persistence_errors import ConflictError
from .model_assets import AssetError, AssetStore, DownloadPaused
from .model_environment import ModelEnvironment
from .credential_store import CredentialStoreError
from .huggingface import CATALOGUE, HuggingFaceAccount, HuggingFaceSource
from .model_catalog import AIM_WEIGHTS, model_info, model_options
from .model_selection import ModelSelection


class ModelManager:
    def __init__(self, profile):
        self.profile = profile
        self._selection = ModelSelection(profile)
        self.kind, self._selection_error = self._selection.read()
        self._store = AssetStore(profile, AIM_WEIGHTS) if self.kind == "aim" else AssetStore(profile)
        self.environment = ModelEnvironment(profile, kind="aim") if self.kind == "aim" else ModelEnvironment(profile)
        self.hub_account = HuggingFaceAccount(profile)
        self.hub_source = HuggingFaceSource(account=self.hub_account)
        if self._store.asset == self.hub_source.model.asset() and self._store._open is None:
            self._store._open = self.hub_source.open_asset
        self._source_status = {"state": "unchecked", "message": "Pinned Hub source has not been checked."} if self.kind == "plant" else {
            "state": "fixed", "message": "AIM uses the pinned official project archive. Hugging Face credentials are not sent to this source."}
        try:
            self._account_status = self.hub_account.status()
        except (CredentialStoreError, ValueError, OSError):
            self._account_status = {"configured": False, "message": "Saved Hugging Face account needs attention."}
        self._readiness = None
        self._lock = RLock()
        self._pause = Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="companion-models")
        self._future = None
        self._closed = False
        self._status = {"state": "unchecked", "bytes": 0, "total": self._store.asset.size,
                        "message": "Saved weights have not been checked in this session."}

    def snapshot(self):
        with self._lock:
            return {**self._status, "busy": self._future is not None and not self._future.done(),
                    "inference_ready": self._readiness is not None, "source": dict(self._source_status),
                    "account": dict(self._account_status), "catalogue": [item.public() for item in CATALOGUE],
                    "selected_model": model_info(self.kind), "models": model_options(), "selection_error": self._selection_error}

    def select_model(self, kind):
        info = model_info(kind)
        with self._lock:
            self._require_idle()
            if kind == self.kind and not self._selection_error:
                return
            store = AssetStore(self.profile, AIM_WEIGHTS) if kind == "aim" else AssetStore(self.profile)
            environment = ModelEnvironment(self.profile, kind=kind)
            if kind == "plant":
                store._open = self.hub_source.open_asset
            self._selection.save(kind)
            self.kind, self._store, self.environment = kind, store, environment
            self._selection_error = None
            self._readiness = None
            self._status = {"state": "unchecked", "bytes": 0, "total": info["size"], "message": "Check saved weights or prepare this selected model."}
            self._source_status = {"state": "unchecked", "message": "Pinned Hub source has not been checked."} if kind == "plant" else {
                "state": "fixed", "message": "AIM uses the pinned official project archive. Hugging Face credentials are not sent to this source."}

    def _require_idle(self):
        if self._closed:
            raise ConflictError("The model manager is closed")
        if self._future is not None and not self._future.done():
            raise ConflictError("Pause the current model action and wait for it to finish")

    def configure_huggingface(self, token):
        with self._lock:
            self._require_idle()
            self.hub_account.save(token)
            self._account_status = self.hub_account.status()
            if self.kind == "plant":
                self._source_status = {"state": "unchecked", "message": "Account saved. Check the pinned source to confirm model access."}

    def clear_huggingface(self):
        with self._lock:
            self._require_idle()
            self.hub_account.clear()
            self._account_status = self.hub_account.status()
            if self.kind == "plant":
                self._source_status = {"state": "unchecked", "message": "Account removed. Public downloads use anonymous access."}

    def _progress(self, state, count, total):
        with self._lock:
            self._status = {"state": state, "bytes": count, "total": total, "message": ""}

    def start(self, action, *, repair=False):
        if action not in {"download", "inspect", "remove", "prepare", "check_environment", "remove_environment", "check_source"} or (repair and action not in {"download", "prepare"}):
            raise ValueError("Unknown model action")
        with self._lock:
            self._require_idle()
            if self._selection_error:
                raise ConflictError(self._selection_error)
            if action == "check_source" and self.kind != "plant":
                raise ValueError("The selected model does not use the Hugging Face source check")
            self._pause.clear()
            if action == "check_source":
                self._source_status = {"state": "checking", "message": "Checking pinned Hugging Face source and access…"}
            else:
                self._readiness = None
                self._status.update(state="starting", message="")
            self._future = self._executor.submit(self._work, action, repair)

    def _work(self, action, repair):
        try:
            if action == "check_source":
                value = self.hub_source.verify_source(cancelled=self._pause.is_set)
                with self._lock:
                    self._source_status = {"state": "verified", "message": "Pinned revision, model bytes and license confirmed. This source check does not verify saved weights or inference.", "model": value}
                return
            elif action in {"prepare", "check_environment"}:
                if action == "prepare":
                    self._store.download(cancelled=self._pause.is_set, progress=self._progress, repair=repair)
                    self.environment.prepare(self._store.path, cancelled=self._pause.is_set, progress=self._progress, repair=repair)
                else:
                    state, _ = self._store.inspect(cancelled=self._pause.is_set, progress=self._progress)
                    if state != "weights_ready":
                        raise AssetError("Download and verify the selected model weights first")
                self._progress("checking_environment", 0, 1)
                from .model_probe import probe
                with self.environment.use(self._pause.is_set):
                    readiness = probe(self.environment, self._pause.is_set)
                with self._lock:
                    self._readiness = readiness
                    self._status.update(state="ready", bytes=1, total=1,
                        message=f"{model_info(self.kind)['name']} environment and pretrained inference verified. Enable it for local runs.")
                return
            elif action == "remove_environment":
                self.environment.remove()
                state, count = "absent", 0
            elif action == "download":
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
                if action == "check_source":
                    self._source_status = {"state": "paused", "message": "Source check paused. Saved model readiness is unchanged."}
                else:
                    self._status.update(state="paused", message="Paused. Resume will check saved bytes and continue.")
        except Exception as error:
            logging.getLogger(__name__).exception("Managed model preparation failed")
            with self._lock:
                message = str(error) if isinstance(error, (AssetError, ConflictError, CredentialStoreError)) else "Model preparation failed. Check saved weights or retry the download."
                if action == "check_source":
                    self._source_status = {"state": "error", "message": message}
                else:
                    self._status.update(state="error", message=message)

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
