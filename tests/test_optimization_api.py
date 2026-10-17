"""HTTP experiments execute through the worker and retain a durable trial journal."""

from threading import Event
from time import monotonic
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.main import create_app
from scenarios import get_parameter_specs
from shared.models import CollisionEvent
from simulator import SimulationRunner
from simulator.test_adapter import TestSimulationAdapter


class CollisionAdapter(TestSimulationAdapter):
    def open(self, recipe):
        frame = super().open(recipe)
        frame.collisions = [CollisionEvent(other_actor_id="world:barrier")]
        return frame


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'runs.db').as_posix()}",
                    storage_dir=tmp_path / "artifacts", simulation_shutdown_timeout_s=3,
                    simulation_progress_interval_s=60)


def app_for(settings, adapter=CollisionAdapter):
    return create_app(settings, runner_factory=lambda **kwargs: SimulationRunner(adapter(), **kwargs))


@pytest.fixture
def client(settings):
    with TestClient(app_for(settings)) as client:
        yield client


def search(**changes):
    return {"kind": "search", "template": "sudden_braking", "mode": "test", "trials": 2,
            "seed": 71, "failure_kind": "collision",
            "parameters": {name: spec.default for name, spec in get_parameter_specs("sudden_braking").items()},
            **changes}


def wait_for(client, url, predicate):
    deadline = monotonic() + 30
    while monotonic() < deadline:
        response = client.get(url)
        assert response.status_code == 200, response.text
        body = response.json()
        if predicate(body):
            return body
        Event().wait(0.02)
    raise AssertionError(f"API operation did not finish: {url}")


def completed(client, operation_id):
    view = wait_for(client, f"/optimizations/{operation_id}",
                    lambda body: body["operation"]["status"] in {"completed", "failed", "cancelled", "interrupted"})
    assert view["operation"]["status"] == "completed", view
    return view


def source_failure(client):
    recipe = {"name": "api-reduction", "seed": 817, "duration_s": 0.2, "actors": [
        {"id": "ego", "type": "ego_vehicle", "speed_mps": 2, "spawn_point_index": 0},
        {"id": "extra", "type": "vehicle", "speed_mps": 1, "spawn_point_index": 1},
    ]}
    launched = client.post("/simulations", json={"scenario": recipe, "mode": "test",
        "options": {"max_ticks": 20}, "evaluation_settings": {"speeding_tolerance_mps": 0.7}})
    assert launched.status_code == 202, launched.text
    run = launched.json()
    simulation_id = run["simulation"]["id"]
    result = wait_for(client, f"/simulations/{simulation_id}/progress",
                      lambda body: body["status"] in {"completed", "failed"})
    assert result["status"] == "completed"
    result = client.get(f"/simulations/{simulation_id}/result").json()
    saved = client.post("/failures", json={"simulation_id": simulation_id,
        "scenario": run["simulation"]["scenario"], "kind": "collision",
        "severity": result["metrics"]["severity"], "summary": "Observed test contact"})
    assert saved.status_code == 201, saved.text
    return saved.json(), run


def test_search_returns_actual_runs_and_positive_failure_evidence(client):
    response = client.post("/optimizations", json=search())
    assert response.status_code == 202, response.text
    operation_id = response.json()["id"]
    view = completed(client, operation_id)
    operation, trials = view["operation"], view["trials"]
    assert operation["engine_version"] == "random-v1"
    assert operation["outcome"]["trials_used"] == len(trials) == 2
    assert operation["outcome"]["unknowns"] == 0
    assert [item["number"] for item in trials] == [0, 1]
    assert [item["scenario"]["seed"] for item in trials] == [71, 72]
    for trial in trials:
        assert trial["assessed"] and trial["score"] == 100 and trial["matches"] is True
        result = client.get(f"/simulations/{trial['simulation_id']}/result").json()
        assert result["simulation"]["scenario"] == trial["scenario"]
        assert result["simulation"]["mode"] == result["runtime"]["adapter"] == "test"
        assert result["telemetry_frames"] == 601
        failure = client.get(f"/failures/{trial['failure_id']}").json()
        assert failure["simulation_id"] == trial["simulation_id"]
        assert failure["severity"] == result["metrics"]["severity"]
    assert client.get("/optimizations?limit=1").json() == [operation]
    assert client.get("/optimizations/queue").json()["accepting"]
    assert client.post(f"/optimizations/{operation_id}/resume", json={"expected_revision": operation["revision"]}).status_code == 409
    assert client.post(f"/optimizations/{operation_id}/cancel").json() == operation


def test_reproduction_minimization_and_reopened_api_preserve_frozen_source(settings):
    with TestClient(app_for(settings)) as client:
        failure, source = source_failure(client)
        response = client.post("/optimizations", json={"kind": "reproduce", "failure_id": failure["id"], "repeats": 3})
        assert response.status_code == 202, response.text
        reproduced = completed(client, response.json()["id"])
        assert reproduced["operation"]["outcome"]["reproduced"] is True
        assert len(reproduced["trials"]) == 3
        for trial in reproduced["trials"]:
            assert trial["scenario"] == failure["scenario"]
            child = client.get(f"/simulations/{trial['simulation_id']}").json()
            assert child["job"]["options"] == source["job"]["options"]
            assert child["job"]["evaluation_settings"] == source["job"]["evaluation_settings"]
        assert client.get(f"/failures/{failure['id']}").json()["reproduced"] is True
        response = client.post("/optimizations", json={"kind": "minimize", "failure_id": failure["id"],
                                                       "max_trials": 3, "repeats": 1})
        assert response.status_code == 202, response.text
        minimized = completed(client, response.json()["id"])
        outcome = minimized["operation"]["outcome"]
        assert outcome["trials_used"] == len(minimized["trials"]) == 3
        assert outcome["reproduced"] is True
        assert len(outcome["best_scenario"]["actors"]) == 1
        assert minimized["trials"][0]["role"] == "minimize_baseline"
        assert all(item["matches"] is True for item in minimized["trials"])
    with TestClient(app_for(settings)) as client:
        for expected in (reproduced, minimized):
            assert client.get(f"/optimizations/{expected['operation']['id']}").json() == expected
        assert len(client.get("/optimizations").json()) == 2
        assert client.get(f"/failures/{failure['id']}").json()["reproduced"] is True


def test_failed_trials_keep_unknown_objectives_and_no_failure_records(settings):
    class Unavailable(TestSimulationAdapter):
        def open(self, recipe):
            raise RuntimeError("injected unavailable world")

    with TestClient(app_for(settings, Unavailable)) as client:
        response = client.post("/optimizations", json=search())
        assert response.status_code == 202, response.text
        view = completed(client, response.json()["id"])
        assert view["operation"]["outcome"]["unknowns"] == 2
        assert view["operation"]["outcome"]["best_simulation_id"] is None
        assert all(item["assessed"] and item["score"] is None and item["matches"] is None for item in view["trials"])
        assert client.get("/failures").json() == []


def test_default_synthetic_runner_search_and_reproduction_use_measured_near_miss(settings):
    # Exercise the production test-mode runner without injected sensor events.
    with TestClient(create_app(settings)) as client:
        response = client.post("/optimizations", json=search(trials=1, failure_kind="near_miss"))
        assert response.status_code == 202, response.text
        view = completed(client, response.json()["id"])
        trial = view["trials"][0]
        assert trial["matches"] is True and 0 < trial["score"] < 100
        result = client.get(f"/simulations/{trial['simulation_id']}/result").json()
        assert result["telemetry_frames"] == 601
        assert result["metrics"]["collision_count"] == 0
        assert result["metrics"]["min_ttc_s"] < result["metrics"]["evaluation_settings"]["ttc_warning_s"]
        response = client.post("/optimizations", json={"kind": "reproduce", "failure_id": trial["failure_id"], "repeats": 2})
        assert response.status_code == 202, response.text
        rerun = completed(client, response.json()["id"])
        assert rerun["operation"]["outcome"]["reproduced"] is True
        assert all(item["score"] == trial["score"] and item["scenario"] == trial["scenario"] for item in rerun["trials"])
        assert client.get(f"/failures/{trial['failure_id']}").json()["reproduced"] is True


@pytest.mark.parametrize("body", [
    {}, {"template": "sudden_braking"}, search(trials=True), search(trials=1001),
    search(strategy="unknown"), search(mode="invalid"), search(seed=-1), search(agent="custom"),
    search(options={"camera": True}), search(parameters={"unknown": 2}),
    search(ranges={"ego_speed_mps": [30, 10]}), search(minimum_severity=101),
    {"kind": "reproduce", "failure_id": str(uuid4()), "repeats": 2, "required_successes": 3},
    {"kind": "reproduce", "failure_id": str(uuid4()), "mode": "test"},
    {"kind": "minimize", "failure_id": str(uuid4()), "repeats": 3, "max_trials": 2},
])
def test_invalid_requests_do_not_create_operations(client, body):
    response = client.post("/optimizations", json=body)
    assert response.status_code == 422, response.text
    assert client.get("/optimizations").json() == []
    assert client.get("/simulations").json() == []


@pytest.mark.parametrize("query", ["limit=0", "limit=1001", "offset=-1"])
def test_invalid_pagination_is_rejected(client, query):
    assert client.get(f"/optimizations?{query}").status_code == 422


def test_missing_operations_and_source_failures_return_404(client):
    absent = uuid4()
    assert client.get(f"/optimizations/{absent}").status_code == 404
    assert client.post(f"/optimizations/{absent}/cancel").status_code == 404
    assert client.post(f"/optimizations/{absent}/resume", json={"expected_revision": 1}).status_code == 404
    for kind in ("reproduce", "minimize"):
        assert client.post("/optimizations", json={"kind": kind, "failure_id": str(absent)}).status_code == 404
    assert client.get("/optimizations/not-a-uuid").status_code == 422


def test_missing_optional_optuna_is_a_service_error_and_random_still_works(client, monkeypatch):
    from optimization import bayesian_search

    original = bayesian_search.importlib.import_module

    def without_optuna(name, *args, **kwargs):
        if name == "optuna":
            raise ImportError("optional dependency missing")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(bayesian_search.importlib, "import_module", without_optuna)
    response = client.post("/optimizations", json=search(strategy="optuna"))
    assert response.status_code == 503
    assert "optional optimization dependencies" in response.json()["detail"]
    assert client.get("/optimizations").json() == []
    response = client.post("/optimizations", json=search(trials=1, options={"max_ticks": 1}))
    assert response.status_code == 202, response.text
    assert completed(client, response.json()["id"])["operation"]["outcome"]["trials_used"] == 1


def test_openapi_includes_discriminated_optimization_contract_without_creating_storage(settings):
    app = app_for(settings)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        schema = client.get("/openapi.json").json()
        request = schema["paths"]["/optimizations"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert request["discriminator"]["propertyName"] == "kind"
        assert len(request["oneOf"]) == 3
        assert "/optimizations/{operation_id}/resume" in schema["paths"]
    assert not settings.storage_dir.exists()
    assert app.state.runtime._optimizer is None
