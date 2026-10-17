"""Exports retain actual persisted evidence without starting execution services."""

import csv
from datetime import timedelta
from io import StringIO
import json
from threading import Event
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.database import Database
from backend.dependencies import get_persistence
from backend.job_repository import JobRepository
from backend.main import create_app
from backend.persistence_errors import ConflictError
from backend.repositories import SimulationRepository
from backend.services.persistence_service import PersistenceService
from backend.services.report_service import build_report, export_report
from backend.storage import ArtifactStore
from scenarios import validate_scenario
from shared.models import CollisionEvent, EvaluationSettings, FailureReport, SafetyMetrics, SimulationOptions, SimulationResult, SimulationStatus
from simulator import SimulationRunner
from simulator.test_adapter import TestSimulationAdapter


def scene(name="exported recipe"):
    return validate_scenario({"name": name, "duration_s": 0.1, "seed": 173,
                              "actors": [{"type": "ego_vehicle", "speed_mps": 2, "spawn_point_index": 0}]})


@pytest.fixture
def persistence(tmp_path):
    service = PersistenceService(Database(f"sqlite:///{(tmp_path / 'runs.db').as_posix()}"), ArtifactStore(tmp_path / "artifacts"))
    service.initialize()
    yield service
    service.database.dispose()


@pytest.fixture
def api(persistence):
    def reject_execution(**kwargs):
        raise AssertionError("A report must never create a runner")
    app = create_app(Settings(_env_file=None), runner_factory=reject_execution)
    app.dependency_overrides[get_persistence] = lambda: persistence
    with TestClient(app) as client:
        yield client, app.state.runtime


def saved(persistence, *, collision=False, name="exported recipe", cancelled=False, job=True):
    class Colliding(TestSimulationAdapter):
        def open(self, scenario):
            frame = super().open(scenario)
            if collision:
                frame.collisions = [CollisionEvent(other_actor_id="world:barrier")]
            return frame
    scenario = persistence.save_scenario(scene(name))
    queued = persistence.create_simulation(scenario.id, mode="test")
    options = SimulationOptions(max_ticks=34)
    settings = EvaluationSettings(speeding_tolerance_mps=0.7)
    if job:
        with persistence.database.session() as session:
            JobRepository(session).create(queued.simulation.id, options, settings)
    cancellation = Event()
    if cancelled:
        cancellation.set()
    output = SimulationRunner(Colliding(), options=options, evaluation_settings=settings).run(queued.simulation, cancellation=cancellation)
    return persistence.persist_output(output, expected_revision=queued.revision)


def test_json_retains_recipe_mode_settings_runtime_timestamps_and_nulls(persistence):
    output = saved(persistence)
    report = build_report(persistence, output.result.simulation.id)
    assert report["schema"] == "poltergeist-run-report-v1"
    record = report["record"]
    assert record["simulation"] == output.result.simulation.model_dump(mode="json")
    assert record["result"] == output.result.model_dump(mode="json")
    assert record["scenario_id"] is not None
    assert record["job"]["options"]["max_ticks"] == 34
    assert record["job"]["evaluation_settings"]["speeding_tolerance_mps"] == 0.7
    assert record["job"]["queue_position"] is None
    assert record["result"]["metrics"]["min_ttc_s"] is None
    assert record["result"]["metrics"]["collision_count"] == 0
    assert report["interpretation"]["mode_label"] == "Synthetic test execution"
    assert report["interpretation"]["coverage"] == "complete"
    assert report["telemetry"] is None and report["telemetry_included"] is False
    assert record["created_at"] and record["updated_at"] and report["generated_at"]


def test_opt_in_finalized_trace_round_trips_exactly(persistence):
    output = saved(persistence)
    exported = export_report(persistence, output.result.simulation.id, include_telemetry=True)
    report = json.loads(exported.content)
    assert report["telemetry"] == [frame.model_dump(mode="json") for frame in output.telemetry]
    assert report["telemetry_included"] is True
    assert exported.media_type == "application/json"
    assert exported.filename == f"poltergeist-{output.result.simulation.id}.json"


def test_report_includes_all_reproduction_outcomes_as_stored_observations(persistence):
    output = saved(persistence, collision=True)
    expected = []
    for outcome in (True, False, None):
        failure = FailureReport(simulation_id=output.result.simulation.id, scenario=output.result.simulation.scenario,
                                kind="collision", summary="Observed contact", severity=100.0, reproduced=outcome)
        expected.append(persistence.save_failure(failure).model_dump(mode="json"))
    report = build_report(persistence, output.result.simulation.id)
    assert sorted(report["failures"], key=lambda item: item["id"]) == sorted(expected, key=lambda item: item["id"])


def test_export_reads_every_failure_page_without_silent_limit(persistence, monkeypatch):
    output = saved(persistence)
    calls = []
    class Report:
        def __init__(self, index):
            self.index = index
        def model_dump(self, **kwargs):
            return {"index": self.index}
    def pages(self, **kwargs):
        calls.append(kwargs)
        return [Report(index) for index in range(kwargs["offset"], min(1003, kwargs["offset"] + kwargs["limit"]))]
    monkeypatch.setattr("backend.services.report_service.FailureRepository.list", pages)
    report = build_report(persistence, output.result.simulation.id)
    assert len(report["failures"]) == 1003
    assert [call["offset"] for call in calls] == [0, 1000]


def test_cancelled_unobserved_run_remains_unscored_unknown_and_job_can_be_missing(persistence):
    output = saved(persistence, cancelled=True, job=False)
    report = build_report(persistence, output.result.simulation.id, include_telemetry=True)
    assert report["record"]["job"] is None
    assert report["record"]["simulation"]["status"] == "cancelled"
    assert report["record"]["result"]["metrics"]["severity"] is None
    assert report["interpretation"]["coverage"] == "unobserved"
    assert report["interpretation"]["scored"] is False
    assert report["telemetry"] == []


def test_legacy_partial_carla_record_does_not_gain_runtime_or_settings(persistence):
    queued = persistence.create_simulation(scene(), mode="carla")
    started = queued.simulation.created_at + timedelta(seconds=1)
    simulation = queued.simulation.model_copy(update={"status": SimulationStatus.FAILED,
        "started_at": started, "finished_at": started + timedelta(seconds=1), "error": "Interrupted"})
    result = SimulationResult(simulation=simulation, metrics=SafetyMetrics(), telemetry_frames=0, measured_duration_s=0.0)
    with persistence.database.session() as session:
        SimulationRepository(session).complete(result, expected_revision=1)
    report = build_report(persistence, simulation.id)
    assert report["record"]["result"]["runtime"] is None
    assert report["record"]["result"]["metrics"]["evaluation_settings"] is None
    assert report["interpretation"]["mode_label"] == "CARLA execution"
    assert report["record"]["result"]["metrics"]["collision_count"] is None


@pytest.mark.parametrize("hostile", ["=WEBSERVICE(\"https://invalid\")", "+1+1", "-1+1", "@SUM(1,2)", "\t=1+1", "<script>alert('x')</script>&\" é"])
def test_csv_json_encoded_text_cannot_be_a_spreadsheet_formula_and_is_lossless(persistence, hostile):
    output = saved(persistence, name=hostile)
    exported = export_report(persistence, output.result.simulation.id, format="csv")
    rows = list(csv.DictReader(StringIO(exported.content.decode("utf-8-sig"))))
    row = next(row for row in rows if row["json_pointer"] == "/record/simulation/scenario/name")
    assert row["value_json"].startswith('"')
    assert json.loads(row["value_json"]) == hostile.strip()
    null_row = next(row for row in rows if row["json_pointer"] == "/record/result/metrics/min_ttc_s")
    assert null_row["value_json"] == "null" and null_row["unit"] == "s"
    zero = next(row for row in rows if row["json_pointer"] == "/record/result/metrics/collision_count")
    assert json.loads(zero["value_json"]) == 0
    assert next(row for row in rows if row["json_pointer"] == "/record/job/options/max_ticks")["value_json"] == "34"
    assert next(row for row in rows if row["json_pointer"] == "/record/result/metrics/coverage/total_frames")["unit"] == "frames"


def test_html_escapes_recipe_and_failure_text_preserving_full_snapshot(persistence):
    hostile = "<script>alert('x')</script><img src=x onerror=alert(1)>&"
    output = saved(persistence, collision=True, name=hostile)
    persistence.save_failure(FailureReport(simulation_id=output.result.simulation.id, scenario=output.result.simulation.scenario,
        kind="collision", summary=hostile, severity=100.0))
    text = export_report(persistence, output.result.simulation.id, format="html").content.decode()
    assert "<script>" not in text and "<img src=x" not in text
    assert "&lt;script&gt;" in text
    assert "Synthetic test execution" in text and "Complete export snapshot" in text
    assert "Unknown / no relevant interaction" in text
    assert "max_ticks" in text and "speeding_tolerance_mps" in text


@pytest.mark.parametrize("format,media_type", [("json", "application/json"), ("csv", "text/csv"), ("html", "text/html")])
def test_api_download_is_owned_attachment_without_worker_start(api, persistence, format, media_type):
    client, runtime = api
    output = saved(persistence)
    response = client.get(f"/reports/simulations/{output.result.simulation.id}", params={"format": format})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(media_type)
    assert response.headers["content-disposition"] == f'attachment; filename="poltergeist-{output.result.simulation.id}.{format}"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert runtime._worker is None and runtime._optimizer is None


def test_api_distinguishes_missing_invalid_and_unfinished_runs(api, persistence):
    client, _ = api
    assert client.get(f"/reports/simulations/{uuid4()}").status_code == 404
    assert client.get("/reports/simulations/invalid-id").status_code == 422
    queued = persistence.create_simulation(scene(), mode="test")
    response = client.get(f"/reports/simulations/{queued.simulation.id}")
    assert response.status_code == 409 and "not finalized" in response.json()["detail"]


@pytest.mark.parametrize("query", ["format=pdf", "format=html&include_telemetry=true", "format=csv&include_telemetry=true", "include_telemetry=bad"])
def test_api_rejects_invalid_export_options(api, persistence, query):
    client, _ = api
    output = saved(persistence)
    assert client.get(f"/reports/simulations/{output.result.simulation.id}?{query}").status_code == 422


def test_api_only_reads_telemetry_when_requested_and_reports_missing_file(api, persistence):
    client, _ = api
    output = saved(persistence)
    path = persistence.artifacts.validate_reference(output.result.artifacts[0], simulation_id=output.result.simulation.id)
    path.unlink()
    route = f"/reports/simulations/{output.result.simulation.id}"
    assert client.get(route).status_code == 200
    response = client.get(route, params={"include_telemetry": True})
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]
    assert str(path) not in response.text


def test_service_rejects_nonterminal_and_wrong_export_options_before_artifact_access(persistence):
    queued = persistence.create_simulation(scene(), mode="test")
    with pytest.raises(ConflictError, match="not finalized"):
        build_report(persistence, queued.simulation.id)
    with pytest.raises(ValueError, match="format"):
        export_report(persistence, queued.simulation.id, format="pdf")
    with pytest.raises(ValueError, match="only for JSON"):
        export_report(persistence, queued.simulation.id, format="html", include_telemetry=True)
