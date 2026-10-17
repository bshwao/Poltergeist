"""Finalized exports across the real synthetic API, worker and storage lifecycle."""

import csv
from io import StringIO
import json
from threading import Event
from time import monotonic

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app


def _json(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def _finish(client, simulation_id):
    deadline = monotonic() + 10
    while monotonic() < deadline:
        progress = _json(client.get(f"/simulations/{simulation_id}/progress"))
        if progress["status"] in {"completed", "failed", "cancelled"}:
            assert progress["status"] == "completed", progress
            return _json(client.get(f"/simulations/{simulation_id}/result"))
        Event().wait(0.01)
    raise AssertionError("Real synthetic worker did not finish within ten seconds")


def test_generate_launch_export_and_reopen_preserves_observed_evidence(tmp_path):
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{(tmp_path / 'database' / 'runs.db').as_posix()}",
        storage_dir=tmp_path / "artifacts",
        simulation_shutdown_timeout_s=2,
        simulation_progress_interval_s=0.01,
    )
    with TestClient(create_app(settings)) as client:
        recipe = _json(client.post("/scenarios/generate", json={
            "template": "sudden_braking", "seed": 1937,
            "parameters": {"lead_distance_m": 5.0},
        }))
        # A shorter window retains the generated event at t=2s. Its close lead
        # placement produces observed near-miss evidence without injected events.
        recipe["duration_s"] = 3.0
        recipe = _json(client.post("/scenarios/validate", json=recipe))
        saved = _json(client.post("/scenarios", json=recipe), 201)
        receipt = _json(client.post("/simulations", json={
            "scenario_id": saved["id"], "mode": "test",
            "options": {"max_ticks": 1000},
            "evaluation_settings": {"speeding_tolerance_mps": 0.75},
        }), 202)
        simulation_id = receipt["simulation"]["id"]
        result = _finish(client, simulation_id)
        assert result["simulation"]["scenario"] == recipe
        assert result["simulation"]["mode"] == result["runtime"]["adapter"] == "test"
        assert result["telemetry_frames"] == 61
        assert result["metrics"]["min_clearance_m"] < 2.0
        assert result["metrics"]["severity"] is not None

        failure = _json(client.post("/failures", json={
            "simulation_id": simulation_id, "scenario": recipe,
            "kind": "near_miss", "summary": "Observed close lead in synthetic integration run",
            "severity": result["metrics"]["severity"], "reproduced": None,
        }), 201)
        report_url = f"/reports/simulations/{simulation_id}"
        for observation in (False, True, None):
            _json(client.patch(f"/failures/{failure['id']}/reproduction", json={"reproduced": observation}))
            report = _json(client.get(report_url))
            assert report["failures"][0]["reproduced"] is observation
            assert report["record"]["result"] == result

        # Editing the library entry must not change exported execution evidence.
        edited = dict(recipe, name="Changed library name after launch")
        _json(client.put(f"/scenarios/{saved['id']}", json={"scenario": edited, "expected_revision": saved["revision"]}))
        complete = _json(client.get(report_url, params={"include_telemetry": True}))
        assert complete["record"]["simulation"]["scenario"] == recipe
        assert complete["record"]["scenario_id"] == saved["id"]
        assert complete["record"]["job"]["options"] == receipt["job"]["options"]
        assert complete["record"]["job"]["evaluation_settings"] == receipt["job"]["evaluation_settings"]
        assert len(complete["telemetry"]) == result["telemetry_frames"]
        artifact = client.get(f"/simulations/{simulation_id}/artifacts/0")
        assert artifact.status_code == 200
        assert [json.loads(line) for line in artifact.text.splitlines()] == complete["telemetry"]

        response = client.get(report_url, params={"format": "csv"})
        assert response.status_code == 200
        rows = list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))
        by_path = {row["json_pointer"]: json.loads(row["value_json"]) for row in rows}
        assert by_path["/record/simulation/scenario/name"] == recipe["name"]
        assert by_path["/record/result/metrics/severity"] == result["metrics"]["severity"]
        assert by_path["/failures/0/reproduced"] is None
        html = client.get(report_url, params={"format": "html"})
        assert html.status_code == 200
        assert "Synthetic test execution" in html.text
        assert "attachment" in html.headers["content-disposition"]

    # A completely new API runtime reloads the volume's data. Reading a report
    # alone must not start the queue or optimization controller.
    reopened = create_app(settings)
    with TestClient(reopened) as client:
        restored = _json(client.get(report_url, params={"include_telemetry": True}))
        assert reopened.state.runtime._worker is None
        assert reopened.state.runtime._optimizer is None
        assert restored["record"] == complete["record"]
        assert restored["telemetry"] == complete["telemetry"]
        assert restored["failures"] == complete["failures"]
        assert restored["interpretation"] == complete["interpretation"]
        assert _json(client.get("/failures", params={"simulation_id": simulation_id, "reproduced": "unknown"}))[0]["id"] == failure["id"]
