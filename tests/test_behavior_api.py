"""Analysis API reads validated history, never starts CARLA or a model."""

from hashlib import sha256
import json
from threading import Event
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from backend.database import Database
from backend.decision_storage import DecisionWriter
from backend.dependencies import get_persistence
from backend.main import create_app
from backend.config import Settings
from backend.services.persistence_service import PersistenceService
from backend.storage import ArtifactStore
from simulator import SimulationRunner
from simulator.test_adapter import TestSimulationAdapter

from test_registered_agent_execution import finished, launch, package, register, scene as registered_scene, settings


@pytest.fixture
def persistence(tmp_path):
    service = PersistenceService(Database(f"sqlite:///{(tmp_path / 'analysis.db').as_posix()}"), ArtifactStore(tmp_path / "artifacts"))
    service.initialize()
    yield service
    service.database.dispose()


@pytest.fixture
def api(persistence):
    def reject(**kwargs):
        raise AssertionError("Reading analysis must not create a simulation runner")
    app = create_app(Settings(_env_file=None), runner_factory=reject)
    app.dependency_overrides[get_persistence] = lambda: persistence
    with TestClient(app) as client:
        yield client, app.state.runtime


def recipe():
    return {"name": "saved behavior API example", "duration_s": .4,
        "actors": [{"id": "ego", "type": "ego_vehicle", "speed_mps": 2},
                   {"id": "npc", "type": "vehicle", "spawn": {"position": {"x": 80}}}],
        "events": [{"actor_id": "npc", "action": "brake", "trigger_time_s": .05, "deceleration_mps2": 4}]}


def save(persistence, *, legacy=False, cancel=False, failed=False):
    class Failing(TestSimulationAdapter):
        def step(self, *args):
            raise RuntimeError("recorded simulator error")
    stored = persistence.create_simulation(recipe(), mode="test")
    writer = None if legacy else DecisionWriter(persistence.artifacts, stored.simulation.id)
    cancellation = Event()
    if cancel:
        cancellation.set()
    runner = SimulationRunner(Failing() if failed else None)
    return persistence.persist_output(runner.run(stored.simulation, cancellation=cancellation,
        **({} if writer is None else {"on_observation": writer.observation, "on_decision": writer.decision,
                                     "on_agent_reset": writer.reset})), expected_revision=stored.revision, decisions=writer)


def get(client, run_id):
    response = client.get(f"/simulations/{run_id}/analysis")
    assert response.status_code == 200, response.text
    return response


def test_get_and_configured_post_are_deterministic_read_only_and_do_not_start_worker(api, persistence):
    client, runtime = api
    saved = save(persistence)
    run_id = saved.result.simulation.id
    before = persistence.get_simulation(run_id)
    files = {str(path): sha256(path.read_bytes()).hexdigest() for path in persistence.artifacts.root.rglob("*") if path.is_file()}
    first = get(client, run_id)
    second = get(client, run_id)
    default_post = client.post(f"/simulations/{run_id}/analysis", json={})
    assert first.content == second.content == default_post.content
    assert first.headers["cache-control"] == "no-store"
    body = first.json()
    assert body["simulation_id"] == str(run_id) and body["schema_version"] == "1.0"
    assert body["coverage"]["decision_coverage"] == "complete"
    assert body["coverage"]["applied_decisions"] == 8
    assert body["stored_safety_metrics"] == saved.result.metrics.model_dump(mode="json")
    assert body["events"][0]["anchor"]["source"] == "scenario_script"
    assert body["events"][0]["anchor"]["frame"] == 1
    changed = client.post(f"/simulations/{run_id}/analysis", json={"response_window_s": .1, "brake_threshold": .4})
    assert changed.status_code == 200, changed.text
    assert changed.json()["settings"]["brake_threshold"] == .4
    assert changed.json()["stored_safety_metrics"] == body["stored_safety_metrics"]
    assert persistence.get_simulation(run_id) == before
    assert files == {str(path): sha256(path.read_bytes()).hexdigest() for path in persistence.artifacts.root.rglob("*") if path.is_file()}
    assert runtime._worker is None and runtime._registry is None and runtime._optimizer is None


@pytest.mark.parametrize("changes", [
    {"brake_threshold": True}, {"brake_threshold": "0.5"}, {"response_window_s": 0}, {"response_window_s": 61},
    {"method_version": "9.9"}, {"max_response_events": 2001}, {"heading_change_deg": -1},
    {"unexpected": "reject"}, {"route_height_tolerance_m": "Infinity"},
])
def test_invalid_analysis_settings_return_422_without_mutating_result(api, persistence, changes):
    client, _ = api
    saved = save(persistence)
    run_id = saved.result.simulation.id
    before = persistence.get_simulation(run_id)
    response = client.post(f"/simulations/{run_id}/analysis", json=changes)
    assert response.status_code == 422, response.text
    assert persistence.get_simulation(run_id) == before


def test_unfinished_unknown_and_invalid_ids_have_explicit_status(api, persistence):
    client, runtime = api
    pending = persistence.create_simulation(recipe(), mode="test")
    assert client.get(f"/simulations/{pending.simulation.id}/analysis").status_code == 409
    assert client.get(f"/simulations/{uuid4()}/analysis").status_code == 404
    assert client.get("/simulations/not-a-uuid/analysis").status_code == 422
    assert runtime._worker is None


@pytest.mark.parametrize("kind", ["legacy", "cancelled", "failed"])
def test_old_and_partial_results_keep_measurements_without_inventing_missing_decisions(api, persistence, kind):
    client, _ = api
    saved = save(persistence, legacy=kind == "legacy", cancel=kind == "cancelled", failed=kind == "failed")
    body = get(client, saved.result.simulation.id).json()
    assert body["execution_status"] == saved.result.simulation.status
    assert body["coverage"]["telemetry_frames"] == saved.result.telemetry_frames
    if kind == "legacy":
        assert body["coverage"]["decision_coverage"] == "unavailable"
        assert body["braking"]["max_deceleration"]["value"] == 0
        assert body["events"][0]["responses"][0]["status"] == "unavailable"
    elif kind == "cancelled":
        assert body["coverage"]["decision_coverage"] == "empty"
        assert body["braking"]["max_deceleration"]["value"] is None
        assert body["events"][0]["anchor"]["frame"] is None
    else:
        assert body["coverage"]["decision_coverage"] == "partial"
        assert body["coverage"]["unresolved_inputs"] == 1


def test_corrupt_timeline_is_rejected_before_any_analysis_is_returned(api, persistence):
    client, runtime = api
    saved = save(persistence)
    ref = next(ref for ref in saved.result.artifacts if ref.kind == "decisions")
    path = persistence.artifacts.validate_reference(ref)
    lines = path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0]) | {"sha256": "0" * 64}
    path.write_text(json.dumps(header) + "\n" + "\n".join(lines[1:]) + "\n", encoding="utf-8", newline="\n")
    response = client.get(f"/simulations/{saved.result.simulation.id}/analysis")
    assert response.status_code == 422
    assert "checksum" in response.json()["detail"]
    assert runtime._worker is None


@pytest.mark.parametrize("sensors,fail", [(False, False), (True, False), (True, True)])
def test_registered_history_retains_identity_inputs_and_ground_truth_separation(settings, package, sensors, fail):
    directory, _ = package(sensors=sensors, configuration={"fail_step": fail})
    with TestClient(create_app(settings)) as client:
        registration = register(client, directory)
        data = registered_scene(sensors=sensors)
        data["events"] = [{"actor_id": "npc", "action": "brake", "trigger_time_s": 0, "deceleration_mps2": 4}]
        run_id = launch(client, registration["id"], recipe=data)["simulation"]["id"]
        # Observe the complete native startup/failure/cleanup lifecycle, whose
        # supported load deadline alone can exceed the old fifteen-second poll.
        saved = finished(client, run_id, timeout=60)
        assert saved["simulation"]["status"] == ("failed" if fail else "completed"), saved["simulation"]["error"]
        before = client.get("/simulations").json()
        # Read historical bytes without reloading or revalidating current code.
        (directory / "agent.py").write_text("raise RuntimeError('must never import for analysis')")
        body = get(client, run_id).json()
        assert body["agent_snapshot"] == saved["simulation"]["agent_snapshot"]
        assert body["agent_inputs"] == saved["result"]["agent_inputs"]
        assert body["stored_safety_metrics"] == saved["result"]["metrics"]
        assert body["coverage"]["decision_coverage"] == ("partial" if fail else "complete")
        if sensors:
            assert not body["agent_inputs"]["ground_truth_actor_inputs"]
            assert body["agent_inputs"]["source"] == "deterministic_fixture"
            assert "data_base64" not in json.dumps(body)
        if fail:
            assert body["coverage"]["unresolved_inputs"] == 1
            assert body["events"][0]["responses"][0]["status"] == "unavailable"
        assert client.get("/simulations").json() == before


def test_validated_sensor_byte_corruption_does_not_produce_an_analysis(settings, package):
    directory, _ = package(sensors=True)
    with TestClient(create_app(settings)) as client:
        registration = register(client, directory)
        run_id = launch(client, registration["id"], recipe=registered_scene(sensors=True))["simulation"]["id"]
        saved = finished(client, run_id)
        assert saved["simulation"]["status"] == "completed", saved["simulation"]["error"]
        persistence = client.app.state.runtime.persistence()
        result = persistence.get_simulation(UUID(run_id)).result
        recording = next(ref for ref in result.artifacts if ref.kind == "recording")
        sample = persistence.artifacts.capture_sample_path(recording, 0)
        sample.write_bytes(b"corrupt")
        response = client.get(f"/simulations/{run_id}/analysis")
        assert response.status_code == 503
        assert "unavailable or invalid" in response.json()["detail"]
