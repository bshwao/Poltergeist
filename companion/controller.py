"""Desktop actions over the existing durable queue; no internal HTTP service.

The GUI serializes calls on one background thread. A blocked shutdown retains
the runtime and profile lease until cleanup finishes; it never reports stopped
while the old worker can still own CARLA or the database.
"""

import hashlib
import json
from pathlib import Path
import sys
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
    def __init__(self, profile, *, bundle=None, runtime_factory=ApiRuntime):
        self.profile = profile_path(profile, bundle=bundle)
        self._factory = runtime_factory
        self._lease = instance_lease(self.profile)
        self._owned = False
        self._runtime = None
        self._worker = None
        self._stopping = False
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

    def _require_open(self):
        if not self._owned:
            raise ConflictError("Open the companion profile first")

    def start(self):
        self._require_open()
        if self._stopping:
            raise ConflictError("The previous worker is still stopping; retry Stop before restarting")
        if self._runtime is None:
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
        if not self.stop():
            return False
        self._lease.release()
        self._owned = False
        return True

    def configure(self, config):
        self._require_open()
        if self._runtime is not None:
            raise ConflictError("Stop the local runtime before saving settings")
        save_config(self.profile, config)
        self.config = load_config(self.profile)

    def _running(self):
        self._require_open()
        if self._runtime is None or self._stopping:
            raise ConflictError("Start the local runtime first")
        return self._runtime

    def submit(self, scenario, *, mode, agent_id=None):
        if mode not in {"test", "carla"}:
            raise ValueError("Choose Synthetic test or CARLA")
        runtime = self._running()
        recipe = validate_scenario(scenario)
        return runtime.worker().submit(recipe, mode=mode, agent_id=agent_id,
            options=SimulationOptions(carla_quality=self.config.carla_quality))

    def cancel(self, simulation_id):
        return self._running().worker().cancel(UUID(str(simulation_id)))

    def register_agent(self, package_path, *, trusted_local_code=False):
        request = AgentRegistration(package_path=str(Path(package_path).resolve()),
            python_executable=sys.executable, trusted_local_code=trusted_local_code)
        return self._running().registry().register(request)

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
                agents = [{"id": str(agent.id), "name": agent.snapshot.manifest.name}
                          for agent in self._runtime.registry().list(active=True, limit=100)]
        return {"queue": status, "runs": runs, "agents": agents,
                "hosted": "Unavailable — pairing is not implemented in this private prototype"}

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
        return {"schema_version": "poltergeist-companion-diagnostics-v1", "profile": str(self.profile),
                "python": sys.executable, "hosted_pairing_available": False,
                "runtime": self.snapshot()["queue"], "readiness": report.model_dump(mode="json")}

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
