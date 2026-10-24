"""Registered packages survive API queuing and control real external workers.

The adapter uses labelled synthetic fixtures. These checks start Python workers,
never CARLA, download models, or execute the reference neural checkpoint.
"""

from contextlib import contextmanager
from copy import deepcopy
import json
import os
import sys
from threading import Event, Thread
from time import monotonic
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import update

from backend.config import Settings
from backend.main import create_app
from backend.models import SimulationJobRecord
from backend.persistence_errors import PersistenceError
from backend.repositories import SimulationRepository
from backend.services.agent_registry_service import AgentRegistryService
from backend.services.persistence_service import PersistenceService
from scenarios import validate_scenario
from shared.models import AgentRegistration, CollisionEvent, Simulation, SimulationResult
from simulator import SimulationRunner
from simulator.agents.process import AgentProcessOptions, ProcessAgent
from simulator.errors import SimulationError
from simulator.test_adapter import TestSimulationAdapter


def scene(*, sensors=False):
    recipe = {
        "name": "registered-agent-execution", "duration_s": .1,
        "fixed_delta_seconds": .05,
        "actors": [
            {"id": "ego", "type": "ego_vehicle", "spawn": {}, "speed_mps": 2},
            {"id": "npc", "type": "vehicle", "spawn": {"position": {"x": 80}}},
        ],
    }
    if sensors:
        recipe["driving_task"] = {
            "destination": {"position": {"x": 20}}, "target_speed_mps": 3,
            "route": [{}, {"position": {"x": 20}}],
        }
    return recipe


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{(tmp_path / 'database' / 'runs.db').as_posix()}",
        storage_dir=tmp_path / "artifacts", simulation_shutdown_timeout_s=5,
        simulation_progress_interval_s=.01,
    )


@pytest.fixture
def package(tmp_path):
    directory = tmp_path / "registered-driver"
    directory.mkdir()
    log = tmp_path / "agent-events.jsonl"
    (directory / "weights.bin").write_bytes(b"explicit-test-model-asset")
    (directory / "agent.py").write_text('''
import json
import os
import time
from pathlib import Path
from simulator.agents import AutonomousAgent

class Driver(AutonomousAgent):
    def record(self, event, **payload):
        with Path(self.configuration["log"]).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, "pid": os.getpid(), **payload}) + "\\n")

    def load(self, configuration):
        self.configuration = configuration
        self.record("load", configuration=configuration)
        if configuration.get("fail_load"):
            raise RuntimeError("registered load failed")

    def reset(self, context):
        self.record("reset", context=context.model_dump(mode="json"))

    def step(self, observation):
        self.record("step", observation=observation.model_dump(mode="json"))
        if self.configuration.get("sleep_step"):
            time.sleep(self.configuration["sleep_step"])
        if self.configuration.get("fail_step"):
            raise RuntimeError("registered inference failed")
        return {"protocol_version": "1.3", "frame": observation.frame,
                "control": {"brake": self.configuration["brake"]}}

    def close(self):
        self.record("close")
''', encoding="utf-8")

    def build(*, sensors=False, configuration=None):
        manifest = {
            "protocol_version": "1.3", "agent_id": "registered-test-driver",
            "name": "Registered test driver", "version": "1.2.3",
            "entry_point": "agent:Driver", "assets": ["weights.bin"],
            "observation_mode": "sensors" if sensors else "simulator_state",
            "configuration": {"log": str(log), "brake": .6, **(configuration or {})},
        }
        if sensors:
            manifest["sensors"] = [
                {"id": "front", "type": "sensor.camera.rgb", "attributes": {
                    "image_size_x": 2, "image_size_y": 1, "fov": 80}},
                {"id": "roof", "type": "sensor.lidar.ray_cast", "attributes": {
                    "channels": 2, "points_per_second": 2000}},
            ]
        (directory / "agent.json").write_text(json.dumps(manifest), encoding="utf-8")
        return directory, log

    return build


def calls(log):
    if not log.exists():
        return []
    # A cancellation poll can race the worker's current append. Inspect only
    # complete JSONL records rather than treating a partial write as an error.
    return [json.loads(line) for line in log.read_text(encoding="utf-8").split("\n")[:-1]]


def wait_until(predicate, *, timeout=15):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        value = predicate()
        if value:
            return value
        Event().wait(.01)
    raise AssertionError("Registered agent did not reach the expected state")


def finished(client, simulation_id, *, timeout=15):
    last_state = {}
    def result():
        response = client.get(f"/simulations/{simulation_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        last_state.update(status=body["simulation"]["status"],
                          result_available=body["result"] is not None)
        return body if body["result"] is not None else None
    try:
        return wait_until(result, timeout=timeout)
    except AssertionError as error:
        error.add_note("Last registered simulation state: " + json.dumps(last_state, sort_keys=True))
        raise


def register(client, directory, **extra):
    response = client.post("/agents", json={
        "package_path": str(directory), "python_executable": sys.executable,
        "trusted_local_code": True, **extra,
    })
    assert response.status_code == 201, response.text
    return response.json()


def launch(client, agent_id=None, *, recipe=None, mode="test", **extra):
    response = client.post("/simulations", json={
        "scenario": scene() if recipe is None else recipe, "mode": mode,
        **({"agent_id": agent_id} if agent_id is not None else {}), **extra,
    })
    assert response.status_code == 202, response.text
    return response.json()


def test_registered_state_agent_controls_worker_and_persists_exact_identity(settings, package):
    directory, log = package()
    with TestClient(create_app(settings)) as client:
        registration = register(client, directory, configuration={"brake": .8})
        queued = launch(client, registration["id"])
        snapshot = queued["simulation"]["agent_snapshot"]
        assert queued["simulation"]["agent"] == "custom"
        assert snapshot == registration["snapshot"] == queued["job"]["agent_snapshot"]
        assert snapshot["registration_id"] == registration["id"]
        assert snapshot["configuration"]["brake"] == .8
        assert snapshot["manifest"]["version"] == "1.2.3"
        assert snapshot["observation_mode"] == "simulator_state"
        assert snapshot["agent_control_mode"] == "authoritative"
        assert len(snapshot["package_sha256"]) == 64
        saved = finished(client, queued["simulation"]["id"])
        assert saved["simulation"]["status"] == "completed", saved["simulation"]["error"]
        assert saved["simulation"]["agent_snapshot"] == snapshot
        assert saved["result"]["simulation"]["agent_snapshot"] == snapshot
        assert saved["result"]["control_audit"]["mode"] == "authoritative"
        assert saved["result"]["control_audit"]["interventions"] == []
        assert saved["result"]["agent_inputs"]["source"] == "synthetic_state"
        rows = calls(log)
        assert [row["event"] for row in rows] == ["load", "reset", "step", "step", "close"]
        assert len({row["pid"] for row in rows}) == 1 and rows[0]["pid"] != os.getpid()
        assert rows[0]["configuration"]["brake"] == .8
        telemetry = client.get(f"/simulations/{queued['simulation']['id']}/telemetry").json()["frames"]
        assert all(frame["control"]["brake"] == .8 for frame in telemetry[1:])
        assert all(row["observation"]["actors"][0]["actor_id"] == "npc"
                   for row in rows if row["event"] == "step")
        for field, value in (("mode", "scripted"), ("controller", "baseline")):
            forged = deepcopy(saved["result"])
            forged["control_audit"][field] = value
            with pytest.raises(ValueError):
                SimulationResult.model_validate(forged)


def test_registered_sensor_agent_records_named_streams_without_legacy_capture_options(settings, package):
    directory, log = package(sensors=True)
    with TestClient(create_app(settings)) as client:
        registration = register(client, directory)
        queued = launch(client, registration["id"], recipe=scene(sensors=True))
        assert queued["job"]["options"]["camera"] is queued["job"]["options"]["lidar"] is False
        saved = finished(client, queued["simulation"]["id"])
        assert saved["simulation"]["status"] == "completed", saved["simulation"]["error"]
        result = saved["result"]
        assert result["agent_inputs"]["source"] == "deterministic_fixture"
        assert result["agent_inputs"]["observation_mode"] == "sensors"
        assert result["agent_inputs"]["ground_truth_actor_inputs"] is False
        assert result["runtime"]["sensor_samples_count"] == 6
        recordings = [(index, item) for index, item in enumerate(result["artifacts"]) if item["kind"] == "recording"]
        assert len(recordings) == 1
        index = recordings[0][0]
        capture = client.get(f"/simulations/{queued['simulation']['id']}/artifacts/{index}")
        assert capture.status_code == 200, capture.text
        samples = capture.json()["samples"]
        assert len(samples) == 6
        assert {item["metadata"]["agent_sensor_id"] for item in samples} == {"front", "roof"}
        assert all(item["metadata"]["source"] == "deterministic_fixture" for item in samples)
        assert client.get(f"/simulations/{queued['simulation']['id']}/artifacts/{index}/samples/0").status_code == 200
        for row in calls(log):
            if row["event"] == "step":
                observation = row["observation"]
                assert observation["actors"] is observation["scene"] is observation["traffic_light_red"] is None
                assert {item["sensor_id"] for item in observation["sensors"]} == {"front", "roof"}
                assert all(item["frame"] == observation["frame"] for item in observation["sensors"])
        forged = deepcopy(result)
        camera = next(item for item in forged["agent_inputs"]["sensors"] if item["id"] == "front")
        camera["attributes"]["fov"] += 1
        with pytest.raises(ValueError):
            SimulationResult.model_validate(forged)


@contextmanager
def gated_client(settings):
    """Hold the first baseline job while subsequent registered work is queued."""
    entered, release = Event(), Event()
    factories = []

    class FirstOpenGate(TestSimulationAdapter):
        def open(self, recipe):
            entered.set()
            assert release.wait(15), "test did not release the first adapter"
            return super().open(recipe)

    def factory(**kwargs):
        factories.append(kwargs)
        return SimulationRunner(FirstOpenGate() if len(factories) == 1 else TestSimulationAdapter(), **kwargs)

    app = create_app(settings, runner_factory=factory)
    with TestClient(app) as client:
        try:
            launch(client)
            assert entered.wait(5)
            yield client, app, release, factories
        finally:
            release.set()


@pytest.mark.parametrize("changed", ["source", "asset", "manifest", "external_model"])
def test_queued_agent_drift_fails_before_factory_or_code_load_and_queue_continues(settings, package, tmp_path, changed):
    directory, log = package()
    model = tmp_path / "external-model.bin"
    model.write_bytes(b"pinned-external-model")
    with gated_client(settings) as (client, _, release, factories):
        registration = register(client, directory, model_files=[str(model)])
        queued = launch(client, registration["id"])
        snapshot = queued["simulation"]["agent_snapshot"]
        if changed == "source":
            with (directory / "agent.py").open("a", encoding="utf-8") as stream:
                stream.write("\nraise RuntimeError('changed code must not execute')\n")
        elif changed == "asset":
            (directory / "weights.bin").write_bytes(b"changed-model")
        elif changed == "manifest":
            payload = json.loads((directory / "agent.json").read_text())
            payload["configuration"]["brake"] = .1
            (directory / "agent.json").write_text(json.dumps(payload))
        else:
            model.write_bytes(b"changed-external-model")
        release.set()
        saved = finished(client, queued["simulation"]["id"])
        assert saved["simulation"]["status"] == "failed"
        assert saved["simulation"]["agent_snapshot"] == snapshot
        assert saved["result"]["telemetry_frames"] == 0
        assert saved["result"]["metrics"]["severity"] is None
        assert not log.exists()
        assert len(factories) == 1
        baseline = finished(client, launch(client)["simulation"]["id"])
        assert baseline["simulation"]["status"] == "completed"
        assert baseline["simulation"]["agent"] == "baseline"
        assert client.get("/simulations/queue").json()["accepting"]


def test_registered_waiting_job_snapshot_survives_restart_and_deactivation(settings, package):
    directory, log = package()
    with gated_client(settings) as (client, app, release, _):
        registration = register(client, directory, configuration={"brake": .9},
                                process_options={"step_timeout_s": 3.5})
        queued = launch(client, registration["id"])
        assert client.post(f"/agents/{registration['id']}/deactivate").status_code == 200
        result = []
        closer = Thread(target=lambda: result.append(app.state.runtime._worker.close()))
        closer.start()
        wait_until(lambda: not app.state.runtime._worker.status()["accepting"])
        release.set()
        closer.join(6)
        assert not closer.is_alive() and result == [True]
        assert client.get(f"/simulations/{queued['simulation']['id']}").json()["simulation"]["status"] == "queued"
        assert not log.exists()
    with TestClient(create_app(settings)) as resumed:
        # Accessing the queue explicitly starts recovery before polling the run.
        assert resumed.get("/simulations/queue").status_code == 200
        saved = finished(resumed, queued["simulation"]["id"])
        assert saved["simulation"]["status"] == "completed", saved["simulation"]["error"]
        assert saved["simulation"]["agent_snapshot"] == queued["simulation"]["agent_snapshot"]
        assert saved["job"]["agent_snapshot"] == queued["job"]["agent_snapshot"]
        assert calls(log)[0]["configuration"]["brake"] == .9
        assert saved["simulation"]["agent_snapshot"]["process_options"]["step_timeout_s"] == 3.5


def test_worker_rechecks_changed_source_before_import_after_parent_inspection(settings, package):
    directory, log = package()
    opened = []

    class SpyAdapter(TestSimulationAdapter):
        def open(self, recipe):
            opened.append(True)
            return super().open(recipe)

    def mutate_after_parent_checks(**kwargs):
        with (directory / "agent.py").open("a", encoding="utf-8") as stream:
            stream.write("\nraise RuntimeError('altered source was imported')\n")
        return SimulationRunner(SpyAdapter(), **kwargs)

    with TestClient(create_app(settings, runner_factory=mutate_after_parent_checks)) as client:
        registration = register(client, directory)
        queued = launch(client, registration["id"])
        saved = finished(client, queued["simulation"]["id"])
        assert saved["simulation"]["status"] == "failed"
        assert "changed" in saved["simulation"]["error"].lower()
        assert "altered source was imported" not in saved["simulation"]["error"]
        assert saved["result"]["telemetry_frames"] == 0
        assert opened == [] and not log.exists()


def test_repository_rejects_changes_to_nested_registered_identity(settings, package):
    directory, _ = package()
    with gated_client(settings) as (client, app, _, _):
        registration = register(client, directory)
        queued = launch(client, registration["id"])
        database = app.state.runtime.persistence().database
        with database.session() as session:
            stored = SimulationRepository(session).get(queued["simulation"]["id"])
            payload = stored.simulation.model_dump(mode="json")
            payload["agent_snapshot"]["configuration"]["brake"] = .1
            payload["agent_snapshot"]["manifest"]["configuration"]["brake"] = .1
            changed = Simulation.model_validate(payload)
            with pytest.raises(PersistenceError, match="immutable"):
                SimulationRepository(session).update(changed, expected_revision=stored.revision)
        saved = client.get(f"/simulations/{queued['simulation']['id']}").json()
        assert saved["simulation"]["agent_snapshot"] == queued["simulation"]["agent_snapshot"]


def test_mismatched_job_snapshot_fails_without_loading_any_agent(settings, package):
    directory, log = package()
    with gated_client(settings) as (client, app, release, factories):
        registration = register(client, directory)
        queued = launch(client, registration["id"])
        database = app.state.runtime.persistence().database
        with database.session() as session:
            session.execute(update(SimulationJobRecord).where(
                SimulationJobRecord.simulation_id == queued["simulation"]["id"],
            ).values(agent_snapshot=None))
        release.set()
        saved = finished(client, queued["simulation"]["id"])
        assert saved["simulation"]["status"] == "failed"
        assert "snapshot" in saved["simulation"]["error"].lower()
        assert saved["result"]["telemetry_frames"] == 0
        assert saved["simulation"]["agent_snapshot"] == queued["simulation"]["agent_snapshot"]
        assert not log.exists() and len(factories) == 1
        assert finished(client, launch(client)["simulation"]["id"])["simulation"]["status"] == "completed"


@pytest.mark.parametrize("configuration,process_options,expected", [
    ({"fail_load": True}, {}, "registered load failed"),
    ({"fail_step": True}, {}, "registered inference failed"),
    ({"sleep_step": 10}, {"step_timeout_s": .1}, "timeout"),
])
def test_registered_agent_failures_finalize_without_fallback_and_worker_recovers(settings, package, configuration, process_options, expected):
    directory, log = package(configuration=configuration)
    with TestClient(create_app(settings)) as client:
        registration = register(client, directory, process_options=process_options)
        queued = launch(client, registration["id"])
        saved = finished(client, queued["simulation"]["id"])
        assert saved["simulation"]["status"] == "failed"
        error = saved["simulation"]["error"].lower()
        assert expected in error or (expected == "timeout" and "deadline" in error)
        assert saved["simulation"]["agent_snapshot"] == queued["simulation"]["agent_snapshot"]
        assert saved["result"]["telemetry_frames"] <= 1
        if configuration.get("fail_step"):
            assert calls(log)[-1]["event"] == "close"
        assert finished(client, launch(client)["simulation"]["id"])["simulation"]["status"] == "completed"
        assert client.get("/simulations/queue").json()["accepting"]


def test_running_registered_agent_can_be_cancelled_during_inference(settings, package):
    directory, log = package(configuration={"sleep_step": 10})
    with TestClient(create_app(settings)) as client:
        registration = register(client, directory, process_options={"step_timeout_s": 15})
        queued = launch(client, registration["id"])
        wait_until(lambda: any(row["event"] == "step" for row in calls(log)))
        response = client.post(f"/simulations/{queued['simulation']['id']}/cancel")
        assert response.status_code == 202, response.text
        saved = finished(client, queued["simulation"]["id"])
        assert saved["simulation"]["status"] == "cancelled"
        assert saved["simulation"]["error"] is None
        assert saved["job"]["cancel_requested"]
        assert saved["simulation"]["agent_snapshot"] == queued["simulation"]["agent_snapshot"]
        assert finished(client, launch(client)["simulation"]["id"])["simulation"]["status"] == "completed"


def test_waiting_registered_cancellation_does_not_load_code_and_keeps_snapshot(settings, package):
    directory, log = package()
    with gated_client(settings) as (client, _, _, _):
        registration = register(client, directory)
        queued = launch(client, registration["id"])
        response = client.post(f"/simulations/{queued['simulation']['id']}/cancel")
        assert response.status_code == 202, response.text
        saved = finished(client, queued["simulation"]["id"])
        assert saved["simulation"]["status"] == "cancelled"
        assert saved["simulation"]["agent_snapshot"] == queued["simulation"]["agent_snapshot"]
        assert saved["result"]["telemetry_frames"] == 0
        assert not log.exists()


def test_unknown_inactive_and_incompatible_selections_leave_no_queued_jobs(settings, package):
    directory, _ = package(sensors=True)
    with TestClient(create_app(settings)) as client:
        registration = register(client, directory)
        invalid = [
            ({"agent_id": str(uuid4()), "scenario": scene()}, 404),
            ({"agent_id": registration["id"], "scenario": scene()}, 422),
            ({"agent_id": registration["id"], "scenario": scene(sensors=True),
              "options": {"no_rendering_mode": True}, "mode": "carla"}, 422),
        ]
        for payload, status in invalid:
            response = client.post("/simulations", json={"mode": "test", **payload})
            assert response.status_code == status, response.text
        assert client.post(f"/agents/{registration['id']}/deactivate").status_code == 200
        rejected = client.post("/simulations", json={
            "scenario": scene(sensors=True), "mode": "test", "agent_id": registration["id"],
        })
        assert rejected.status_code in (409, 422), rejected.text
        assert client.get("/simulations").json() == []
        assert client.get("/simulations/queue").json()["pending"] == 0


def test_registration_control_mode_explicitly_preserves_legacy_scripted_interventions(settings, package):
    directory, _ = package()
    recipe = scene()
    recipe["events"] = [{"actor_id": "ego", "action": "set_speed", "trigger_time_s": 0, "speed_mps": 1}]
    with TestClient(create_app(settings)) as client:
        authoritative = register(client, directory)
        rejected = client.post("/simulations", json={
            "scenario": recipe, "mode": "test", "agent_id": authoritative["id"],
        })
        assert rejected.status_code == 422, rejected.text
        scripted = register(client, directory, agent_control_mode="scripted")
        queued = launch(client, scripted["id"], recipe=recipe)
        saved = finished(client, queued["simulation"]["id"])
        assert saved["simulation"]["status"] == "completed", saved["simulation"]["error"]
        assert saved["result"]["control_audit"]["mode"] == "scripted"
        assert saved["result"]["control_audit"]["interventions"]
        assert saved["simulation"]["agent_snapshot"]["agent_control_mode"] == "scripted"


@pytest.mark.parametrize("kind", ["reproduce", "minimize"])
def test_custom_agent_failure_optimization_preserves_registered_identity(settings, package, kind):
    directory, _ = package()

    class CollisionAdapter(TestSimulationAdapter):
        def open(self, recipe):
            frame = super().open(recipe)
            frame.collisions = [CollisionEvent(other_actor_id="world:fixture-barrier")]
            return frame

    app = create_app(settings, runner_factory=lambda **kwargs: SimulationRunner(CollisionAdapter(), **kwargs))
    with TestClient(app) as client:
        registration = register(client, directory)
        queued = launch(client, registration["id"])
        saved = finished(client, queued["simulation"]["id"])
        assert saved["simulation"]["status"] == "completed", saved["simulation"]["error"]
        failure = client.post("/failures", json={
            "simulation_id": saved["simulation"]["id"], "scenario": saved["simulation"]["scenario"],
            "kind": "collision", "severity": saved["result"]["metrics"]["severity"],
            "summary": "Observed fixture contact from the registered agent run",
        })
        assert failure.status_code == 201, failure.text
        response = client.post("/optimizations", json={"kind": kind, "failure_id": failure.json()["id"],
            "repeats": 1, **({"max_trials": 1} if kind == "minimize" else {})})
        assert response.status_code == 202, response.text
        identifier = response.json()["id"]
        assert response.json()["agent_snapshot"] == registration["snapshot"]

        def done():
            view = client.get(f"/optimizations/{identifier}").json()
            return view if view["operation"]["status"] not in {"queued", "running"} else None

        view = wait_until(done, timeout=60)
        assert view["operation"]["status"] == "completed", view
        assert len(view["trials"]) == 1
        child = client.get(f'/simulations/{view["trials"][0]["simulation_id"]}').json()
        assert child["simulation"]["agent"] == "custom"
        assert child["simulation"]["agent_snapshot"] == registration["snapshot"]
        assert len(client.get("/simulations").json()) == 2


@pytest.mark.parametrize("spoof", ["unregistered", "other_registration", "control_mode"])
def test_direct_runner_rejects_spoofed_registered_execution_identity_before_loading(settings, package, spoof):
    directory, log = package()
    persistence = PersistenceService.from_settings(settings)
    persistence.initialize()
    driver = None
    opened = []

    class SpyAdapter(TestSimulationAdapter):
        def open(self, recipe):
            opened.append(True)
            return super().open(recipe)

    try:
        registry = AgentRegistryService(persistence.database)
        request = AgentRegistration(package_path=str(directory), python_executable=sys.executable,
                                    trusted_local_code=True)
        selected = registry.register(request).snapshot
        supplied = selected
        if spoof == "other_registration":
            supplied = registry.register(AgentRegistration.model_validate(request.model_dump() | {
                "configuration": {"brake": .1},
            })).snapshot
        driver = ProcessAgent(directory, python_executable=sys.executable,
                              options=AgentProcessOptions(**supplied.process_options.model_dump()),
                              snapshot=None if spoof == "unregistered" else supplied)
        queued = Simulation(scenario=validate_scenario(scene()), mode="test", agent="custom", agent_snapshot=selected)
        with pytest.raises(SimulationError, match="[Ss]napshot|[Rr]egistered|[Cc]ontrol mode"):
            SimulationRunner(SpyAdapter()).run(queued, agent=driver,
                                               agent_control_mode="scripted" if spoof == "control_mode" else None)
        assert opened == [] and not log.exists()
        assert not driver.is_running
    finally:
        if driver is not None:
            driver.close()
        persistence.database.dispose()
