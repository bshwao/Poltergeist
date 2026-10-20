"""Persist finalized runner output without owning simulation execution or a queue."""

from collections.abc import Mapping
import json
from math import ceil, isclose
from uuid import UUID

from evaluation import validate_frames
from scenarios import validate_scenario
from shared.models import FailureReport, Scenario, Simulation, SimulationResult, SimulationStatus, Transform
from simulator.simulation_runner import RunOutput

from backend.config import Settings, get_settings
from backend.database import Database
from backend.persistence_errors import ConflictError, PersistenceError
from backend.repositories import FailureRepository, ScenarioRepository, SimulationRepository
from backend.storage import ArtifactStore, CaptureWriter
from backend.decision_storage import DecisionWriter, decision_page


def _canonical(model) -> str:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False)


class PersistenceService:
    """Short database transactions and storage-owned artifacts; no CARLA calls.

    Files and SQLite cannot share one atomic transaction. Files publish first,
    and failures remove only this attempt's unreferenced artifacts. An uncertain
    commit retains files until an operator can reconcile them with the database.
    """

    def __init__(self, database: Database, artifacts: ArtifactStore):
        self.database = database
        self.artifacts = artifacts

    @classmethod
    def from_settings(cls, settings: Settings | None = None):
        settings = get_settings() if settings is None else settings
        return cls(Database(settings.database_url), ArtifactStore(settings.storage_dir))

    def initialize(self) -> None:
        """Explicit, repeatable schema migration and storage initialization."""
        self.database.initialize()
        self.artifacts.initialize()

    def save_scenario(self, scenario: Scenario | Mapping, *, id: UUID | None = None):
        with self.database.session() as session:
            return ScenarioRepository(session).create(scenario, id=id)

    def create_simulation(
        self, scenario: Scenario | Mapping | UUID, *, mode: str = "carla", agent: str = "baseline",
    ):
        """Save a queued recipe, optionally linked to a saved scenario UUID."""
        with self.database.session() as session:
            scenario_id = scenario if isinstance(scenario, UUID) else None
            recipe = ScenarioRepository(session).get(scenario_id).scenario if scenario_id is not None else validate_scenario(scenario)
            simulation = Simulation(scenario=recipe, mode=mode, agent=agent)
            return SimulationRepository(session).create(simulation, scenario_id=scenario_id)

    def get_simulation(self, simulation_id: UUID):
        with self.database.session(write=False) as session:
            return SimulationRepository(session).get(simulation_id)

    def save_failure(self, report: FailureReport):
        with self.database.session() as session:
            return FailureRepository(session).create(report)

    @staticmethod
    def _validate_trace(result: SimulationResult, telemetry):
        frames = validate_frames(telemetry)
        if len(frames) != result.telemetry_frames:
            raise PersistenceError("Result frame count does not match finalized telemetry")
        scenario = result.simulation.scenario
        maximum = ceil(scenario.duration_s / scenario.fixed_delta_seconds - 1e-9) + 1
        if len(frames) > maximum:
            raise PersistenceError("Finalized telemetry length does not match the scenario execution window")
        duration = 0.0 if not frames else frames[-1].time_s - frames[0].time_s
        if not isclose(duration, result.measured_duration_s, rel_tol=1e-9, abs_tol=1e-6):
            raise PersistenceError("Result duration does not match finalized telemetry")
        ego_id = next(actor.id for actor in result.simulation.scenario.actors if actor.type == "ego_vehicle")
        if any(frame.ego.actor_id != ego_id for frame in frames):
            raise PersistenceError("Telemetry ego does not match the persisted scenario")
        if frames and not isclose(frames[0].time_s, 0.0, abs_tol=1e-6):
            raise PersistenceError("Runner telemetry must begin at elapsed time zero")
        for previous, current in zip(frames, frames[1:]):
            if current.frame != previous.frame + 1 or not isclose(
                current.time_s - previous.time_s, result.simulation.scenario.fixed_delta_seconds,
                rel_tol=1e-5, abs_tol=1e-6,
            ):
                raise PersistenceError("Runner telemetry must preserve consecutive fixed-step frames")
        PersistenceService._validate_control_audit(result, frames)
        early_goal_completion = PersistenceService._validate_driving_outcome(result, frames)
        if result.simulation.status == SimulationStatus.COMPLETED and len(frames) != maximum and not early_goal_completion:
            raise PersistenceError("Finalized telemetry length does not match the scenario execution window")
        return tuple(frames)

    @staticmethod
    def _validate_control_audit(result: SimulationResult, frames) -> None:
        audit = result.control_audit
        interventions = audit.interventions if audit is not None else (
            [] if result.driving_outcome is None else result.driving_outcome.interventions)
        indices = {frame.frame: index for index, frame in enumerate(frames)}
        for intervention in interventions:
            index = indices.get(intervention.frame)
            if index is None or index + 1 >= len(frames):
                raise PersistenceError("Control intervention must reference an observed transition")
            if not isclose(intervention.time_s, frames[index].time_s, rel_tol=1e-5, abs_tol=1e-6):
                raise PersistenceError("Control intervention time does not match its observation")
            if intervention.applied_control != frames[index + 1].control:
                raise PersistenceError("Control intervention applied control does not match finalized telemetry")

    @staticmethod
    def _validate_driving_outcome(result: SimulationResult, frames) -> bool:
        """Verify goal evidence rather than trusting a claimed early success.

        Lifecycle completion normally requires the entire execution window.
        A declared stop-on-completion task is the sole shorter-window exception;
        replay uses finalized measured poses, speeds, height, and hold time.
        """
        task = result.simulation.scenario.driving_task
        outcome = result.driving_outcome
        if task is None:
            if outcome is not None:
                raise PersistenceError("Driving outcome requires the recorded scenario task")
            return False
        if outcome is None:
            if frames or result.simulation.status == SimulationStatus.COMPLETED:
                raise PersistenceError("Task execution requires a recorded driving outcome")
            # An interrupted setup may never acquire a first measured ego pose.
            return False
        if not frames:
            raise PersistenceError("Driving outcome requires measured task telemetry")

        from simulator.navigation import TaskMonitor

        monitor = TaskMonitor(task, outcome.resolved_route, frames[0], outcome.control_mode)
        for frame in frames[1:]:
            monitor.update(frame)
        reason = {SimulationStatus.FAILED: "execution_failed", SimulationStatus.CANCELLED: "cancelled"}.get(
            result.simulation.status,
            "goal_reached" if task.stop_on_completion and monitor.reached else "duration_elapsed",
        )
        expected = monitor.outcome(result.simulation.status, outcome.interventions, reason)
        if expected != outcome:
            raise PersistenceError("Driving outcome does not match finalized task telemetry")
        early_goal_completion = (
            result.simulation.status == SimulationStatus.COMPLETED
            and task.stop_on_completion and outcome.status == "reached"
            and outcome.termination_reason == "goal_reached"
            and monitor.reached and monitor.reached_at_s is not None
            and isclose(monitor.reached_at_s, frames[-1].time_s, rel_tol=1e-5, abs_tol=1e-6)
        )
        return early_goal_completion

    @staticmethod
    def _validate_pending(stored, result, expected_revision):
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise PersistenceError("expected_revision must be a positive integer")
        if stored.revision != expected_revision:
            raise ConflictError("Simulation revision changed before output persistence")
        if stored.result is not None or stored.simulation.status not in {SimulationStatus.QUEUED, SimulationStatus.RUNNING}:
            raise ConflictError("A terminal simulation cannot be completed again")
        previous, current = stored.simulation, result.simulation
        if any(getattr(previous, name) != getattr(current, name) for name in ("id", "agent", "mode", "created_at")):
            raise PersistenceError("Runner output does not match the queued simulation identity")
        if _canonical(previous.scenario) != _canonical(current.scenario):
            raise PersistenceError("Runner output changed the queued scenario snapshot")

    def persist_output(self, output: RunOutput, *, expected_revision: int, capture: CaptureWriter | None = None,
                       decisions: DecisionWriter | None = None) -> RunOutput:
        """Publish finalized artifacts, then atomically save the terminal result.

        The caller supplies the current database revision (including any saved
        progress updates). Decision-enabled executions retain valid partial raw
        inputs on failure; legacy callers keep their discard-on-failure behavior.
        This method never retries a
        stale completion or replaces a previously saved result.
        """
        created = []
        simulation_id = None
        owns_capture = False
        try:
            result = SimulationResult.model_validate(output.result.model_dump(mode="json"))
            simulation_id = result.simulation.id
            owns_capture = capture is not None and capture.simulation_id == simulation_id and capture.belongs_to(self.artifacts)
            if result.artifacts:
                raise PersistenceError("Persist runner output without preattached artifact references")
            frames = self._validate_trace(result, output.telemetry)
            stored = self.get_simulation(simulation_id)
            self._validate_pending(stored, result, expected_revision)
            if capture is not None and not owns_capture:
                raise PersistenceError("Capture belongs to a different simulation or artifact store")

            if decisions is not None and (decisions.store is not self.artifacts or decisions.simulation_id != simulation_id):
                raise PersistenceError("Decision writer belongs to a different run or store")
            if capture is not None:
                if result.simulation.status == SimulationStatus.FAILED and decisions is None:
                    capture.abort()
                else:
                    reference = capture.commit()
                    if reference is not None:
                        created.append(reference)
                        self._validate_recording(reference, result, frames)
                    elif result.runtime is not None and result.runtime.sensor_samples_count:
                        raise PersistenceError("Capture is missing the sensor samples recorded by the runner")
            created.append(self.artifacts.write_telemetry(simulation_id, frames))
            if decisions is not None:
                recording_index = next((index for index, ref in enumerate(created) if ref.kind == "recording"), None)
                created.append(decisions.commit(recording_index))
            for reference in created:
                self.artifacts.validate_reference(reference, simulation_id=simulation_id)
            result = SimulationResult.model_validate(result.model_dump(mode="json") | {
                "artifacts": [reference.model_dump(mode="json") for reference in created],
            })
            if decisions is not None:
                decision_page(self.artifacts, result, frames, validate_sensors=True)
            with self.database.session() as session:
                SimulationRepository(session).complete(result, expected_revision=expected_revision)
            return RunOutput(result=result, telemetry=frames)
        except BaseException as error:
            if decisions is not None and decisions.store is self.artifacts and decisions.simulation_id == simulation_id:
                try:
                    decisions.abort()
                except BaseException as cleanup:
                    error.add_note(f"Decision cleanup failed: {cleanup}")
            if owns_capture:
                try:
                    capture.abort()
                except BaseException as cleanup:
                    error.add_note(f"Capture cleanup failed: {cleanup}")
            self._remove_unreferenced(created, simulation_id, error)
            raise

    def _validate_recording(self, reference, result, frames):
        manifest = self.artifacts.read_capture_manifest(reference)
        timeline = {frame.frame for frame in frames}
        if any(sample["frame"] not in timeline for sample in manifest["samples"]):
            raise PersistenceError("Capture contains samples outside the finalized telemetry timeline")
        if result.runtime is not None and manifest["sample_count"] != result.runtime.sensor_samples_count:
            raise PersistenceError("Capture sample count does not match runner runtime metadata")
        inputs = result.agent_inputs
        if inputs is None or inputs.observation_mode != "sensors":
            return
        declared = {spec.id: spec for spec in inputs.sensors}
        observed = {frame.frame: set() for frame in frames}
        times = {frame.frame: frame.time_s for frame in frames}
        time_origin = None
        for sample in manifest["samples"]:
            metadata = sample["metadata"]
            if "agent_sensor_id" not in metadata:
                # Optional recording sensors remain distinct legacy streams;
                # they cannot stand in for the agent's declared sensor bundle.
                continue
            sensor_id = metadata["agent_sensor_id"]
            spec = declared.get(sensor_id)
            if spec is None:
                raise PersistenceError("Capture contains an undeclared agent sensor ID")
            camera = spec.type == "sensor.camera.rgb"
            if (sample["kind"] != ("camera" if camera else "lidar")
                    or metadata.get("sensor_type") != spec.type
                    or metadata.get("source") != inputs.source
                    or metadata.get("encoding") != ("bgra8" if camera else "float32_xyzi")):
                raise PersistenceError("Capture agent sensor type or source does not match its declared inputs")
            try:
                mount = Transform.model_validate(metadata.get("mount"))
            except (ValueError, TypeError) as error:
                raise PersistenceError("Capture agent sensor mount is invalid") from error
            if mount != spec.mount:
                raise PersistenceError("Capture agent sensor mount does not match its declared calibration")
            if camera:
                fov = metadata.get("fov_deg")
                if (metadata.get("width") != spec.attributes["image_size_x"]
                        or metadata.get("height") != spec.attributes["image_size_y"]
                        or metadata.get("channels") is not None
                        or type(fov) not in (int, float)
                        or not isclose(fov, spec.attributes["fov"], rel_tol=0, abs_tol=1e-6)):
                    raise PersistenceError("Capture camera dimensions or FOV do not match its declared calibration")
            elif (metadata.get("channels") != spec.attributes["channels"]
                    or any(metadata.get(field) is not None for field in ("width", "height", "fov_deg"))):
                raise PersistenceError("Capture LiDAR channels do not match its declared calibration")
            origin = sample["timestamp"] - times[sample["frame"]]
            if time_origin is None:
                time_origin = origin
            elif not isclose(origin, time_origin, rel_tol=0, abs_tol=1e-6):
                raise PersistenceError("Capture agent sensor timestamp does not match the finalized telemetry clock")
            if sensor_id in observed[sample["frame"]]:
                raise PersistenceError("Capture contains a duplicate declared sensor for one telemetry frame")
            observed[sample["frame"]].add(sensor_id)
        for index, frame in enumerate(frames):
            # Cancellation can interrupt capture of the last measured packet,
            # including between two recording callbacks. Earlier frames were
            # fully observed before execution could advance to the next tick.
            complete = result.simulation.status == SimulationStatus.COMPLETED or index < len(frames) - 1
            if complete and observed[frame.frame] != set(declared):
                raise PersistenceError("Capture is missing a declared agent sensor in a finalized frame")

    def _remove_unreferenced(self, created, simulation_id, error):
        if not created:
            return
        try:
            stored = self.get_simulation(simulation_id)
            referenced = set() if stored.result is None else {reference.path for reference in stored.result.artifacts}
        except BaseException as lookup:
            # A commit failure can be ambiguous. Preserve files rather than
            # deleting an artifact that a successfully committed row references.
            error.add_note(f"Could not determine artifact commit state; files retained: {lookup}")
            return
        for reference in reversed(created):
            if reference.path not in referenced:
                try:
                    self.artifacts.remove(reference)
                except BaseException as cleanup:
                    error.add_note(f"Artifact cleanup failed for {reference.path}: {cleanup}")

    def read_output(self, simulation_id: UUID) -> RunOutput:
        """Load a saved terminal result and its validated finalized trace."""
        stored = self.get_simulation(simulation_id)
        result = stored.result
        if result is None:
            raise ConflictError("Simulation has no persisted result yet")
        telemetry = [reference for reference in result.artifacts if reference.kind == "telemetry"]
        if len(telemetry) != 1:
            raise PersistenceError("Persisted result must reference exactly one telemetry artifact")
        for reference in result.artifacts:
            self.artifacts.validate_reference(reference, simulation_id=simulation_id)
        frames = self._validate_trace(result, self.artifacts.read_telemetry(telemetry[0]))
        for reference in result.artifacts:
            if reference.kind == "recording":
                self._validate_recording(reference, result, frames)
        decision_page(self.artifacts, result, frames, validate_sensors=True)
        return RunOutput(result, frames)

    def read_decisions(self, simulation_id: UUID, *, offset=0, limit=25):
        stored = self.get_simulation(simulation_id)
        if stored.result is None:
            raise ConflictError("Simulation output is not finalized yet")
        refs = [ref for ref in stored.result.artifacts if ref.kind == "telemetry"]
        if len(refs) != 1:
            raise PersistenceError("Persisted result must reference exactly one telemetry artifact")
        self.artifacts.validate_reference(refs[0], simulation_id)
        frames = self._validate_trace(stored.result, self.artifacts.read_telemetry(refs[0]))
        return decision_page(self.artifacts, stored.result, frames, offset=offset, limit=limit)
