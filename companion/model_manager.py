"""One local model transfer at a time, independent of the simulation worker."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
import platform
import sys
from threading import Event, RLock

from backend.persistence_errors import ConflictError
from .model_assets import AssetError, AssetStore, DownloadPaused
from .model_catalog import load_bundled_capsule
from .model_capsule import CapsuleStore
from .model_readiness import ModelReadiness, managed_registration


class ModelManager:
    def __init__(self, profile, *, bundle=None):
        self.profile = Path(profile)
        self._bundle = bundle
        self._store = AssetStore(profile)
        self._pack = self._capsules = None
        self._probe = ModelReadiness()
        self._ready = None
        self._external = False
        self._blocks_execution = False
        self._lock = RLock()
        self._pause = Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="companion-models")
        self._future = None
        self._closed = False
        self._status = {"state": "unchecked", "bytes": 0, "total": self._store.asset.size,
                        "message": "Saved weights have not been checked in this session."}
        present = bundle is not None and (Path(bundle) / "modelpacks/catalog.json").is_file()
        self._environment = {"state": "unchecked" if present else "unavailable", "progress": 0, "total": 1,
            "message": "The installed environment has not been checked in this session." if present
                       else "This build does not include the PlanT CPU environment.", "readiness": None}

    def _busy(self):
        return self._external or (self._future is not None and not self._future.done()) or self._probe.retained

    @property
    def execution_blocked(self):
        with self._lock:
            return self._probe.retained or (self._blocks_execution and self._busy())

    def snapshot(self):
        with self._lock:
            return {**self._status, "busy": self._busy(), "environment": deepcopy(self._environment),
                    "model_process_retained": self._probe.retained, "execution_blocked": self.execution_blocked}

    def _progress(self, state, count, total):
        with self._lock:
            self._status = {"state": state, "bytes": count, "total": total, "message": ""}

    def start(self, action, *, repair=False, trusted_local_code=False):
        environment = action in {"install_environment", "inspect_environment", "remove_environment"}
        if action not in {"download", "inspect", "remove", "install_environment", "inspect_environment", "remove_environment"} or (
                repair and action not in {"download", "install_environment"}):
            raise ValueError("Unknown model action")
        if action in {"install_environment", "inspect_environment"} and trusted_local_code is not True:
            raise ValueError("Model checks require explicit trust for the bundled executable code")
        with self._lock:
            if self._closed:
                raise ConflictError("The model manager is closed")
            if self._busy():
                raise ConflictError("Pause the current model action and wait for it to finish")
            self._pause.clear()
            self._blocks_execution = environment or repair or action == "remove"
            if environment:
                self._ready = None
                self._environment.update(state="starting", message="", readiness=None)
            else:
                if repair or action == "remove":
                    self._invalidate_ready()
                self._status.update(state="starting", message="")
            self._future = self._executor.submit(self._work, action, repair, environment)

    def _invalidate_ready(self):
        self._ready = None
        if self._environment["state"] != "unavailable":
            self._environment.update(state="unchecked", readiness=None,
                message="Check the model environment before using PlanT again.")

    def _resolve_pack(self, *, required=True):
        if self._pack is None:
            self._pack = load_bundled_capsule(self._bundle)
            if self._pack is not None:
                self._capsules = CapsuleStore(self.profile, self._pack.spec)
        if self._pack is None and required:
            raise AssetError("This build does not include the PlanT CPU environment")
        return self._pack

    def _environment_progress(self, state, count, total):
        with self._lock:
            self._environment.update(state=state, progress=count, total=total, message="")

    @contextmanager
    def _exclude_weight_users(self):
        pack = self._resolve_pack(required=False)
        root = self.profile / "model-environments"
        if root.exists():
            from .model_capsule import _plain
            _plain(root, directory=True)
            if any(path.name != (pack.spec.id if pack else None) for path in root.iterdir()):
                raise AssetError("Other retained environments need their matching app build before these weights can be changed")
        if self._capsules is None:
            yield
        else:
            with self._capsules.exclude_users():
                yield

    def _work_environment(self, action, repair):
        pack = self._resolve_pack()
        if action == "remove_environment":
            self._capsules.remove()
            with self._lock:
                self._environment.update(state="absent", progress=0, total=1, readiness=None,
                    message="The CPU environment was removed. Model weights are kept separately.")
            return
        if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"} or sys.maxsize <= 2**32:
            raise AssetError("The bundled PlanT environment requires 64-bit Windows")
        state, count = self._store.inspect(cancelled=self._pause.is_set, progress=self._progress)
        with self._lock:
            self._status.update(state=state, bytes=count,
                message="Weights verified." if state == "weights_ready" else "Download and verify PlanT weights first.")
        if state != "weights_ready":
            raise AssetError("Download and verify PlanT weights before checking its environment")
        if action == "install_environment":
            installed = self._capsules.install(pack.source, repair=repair, cancelled=self._pause.is_set,
                                                progress=self._environment_progress)
        else:
            installed = self._capsules.inspect(cancelled=self._pause.is_set, progress=self._environment_progress)
            if installed is None:
                raise AssetError("Install the PlanT CPU environment first")
        report = self._probe.run(installed, self._store.path, cancelled=self._pause.is_set,
                                 progress=self._environment_progress)
        if (not isinstance(report, dict) or report.get("schema") != "poltergeist-managed-model-readiness-v1"
                or report.get("passed") is not True or report.get("generation") != installed.generation
                or report.get("checkpoint_sha256") != self._store.asset.sha256):
            raise AssetError("The model check did not return a valid readiness receipt")
        with self._lock:
            self._ready = {"generation": installed.generation, "manifest_sha256": pack.spec.manifest_sha256,
                           "prefix": str(installed.interpreter.parent)}
            self._environment.update(state="ready", progress=1, total=1, readiness=report,
                message="CPU model checks passed. Choose Use PlanT CPU; recipe runs require CARLA.")

    def _work(self, action, repair, environment=False):
        try:
            if environment:
                self._work_environment(action, repair)
                return
            if action == "download":
                if repair:
                    with self._exclude_weight_users():
                        self._store.download(cancelled=self._pause.is_set, progress=self._progress, repair=True)
                else:
                    self._store.download(cancelled=self._pause.is_set, progress=self._progress)
                state, count = "weights_ready", self._store.asset.size
            elif action == "inspect":
                state, count = self._store.inspect(cancelled=self._pause.is_set, progress=self._progress)
            else:
                with self._exclude_weight_users():
                    self._store.remove()
                state, count = "absent", 0
            message = ("Weights verified. Install or check the CPU environment to prepare PlanT."
                       if state == "weights_ready" else "No weights installed." if state == "absent"
                       else "A partial download is saved. Resume to continue.")
            with self._lock:
                self._status.update(state=state, bytes=count, message=message)
        except DownloadPaused:
            with self._lock:
                target = self._environment if environment else self._status
                target.update(state="paused", message="Paused. The next attempt will check saved bytes before continuing.")
        except Exception as error:
            with self._lock:
                target = self._environment if environment else self._status
                target.update(state="stopping" if self._probe.retained else "error", message=str(error) if isinstance(error, (AssetError, ConflictError))
                                    else "Model preparation failed. Check saved weights or retry the download.")

    def manages(self, prefix):
        return Path(prefix).is_relative_to(self.profile / "model-environments")

    def matches(self, prefix):
        with self._lock:
            return self._ready is not None and self._ready["prefix"] == str(prefix)

    def checked_registration(self, *, expected_prefix=None):
        with self._lock:
            if self._busy() or self._closed:
                raise ConflictError("Wait for the current model operation to stop")
            if self._ready is None:
                raise ConflictError("Check the model environment on AI models before using PlanT")
            self._external = True
            self._pause.clear()
            ready = dict(self._ready)
        try:
            self._resolve_pack()
            installed = self._capsules.inspect(cancelled=self._pause.is_set, progress=self._environment_progress)
            if (installed is None or installed.generation != ready["generation"]
                    or installed.manifest_sha256 != ready["manifest_sha256"]
                    or (expected_prefix is not None and str(installed.interpreter.parent) != str(expected_prefix))):
                raise ConflictError("The model generation changed. Check it and choose Use PlanT CPU again.")
            state, _ = self._store.inspect(cancelled=self._pause.is_set, progress=self._progress)
            if state != "weights_ready":
                raise ConflictError("Verify PlanT weights before using its environment")
            return managed_registration(installed, self._store.path)
        except BaseException:
            with self._lock:
                self._invalidate_ready()
            raise
        finally:
            with self._lock:
                self._external = False
                if self._ready is not None:
                    self._status.update(state="weights_ready", bytes=self._store.asset.size)
                    self._environment.update(state="ready", progress=1, total=1,
                        message="CPU model checks passed. Choose Use PlanT CPU; recipe runs require CARLA.")

    def pause(self):
        self._pause.set()
        with self._lock:
            if not self._external and (self._future is None or self._future.done()) and self._probe.retained:
                self._future = self._executor.submit(self._retry_cleanup)

    def _retry_cleanup(self):
        clean = self._probe.close()
        with self._lock:
            self._ready = None
            self._environment.update(state="unchecked" if clean else "stopping", readiness=None,
                message="The model worker stopped. Check the environment again." if clean
                else "The model worker is still stopping. Keep the app open and retry Pause or Close.")

    def close(self):
        with self._lock:
            self._pause.set()
            if self._external or (self._future is not None and not self._future.done()):
                return False  # Keep the profile lease while I/O is still owned.
        if not self._probe.close():
            return False
        with self._lock:
            if not self._closed:
                self._closed = True
                self._executor.shutdown(wait=True)
            return True
