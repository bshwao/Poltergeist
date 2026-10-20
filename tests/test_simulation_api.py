"""HTTP launch-to-result flow through a real worker, SQLite and artifact store."""

import json
from threading import Event
from time import monotonic
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.main import create_app
from shared.models import Simulation
from simulator import SimulationRunner
from simulator.protocols import SensorSample
from simulator.test_adapter import TestSimulationAdapter


def scene():
    return {
        "name": "api-test", "duration_s": 0.2,
        "actors": [{"type": "ego_vehicle", "speed_mps": 2, "spawn_point_index": 0}],
    }


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'database' / 'runs.db').as_posix()}",
                    storage_dir=tmp_path / "artifacts", simulation_shutdown_timeout_s=2,
                    simulation_progress_interval_s=0.01)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def wait_for(client, simulation_id, predicate=lambda item: item["status"] in ("completed", "failed", "cancelled")):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        response = client.get(f"/simulations/{simulation_id}/progress")
        assert response.status_code == 200, response.text
        item = response.json()
        if predicate(item):
            return item
        Event().wait(0.01)
    raise AssertionError("API worker did not reach the expected state")


def test_launch_from_saved_recipe_persists_results_and_downloadable_telemetry(client):
    saved = client.post("/scenarios", json=scene()).json()
    response = client.post("/simulations", json={"scenario_id": saved["id"], "mode": "test", "evaluation_settings": {"speeding_tolerance_mps": 0.7}})
    assert response.status_code == 202, response.text
    body = response.json()
    simulation_id = body["simulation"]["id"]
    assert body["scenario_id"] == saved["id"]
    assert body["job"]["evaluation_settings"]["speeding_tolerance_mps"] == 0.7
    assert wait_for(client, simulation_id)["status"] == "completed"
    result = client.get(f"/simulations/{simulation_id}/result").json()
    assert result["simulation"]["mode"] == result["runtime"]["adapter"] == "test"
    assert result["telemetry_frames"] == 5
    assert result["metrics"]["evaluation_settings"]["speeding_tolerance_mps"] == 0.7
    page = client.get(f"/simulations/{simulation_id}/telemetry?offset=1&limit=2")
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 5
    assert [frame["frame"] for frame in page.json()["frames"]] == [1, 2]
    artifact = client.get(f"/simulations/{simulation_id}/artifacts/0")
    assert artifact.status_code == 200
    assert "application/x-ndjson" in artifact.headers["content-type"]
    assert len([json.loads(line) for line in artifact.text.splitlines()]) == 5
    assert client.get(f"/simulations/{simulation_id}/artifacts/999").status_code == 404
    assert client.get(f"/simulations/{simulation_id}/artifacts/-1").status_code == 422
    assert client.get(f"/simulations/{simulation_id}/artifacts/0/samples/0").status_code == 404
    listing = client.get("/simulations?status=completed&mode=test&limit=1").json()
    assert listing[0]["simulation"]["id"] == simulation_id
    assert client.get("/simulations/queue").json()["accepting"]
    assert client.post(f"/simulations/{simulation_id}/cancel").status_code == 202
    assert client.get(f"/simulations/{simulation_id}/result").json() == result


def test_inline_recipe_normalizes_ids_before_event_references(client):
    recipe = scene() | {"events": [{"action": "set_speed", "actor_id": "ego", "trigger_time_s": 0, "speed_mps": 1}]}
    response = client.post("/simulations", json={"scenario": recipe, "mode": "test"})
    assert response.status_code == 202, response.text
    assert response.json()["simulation"]["scenario"]["actors"][0]["id"] == "ego"
    assert wait_for(client, response.json()["simulation"]["id"])["status"] == "completed"


@pytest.mark.parametrize("quality", [None, "Low", "Epic"])
def test_graphics_startup_preference_is_preserved_in_the_queued_job(client, quality):
    # Synthetic execution verifies persistence without opening a native window.
    response = client.post("/simulations", json={
        "scenario": scene(), "mode": "test", "options": {"carla_quality": quality},
    })
    assert response.status_code == 202, response.text
    record = response.json()
    assert record["job"]["options"]["carla_quality"] == quality
    simulation_id = record["simulation"]["id"]
    assert wait_for(client, simulation_id)["status"] == "completed"
    assert client.get(f"/simulations/{simulation_id}").json()["job"]["options"]["carla_quality"] == quality


def test_service_unavailable_acknowledgement_can_follow_a_durable_launch(client, monkeypatch):
    worker = client.app.state.runtime.worker()
    def fail_receipt(_):
        raise OSError("Injected receipt lookup failure after enqueue")
    with monkeypatch.context() as patch:
        patch.setattr(worker, "job_info", fail_receipt)
        response = client.post("/simulations", json={"scenario": scene(), "mode": "test"})
    assert response.status_code == 503
    history = client.get("/simulations").json()
    assert len(history) == 1
    assert wait_for(client, history[0]["simulation"]["id"])["status"] == "completed"


def test_generated_duration_survives_save_and_controls_experiment_runtime(client):
    generated = client.post("/scenarios/generate", json={
        "template": "sudden_braking", "duration_s": 1.0, "parameters": {"brake_delay_s": 0.5},
    })
    assert generated.status_code == 200, generated.text
    saved = client.post("/scenarios", json=generated.json())
    assert saved.status_code == 201, saved.text
    response = client.post("/simulations", json={"scenario_id": saved.json()["id"], "mode": "test"})
    assert response.status_code == 202, response.text
    simulation_id = response.json()["simulation"]["id"]
    assert wait_for(client, simulation_id)["status"] == "completed"

    result = client.get(f"/simulations/{simulation_id}/result").json()
    assert result["simulation"]["scenario"]["duration_s"] == 1.0
    assert result["measured_duration_s"] == pytest.approx(1.0)
    assert result["telemetry_frames"] == 21


@pytest.mark.parametrize("change", [
    {}, {"scenario": scene(), "scenario_id": str(uuid4())}, {"scenario_id": "bad-id"},
    {"scenario": scene(), "mode": "unknown"}, {"scenario": scene(), "agent": "custom"},
    {"scenario": scene(), "mode": "test", "options": {"camera": True}},
    {"scenario": scene(), "options": {"allow_map_reload": "true"}},
    {"scenario": scene(), "options": {"max_ticks": True}},
    {"scenario": scene(), "options": {"carla_quality": "Medium"}},
    {"scenario": scene(), "options": {"carla_quality": True}},
    {"scenario": scene(), "evaluation_settings": {"ttc_warning_s": -1}},
])
def test_invalid_launch_contract_is_rejected(client, change):
    response = client.post("/simulations", json=change)
    assert response.status_code == 422, response.text
    assert client.get("/simulations").json() == []


@pytest.mark.parametrize("query", ["limit=0", "limit=1001", "offset=-1", "mode=invalid", "status=imaginary"])
def test_invalid_list_filters_are_rejected(client, query):
    assert client.get(f"/simulations?{query}").status_code == 422


def test_missing_and_unmanaged_runs_are_distinct(client):
    missing = uuid4()
    for suffix in ("", "/progress", "/result", "/telemetry", "/artifacts/0"):
        assert client.get(f"/simulations/{missing}{suffix}").status_code == 404
    assert client.post(f"/simulations/{missing}/cancel").status_code == 404
    assert client.post("/simulations", json={"scenario_id": str(missing), "mode": "test"}).status_code == 404
    persistence = client.app.state.runtime.persistence()
    manual = persistence.create_simulation(scene(), mode="test")
    simulation_id = manual.simulation.id
    assert client.get(f"/simulations/{simulation_id}").json()["job"] is None
    assert client.get(f"/simulations/{simulation_id}/result").status_code == 409
    assert client.post(f"/simulations/{simulation_id}/cancel").status_code == 404
    assert client.get(f"/simulations/{simulation_id}").json()["simulation"]["status"] == "queued"


def test_queue_capacity_and_queued_cancellation_are_available_during_native_open(settings):
    entered, release = Event(), Event()

    class Blocking(TestSimulationAdapter):
        def open(self, recipe):
            entered.set()
            assert release.wait(3), "test native call was not released"
            return super().open(recipe)

    app = create_app(settings.model_copy(update={"simulation_queue_size": 1}), runner_factory=lambda **kwargs: SimulationRunner(Blocking(), **kwargs))
    try:
        with TestClient(app) as client:
            first = client.post("/simulations", json={"scenario": scene(), "mode": "test"}).json()["simulation"]["id"]
            assert entered.wait(3)
            second = client.post("/simulations", json={"scenario": scene(), "mode": "test"})
            assert second.status_code == 202, second.text
            second_id = second.json()["simulation"]["id"]
            assert second.json()["job"]["queue_position"] == 1
            assert client.post("/simulations", json={"scenario": scene(), "mode": "test"}).status_code == 429
            cancelled = client.post(f"/simulations/{second_id}/cancel")
            assert cancelled.status_code == 202
            assert cancelled.json()["simulation"]["status"] == "cancelled"
            assert client.get(f"/simulations/{second_id}/result").json()["telemetry_frames"] == 0
            assert client.post(f"/simulations/{first}/cancel").json()["job"]["cancel_requested"]
            release.set()
            assert wait_for(client, first)["status"] == "cancelled"
    finally:
        release.set()


def test_persisted_running_progress_and_active_cancel(settings):
    entered, release = Event(), Event()

    class Blocking(TestSimulationAdapter):
        def step(self, control, commands):
            entered.set()
            assert release.wait(3)
            return super().step(control, commands)

    try:
        with TestClient(create_app(settings, runner_factory=lambda **kwargs: SimulationRunner(Blocking(), **kwargs))) as client:
            simulation_id = client.post("/simulations", json={"scenario": scene(), "mode": "test"}).json()["simulation"]["id"]
            assert entered.wait(3)
            progress = wait_for(client, simulation_id, lambda item: item["status"] == "running")
            assert progress["started_at"] is not None
            assert progress["revision"] >= 2
            response = client.post(f"/simulations/{simulation_id}/cancel")
            assert response.status_code == 202
            assert response.json()["job"]["cancel_requested"]
            release.set()
            assert wait_for(client, simulation_id)["status"] == "cancelled"
            result = client.get(f"/simulations/{simulation_id}/result").json()
            assert result["metrics"]["severity"] is None
            assert result["telemetry_frames"] > 0
    finally:
        release.set()


def test_carla_execution_failure_is_reported_without_synthetic_fallback(settings):
    class Unavailable(TestSimulationAdapter):
        mode = "carla"

        def open(self, recipe):
            raise RuntimeError("CARLA server unavailable")

    with TestClient(create_app(settings, runner_factory=lambda **kwargs: SimulationRunner(Unavailable(), **kwargs))) as client:
        response = client.post("/simulations", json={"scenario": scene()})
        assert response.status_code == 202
        simulation_id = response.json()["simulation"]["id"]
        assert wait_for(client, simulation_id)["status"] == "failed"
        result = client.get(f"/simulations/{simulation_id}/result").json()
        assert result["simulation"]["mode"] == "carla"
        assert "server unavailable" in result["simulation"]["error"]
        assert result["metrics"]["severity"] is None
        assert result["runtime"] is None
        assert result["telemetry_frames"] == 0


def test_raw_capture_manifest_and_sample_downloads_are_indexed_and_verified(settings):
    class Capturing(TestSimulationAdapter):
        mode = "carla"

        def __init__(self):
            super().__init__()
            self.last_sample = -1

        def sensor_samples(self):
            if not self._active or self._frame_index == self.last_sample:
                return ()
            self.last_sample = self._frame_index
            return (SensorSample("camera", self._frame_index, self._frame_index * 0.05, b"abcd", {
                "encoding": "bgra8", "width": 1, "height": 1,
            }),)

    with TestClient(create_app(settings, runner_factory=lambda **kwargs: SimulationRunner(Capturing(), **kwargs))) as client:
        response = client.post("/simulations", json={"scenario": scene(), "mode": "carla", "options": {"camera": True}})
        assert response.status_code == 202, response.text
        simulation_id = response.json()["simulation"]["id"]
        assert wait_for(client, simulation_id)["status"] == "completed"
        result = client.get(f"/simulations/{simulation_id}/result").json()
        index = next(i for i, ref in enumerate(result["artifacts"]) if ref["kind"] == "recording")
        manifest = client.get(f"/simulations/{simulation_id}/artifacts/{index}")
        assert manifest.status_code == 200
        assert manifest.json()["sample_count"] == 5
        sample = client.get(f"/simulations/{simulation_id}/artifacts/{index}/samples/0")
        assert sample.status_code == 200 and sample.content == b"abcd"
        assert client.get(f"/simulations/{simulation_id}/artifacts/{index}/samples/999").status_code == 404
        persistence = client.app.state.runtime.persistence()
        from shared.models import ArtifactReference
        ref = ArtifactReference.model_validate(result["artifacts"][index])
        path = persistence.artifacts.capture_sample_path(ref, 0)
        path.write_bytes(b"oops")
        response = client.get(f"/simulations/{simulation_id}/artifacts/{index}/samples/0")
        assert response.status_code == 503
        assert response.json()["detail"] == "Stored artifact is unavailable or invalid"


def test_default_runner_uses_app_specific_carla_connection(settings):
    from backend.dependencies import ApiRuntime
    from shared.models import EvaluationSettings
    from simulator import SimulationOptions

    runtime = ApiRuntime(settings.model_copy(update={"carla_host": "server.example", "carla_port": 2500, "carla_timeout": 1.5}))
    runner = runtime.runner_factory(options=SimulationOptions(), evaluation_settings=EvaluationSettings())
    assert runner.connection_settings.host == "server.example"
    assert runner.connection_settings.port == 2500
    assert runner.connection_settings.timeout == 1.5
    assert runtime.close()


def test_another_application_cannot_start_a_second_worker_for_the_database(settings):
    with TestClient(create_app(settings)) as first, TestClient(create_app(settings)) as second:
        assert first.get("/simulations/queue").status_code == 200
        assert second.get("/simulations/queue").status_code == 409
        assert second.get("/health").status_code == 200
        assert second.get("/scenarios").status_code == 200


def test_health_and_openapi_do_not_initialize_storage(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        schema = client.get("/openapi.json")
        assert schema.status_code == 200
        assert "/simulations/{simulation_id}/cancel" in schema.json()["paths"]
        assert not settings.storage_dir.exists()


def test_unavailable_server_persistence_is_503_while_health_stays_live(settings):
    unavailable = settings.model_copy(update={"database_url": "postgresql://unavailable.invalid/database"})
    with TestClient(create_app(unavailable)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/scenarios/templates").status_code == 200
        response = client.get("/scenarios")
        assert response.status_code == 503
        assert response.json()["detail"] == "Persistence initialization failed"
        assert not settings.storage_dir.exists()
