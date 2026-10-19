"""Use actual SQLite transactions, the runner, and disk artifacts together."""

from datetime import timedelta
from contextlib import contextmanager
import json
from threading import Event
from uuid import uuid4

import pytest

from backend.config import Settings
from backend.database import Database
from backend.persistence_errors import ConflictError, PersistenceError
from backend.repositories import FailureRepository, ScenarioRepository, SimulationRepository
from backend.services.persistence_service import PersistenceService
from backend.storage import ArtifactStore
from scenarios import validate_scenario
from shared.models import CollisionEvent, FailureReport
from simulator import SimulationRunner
from simulator.protocols import SensorSample
from simulator.test_adapter import TestSimulationAdapter


def scene():
    return validate_scenario({
        "name": "stored-simulation", "duration_s": 0.2,
        "actors": [{"type": "ego_vehicle", "speed_mps": 2, "spawn_point_index": 0}],
    })


@pytest.fixture
def service(tmp_path):
    instance = PersistenceService(Database(f"sqlite:///{(tmp_path / 'database' / 'test.db').as_posix()}"), ArtifactStore(tmp_path / "artifacts"))
    instance.initialize()
    yield instance
    instance.database.dispose()


class Capturing(TestSimulationAdapter):
    def __init__(self):
        super().__init__()
        self.last_sample = -1

    def sensor_samples(self):
        if self._frame_index == self.last_sample or not self._active:
            return ()
        self.last_sample = self._frame_index
        return (SensorSample("camera", self._frame_index, self._frame_index * 0.05, b"abcd", {
            "encoding": "bgra8", "width": 1, "height": 1,
        }),)


def test_construction_and_settings_do_not_create_files(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'database' / 'test.db').as_posix()}", storage_dir=tmp_path / "artifacts")
    instance = PersistenceService.from_settings(settings)
    assert list(tmp_path.iterdir()) == []
    instance.initialize()
    instance.initialize()
    assert (tmp_path / "database" / "test.db").is_file()
    assert (tmp_path / "artifacts").is_dir()
    instance.database.dispose()


def test_saved_scenario_edits_do_not_rewrite_queued_or_finished_run_snapshots(service):
    saved = service.save_scenario(scene())
    queued = service.create_simulation(saved.id, mode="test")
    with service.database.session() as session:
        changed = scene().model_dump() | {"name": "edited"}
        ScenarioRepository(session).update(saved.id, changed, expected_revision=saved.revision)
    output = SimulationRunner().run(queued.simulation)
    before = output.result.model_dump(mode="json")
    persisted = service.persist_output(output, expected_revision=queued.revision)
    assert output.result.model_dump(mode="json") == before
    assert persisted.result.simulation.scenario.name == "stored-simulation"
    assert persisted.result.simulation.id == queued.simulation.id
    assert persisted.result.runtime == output.result.runtime
    assert persisted.result.metrics == output.result.metrics
    assert len(persisted.result.artifacts) == 1
    assert service.read_output(queued.simulation.id) == persisted


def test_persisted_result_and_finalized_trace_survive_reopening(service):
    queued = service.create_simulation(scene(), mode="test")
    output = service.persist_output(SimulationRunner().run(queued.simulation), expected_revision=queued.revision)
    service.database.dispose()
    service.database.initialize()
    loaded = service.read_output(queued.simulation.id)
    assert loaded == output
    assert loaded.result.metrics.evaluation_settings is not None
    assert loaded.result.runtime.adapter == "test"


def test_pre_start_cancellation_round_trips_nulls_and_empty_trace(service):
    queued = service.create_simulation(scene(), mode="test")
    cancelled = Event()
    cancelled.set()
    output = SimulationRunner().run(queued.simulation, cancellation=cancelled)
    persisted = service.persist_output(output, expected_revision=queued.revision)
    loaded = service.read_output(queued.simulation.id)
    assert loaded == persisted
    assert loaded.result.simulation.status == "cancelled"
    assert loaded.result.simulation.started_at is None
    assert loaded.telemetry == ()
    assert loaded.result.metrics.collision_count is None
    assert loaded.result.metrics.severity is None
    assert service.artifacts.read_bytes(loaded.result.artifacts[0]) == b""


def test_capture_bytes_manifest_and_runtime_are_linked_only_after_finalization(service):
    queued = service.create_simulation(scene(), mode="test")
    capture = service.artifacts.capture(queued.simulation.id)
    output = SimulationRunner(Capturing()).run(queued.simulation, on_sensor=capture.write)
    assert not list(service.artifacts.root.rglob("manifest.json"))
    stored = service.persist_output(output, expected_revision=queued.revision, capture=capture)
    recording, telemetry = stored.result.artifacts
    manifest = service.artifacts.read_capture_manifest(recording)
    assert manifest["sample_count"] == stored.result.runtime.sensor_samples_count == 5
    assert [sample["frame"] for sample in manifest["samples"]] == [frame.frame for frame in output.telemetry]
    assert telemetry.kind == "telemetry"
    assert service.read_output(queued.simulation.id) == stored


def test_failed_capture_callback_discards_raw_capture_but_saves_failed_telemetry(service):
    queued = service.create_simulation(scene(), mode="test")
    capture = service.artifacts.capture(queued.simulation.id)

    def fail_after_first_sample(sample):
        capture.write(sample)
        raise RuntimeError("capture subscriber failed")

    output = SimulationRunner(Capturing()).run(queued.simulation, on_sensor=fail_after_first_sample)
    persisted = service.persist_output(output, expected_revision=queued.revision, capture=capture)
    assert persisted.result.simulation.status == "failed"
    assert "capture subscriber failed" in persisted.result.simulation.error
    assert persisted.result.telemetry_frames == 1
    assert persisted.result.metrics.severity is None
    assert [ref.kind for ref in persisted.result.artifacts] == ["telemetry"]
    assert not list(service.artifacts.root.rglob("*.bin"))
    assert not list(service.artifacts.root.rglob("*.staging"))


@pytest.mark.parametrize("boundary", ["count", "duration", "ego", "recipe", "identity", "created", "frame_gap"])
def test_forged_output_is_rejected_without_published_artifacts(service, boundary):
    queued = service.create_simulation(scene(), mode="test")
    output = SimulationRunner().run(queued.simulation)
    if boundary == "count":
        output.result.telemetry_frames += 1
    elif boundary == "duration":
        output.result.measured_duration_s += 1
    elif boundary == "ego":
        output.telemetry[0].ego.actor_id = "another-ego"
    elif boundary == "recipe":
        output.result.simulation.scenario.name = "rewritten"
    elif boundary == "identity":
        output.result.simulation.agent = "custom"
    elif boundary == "frame_gap":
        output.telemetry[-1].frame += 1
    else:
        output.result.simulation.created_at -= timedelta(seconds=1)
    with pytest.raises(ValueError):
        service.persist_output(output, expected_revision=queued.revision)
    assert not list(service.artifacts.root.rglob("frames.ndjson"))
    assert service.get_simulation(queued.simulation.id).simulation.status == "queued"


def test_repeated_completion_does_not_overwrite_or_delete_existing_artifacts(service):
    queued = service.create_simulation(scene(), mode="test")
    output = SimulationRunner().run(queued.simulation)
    persisted = service.persist_output(output, expected_revision=queued.revision)
    before = {path: path.read_bytes() for path in service.artifacts.root.rglob("frames.ndjson")}
    with pytest.raises(ConflictError):
        service.persist_output(output, expected_revision=queued.revision)
    assert {path: path.read_bytes() for path in service.artifacts.root.rglob("frames.ndjson")} == before
    assert service.read_output(queued.simulation.id) == persisted


@pytest.mark.parametrize("keep", [0, 2])
def test_completed_output_cannot_claim_success_with_a_shortened_trace(service, keep):
    from simulator.simulation_runner import RunOutput

    queued = service.create_simulation(scene(), mode="test")
    output = SimulationRunner().run(queued.simulation)
    frames = output.telemetry[:keep]
    output.result.telemetry_frames = keep
    output.result.measured_duration_s = 0.0 if not frames else frames[-1].time_s
    with pytest.raises(PersistenceError, match="execution window"):
        service.persist_output(RunOutput(output.result, frames), expected_revision=queued.revision)
    assert not list(service.artifacts.root.rglob("frames.ndjson"))


def task_output(service, task, positions, *, speeds=None, heights=None, termination_reason="goal_reached"):
    """Create measured task evidence independently of the runner's loop."""
    from datetime import UTC, datetime

    from evaluation import evaluate_safety
    from shared.models import ActorState, Simulation, SimulationResult, TelemetryFrame
    from simulator.navigation import TaskMonitor
    from simulator.simulation_runner import RunOutput

    scenario = validate_scenario(scene().model_dump() | {"duration_s": 0.5, "driving_task": task})
    queued = service.create_simulation(scenario, mode="test")
    frames = tuple(TelemetryFrame(
        frame=index, time_s=index * 0.05,
        ego=ActorState(actor_id="ego", transform={"position": {"x": x, "z": 0 if heights is None else heights[index]}},
                       velocity_mps={"x": 0 if speeds is None else speeds[index]}),
        actors=[], collisions=[], traffic_violations=[],
    ) for index, x in enumerate(positions))
    route = [frames[0].ego.transform, scenario.driving_task.destination]
    monitor = TaskMonitor(scenario.driving_task, route, frames[0], "scripted")
    for frame in frames[1:]:
        monitor.update(frame)
    now = datetime.now(UTC)
    simulation = Simulation.model_validate(queued.simulation.model_dump() | {
        "status": "completed", "progress": 1.0, "started_at": now, "finished_at": now,
    })
    outcome = monitor.outcome("completed", [], termination_reason)
    result = SimulationResult(simulation=simulation, metrics=evaluate_safety(frames),
                              telemetry_frames=len(frames), measured_duration_s=frames[-1].time_s,
                              driving_outcome=outcome)
    return queued, RunOutput(result, frames)


def completion_task(**changes):
    return {"destination": {"position": {"x": 0.2}}, "completion_radius_m": 0.01,
            "stop_on_completion": True, **changes}


def test_goal_completion_can_persist_a_verified_shorter_trace(service):
    queued, output = task_output(service, completion_task(), [0.0, 0.1, 0.2])
    persisted = service.persist_output(output, expected_revision=queued.revision)
    loaded = service.read_output(queued.simulation.id)
    assert persisted == loaded
    assert loaded.result.driving_outcome.status == "reached"
    assert loaded.result.driving_outcome.reached_at_s == 0.1
    assert loaded.result.measured_duration_s < loaded.result.simulation.scenario.duration_s


@pytest.mark.parametrize("constraint", ["distance", "height", "speed", "hold", "claimed_time"])
def test_claimed_early_goal_must_match_measured_distance_height_speed_and_hold(service, constraint):
    task = completion_task(**{
        "completion_height_tolerance_m": 0.5, "completion_speed_mps": 0.1,
        "completion_hold_s": 0.1 if constraint == "hold" else 0.0,
    })
    positions = [0.0, 0.1, 0.15] if constraint == "distance" else [0.0, 0.1, 0.2]
    heights = [0.0, 0.0, 3.0] if constraint == "height" else None
    speeds = [0.0, 0.0, 2.0] if constraint == "speed" else None
    queued, output = task_output(service, task, positions, speeds=speeds, heights=heights,
                                 termination_reason="duration_elapsed")
    # A forged success declaration must never make a truncated trace valid.
    output.result.driving_outcome.status = "reached"
    output.result.driving_outcome.reached_at_s = 0.05 if constraint == "claimed_time" else 0.1
    output.result.driving_outcome.termination_reason = "goal_reached"
    with pytest.raises(PersistenceError, match="task telemetry"):
        service.persist_output(output, expected_revision=queued.revision)
    assert not list(service.artifacts.root.rglob("frames.ndjson"))


@pytest.mark.parametrize("stop_on_completion", [False, True])
def test_early_completion_requires_opt_in_and_the_first_reached_frame(service, stop_on_completion):
    positions = [0.0, 0.1, 0.2] if not stop_on_completion else [0.0, 0.2, 0.2]
    queued, output = task_output(service, completion_task(stop_on_completion=stop_on_completion), positions)
    with pytest.raises(PersistenceError, match="execution window|task telemetry"):
        service.persist_output(output, expected_revision=queued.revision)


def test_duration_completion_can_persist_an_unreached_driving_task(service):
    queued, output = task_output(service, completion_task(stop_on_completion=False),
                                 [0.0] * 11, termination_reason="duration_elapsed")
    persisted = service.persist_output(output, expected_revision=queued.revision)
    assert persisted.result.simulation.status == "completed"
    assert persisted.result.driving_outcome.status == "timed_out"


def test_task_cannot_use_a_missing_outcome_to_bypass_execution_window(service):
    queued, output = task_output(service, completion_task(), [0.0, 0.1, 0.2])
    output.result.driving_outcome = None
    with pytest.raises(PersistenceError, match="recorded driving outcome"):
        service.persist_output(output, expected_revision=queued.revision)


@pytest.mark.parametrize("mismatch", [None, "frame", "time", "applied_control"])
def test_control_audit_round_trips_only_frame_associated_applied_controls(service, mismatch):
    from shared.models import ControlAudit, ControlIntervention

    queued = service.create_simulation(scene(), mode="test")
    output = SimulationRunner().run(queued.simulation)
    before, after = output.telemetry[:2]
    intervention = ControlIntervention(frame=before.frame, time_s=before.time_s,
        kind="scripted_ego_speed_target", target_speed_mps=2.0,
        proposed_control=after.control, applied_control=after.control)
    if mismatch == "frame":
        intervention.frame = output.telemetry[-1].frame
    elif mismatch == "time":
        intervention.time_s = 0.025
    elif mismatch == "applied_control":
        intervention.applied_control = intervention.applied_control.model_copy(update={"brake": 1.0})
    output.result.control_audit = ControlAudit(mode="scripted", controller="baseline",
        navigation="legacy_assisted", interventions=[intervention])
    if mismatch is not None:
        with pytest.raises(PersistenceError, match="Control intervention"):
            service.persist_output(output, expected_revision=queued.revision)
        assert not list(service.artifacts.root.rglob("frames.ndjson"))
    else:
        persisted = service.persist_output(output, expected_revision=queued.revision)
        assert service.read_output(queued.simulation.id).result.control_audit == persisted.result.control_audit
        assert persisted.result.control_audit.interventions == [intervention]


@pytest.mark.parametrize("failure", ["adapter_step", "initial_frame_callback", "later_frame_callback"])
def test_failed_task_execution_preserves_only_measured_interventions_and_goal_evidence(service, failure):
    class FailingStep(TestSimulationAdapter):
        def step(self, control, commands):
            raise RuntimeError("world step failed")

    scenario = validate_scenario(scene().model_dump() | {
        "driving_task": completion_task(target_speed_mps=3.0, stop_on_completion=False),
        "events": [{"action": "brake", "actor_id": "ego", "trigger_time_s": 0.0, "deceleration_mps2": 1.0}],
    })
    queued = service.create_simulation(scenario, mode="test")

    def fail_on_frame(frame):
        if (failure == "initial_frame_callback" and frame.frame == 0) or (
                failure == "later_frame_callback" and frame.frame == 1):
            raise RuntimeError("frame subscriber failed")

    adapter = FailingStep() if failure == "adapter_step" else TestSimulationAdapter()
    output = SimulationRunner(adapter).run(queued.simulation, on_frame=fail_on_frame)
    assert output.result.simulation.status == "failed"
    assert output.result.driving_outcome.status == "failed"
    expected_steps = 1 if failure == "later_frame_callback" else 0
    assert len(output.telemetry) == expected_steps + 1
    assert len(output.result.control_audit.interventions) == expected_steps
    assert output.result.driving_outcome.interventions == output.result.control_audit.interventions
    persisted = service.persist_output(output, expected_revision=queued.revision)
    assert service.read_output(queued.simulation.id) == persisted


def test_conflict_at_database_commit_removes_only_this_attempts_files(service, monkeypatch):
    queued = service.create_simulation(scene(), mode="test")
    capture = service.artifacts.capture(queued.simulation.id)
    output = SimulationRunner(Capturing()).run(queued.simulation, on_sensor=capture.write)
    unrelated = service.artifacts.write_report(uuid4(), {"keep": True})

    def reject(*args, **kwargs):
        raise ConflictError("another completion won")

    monkeypatch.setattr(SimulationRepository, "complete", reject)
    with pytest.raises(ConflictError, match="another completion"):
        service.persist_output(output, expected_revision=queued.revision, capture=capture)
    assert not list(service.artifacts.root.rglob("frames.ndjson"))
    assert not list(service.artifacts.root.rglob("manifest.json"))
    assert not list(service.artifacts.root.rglob("*.bin"))
    assert json.loads(service.artifacts.read_bytes(unrelated)) == {"keep": True}
    assert service.get_simulation(queued.simulation.id).result is None


def test_telemetry_publish_failure_aborts_capture_and_leaves_database_pending(service, monkeypatch):
    queued = service.create_simulation(scene(), mode="test")
    capture = service.artifacts.capture(queued.simulation.id)
    output = SimulationRunner(Capturing()).run(queued.simulation, on_sensor=capture.write)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(service.artifacts, "write_telemetry", fail)
    with pytest.raises(OSError, match="disk full"):
        service.persist_output(output, expected_revision=queued.revision, capture=capture)
    assert not list(service.artifacts.root.rglob("manifest.json"))
    assert not list(service.artifacts.root.rglob("*.staging"))
    assert service.get_simulation(queued.simulation.id).result is None


def test_progress_transactions_and_terminal_completion_share_the_same_identity(service):
    queued = service.create_simulation(scene(), mode="test")
    revision = queued.revision

    def save_progress(simulation):
        nonlocal revision
        if simulation.status == "running":
            with service.database.session() as session:
                saved = SimulationRepository(session).update(simulation, expected_revision=revision)
            revision = saved.revision

    output = SimulationRunner().run(queued.simulation, on_progress=save_progress)
    assert service.get_simulation(queued.simulation.id).simulation.status == "running"
    persisted = service.persist_output(output, expected_revision=revision)
    assert service.read_output(queued.simulation.id) == persisted


def test_ambiguous_commit_that_succeeded_retains_referenced_files(service, monkeypatch):
    queued = service.create_simulation(scene(), mode="test")
    output = SimulationRunner().run(queued.simulation)
    original_session = service.database.session
    original_complete = SimulationRepository.complete
    fail_after_commit = False

    def complete(*args, **kwargs):
        nonlocal fail_after_commit
        result = original_complete(*args, **kwargs)
        fail_after_commit = True
        return result

    @contextmanager
    def uncertain_session(**kwargs):
        nonlocal fail_after_commit
        with original_session(**kwargs) as session:
            yield session
        if fail_after_commit:
            fail_after_commit = False
            raise OSError("commit acknowledgement lost")

    monkeypatch.setattr(SimulationRepository, "complete", complete)
    monkeypatch.setattr(service.database, "session", uncertain_session)
    with pytest.raises(OSError, match="acknowledgement"):
        service.persist_output(output, expected_revision=queued.revision)
    loaded = service.read_output(queued.simulation.id)
    assert loaded.result.simulation.status == "completed"
    assert loaded.telemetry == output.telemetry
    assert len(loaded.result.artifacts) == 1


def test_unavailable_commit_state_retains_files_for_reconciliation(service, monkeypatch):
    queued = service.create_simulation(scene(), mode="test")
    output = SimulationRunner().run(queued.simulation)
    original_get = service.get_simulation
    reads = 0

    def unavailable_after_preflight(id):
        nonlocal reads
        reads += 1
        if reads > 1:
            raise PersistenceError("database offline")
        return original_get(id)

    def fail(*args, **kwargs):
        raise OSError("commit failed")

    monkeypatch.setattr(service, "get_simulation", unavailable_after_preflight)
    monkeypatch.setattr(SimulationRepository, "complete", fail)
    with pytest.raises(OSError, match="commit failed") as error:
        service.persist_output(output, expected_revision=queued.revision)
    assert any("files retained" in note for note in error.value.__notes__)
    assert len(list(service.artifacts.root.rglob("frames.ndjson"))) == 1
    assert original_get(queued.simulation.id).result is None


def test_interrupted_database_completion_removes_published_attempt(service, monkeypatch):
    queued = service.create_simulation(scene(), mode="test")
    output = SimulationRunner().run(queued.simulation)

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(SimulationRepository, "complete", interrupt)
    with pytest.raises(KeyboardInterrupt):
        service.persist_output(output, expected_revision=queued.revision)
    assert not list(service.artifacts.root.rglob("frames.ndjson"))
    assert service.get_simulation(queued.simulation.id).result is None


def test_wrong_capture_store_is_rejected_before_foreign_publication(service, tmp_path):
    queued = service.create_simulation(scene(), mode="test")
    foreign = ArtifactStore(tmp_path / "foreign")
    foreign.initialize()
    capture = foreign.capture(queued.simulation.id)
    output = SimulationRunner(Capturing()).run(queued.simulation, on_sensor=capture.write)
    with pytest.raises(PersistenceError, match="different simulation or artifact store"):
        service.persist_output(output, expected_revision=queued.revision, capture=capture)
    assert not list(foreign.root.rglob("manifest.json"))
    assert not list(service.artifacts.root.rglob("frames.ndjson"))
    capture.abort()


@pytest.mark.parametrize("mismatch", ["frame", "count"])
def test_recording_must_match_finalized_trace_and_runtime_sample_count(service, mismatch):
    queued = service.create_simulation(scene(), mode="test")
    capture = service.artifacts.capture(queued.simulation.id)
    output = SimulationRunner().run(queued.simulation)
    capture.write(SensorSample("camera", 999 if mismatch == "frame" else 0, 0, b"abcd", {
        "encoding": "bgra8", "width": 1, "height": 1,
    }))
    with pytest.raises(PersistenceError, match="timeline|sample count"):
        service.persist_output(output, expected_revision=queued.revision, capture=capture)
    assert not list(service.artifacts.root.rglob("manifest.json"))
    assert not list(service.artifacts.root.rglob("*.bin"))


def test_failure_report_references_persisted_evidence(service):
    class Colliding(TestSimulationAdapter):
        def open(self, scenario):
            frame = super().open(scenario)
            frame.collisions = [CollisionEvent(other_actor_id="world:barrier")]
            return frame

    queued = service.create_simulation(scene(), mode="test")
    persisted = service.persist_output(SimulationRunner(Colliding()).run(queued.simulation), expected_revision=queued.revision)
    report = FailureReport(simulation_id=queued.simulation.id, scenario=queued.simulation.scenario, kind="collision", summary="Observed ego contact", severity=persisted.result.metrics.severity)
    assert service.save_failure(report) == report
    with service.database.session() as session:
        assert FailureRepository(session).get(report.id) == report
        updated = FailureRepository(session).set_reproduced(report.id, False)
    assert updated.reproduced is False


def test_read_output_rejects_missing_artifact_instead_of_returning_empty_trace(service):
    queued = service.create_simulation(scene(), mode="test")
    output = service.persist_output(SimulationRunner().run(queued.simulation), expected_revision=queued.revision)
    service.artifacts.remove(output.result.artifacts[0])
    with pytest.raises((FileNotFoundError, ValueError)):
        service.read_output(queued.simulation.id)


def test_cli_initializes_explicitly_without_starting_simulation(service, monkeypatch, capsys):
    from backend.persistence import main

    monkeypatch.setattr(PersistenceService, "from_settings", lambda: service)
    assert main(["init"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["database"] == "initialized"
    assert payload["storage"] == str(service.artifacts.root)
