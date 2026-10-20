"""Phase 7 evidence through real workers, synthetic fixtures and durable files."""

from hashlib import sha256
import json
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from backend.decision_storage import DecisionWriter
from backend.main import create_app
from backend.persistence_errors import PersistenceError
from simulator import SimulationRunner
from simulator.errors import SimulationError
from simulator.test_adapter import TestSimulationAdapter

from test_registered_agent_execution import calls, finished, launch, package, register, scene, settings, wait_until


def timeline(client, run_id, query=""):
    response = client.get(f"/simulations/{run_id}/decisions{query}")
    assert response.status_code == 200, response.text
    return response.json()


def test_exact_state_input_diagnostics_controls_and_reset_survive_restart(settings, package):
    directory, log = package()
    source = directory / "agent.py"
    source.write_text(source.read_text().replace('"control": {"brake": self.configuration["brake"]}',
        '"control": {"brake": self.configuration["brake"]}, "diagnostics": {"maneuver": "yield", '
        '"reason_codes": ["obstacle"], "hazard_actor_ids": ["npc"], "metadata": {"confidence": 0.7}}'))
    with TestClient(create_app(settings)) as client:
        registration = register(client, directory)
        run_id = launch(client, registration["id"])["simulation"]["id"]
        saved = finished(client, run_id)
        assert saved["simulation"]["status"] == "completed", saved["simulation"]["error"]
        page = timeline(client, run_id, "?offset=1&limit=1")
        assert (page["coverage"], page["total"], page["applied"], page["transitions"]) == ("complete", 2, 2, 2)
        record = page["records"][0]
        steps = [call for call in calls(log) if call["event"] == "step"]
        assert record["observation"] == steps[1]["observation"]
        assert page["reset"] == next(call["context"] for call in calls(log) if call["event"] == "reset")
        assert record["agent_result"]["diagnostics"]["maneuver"] == "yield"
        assert record["proposed_control"] == record["applied_control"] == page["telemetry"][1]["control"]
        assert page["telemetry"][0]["frame"] == record["frame"] == 1
        assert page["telemetry"][1]["ego"] == record["resulting_ego"]
        assert record["worker_step_wall_time_s"] >= 0
        assert record["round_trip_wall_time_s"] >= record["worker_step_wall_time_s"]
        assert timeline(client, run_id, "?offset=99&limit=1")["records"] == []
        for query in ("?offset=-1", "?limit=0", "?limit=101"):
            assert client.get(f"/simulations/{run_id}/decisions{query}").status_code == 422
    with TestClient(create_app(settings)) as client:
        assert timeline(client, run_id, "?offset=1&limit=1") == page


def test_baseline_script_intervention_has_distinct_proposal_and_applied_brake(settings):
    recipe = scene() | {"events": [{"actor_id": "ego", "action": "brake", "trigger_time_s": 0,
                                    "deceleration_mps2": 8}]}
    with TestClient(create_app(settings)) as client:
        run_id = launch(client, recipe=recipe)["simulation"]["id"]
        saved = finished(client, run_id)
        assert saved["simulation"]["status"] == "completed", saved["simulation"]["error"]
        record = timeline(client, run_id)["records"][0]
        assert record["interventions"] == ["scripted_ego_braking"]
        assert record["proposed_control"] != record["applied_control"]
        assert record["applied_control"]["brake"] == 1
        assert record["agent_result"] is None


@pytest.mark.parametrize("fail_step", [False, True])
def test_sensor_input_bytes_are_referenced_once_even_on_inference_failure(settings, package, fail_step):
    directory, log = package(sensors=True, configuration={"fail_step": fail_step})
    with TestClient(create_app(settings)) as client:
        registration = register(client, directory)
        run_id = launch(client, registration["id"], recipe=scene(sensors=True))["simulation"]["id"]
        saved = finished(client, run_id)
        assert saved["simulation"]["status"] == ("failed" if fail_step else "completed"), saved["simulation"]["error"]
        page = timeline(client, run_id)
        assert page["coverage"] == ("partial" if fail_step else "complete")
        record = page["records"][0]
        assert record["status"] == ("unresolved" if fail_step else "applied")
        observed = next(call["observation"] for call in calls(log) if call["event"] == "step")
        assert record["observation"] == observed | {"sensors": []}
        assert len(record["sensors"]) == 2
        artifact_index = page["recording_artifact_index"]
        for sensor, original in zip(record["sensors"], observed["sensors"]):
            assert sensor["header"] == {key: value for key, value in original.items() if key != "data_base64"}
            response = client.get(f"/simulations/{run_id}/artifacts/{artifact_index}/samples/{sensor['sample_index']}")
            assert response.status_code == 200
            assert sha256(response.content).hexdigest() == sensor["header"]["sha256"]
            assert len(response.content) == sensor["byte_length"]
        evidence_index = next(index for index, ref in enumerate(saved["result"]["artifacts"]) if ref["kind"] == "decisions")
        raw = client.get(f"/simulations/{run_id}/artifacts/{evidence_index}")
        assert "data_base64" not in raw.text
        assert raw.text.count("\n") == page["total"] + 1
        assert client.app.state.runtime.persistence().read_output(UUID(run_id)).result == client.app.state.runtime.persistence().get_simulation(UUID(run_id)).result


def test_capture_failure_after_measured_transition_exposes_a_gap(settings, package):
    class MissingNextPacket(TestSimulationAdapter):
        def agent_sensor_samples(self, frame):
            if frame.frame == 1:
                raise SimulationError("next packet failed")
            return super().agent_sensor_samples(frame)

    directory, _ = package(sensors=True)
    factory = lambda **kwargs: SimulationRunner(adapter=MissingNextPacket(), **kwargs)
    with TestClient(create_app(settings, runner_factory=factory)) as client:
        registration = register(client, directory)
        run_id = launch(client, registration["id"], recipe=scene(sensors=True))["simulation"]["id"]
        saved = finished(client, run_id)
        assert saved["simulation"]["status"] == "failed", saved
        page = timeline(client, run_id)
        assert (page["coverage"], page["transitions"], page["applied"], page["total"]) == ("partial", 1, 0, 1)
        assert page["records"][0]["applied_control"] is None
        assert len(page["telemetry"]) == 2


def test_cancelled_external_step_retains_input_without_inventing_action(settings, package):
    directory, log = package(configuration={"sleep_step": 10})
    with TestClient(create_app(settings)) as client:
        registration = register(client, directory)
        run_id = launch(client, registration["id"])["simulation"]["id"]
        wait_until(lambda: any(call["event"] == "step" for call in calls(log)))
        assert client.post(f"/simulations/{run_id}/cancel").status_code == 202
        saved = finished(client, run_id)
        assert saved["simulation"]["status"] == "cancelled"
        page = timeline(client, run_id)
        assert (page["coverage"], page["total"], page["applied"]) == ("partial", 1, 0)
        assert page["records"][0]["status"] == "unresolved"


def test_sensor_capture_storage_failure_keeps_failed_telemetry_without_dangling_inputs(settings, package, monkeypatch):
    from backend.storage import CaptureWriter
    original = CaptureWriter.write

    def fail_later(writer, sample):
        if sample.frame == 2:
            raise OSError("raw sensor disk write failed")
        return original(writer, sample)

    monkeypatch.setattr(CaptureWriter, "write", fail_later)
    directory, _ = package(sensors=True)
    with TestClient(create_app(settings)) as client:
        registration = register(client, directory)
        run_id = launch(client, registration["id"], recipe=scene(sensors=True))["simulation"]["id"]
        saved = finished(client, run_id)
        assert saved["simulation"]["status"] == "failed", saved
        assert "disk write failed" in saved["simulation"]["error"]
        assert saved["result"]["telemetry_frames"] == 3
        assert [ref["kind"] for ref in saved["result"]["artifacts"]] == ["telemetry"]
        page = timeline(client, run_id)
        assert not page["available"] and page["coverage"] == "unavailable"
        assert not list(settings.storage_dir.rglob("*.bin"))
        assert client.app.state.runtime.persistence().read_output(UUID(run_id)).result.telemetry_frames == 3
        recovered = finished(client, launch(client)["simulation"]["id"])
        assert recovered["simulation"]["status"] == "completed"


def test_legacy_result_has_explicit_unavailable_evidence_and_active_runs_conflict(settings):
    with TestClient(create_app(settings)) as client:
        persistence = client.app.state.runtime.persistence()
        queued = persistence.create_simulation(scene(), mode="test")
        run_id = str(queued.simulation.id)
        assert client.get(f"/simulations/{run_id}/decisions").status_code == 409
        persistence.persist_output(SimulationRunner().run(queued.simulation), expected_revision=queued.revision)
        page = timeline(client, run_id)
        assert not page["available"] and page["coverage"] == "unavailable"
        assert page["records"] == page["telemetry"] == []
        assert client.get(f"/simulations/{uuid4()}/decisions").status_code == 404


@pytest.mark.parametrize("corruption", ["checksum", "state", "control", "frame", "reset"])
def test_corrupt_decisions_are_rejected_on_read(settings, corruption):
    with TestClient(create_app(settings)) as client:
        run_id = launch(client)["simulation"]["id"]
        saved = finished(client, run_id)
        ref = next(ref for ref in saved["result"]["artifacts"] if ref["kind"] == "decisions")
        from shared.models import ArtifactReference
        path = client.app.state.runtime.persistence().artifacts.validate_reference(ArtifactReference.model_validate(ref))
        lines = path.read_text(encoding="utf-8").splitlines()
        header, first = json.loads(lines[0]), json.loads(lines[1])
        if corruption == "reset":
            header["reset"] = {"seed": 999, "map_name": "Town99", "ego_actor_id": "ego", "fixed_delta_seconds": .05}
        elif corruption == "state":
            first["observation"]["ego"]["transform"]["position"]["x"] += 100
        elif corruption == "control":
            first["applied_control"]["brake"] = .99
        elif corruption == "frame":
            first["resulting_frame"] += 1
        else:
            header["sha256"] = "0" * 64
        lines[1] = json.dumps(first)
        path.write_text(json.dumps(header) + "\n" + "\n".join(lines[1:]) + "\n", encoding="utf-8", newline="\n")
        with pytest.raises((PersistenceError, ValueError)):
            client.app.state.runtime.persistence().read_decisions(UUID(run_id))


def test_rejected_publication_rolls_back_only_owned_artifacts(settings):
    with TestClient(create_app(settings)) as client:
        persistence = client.app.state.runtime.persistence()
        queued = persistence.create_simulation(scene(), mode="test")
        writer = DecisionWriter(persistence.artifacts, queued.simulation.id)
        output = SimulationRunner().run(queued.simulation, on_observation=writer.observation,
            on_decision=writer.decision, on_agent_reset=writer.reset)
        # Drop the spool after execution: a completed episode cannot publish an
        # apparently empty decision chain over two measured transitions.
        (writer.stage / ".records").write_bytes(b"")
        with pytest.raises(PersistenceError):
            persistence.persist_output(output, expected_revision=queued.revision, decisions=writer)
        assert persistence.get_simulation(queued.simulation.id).result is None
        assert not writer.stage.exists() and not writer.final.exists()
        assert not list(settings.storage_dir.rglob("timeline.ndjson"))
