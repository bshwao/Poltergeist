"""Analytics retain mode boundaries, coverage nulls, and the full saved history."""

from datetime import timedelta

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


def scene():
    return validate_scenario({"name": "analytics-api", "duration_s": 0.1,
                              "actors": [{"type": "ego_vehicle"}]})


def save(service, mode="test", status="completed", **metrics):
    queued = Simulation(scenario=scene(), mode=mode)
    with service.database.session() as session:
        repository = SimulationRepository(session)
        repository.create(queued)
        if status == "queued":
            return queued
        started = queued.created_at + timedelta(seconds=1)
        simulation = queued.model_copy(update={"status": SimulationStatus(status),
            "started_at": started, "finished_at": None if status == "running" else started + timedelta(seconds=1),
            "progress": 1.0 if status == "completed" else 0.0,
            "error": "execution failed" if status == "failed" else None})
        if status == "running":
            repository.update(simulation, expected_revision=1)
            return simulation
        result = SimulationResult(simulation=simulation, metrics=SafetyMetrics(**metrics),
                                  telemetry_frames=0, measured_duration_s=0.0)
        repository.complete(result, expected_revision=1)
        return result


def groups(response):
    assert response.status_code == 200
    return {group["mode"]: group for group in response.json()["groups"]}


def test_empty_history_has_distinct_zero_groups_with_unknown_average(api):
    client, _ = api
    result = groups(client.get("/analytics"))
    assert set(result) == {"carla", "test"}
    for group in result.values():
        assert group["total_runs"] == group["result_count"] == group["scored_runs"] == 0
        assert group["mean_severity"] is None
        assert group["failure_kinds"] == {}
        assert group["status_counts"] == dict.fromkeys(("queued", "running", "completed", "failed", "cancelled"), 0)


def test_mixed_modes_do_not_blend_scores_or_count_unknowns_as_zero(api):
    client, service = api
    save(service, mode="test", severity=0.0, collision_count=0, traffic_violation_count=0)
    collision = save(service, mode="test", status="failed", severity=100.0,
                     collision_count=1, traffic_violation_count=None)
    save(service, mode="test", status="cancelled")
    save(service, mode="test", status="queued")
    save(service, mode="test", status="running")
    save(service, mode="carla", severity=20.0, collision_count=0, traffic_violation_count=2)
    save(service, mode="carla")
    service.save_failure(FailureReport(simulation_id=collision.simulation.id,
        scenario=collision.simulation.scenario, kind="collision", summary="Collision observed",
        severity=100.0, reproduced=None))
    result = groups(client.get("/analytics"))
    test = result["test"]
    assert test == {
        "mode": "test", "total_runs": 5,
        "status_counts": {"queued": 1, "running": 1, "completed": 1, "failed": 1, "cancelled": 1},
        "result_count": 3, "scored_runs": 2, "unscored_results": 1, "mean_severity": 50.0,
        "collision_runs": 1, "collision_observed_runs": 2,
        "traffic_violation_runs": 0, "traffic_observed_runs": 1,
        "failure_reports": 1, "failure_kinds": {"collision": 1},
    }
    real = result["carla"]
    assert real["total_runs"] == real["result_count"] == 2
    assert real["mean_severity"] == 20.0
    assert real["scored_runs"] == real["unscored_results"] == 1
    assert real["collision_runs"] == 0 and real["collision_observed_runs"] == 1
    assert real["traffic_violation_runs"] == real["traffic_observed_runs"] == 1
    assert real["failure_reports"] == 0
    assert groups(client.get("/analytics?mode=test")) == {"test": test}
    assert groups(client.get("/analytics?mode=carla")) == {"carla": real}


@pytest.mark.parametrize("mode", ["all", "synthetic", "CARLA", ""])
def test_unknown_mode_is_rejected(api, mode):
    client, _ = api
    assert client.get("/analytics", params={"mode": mode}).status_code == 422


def test_history_has_no_repository_page_limit(api):
    client, service = api
    recipe = scene()
    with service.database.session() as session:
        repository = SimulationRepository(session)
        for _ in range(1003):
            repository.create(Simulation(scenario=recipe, mode="test"))
    group = groups(client.get("/analytics?mode=test"))["test"]
    assert group["total_runs"] == group["status_counts"]["queued"] == 1003
    assert group["result_count"] == 0
    assert group["mean_severity"] is None
