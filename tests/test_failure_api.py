"""Failure routes validate saved evidence and preserve nullable reproduction."""

from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.database import Database
from backend.dependencies import get_persistence
from backend.main import create_app
from backend.repositories import SimulationRepository
from backend.services.persistence_service import PersistenceService
from backend.storage import ArtifactStore
from scenarios import validate_scenario
from shared.models import FailureReport, SafetyMetrics, Simulation, SimulationResult, SimulationStatus


def scene():
    return validate_scenario({"name": "failure-api", "duration_s": 0.1,
                              "actors": [{"type": "ego_vehicle"}]})


def saved_run(service, **metrics):
    queued = Simulation(scenario=scene(), mode="test")
    started = queued.created_at + timedelta(seconds=1)
    finished = queued.model_copy(update={"status": SimulationStatus.COMPLETED,
        "started_at": started, "finished_at": started + timedelta(seconds=1), "progress": 1.0})
    result = SimulationResult(simulation=finished, metrics=SafetyMetrics(**metrics),
                              telemetry_frames=0, measured_duration_s=0.0)
    with service.database.session() as session:
        repository = SimulationRepository(session)
        repository.create(queued)
        repository.complete(result, expected_revision=1)
    return result


@pytest.fixture
def api(tmp_path):
    service = PersistenceService(Database(f"sqlite:///{(tmp_path / 'results.db').as_posix()}"),
                                 ArtifactStore(tmp_path / "artifacts"))
    service.initialize()
    app = create_app(Settings(_env_file=None))
    app.dependency_overrides[get_persistence] = lambda: service
    with TestClient(app) as client:
        yield client, service
    service.database.dispose()


def report_for(result, **overrides):
    return FailureReport(simulation_id=result.simulation.id, scenario=result.simulation.scenario,
                         kind="collision", severity=result.metrics.severity,
                         summary="Observed collision", **overrides).model_dump(mode="json")


def test_create_read_and_list_round_trip_through_sqlite(api):
    client, service = api
    result = saved_run(service, collision_count=1, severity=100.0)
    body = report_for(result)
    response = client.post("/failures", json=body)
    assert response.status_code == 201
    assert response.json() == body
    assert response.json()["reproduced"] is None
    assert client.get(f"/failures/{body['id']}").json() == body
    assert client.get("/failures").json() == [body]
    assert client.get("/failures", params={"simulation_id": str(result.simulation.id)}).json() == [body]
    assert client.get("/failures", params={"simulation_id": str(uuid4())}).json() == []


@pytest.mark.parametrize("outcome", [True, False, None])
def test_explicit_reproduction_preserves_three_distinct_outcomes(api, outcome):
    client, service = api
    result = saved_run(service, collision_count=1, severity=100.0)
    body = report_for(result)
    client.post("/failures", json=body)
    response = client.patch(f"/failures/{body['id']}/reproduction", json={"reproduced": outcome})
    assert response.status_code == 200
    assert response.json()["reproduced"] is outcome
    assert client.get(f"/failures/{body['id']}").json()["reproduced"] is outcome


def test_reproduction_filters_and_pagination_do_not_confuse_false_with_unknown(api):
    client, service = api
    result = saved_run(service, collision_count=1, severity=100.0)
    reports = [report_for(result, reproduced=value) for value in (True, False, None)]
    for body in reports:
        assert client.post("/failures", json=body).status_code == 201
    for label, expected in zip(("true", "false", "unknown"), reports):
        assert client.get("/failures", params={"reproduced": label}).json() == [expected]
    ordered = client.get("/failures", params={"reproduced": "all"}).json()
    assert len(ordered) == 3
    assert client.get("/failures", params={"limit": 1, "offset": 1}).json() == ordered[1:2]


@pytest.mark.parametrize("query", ["limit=0", "limit=1001", "offset=-1", "simulation_id=bad", "reproduced=null", "reproduced=0"])
def test_invalid_filters_are_wire_validation_errors(api, query):
    client, _ = api
    assert client.get(f"/failures?{query}").status_code == 422


@pytest.mark.parametrize("body", [{}, {"reproduced": 0}, {"reproduced": "false"}, {"reproduced": False, "extra": 1}])
def test_reproduction_requires_an_explicit_strict_nullable_boolean(api, body):
    client, service = api
    result = saved_run(service, collision_count=1, severity=100.0)
    report = report_for(result)
    client.post("/failures", json=report)
    assert client.patch(f"/failures/{report['id']}/reproduction", json=body).status_code == 422
    assert client.get(f"/failures/{report['id']}").json()["reproduced"] is None


def test_missing_duplicate_and_bad_identity_use_distinct_errors(api):
    client, service = api
    absent = str(uuid4())
    assert client.get(f"/failures/{absent}").status_code == 404
    assert client.patch(f"/failures/{absent}/reproduction", json={"reproduced": True}).status_code == 404
    assert client.get("/failures/not-a-uuid").status_code == 422
    result = saved_run(service, collision_count=1, severity=100.0)
    body = report_for(result)
    assert client.post("/failures", json=body).status_code == 201
    assert client.post("/failures", json=body).status_code == 409
    body["id"], body["simulation_id"] = str(uuid4()), absent
    assert client.post("/failures", json=body).status_code == 404


@pytest.mark.parametrize("change", [
    {"kind": "traffic_violation"}, {"severity": 80.0},
    {"scenario": scene().model_dump(mode="json") | {"name": "different-recipe"}},
])
def test_positive_evidence_and_immutable_run_snapshot_are_required(api, change):
    client, service = api
    result = saved_run(service, collision_count=1, traffic_violation_count=0, severity=100.0)
    assert client.post("/failures", json=report_for(result) | change).status_code == 422
    assert client.get("/failures").json() == []


def test_queued_or_unscored_runs_cannot_supply_failure_evidence(api):
    client, service = api
    queued = service.create_simulation(scene(), mode="test")
    body = {"simulation_id": str(queued.simulation.id), "scenario": scene().model_dump(mode="json"),
            "kind": "collision", "severity": 100.0, "summary": "Unproven collision"}
    assert client.post("/failures", json=body).status_code == 422
    unscored = saved_run(service)
    body["simulation_id"] = str(unscored.simulation.id)
    assert client.post("/failures", json=body).status_code == 422
