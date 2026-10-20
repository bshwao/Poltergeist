"""The dashboard and every behavior export use the same validated saved evidence."""

import csv
from hashlib import sha256
from html.parser import HTMLParser
from io import StringIO
import json
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from backend.decision_storage import DecisionWriter
from backend.main import create_app
from backend.services.behavior_report import DiagnosticEvidence
from backend.services.report_service import build_report, export_report
from shared.models.behavior import BehaviorSettings
from shared.models.decision import DecisionRecord
from simulator import SimulationRunner

from test_reports import api, persistence, saved
from test_behavior_api import save
from test_registered_agent_execution import launch, package, register, scene, settings, wait_until


class Snapshot(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inside = False
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag == "pre" and dict(attrs).get("id") == "export-snapshot":
            self.inside = True
    def handle_endtag(self, tag):
        if tag == "pre":
            self.inside = False
    def handle_data(self, text):
        if self.inside:
            self.parts.append(text)


def export(client, run_id, format="json", **options):
    response = client.get(f"/reports/simulations/{run_id}", params={"include_behavior": True, "format": format, **options})
    assert response.status_code == 200, response.text
    assert response.headers["x-poltergeist-report-schema"] == "poltergeist-run-report-v2"
    return response


def test_all_formats_preserve_the_dashboard_analysis_settings_identity_and_native_evidence(api, persistence):
    client, runtime = api
    output = save(persistence)
    run_id = output.result.simulation.id
    original = persistence.get_simulation(run_id)
    files = {str(p): sha256(p.read_bytes()).hexdigest() for p in persistence.artifacts.root.rglob("*") if p.is_file()}
    analysis = client.get(f"/simulations/{run_id}/analysis").json()
    body = export(client, run_id).json()
    assert body["schema"] == "poltergeist-run-report-v2"
    assert body["behavior_analysis"] == analysis
    assert body["record"]["result"] == output.result.model_dump(mode="json")
    assert body["telemetry"] is None
    assert len(body["qualified_interpretations"]) == 6 * len(analysis["events"])
    assert body["agent_reported_diagnostics"]["total_decisions"] == analysis["coverage"]["applied_decisions"]
    rows = {row["json_pointer"]: row for row in csv.DictReader(StringIO(export(client, run_id, "csv").content.decode("utf-8-sig")))}
    assert json.loads(rows["/behavior_analysis/simulation_id"]["value_json"]) == str(run_id)
    assert json.loads(rows["/behavior_analysis/route/max_deviation/value"]["value_json"]) is None
    assert rows["/behavior_analysis/route/max_deviation/value"]["unit"] == "m"
    assert json.loads(rows["/behavior_analysis/braking/max_deceleration/value"]["value_json"]) == 0
    assert rows["/behavior_analysis/braking/max_deceleration/value"]["unit"] == "m/s²"
    assert rows["/behavior_analysis/events/0/responses/0/threshold"]["unit"] == "normalized_control"
    assert json.loads(rows["/qualified_interpretations/0/statement"]["value_json"]) == body["qualified_interpretations"][0]["statement"]
    html = export(client, run_id, "html").text
    snapshot = Snapshot(); snapshot.feed(html)
    rendered = json.loads("".join(snapshot.parts))
    for key in ("behavior_analysis", "agent_reported_diagnostics", "qualified_interpretations"):
        assert rendered[key] == body[key]
    for label in ("Measured behavior", "Agent-reported diagnostics", "Qualified interpretations", "Supporting frames", "No completed stops", "No response observed in measured window"):
        assert label in html
    assert persistence.get_simulation(run_id) == original
    assert files == {str(p): sha256(p.read_bytes()).hexdigest() for p in persistence.artifacts.root.rglob("*") if p.is_file()}
    assert runtime._worker is None and runtime._optimizer is None and runtime._registry is None


def test_behavior_and_optional_telemetry_share_one_validated_read(api, persistence, monkeypatch):
    client, _ = api
    output = save(persistence)
    read = persistence.read_output
    calls = []
    def tracked(*args, **kwargs):
        calls.append(args[0]); return read(*args, **kwargs)
    monkeypatch.setattr(persistence, "read_output", tracked)
    body = export(client, output.result.simulation.id, include_telemetry=True).json()
    assert len(calls) == 1
    assert body["telemetry"] == [frame.model_dump(mode="json") for frame in output.telemetry]


@pytest.mark.parametrize("kind", ["legacy", "cancelled", "failed"])
def test_partial_and_old_runs_keep_unknowns_and_unresolved_inputs_in_every_format(api, persistence, kind):
    client, _ = api
    output = save(persistence, legacy=kind == "legacy", cancel=kind == "cancelled", failed=kind == "failed")
    run_id = output.result.simulation.id
    body = export(client, run_id).json()
    analysis = client.get(f"/simulations/{run_id}/analysis").json()
    assert body["behavior_analysis"] == analysis
    if kind == "legacy":
        assert not body["agent_reported_diagnostics"]["timeline_available"]
    elif kind == "failed":
        assert body["agent_reported_diagnostics"]["unresolved_inputs"] == 1
    else:
        assert analysis["braking"]["max_deceleration"]["value"] is None
    assert body["agent_reported_diagnostics"]["records"] == []
    assert export(client, run_id, "csv").content
    assert "Missing statements are not reconstructed" in export(client, run_id, "html").text


def test_configured_export_uses_the_exact_analysis_post_settings_and_preserves_saved_scores(api, persistence):
    client, _ = api
    output = save(persistence)
    run_id = output.result.simulation.id
    changes = {"response_window_s": .1, "brake_threshold": .4, "max_response_events": 1}
    expected = client.post(f"/simulations/{run_id}/analysis", json=changes).json()
    for format in ("json", "html", "csv"):
        response = client.post(f"/reports/simulations/{run_id}?format={format}", json=changes)
        assert response.status_code == 200, response.text
        if format == "json":
            assert response.json()["behavior_analysis"] == expected
            assert response.json()["record"]["result"]["metrics"] == output.result.metrics.model_dump(mode="json")
        elif format == "html":
            snapshot = Snapshot(); snapshot.feed(response.text)
            assert json.loads("".join(snapshot.parts))["behavior_analysis"] == expected
        else:
            values = {r["json_pointer"]: json.loads(r["value_json"]) for r in csv.DictReader(StringIO(response.content.decode("utf-8-sig")))}
            assert values["/behavior_analysis/settings/brake_threshold"] == .4
    assert persistence.get_simulation(run_id).result == output.result


@pytest.mark.parametrize("query,body", [("format=pdf", {}), ("format=csv&include_telemetry=true", {}), ("", {"brake_threshold": 5}), ("", {"unknown": 1})])
def test_configured_report_rejects_invalid_options_before_artifact_access(api, persistence, query, body):
    client, _ = api
    output = save(persistence)
    response = client.post(f"/reports/simulations/{output.result.simulation.id}?{query}", json=body)
    assert response.status_code == 422


def test_unfinished_and_missing_behavior_exports_keep_explicit_status_codes(api, persistence):
    client, _ = api
    pending = persistence.create_simulation(scene(), mode="test")
    assert client.get(f"/reports/simulations/{pending.simulation.id}?include_behavior=true").status_code == 409
    assert client.get(f"/reports/simulations/{uuid4()}?include_behavior=true").status_code == 404
    assert client.get("/reports/simulations/not-an-id?include_behavior=true").status_code == 422


def test_unavailable_or_corrupt_evidence_never_publishes_new_measurements(api, persistence):
    client, _ = api
    output = save(persistence)
    run_id = output.result.simulation.id
    ref = next(ref for ref in output.result.artifacts if ref.kind == "decisions")
    path = persistence.artifacts.validate_reference(ref)
    path.write_text(path.read_text().replace('"sha256":', '"bad_checksum":', 1), encoding="utf-8")
    assert client.get(f"/reports/simulations/{run_id}?include_behavior=true").status_code == 422
    path.unlink()
    assert client.get(f"/reports/simulations/{run_id}?include_behavior=true").status_code == 503
    assert client.get(f"/reports/simulations/{run_id}").status_code == 200


def test_script_influenced_braking_is_not_credited_as_agent_reasoning(api, persistence):
    client, _ = api
    recipe = scene() | {"events": [{"actor_id": "ego", "action": "brake", "trigger_time_s": 0, "deceleration_mps2": 8}]}
    stored = persistence.create_simulation(recipe, mode="test")
    writer = DecisionWriter(persistence.artifacts, stored.simulation.id)
    output = persistence.persist_output(SimulationRunner().run(stored.simulation, on_observation=writer.observation,
        on_decision=writer.decision, on_agent_reset=writer.reset), expected_revision=stored.revision, decisions=writer)
    body = export(client, output.result.simulation.id).json()
    influenced = [item for item in body["qualified_interpretations"] if item["attribution"] == "script_influenced"]
    assert influenced and all("do not credit an independent agent response" in item["statement"] for item in influenced)
    assert "includes scenario assistance" in export(client, output.result.simulation.id, "html").text


def test_diagnostic_caps_retain_an_exact_prefix_and_never_keep_observations(monkeypatch):
    data = json.loads((Path(__file__).resolve().parents[1] / "frontend/tests/fixtures/behavior-review.json").read_text(encoding="utf-8"))
    records = [DecisionRecord.model_validate(v) for v in data["records"]]
    monkeypatch.setattr("backend.services.behavior_report.DIAGNOSTIC_RECORD_LIMIT", 2)
    evidence = DiagnosticEvidence()
    for record in records: evidence(record)
    body = evidence.export(True)
    assert body["retained_records"] == 2 and body["truncated"]
    assert body["decisions_with_diagnostics"] == len(records)
    assert [r["frame"] for r in body["records"]] == [100, 101]
    assert all("observation" not in item and "sensors" not in item for item in body["records"])


def test_diagnostic_byte_budget_is_explicit_and_does_not_skip_to_later_records(monkeypatch):
    data = json.loads((Path(__file__).resolve().parents[1] / "frontend/tests/fixtures/behavior-review.json").read_text(encoding="utf-8"))
    records = [DecisionRecord.model_validate(v) for v in data["records"]]
    monkeypatch.setattr("backend.services.behavior_report.DIAGNOSTIC_BYTE_LIMIT", 1)
    evidence = DiagnosticEvidence()
    for record in records: evidence(record)
    body = evidence.export(True)
    assert body["records"] == [] and body["retained_record_json_bytes"] == 0 and body["truncated"]
    assert body["decisions_with_diagnostics"] == len(records)


@pytest.mark.parametrize("sensors", [False, True])
def test_registered_diagnostics_identity_and_sensor_provenance_survive_without_loading_current_agent(settings, package, sensors):
    directory, _ = package(sensors=sensors, configuration={"measurement": {"value": 1, "unit": "=SUM(1,2)"}})
    hostile = '<script>alert("diagnostic")</script>=1+1'
    source = directory / "agent.py"
    diagnostics = {"maneuver": hostile, "reason_codes": ["obstacle"], "metadata": {"=formula": hostile, "confidence": .7}}
    source.write_text(source.read_text().replace('"control": {"brake": self.configuration["brake"]}',
        '"control": {"brake": self.configuration["brake"]}, "diagnostics": ' + repr(diagnostics)), encoding="utf-8")
    with TestClient(create_app(settings)) as client:
        registration = register(client, directory)
        run_id = launch(client, registration["id"], recipe=scene(sensors=sensors))["simulation"]["id"]
        def finalized():
            response = client.get(f"/simulations/{run_id}")
            assert response.status_code == 200, response.text
            body = response.json()
            return body if body["result"] is not None else None
        # The supported worker load deadline is 30 s; allow it to finish before
        # the test's polling deadline, including cold Windows Python startup.
        output = wait_until(finalized, timeout=60)
        assert output["simulation"]["status"] == "completed", output["simulation"]["error"]
        source.write_text("raise RuntimeError('must not import for export')", encoding="utf-8")
        body = export(client, run_id).json()
        assert body["behavior_analysis"] == client.get(f"/simulations/{run_id}/analysis").json()
        assert body["behavior_analysis"]["agent_snapshot"] == output["simulation"]["agent_snapshot"]
        assert body["behavior_analysis"]["agent_inputs"] == output["result"]["agent_inputs"]
        records = body["agent_reported_diagnostics"]["records"]
        assert len(records) == 2 and records[0]["diagnostics"]["maneuver"] == hostile
        assert all("observation" not in record and "sensors" not in record for record in records)
        html = export(client, run_id, "html").text
        assert "<script>" not in html and "&lt;script&gt;" in html
        rows = list(csv.DictReader(StringIO(export(client, run_id, "csv").content.decode("utf-8-sig"))))
        row = next(r for r in rows if r["json_pointer"] == "/agent_reported_diagnostics/records/0/diagnostics/maneuver")
        assert row["value_json"].startswith('"') and json.loads(row["value_json"]) == hostile
        assert next(r for r in rows if r["json_pointer"] == "/behavior_analysis/agent_snapshot/configuration/measurement/value")["unit"] == ""
        assert "data_base64" not in json.dumps(body)
        if sensors:
            assert body["behavior_analysis"]["agent_inputs"]["source"] == "deterministic_fixture"
