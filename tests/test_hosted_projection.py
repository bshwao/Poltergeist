"""Public summaries disclose selected facts, never local execution metadata."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError
import pytest

from backend.job_repository import StoredJob
from backend.repositories import StoredSimulation
from companion.hosted_projection import HostedProjectionError, project_hosted_run
from shared.models import (AgentInputInfo, ArtifactReference, ControlAudit,
    EvaluationCoverage, EvaluationSettings, SafetyMetrics, Scenario, Simulation,
    SimulationResult, SimulationRuntime, Transform)
from shared.models.agent import AgentManifest
from shared.models.hosted import HostedRunSummaryV1
from shared.models.registry import (AgentExecutionOptions, AgentExternalFileFingerprint,
    AgentFileFingerprint, AgentSnapshot)
from shared.models.runtime import SimulationOptions


NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)
SENTINEL = "private_hosted_path_and_token_sentinel"
SCENARIO_SHA = "a" * 64


class FutureMetrics(SafetyMetrics):
    private_metadata: dict


class FutureRuntime(SimulationRuntime):
    diagnostics: dict


class FutureResult(SimulationResult):
    private_metadata: dict


class FutureOptions(SimulationOptions):
    local_configuration: dict


def record(status="completed", *, mode="test", cancel=False, with_result=True,
           metrics=None, runtime=None):
    scenario = Scenario.model_validate({"name": "Approved baseline experiment", "map": "Town03",
        "description": "C:/" + SENTINEL + "/configuration.json",
        "parameters": {"local_note": "Bearer " + SENTINEL},
        "duration_s": .1, "actors": [{"id": "ego", "type": "ego_vehicle", "speed_mps": 2.}]})
    terminal = status not in ("queued", "running")
    simulation = Simulation(scenario=scenario, mode=mode, status=status,
        progress=1. if status == "completed" else .25 if status == "running" else 0.,
        created_at=NOW, started_at=None if status == "queued" else NOW + timedelta(seconds=1),
        finished_at=NOW + timedelta(seconds=2) if terminal else None,
        error="Cannot open C:/" + SENTINEL + "/model.bin; token=" + SENTINEL if status == "failed" else None)
    if metrics is None:
        metrics = FutureMetrics(collision_count=0, min_ttc_s=None, severity=None,
            coverage=EvaluationCoverage(total_frames=5, collision_frames=5, braking_frames=5,
                traffic_rule_frames=0, clearance_frames=5, ttc_frames=5,
                actor_pairs=10, clearance_pairs=8, ttc_pairs=2),
            evaluation_settings=EvaluationSettings(),
            private_metadata={"worker": {"python": "C:/" + SENTINEL + "/python.exe", "token": SENTINEL}})
    if runtime is None:
        runtime = FutureRuntime(adapter=mode, server_version="0.9.16",
            client_version="0.9.16 token=" + SENTINEL,
            map_name="/Game/Carla/" + SENTINEL + "/Maps/Town10HD_Opt/Town10HD_Opt",
            fixed_delta_seconds=.05, seed=7,
            resolved_spawns={"C:/" + SENTINEL + "/actor": Transform()},
            diagnostics={"paths": ["C:/" + SENTINEL + "/cache"], "secret": SENTINEL})
    result = None
    if terminal and with_result:
        result = FutureResult(simulation=simulation, metrics=metrics, telemetry_frames=5,
            measured_duration_s=.1, runtime=runtime,
            artifacts=[ArtifactReference(kind="recording", path="recordings/" + SENTINEL + "/manifest.json",
                media_type="application/" + SENTINEL)],
            agent_inputs=AgentInputInfo(observation_mode="simulator_state",
                source="synthetic_state" if mode == "test" else "carla_ground_truth",
                ego_state_source="simulator_state", ground_truth_actor_inputs=True,
                assistance=["C:/" + SENTINEL + "/privileged-input"]),
            control_audit=ControlAudit(mode="scripted", controller="baseline", navigation="legacy_assisted"),
            private_metadata={"exceptions": [SENTINEL], "credentials": {"bearer": SENTINEL}})
    stored = StoredSimulation(simulation=simulation, result=result, scenario_id=uuid4(), revision=9,
        created_at=NOW, updated_at=NOW + timedelta(seconds=3))
    job = StoredJob(simulation.id, FutureOptions(local_configuration={"paths": [SENTINEL], "secrets": {"key": SENTINEL}}),
        EvaluationSettings(), cancel_requested=cancel, claimed=status != "queued")
    return stored, job


def project(stored, job, **changes):
    binding = {"device_id": uuid4(), "ownership_generation": 1, "execute_request_id": uuid4(),
        "receipt_revision": 11, "scenario_sha256": SCENARIO_SHA,
        "evidence_state": "local_only" if stored.result is not None else "missing"}
    binding.update(changes)
    return project_hosted_run(stored, job, **binding)


def test_failed_summary_omits_nested_secrets_paths_exceptions_and_future_local_fields():
    stored, job = record("failed", cancel=True)
    summary = project(stored, job)
    wire = summary.model_dump_json()
    assert SENTINEL not in wire
    for private_field in ("agent_snapshot", "configuration", "diagnostics", "resolved_spawns", "assistance", "artifacts"):
        assert '"' + private_field + '":' not in wire
    assert summary.error.code == "execution_failed"
    assert summary.error.message == "The experiment failed on this computer. Review its local diagnostics."
    assert summary.error.correlation_id is not None
    assert summary.cancel_requested and summary.status == "failed"
    assert summary.runtime.map_name == "Town10HD_Opt"
    assert summary.runtime.client_version is None and summary.runtime.server_version == "0.9.16"
    assert summary.scenario.name == stored.simulation.scenario.name and summary.scenario.sha256 == SCENARIO_SHA
    assert summary.evidence.artifact_reference_count == 1
    assert HostedRunSummaryV1.model_validate_json(wire) == summary


def test_projection_performs_no_local_model_dumps_or_filesystem_reads(monkeypatch):
    stored, job = record()
    def forbidden(*args, **kwargs):
        raise AssertionError("Generic local serialization or file I/O is forbidden")
    with monkeypatch.context() as scoped:
        for kind in (Scenario, Simulation, SimulationResult, SafetyMetrics, EvaluationSettings,
                     SimulationRuntime, AgentInputInfo, ControlAudit, SimulationOptions):
            scoped.setattr(kind, "model_dump", forbidden)
            scoped.setattr(kind, "model_dump_json", forbidden)
        scoped.setattr(Path, "open", forbidden)
        scoped.setattr(Path, "exists", forbidden)
        summary = project(stored, job)
    assert summary.result_available and summary.metrics.collision_count == 0


def test_missing_evidence_preserves_recorded_counts_nulls_and_incomplete_coverage():
    stored, job = record()
    summary = project(stored, job, evidence_state="missing")
    assert summary.result_available and summary.evidence.state == "missing"
    assert summary.evidence.telemetry_frames == 5 and summary.evidence.artifact_reference_count == 1
    assert summary.metrics.collision_count == 0 and summary.metrics.min_ttc_s is None
    assert summary.metrics.traffic_violation_count is None and summary.metrics.severity is None
    assert summary.metrics.coverage.total_frames == 5 and not summary.metrics.coverage.complete
    assert summary.metrics.coverage.traffic_rule_frames == 0 and summary.metrics.coverage.ttc_pairs == 2
    assert summary.metrics.evaluation_settings.ttc_weight == 45.
    assert summary.measured_duration_s == .1


def test_unavailable_scoring_provenance_remains_null_instead_of_using_requested_defaults():
    stored, job = record(metrics=SafetyMetrics())
    summary = project(stored, job)
    assert summary.metrics.evaluation_settings is None
    assert summary.metrics.coverage.total_frames == 0 and not summary.metrics.coverage.complete
    assert all(getattr(summary.metrics, name) is None for name in ("collision_count", "traffic_violation_count",
        "min_clearance_m", "min_ttc_s", "max_deceleration_mps2", "hard_braking_count",
        "max_stopping_distance_m", "max_stopping_time_s", "severity"))


@pytest.mark.parametrize("status", ["queued", "running", "completed", "failed", "cancelled"])
def test_lifecycle_and_cancellation_request_are_distinct(status):
    stored, job = record(status, cancel=True)
    summary = project(stored, job, queue_position=2 if status == "queued" else None)
    assert summary.status == status and summary.cancel_requested is True
    assert summary.started_at == stored.simulation.started_at and summary.finished_at == stored.simulation.finished_at
    assert summary.queue_position == (2 if status == "queued" else None)
    assert summary.result_available == (status not in ("queued", "running"))
    if status in ("queued", "running"):
        assert summary.metrics is None and summary.runtime is None and summary.provenance is None
        assert summary.error is None and summary.evidence.telemetry_frames is None
    if status == "cancelled":
        assert summary.error is None


def test_missing_job_cancellation_is_unknown_and_receipt_sequence_can_advance_independently():
    stored, job = record("running")
    assert project(stored, None).cancel_requested is None
    kwargs = {"device_id": uuid4(), "execute_request_id": uuid4()}
    first = project(stored, job, receipt_revision=1, **kwargs)
    second = project(stored, replace(job, cancel_requested=True), receipt_revision=2, **kwargs)
    assert first.revision == second.revision == stored.revision
    assert first.receipt_revision == 1 and second.receipt_revision == 2
    assert not first.cancel_requested and second.cancel_requested and second.status == "running"


@pytest.mark.parametrize("status", ["completed", "cancelled"])
def test_terminal_lifecycle_without_result_is_explicitly_unavailable(status):
    stored, job = record(status, with_result=False)
    summary = project(stored, job)
    assert summary.status == status and not summary.result_available
    assert summary.metrics is None and summary.evidence.state == "missing"
    assert summary.error.code == "result_unavailable"
    with pytest.raises(HostedProjectionError):
        project(stored, job, evidence_state="local_only")


@pytest.mark.parametrize("map_name,expected", [("/Game/Carla/Maps/Town03", "Town03"),
    ("C:\\private\\Town10HD_Opt.umap", "Town10HD_Opt"), ("Town12", "Town12"),
    ("/private/" + SENTINEL, None), ("Town03?token=" + SENTINEL, None)])
def test_only_allowlisted_runtime_basenames_are_public(map_name, expected):
    runtime = SimulationRuntime(adapter="carla", server_version="0.9.16", client_version=SENTINEL,
        map_name=map_name, fixed_delta_seconds=.05, seed=7)
    stored, job = record(mode="carla", runtime=runtime)
    summary = project(stored, job)
    assert summary.runtime.map_name == expected and summary.runtime.client_version is None
    assert SENTINEL not in summary.model_dump_json()


def snapshot(tmp_path):
    configuration = {"credentials": {"token": SENTINEL}}
    manifest = AgentManifest(agent_id="private-agent", name="Private agent", version="1.0",
        entry_point="agent:Agent", configuration=configuration)
    return AgentSnapshot(registration_id=uuid4(), package_path=str(tmp_path / SENTINEL),
        python_executable=str(tmp_path / SENTINEL / "python.exe"), python_sha256="a" * 64,
        python_size_bytes=1, manifest=manifest, configuration=configuration,
        observation_mode="simulator_state", files=[AgentFileFingerprint(path="agent.py", sha256="a" * 64, size_bytes=1)],
        model_files=[AgentExternalFileFingerprint(path=str(tmp_path / SENTINEL / "model.bin"), sha256="b" * 64, size_bytes=1)],
        package_sha256="c" * 64, process_options=AgentExecutionOptions(), agent_control_mode="scripted")


def test_custom_agent_and_hidden_job_agent_are_rejected_without_disclosing_snapshot(tmp_path):
    stored, job = record("queued")
    agent = snapshot(tmp_path)
    custom = Simulation(scenario=stored.simulation.scenario, mode="test", agent="custom", agent_snapshot=agent,
        created_at=NOW)
    for changed, changed_job in ((replace(stored, simulation=custom), job),
            (stored, replace(job, agent_snapshot=agent))):
        with pytest.raises(HostedProjectionError) as error:
            project(changed, changed_job)
        assert SENTINEL not in str(error.value)


@pytest.mark.parametrize("change", [{"ownership_generation": True}, {"ownership_generation": 0},
    {"receipt_revision": 0}, {"scenario_sha256": "A" * 64}, {"scenario_sha256": SENTINEL},
    {"device_id": str(uuid4())}, {"queue_position": True}, {"evidence_state": "available"}])
def test_binding_and_public_metadata_are_strict(change):
    stored, job = record()
    with pytest.raises(HostedProjectionError) as error:
        project(stored, job, **change)
    assert str(error.value) == "The local run could not be projected safely"


def test_detached_job_result_and_baseline_identity_must_match():
    stored, job = record()
    other, _ = record()
    for changed, changed_job in ((stored, replace(job, simulation_id=uuid4())),
            (replace(stored, result=other.result), job),
            (replace(stored, simulation=stored.simulation.model_copy(update={"agent": "custom"})), job)):
        with pytest.raises(HostedProjectionError):
            project(changed, changed_job)


@pytest.mark.parametrize("path,value", [("progress", "1.0"), ("progress", True), ("progress", float("nan")),
    ("receipt_revision", True), ("agent", "sdk_agent"), ("status", "secret-status"),
    ("remote_execution_enabled", True), ("path", "C:/" + SENTINEL)])
def test_wire_dto_rejects_coercions_nonfinite_fields_and_unapproved_extensions(path, value):
    stored, job = record()
    payload = json.loads(project(stored, job).model_dump_json())
    payload[path] = value
    with pytest.raises(ValidationError) as error:
        HostedRunSummaryV1.model_validate_json(json.dumps(payload))
    assert SENTINEL not in str(error.value)


def test_nested_public_error_metrics_coverage_and_runtime_cannot_accept_private_extensions():
    stored, job = record("failed")
    original = json.loads(project(stored, job).model_dump_json())
    for field in ("error", "metrics", "runtime", "provenance", "scenario", "evidence"):
        payload = json.loads(json.dumps(original))
        payload[field]["private_metadata"] = {"path": "C:/" + SENTINEL, "token": SENTINEL}
        with pytest.raises(ValidationError) as error:
            HostedRunSummaryV1.model_validate_json(json.dumps(payload))
        assert SENTINEL not in str(error.value)
    for changes in ({"complete": True}, {"ttc_pairs": 99}):
        payload = json.loads(json.dumps(original))
        payload["metrics"]["coverage"].update(changes)
        with pytest.raises(ValidationError):
            HostedRunSummaryV1.model_validate_json(json.dumps(payload))
    payload = json.loads(json.dumps(original))
    payload["error"]["message"] = "C:/" + SENTINEL
    with pytest.raises(ValidationError) as error:
        HostedRunSummaryV1.model_validate_json(json.dumps(payload))
    assert SENTINEL not in str(error.value)


@pytest.mark.parametrize("change", ["numeric_provenance_bool", "missing_measured_duration",
    "missing_evidence_count", "queued_with_result", "update_before_finish", "completed_with_partial_progress"])
def test_wire_lifecycle_and_measured_metadata_cannot_contradict_the_record(change):
    stored, job = record()
    payload = json.loads(project(stored, job).model_dump_json())
    if change == "numeric_provenance_bool":
        payload["provenance"]["ground_truth_actor_inputs"] = 1
    elif change == "missing_measured_duration":
        payload["measured_duration_s"] = None
    elif change == "missing_evidence_count":
        payload["evidence"]["telemetry_frames"] = None
    elif change == "queued_with_result":
        payload.update(status="queued", started_at=None, finished_at=None, progress=0.)
    elif change == "update_before_finish":
        payload["updated_at"] = NOW.isoformat()
    else:
        payload["progress"] = .5
    with pytest.raises(ValidationError):
        HostedRunSummaryV1.model_validate_json(json.dumps(payload))
