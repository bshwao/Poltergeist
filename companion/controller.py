"""Desktop actions over the existing durable queue; no internal HTTP service.

The GUI serializes calls on one background thread. A blocked shutdown retains
the runtime and profile lease until cleanup finishes; it never reports stopped
while the old worker can still own CARLA or the database.
"""

import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
from threading import RLock
from uuid import UUID, uuid4

from backend.dependencies import ApiRuntime
from backend.persistence_errors import ConflictError
from backend.repositories import SimulationRepository
from backend.services.report_service import export_report
from scenarios import validate_scenario
from shared.models import AgentRegistration
from shared.models.api import SimulationRecord
from simulator.protocols import SimulationOptions

from .profile import (instance_lease, load_config, local_settings,
                      profile_path, save_config)
from .credential_store import WindowsCredentialStore
from .pairing_client import PairingClient

MAX_IMPORT_BYTES = 10 * 1024 * 1024


def _reject_constant(value):
    raise ValueError(f"Nonfinite JSON number: {value}")


def read_json(path):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        raise ValueError("Import exceeds the 10 MiB desktop limit")
    return json.loads(data, parse_constant=_reject_constant)


class CompanionController:
    def __init__(self, profile, *, bundle=None, runtime_factory=ApiRuntime,
                 pairing_factory=PairingClient, credential_store_factory=WindowsCredentialStore,
                 relay_factory=None, _private_remote_execution=False):
        self.profile = profile_path(profile, bundle=bundle)
        self._bundle = None if bundle is None else Path(bundle).resolve()
        self._factory = runtime_factory
        self._lease = instance_lease(self.profile)
        self._owned = False
        self._runtime = None
        self._worker = None
        self._stopping = False
        self._models = None
        self._pairing_factory = pairing_factory
        self._credential_store_factory = credential_store_factory
        self._pairing_display = None
        self._pairing_client = None
        self._remote_lock = RLock()
        self._relay = None
        self._relay_admission = False
        self._relay_execution = False
        self._relay_approval = None
        self._relay_factory = relay_factory
        # Compatibility for the earlier private acceptance driver. Ordinary
        # builds need a fresh explicit local consent action below; saved config
        # and environment variables cannot enable hosted requests.
        self._private_remote_execution = _private_remote_execution is True
        self.config = load_config(self.profile)

    def open(self):
        if not self._owned:
            try:
                self._lease.acquire()
            except ConflictError as error:
                raise ConflictError("This companion profile is already open, or its instance lock is unavailable") from error
            try:
                self.config = load_config(self.profile)
            except BaseException:
                self._lease.release()
                raise
            self._owned = True
            self._pairing_display = None

    def _require_open(self):
        if not self._owned:
            raise ConflictError("Open the companion profile first")

    def start(self):
        self._require_open()
        if self._stopping:
            raise ConflictError("The previous worker is still stopping; retry Stop before restarting")
        if self._runtime is None:
            if self._models is not None and self._models.execution_blocked:
                raise ConflictError("Wait for model preparation to finish or stop before starting the local runtime")
            runtime = self._factory(local_settings(self.profile, self.config))
            try:
                worker = runtime.worker()
            except BaseException:
                runtime.close()
                raise
            self._runtime = runtime
            self._worker = worker
        return self.snapshot()

    def stop(self):
        self._require_open()
        if not self.disconnect_relay():
            return False
        if self._runtime is None:
            return True
        self._stopping = True
        clean = self._runtime.close()
        if clean:
            self._runtime = None
            self._worker = None
            self._stopping = False
        return clean

    def close(self):
        if not self._owned:
            return True
        if self._models is not None:
            self._models.pause()
        if not self.stop():
            return False
        if not self._close_pairing_client():
            return False
        if self._models is not None and not self._models.close():
            return False
        self._models = None
        self._lease.release()
        self._owned = False
        return True

    def configure(self, config):
        self._require_open()
        if self._runtime is not None:
            raise ConflictError("Stop the local runtime before saving settings")
        self._check_origin_change(config.coordinator_origin)
        save_config(self.profile, config)
        self.config = load_config(self.profile)

    def _check_origin_change(self, origin):
        if origin != self.config.coordinator_origin and self._credential_store_factory(self.profile).load() is not None:
            raise ConflictError("Revoke the saved pairing or discard its unapproved code before changing the coordinator")
        if origin != self.config.coordinator_origin and not self._close_pairing_client():
            raise ConflictError("The previous enrollment request is still stopping; retry before changing the coordinator")

    def _close_pairing_client(self):
        client = self._pairing_client
        if client is None:
            return True
        close = getattr(client, "close", None)
        if callable(close) and close() is not True:
            return False
        self._pairing_client = None
        return True

    def configure_pairing(self, origin):
        self._require_open()
        config = self.config.model_copy(update={"coordinator_origin": origin})
        config = type(config).model_validate(config.model_dump())
        self._check_origin_change(config.coordinator_origin)
        save_config(self.profile, config)
        self.config = load_config(self.profile)
        self._pairing_display = None
        return self.pairing_status()

    def _pairing(self):
        self._require_open()
        if self.config.coordinator_origin is None:
            raise ConflictError("Save an HTTPS coordinator address first")
        if self._pairing_client is None:
            self._pairing_client = self._pairing_factory(self.config.coordinator_origin, self._credential_store_factory(self.profile))
        return self._pairing_client

    def pairing_status(self, *, refresh=False):
        self._require_open()
        if self.config.coordinator_origin is None:
            return {"state": "unconfigured"}
        client = self._pairing()
        saved = client.saved_status()
        if refresh and saved["state"] == "attempt":
            self._pairing_display = client.poll()
        elif refresh and saved["state"] in ("pending_approval", "paired"):
            client.resume()
            saved = client.saved_status()
        if saved["state"] == "attempt" and self._pairing_display is not None:
            saved["claim"] = self._pairing_display.model_dump(mode="json")
        if saved["state"] == "paired":
            saved["ownership_generation"] = 1
        return saved

    def begin_pairing(self, display_name, scopes):
        self._pairing().begin(display_name, scopes)
        self._pairing_display = None
        return self.pairing_status()

    def approve_pairing(self, displayed_status, policy):
        self._pairing().approve(displayed_status, policy)
        self._pairing_display = None
        return self.pairing_status()

    def deny_pairing(self, displayed_status):
        self._pairing().deny(displayed_status)
        self._pairing_display = None
        return self.pairing_status()

    def discard_pairing_attempt(self):
        self._pairing().forget_unapproved_attempt()
        self._pairing_display = None
        return self.pairing_status()

    def revoke_pairing(self):
        if not self.disconnect_relay():
            raise ConflictError("The relay is still disconnecting; retry before revoking pairing")
        self._pairing().revoke()
        self._pairing_display = None
        return self.pairing_status()

    def cancel_pending_pairing(self):
        if not self.disconnect_relay():
            raise ConflictError("The relay is still disconnecting; retry before cancelling the saved approval")
        self._pairing().cancel_pending_approval()
        self._pairing_display = None
        return self.pairing_status()

    @staticmethod
    def _relay_consent(binding, policy):
        return {"state": "paired", "origin": binding.origin, "device_id": str(binding.device_id),
                "owner_id": str(binding.owner_id), "ownership_generation": binding.generation,
                "policy": policy.model_dump(mode="json")}

    @classmethod
    def _relay_fingerprint(cls, binding, policy, credential):
        # The protected credential remains private even in the controller's
        # retained comparison value. A rotation also fences the old connection.
        content = cls._relay_consent(binding, policy)
        content["credential_sha256"] = hashlib.sha256(credential.encode("utf-8")).hexdigest()
        return json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def _protected_enrollment(self):
        from backend.remote_receipts import RemoteBinding
        from .pairing_client import EnrollmentRecord
        try:
            record = EnrollmentRecord.model_validate(self._credential_store_factory(self.profile).load())
            if (record.state != "paired" or record.origin != self.config.coordinator_origin
                    or record.approval is None):
                raise ValueError
            binding = RemoteBinding(record.origin, record.created.device_id,
                record.approval.expected_account_id, 1)
            return binding, record.approval.policy, record.approval.credential
        except Exception:
            raise ConflictError("Review the saved local approval before connecting") from None

    def _remote_enrollment(self):
        with self._remote_lock:
            self._require_open()
            if not self._relay_admission or self._runtime is None or self._stopping:
                raise ConflictError("The local relay is not accepting commands")
            try:
                binding, policy, credential = self._protected_enrollment()
                if (self._relay_approval is not None and
                        self._relay_fingerprint(binding, policy, credential) != self._relay_approval):
                    raise ValueError
                return binding, policy, credential
            except Exception:
                self._relay_admission = False
                raise ConflictError("Review the saved local approval before connecting") from None

    def connect_relay(self, *, allow_remote_execution=False, displayed_status=None):
        """Connect only by local action; enabling requires the displayed binding."""
        from .relay_client import RelayClient
        from .remote_service import RemoteService
        if type(allow_remote_execution) is not bool:
            raise ValueError("Choose whether to allow remote execution using a checkbox")
        start_failed = False
        with self._remote_lock:
            runtime = self._running()
            if self._relay is not None:
                raise ConflictError("Disconnect the existing relay first")
            try:
                binding, policy, credential = self._protected_enrollment()
                if allow_remote_execution:
                    expected = self._relay_consent(binding, policy)
                    try:
                        if (not isinstance(displayed_status, dict)
                                or type(displayed_status.get("ownership_generation")) is not int):
                            raise ValueError
                        shown = {key: displayed_status[key] for key in expected}
                        same = (json.dumps(shown, sort_keys=True, separators=(",", ":"), allow_nan=False)
                            == json.dumps(expected, sort_keys=True, separators=(",", ":"), allow_nan=False))
                    except (ValueError, TypeError, KeyError, RecursionError):
                        same = False
                    if not same:
                        raise ConflictError("Pairing changed; review the saved account and policy before connecting")
                self._relay_execution = allow_remote_execution or self._private_remote_execution
                self._relay_approval = self._relay_fingerprint(binding, policy, credential)
                self._relay_admission = True
                service = RemoteService(runtime.worker(), lambda: self._remote_enrollment()[:2],
                    lock=self._remote_lock)
                factory = self._relay_factory or RelayClient
                relay = factory(binding.origin, self._remote_enrollment, service,
                    remote_execution_enabled=self._relay_execution)
                self._relay = relay
                try:
                    relay.start()
                except Exception:
                    # Join outside admission ownership; a partially started
                    # transport may be waiting to inspect its local binding.
                    self._relay_admission = False
                    start_failed = True
            except Exception:
                self._relay_admission = False
                self._relay_execution = False
                self._relay_approval = None
                self._relay = None
                raise
        if start_failed:
            self.disconnect_relay()
            raise ConflictError("The relay could not start; use Disconnect before retrying if cleanup is still running") from None
        return self.relay_status()

    def disconnect_relay(self):
        with self._remote_lock:
            self._relay_admission = False
            self._relay_execution = False
            relay = self._relay
        # Joining outside the admission lock lets an in-flight action finish or
        # observe the disabled binding. Keep runtime/profile ownership on timeout.
        if relay is not None and not relay.stop():
            return False
        with self._remote_lock:
            self._relay = None
            self._relay_approval = None
        return True

    def relay_status(self):
        with self._remote_lock:
            if self._relay is None:
                return {"state": "disconnected", "owned": False, "running": False,
                        "admission": False, "remote_execution_enabled": False}
            try:
                public = self._relay.snapshot()
                running = public.get("running") is True
                state = public.get("state")
                if state not in {"disconnected", "connecting", "connected", "offline"}:
                    state = "error"
            except Exception:
                self._relay_admission = False
                running, state = False, "error"
            if self._relay_admission:
                try:
                    self._remote_enrollment()
                except Exception:
                    self._relay_admission = False
            if not self._relay_admission:
                state = "disconnecting"
            elif not running:
                state = "error"
            return {"state": state, "owned": True, "running": running,
                    "admission": self._relay_admission,
                    "remote_execution_enabled": (self._relay_admission and self._relay_execution
                        and running and state == "connected")}

    @staticmethod
    def _hosted_message(relay):
        if relay["state"] == "disconnected":
            return "Unavailable — the hosted relay is disconnected"
        if relay["state"] == "disconnecting":
            return "Disconnecting — remote requests are disabled; retry Disconnect to finish cleanup"
        if relay["state"] == "offline":
            return "Offline — retrying the approved coordinator"
        if relay["state"] == "connecting":
            return "Connecting to the approved coordinator"
        if relay["state"] == "connected":
            return ("Connected — approved remote requests are allowed" if relay["remote_execution_enabled"]
                else "Connected — remote requests are paused")
        return "Unavailable — disconnect the retained relay before trying again"

    def _running(self):
        self._require_open()
        if self._runtime is None or self._stopping:
            raise ConflictError("Start the local runtime first")
        return self._runtime

    def submit(self, scenario, *, mode, agent_id=None):
        if mode not in {"test", "carla"}:
            raise ValueError("Choose Synthetic test or CARLA")
        runtime = self._running()
        if agent_id is not None:
            registered = runtime.registry().get(UUID(str(agent_id)))
            snapshot = registered.snapshot
            if snapshot.isolated_runtime is not None and self._model_manager().manages(snapshot.isolated_runtime.prefix):
                if snapshot.manifest.agent_id == "poltergeist-reference-ai" and mode != "carla":
                    raise ValueError("PlanT recipe runs require CARLA and its measured road raster")
                request = self._model_manager().checked_registration(expected_prefix=snapshot.isolated_runtime.prefix)
                if request.isolated_runtime != snapshot.isolated_runtime or (snapshot.manifest.agent_id == "poltergeist-reference-ai"
                        and (request.package_path != snapshot.package_path or request.configuration != snapshot.configuration)):
                    raise ConflictError("Choose Use PlanT CPU again to register the current managed version")
        recipe = validate_scenario(scenario)
        return runtime.worker().submit(recipe, mode=mode, agent_id=agent_id,
            options=SimulationOptions(carla_quality=self.config.carla_quality))

    def cancel(self, simulation_id):
        return self._running().worker().cancel(UUID(str(simulation_id)))

    def register_agent(self, package_path, *, trusted_local_code=False):
        request = AgentRegistration(package_path=str(Path(package_path).resolve()),
            python_executable=sys.executable, trusted_local_code=trusted_local_code)
        return self._running().registry().register(request)

    def _model_manager(self):
        self._require_open()
        if self._models is None:
            from .model_manager import ModelManager
            self._models = ModelManager(self.profile, bundle=self._bundle)
        return self._models

    def model_status(self):
        return self._model_manager().snapshot()

    def _model_jobs_pending(self):
        path = self.profile / "database/poltergeist.db"
        if not path.exists():
            return False
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.)) as connection:
                rows = connection.execute("SELECT j.agent_snapshot FROM simulation_jobs j JOIN simulations s ON s.id=j.simulation_id WHERE s.status IN ('queued','running') AND j.agent_snapshot IS NOT NULL")
                for (raw,) in rows:
                    from shared.models.registry import AgentSnapshot
                    saved = AgentSnapshot.model_validate_json(raw)
                    if saved.isolated_runtime is not None and self._model_manager().manages(saved.isolated_runtime.prefix):
                        return True
                    for model in saved.model_files:
                        if Path(model.path).is_relative_to(self.profile / "models"):
                            return True
        except (sqlite3.Error, ValueError, TypeError, AttributeError):
            raise ConflictError("Saved model jobs could not be checked; keep their files until local recovery finishes") from None
        return False

    def prepare_model(self, action, *, repair=False, trusted_local_code=False):
        self._require_open()
        environment = action in {"install_environment", "inspect_environment", "remove_environment"}
        if (repair or action == "remove" or environment) and self._runtime is not None:
            raise ConflictError("Stop the local runtime before installing, checking, repairing or removing model files")
        if action in {"remove", "install_environment", "remove_environment"} and self._model_jobs_pending():
            raise ConflictError("Finish or cancel saved model jobs before replacing or removing their files")
        self._model_manager().start(action, repair=repair, trusted_local_code=trusted_local_code)

    def use_managed_model(self, *, trusted_local_code=False):
        if trusted_local_code is not True:
            raise ValueError("Managed agent registration requires explicit trust for its local code")
        runtime = self._running()
        request = self._model_manager().checked_registration()
        registered = runtime.registry().register(request)
        return {"agent_id": str(registered.id), "snapshot": self.snapshot()}

    def pause_model_download(self):
        self._model_manager().pause()

    def snapshot(self):
        self._require_open()
        status = {"state": "stopped", "accepting": False, "active_id": None, "pending": 0, "error": None}
        runs, agents = [], []
        if self._runtime is not None:
            # Do not call runtime.worker() after ApiRuntime.close() began.
            worker = self._worker
            queue = worker.status()
            status = {**queue, "state": "stopping" if self._stopping else "running" if queue["accepting"] else "error"}
            persistence = worker.persistence
            with persistence.database.session(write=False) as session:
                records = SimulationRepository(session).list(limit=100)
            for stored in records:
                run = stored.simulation
                runs.append({"id": str(run.id), "name": run.scenario.name, "mode": run.mode,
                             "status": run.status.value, "progress": run.progress, "error": run.error,
                             "has_result": stored.result is not None})
            if not self._stopping:
                for agent in self._runtime.registry().list(active=True, limit=100):
                    selected = agent.snapshot
                    if (selected.isolated_runtime is not None and selected.manifest.agent_id == "poltergeist-reference-ai"
                            and self._model_manager().manages(selected.isolated_runtime.prefix)
                            and not self._model_manager().matches(selected.isolated_runtime.prefix)):
                        continue
                    agents.append({"id": str(agent.id), "name": selected.manifest.name})
        try:
            pairing = self.pairing_status()
        except Exception:
            # A damaged credential must not hide local runs or stop local work.
            pairing = {"state": "error", "message": "Saved pairing could not be read. Use Connection to review it."}
        relay = self.relay_status()
        return {"queue": status, "runs": runs, "agents": agents, "pairing": pairing,
                "relay": relay, "hosted": self._hosted_message(relay)}

    def export(self, simulation_id, *, format="json"):
        runtime = self._running()
        report = export_report(runtime.persistence(), UUID(str(simulation_id)), format=format)
        directory = self.profile / "exports"
        directory.mkdir(exist_ok=True)
        path = directory / f"{uuid4().hex}-{report.filename}"
        with path.open("xb") as stream:
            stream.write(report.content)
        return path

    def diagnostics(self, *, check_carla=False):
        from backend.services.readiness_service import collect_readiness
        self._require_open()
        report = collect_readiness(local_settings(self.profile, self.config), connection_requested=check_carla)
        snapshot = self.snapshot()
        return {"schema_version": "poltergeist-companion-diagnostics-v1", "profile": str(self.profile),
                "python": sys.executable, "hosted_pairing_available": self.config.coordinator_origin is not None,
                "remote_execution_enabled": snapshot["relay"]["remote_execution_enabled"],
                "relay": snapshot["relay"], "runtime": snapshot["queue"], "readiness": report.model_dump(mode="json"),
                "models": self.model_status()}

    def import_report(self, path):
        """Archive an explicit export; never restore its queue, paths or code trust."""
        self._require_open()
        report = read_json(path)
        if not isinstance(report, dict) or report.get("schema") not in {"poltergeist-run-report-v1", "poltergeist-run-report-v2"}:
            raise ValueError("Select an exported Poltergeist JSON run report")
        record = SimulationRecord.model_validate(report.get("record"))
        if record.result is None:
            raise ValueError("Only finalized run reports can be archived")
        data = (json.dumps(report, indent=2, allow_nan=False) + "\n").encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        directory = self.profile / "imported-reports"
        directory.mkdir(exist_ok=True)
        target = directory / f"{record.simulation.id}-{digest[:16]}.json"
        try:
            with target.open("xb") as stream:
                stream.write(data)
        except FileExistsError:
            if target.read_bytes() != data:
                raise ConflictError("An imported report with a different identity already exists")
        return target
