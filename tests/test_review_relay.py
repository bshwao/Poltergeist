"""Report capabilities, generation ownership and read-only remote review."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from backend.models import ExperimentDocumentRecord
from shared.models.analysis import AnalysisCoverage, EvidenceAnalysis
from shared.models.coordination import DevicePolicy, PermissionScope
from shared.models.lab import LabAnalysisResult, LabCommand, LabReply
from shared.models.remote import ClientReady, RelayReply, StatusCommand, DeviceStatusReply, canonical_json, parse_message
from test_coordinator_relay import web, await_available
from test_remote_lab import lab, action, wait, remote_proposal
from test_experimentation import settings


def allow_review(app, device):
    with app.state.store.transaction() as connection:
        policy = DevicePolicy(scopes=["status:read", "run:execute:test", "run:cancel", "result:read"], allow_evidence_read=True)
        connection.execute("UPDATE devices SET policy_json=? WHERE id=?", (policy.model_dump_json(), str(device)))


@pytest.mark.parametrize("advertise", [False, True])
def test_old_or_declining_companions_refuse_new_reports_and_keep_normal_control(web, advertise):
    client, app, device, credential, alice, bob = web
    allow_review(app, device)
    headers = {"Authorization": "Bearer " + credential, "X-Poltergeist-Lab": "v1"}
    if advertise:
        headers["X-Poltergeist-Analysis"] = "v1"
    with client.websocket_connect("wss://coordinator.test/v1/device/connect", headers=headers) as socket:
        hello = socket.receive_json()
        assert ("analysis_supported" in hello) == advertise
        envelope = {key: hello[key] for key in ("device_id", "ownership_generation", "connection_generation")}
        ready = ClientReady(**envelope, remote_execution_enabled=True, lab_supported=True)
        assert "analysis_supported" not in ready.model_dump(mode="json")
        socket.send_text(canonical_json(ready))
        await_available(app, device)
        assert client.get("/v1/browser/devices", headers=alice).json()[0]["connection"]["analysis_supported"] is False
        request = LabCommand(request_id=uuid4(), action={"kind": "analysis_get", "id": uuid4()})
        response = client.post(f"/v1/devices/{device}/experimentation", json=request.model_dump(mode="json"), headers=alice)
        assert response.status_code == 503 and response.json()["error"]["code"] == "analysis_unavailable"
        status = StatusCommand(request_id=uuid4())
        assert client.post(f"/v1/devices/{device}/commands", json=status.model_dump(mode="json"), headers=alice).status_code == 202
        delivered = parse_message(socket.receive_text())
        assert delivered.command == status
        socket.send_text(canonical_json(RelayReply(**envelope, reply=DeviceStatusReply(
            request_id=status.request_id, accepting=True, active=False, waiting_remote_runs=0))))
        assert app.state.relay.transfer_count == 0


def test_report_capability_is_negotiated_and_transport_remains_owner_scoped(web):
    client, app, device, credential, alice, bob = web
    allow_review(app, device)
    request = LabCommand(request_id=uuid4(), action={"kind": "analysis_get", "id": uuid4()})
    url = f"/v1/devices/{device}/experimentation"
    assert client.post(url, json=request.model_dump(mode="json"), headers=bob).status_code == 404
    with client.websocket_connect("wss://coordinator.test/v1/device/connect", headers={"Authorization": "Bearer " + credential,
            "X-Poltergeist-Lab": "v1", "X-Poltergeist-Analysis": "v1"}) as socket:
        hello = socket.receive_json()
        assert hello["analysis_supported"] is True
        envelope = {key: hello[key] for key in ("device_id", "ownership_generation", "connection_generation")}
        socket.send_text(canonical_json(ClientReady(**envelope, remote_execution_enabled=True, lab_supported=True, analysis_supported=True)))
        await_available(app, device)
        assert client.get("/v1/browser/devices", headers=alice).json()[0]["connection"]["analysis_supported"] is True
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(client.post, url, json=request.model_dump(mode="json"), headers=alice)
            delivered = parse_message(socket.receive_text())
            assert delivered.command == request
            report = EvidenceAnalysis(simulation_id=request.action.id, mode="test", execution_status="completed",
                state="unavailable", summary="Saved evidence is unavailable", coverage=AnalysisCoverage(),
                observations=[], unresolved_questions=[], limitations=[])
            socket.send_text(canonical_json(RelayReply(**envelope, reply=LabReply(request_id=request.request_id,
                result=LabAnalysisResult(analysis=report)))))
            response = pending.result(timeout=10)
            assert response.status_code == 200 and response.json()["result"]["analysis"]["state"] == "unavailable"
        with app.state.store.transaction(write=False) as connection:
            assert connection.execute("SELECT count(*) FROM commands").fetchone()[0] == 0


def test_remote_analyst_reuses_owned_results_without_receipts_or_new_work(lab):
    runtime, binding, current, remote = lab
    plan = action(remote, binding, {"kind": "plan_propose", "proposal": remote_proposal()}).result.plan
    launched = action(remote, binding, {"kind": "run_launch", "launch": {"mode": "test", "scenario": plan.scenarios[0],
        "options": {"max_ticks": 10000}}}, mode="test")
    identity = launched.result.record.simulation.id
    wait(lambda: runtime.worker().get(identity).result is not None)
    with runtime.persistence().database.session(write=False) as session:
        before = session.scalar(select(func.count()).select_from(ExperimentDocumentRecord))
    first = action(remote, binding, {"kind": "analysis_get", "id": identity})
    assert first.kind == "lab" and first.result.analysis.simulation_id == identity
    assert first.result.analysis.coverage.telemetry_frames > 0
    assert action(remote, binding, {"kind": "analysis_get", "id": identity}).result == first.result
    with runtime.persistence().database.session(write=False) as session:
        assert session.scalar(select(func.count()).select_from(ExperimentDocumentRecord)) == before
    current[0] = stranger = replace(binding, owner_id=uuid4(), generation=2)
    assert action(remote, stranger, {"kind": "analysis_get", "id": identity}).code == "run_not_found"
    current[0] = binding
    current[1] = DevicePolicy(scopes=[PermissionScope.STATUS])
    assert action(remote, binding, {"kind": "analysis_get", "id": identity}).code == "permission_denied"


def test_connected_regression_review_does_not_invent_a_baseline_artifact_version(lab):
    runtime, binding, current, remote = lab
    plan = action(remote, binding, {"kind": "plan_propose", "proposal": remote_proposal()}).result.plan
    definition = {"kind": "suite", "name": "Owned baseline journal", "mode": "test", "options": {"max_ticks": 10000},
        "cases": [{"name": "Owned scene", "scenario": plan.scenarios[0]}]}
    suite = action(remote, binding, {"kind": "suite_save", "save": {"definition": definition}}).result.suite
    identity = uuid4()
    accepted = action(remote, binding, {"kind": "suite_run", "id": suite.id,
        "launch": {"request_id": identity, "expected_revision": 1}}, mode="test", identity=identity)
    assert accepted.kind == "lab"
    wait(lambda: runtime.optimizer().get(identity).operation.status == "completed")
    report = action(remote, binding, {"kind": "regression_report", "id": identity}).result.report
    assert report.state == "inconclusive" and report.controllers[0].execution_digest is None
    assert report.assessments == []
    current[0] = stranger = replace(binding, owner_id=uuid4(), generation=2)
    assert action(remote, stranger, {"kind": "regression_report", "id": identity}).code == "run_not_found"
