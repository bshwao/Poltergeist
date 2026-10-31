"""Negotiated guided experiments retain device consent and durable local scope."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest

from backend.dependencies import ApiRuntime
from backend.remote_receipts import RemoteBinding
from companion.remote_service import RemoteService
from shared.models.coordination import DevicePolicy, PermissionScope
from shared.models.experimentation import DiscoveryProposal
from shared.models.lab import LabCommand, LabGalleryResult, LabReply
from shared.models.remote import ClientReady, RelayReply, canonical_json, parse_message
from shared.models.runtime import SimulationOptions
from simulator import SimulationRunner
from simulator.test_adapter import TestSimulationAdapter

from test_experimentation import ContactAdapter, proposal, settings
from test_coordinator_relay import web, await_available


@pytest.fixture
def lab(settings):
    runtime = ApiRuntime(settings, runner_factory=lambda **kw: SimulationRunner(ContactAdapter(), **kw))
    binding = RemoteBinding("https://coordinator.test", uuid4(), uuid4(), 1)
    current = [binding, DevicePolicy(scopes=list(PermissionScope), allow_evidence_read=True)]
    remote = RemoteService(runtime.worker(), lambda: tuple(current), optimizer_factory=runtime.optimizer)
    yield runtime, binding, current, remote
    assert runtime.close()


def action(remote, binding, value, *, mode=None, identity=None):
    return remote.dispatch(binding, LabCommand(request_id=identity or uuid4(), action=value, mode=mode))


def remote_proposal():
    value = DiscoveryProposal.model_validate(proposal())
    value.options = SimulationOptions(max_ticks=10000)
    return value


def wait(predicate):
    deadline, value = monotonic() + 30, None
    while monotonic() < deadline:
        value = predicate()
        if value:
            return value
        Event().wait(.01)
    raise AssertionError(f"Remote experiment did not finish: {value}")


def test_remote_plan_approval_confirmation_receipt_and_owner_isolation(lab):
    runtime, binding, current, remote = lab
    request = LabCommand(request_id=uuid4(), action={"kind": "plan_propose", "proposal": remote_proposal()})
    created = remote.dispatch(binding, request)
    assert created.kind == "lab", created
    plan = created.result.plan
    assert runtime._optimizer is None and runtime.worker().status()["pending"] == 0
    assert remote.dispatch(binding, request) == created
    altered = request.model_copy(deep=True)
    altered.action.proposal.goal.description = "Changed goal under the old identity"
    assert remote.dispatch(binding, altered).code == "request_conflict"
    approval = LabCommand(request_id=uuid4(), action={"kind": "plan_approve", "id": plan.id,
        "approval": {"expected_revision": plan.revision, "digest": plan.digest, "approve": True}}, mode="test")
    accepted = remote.dispatch(binding, approval)
    assert accepted.kind == "lab" and accepted.result.operation.id == plan.id
    assert remote.dispatch(binding, approval) == accepted
    def done():
        reply = action(remote, binding, {"kind": "discovery_report", "id": plan.id})
        assert reply.kind == "lab", reply
        report = reply.result.report
        return report if report.evidence.operation.status not in {"queued", "running"} else None
    report = wait(done)
    assert report.confirmed_failures == 2 and report.executions_used == 6
    child = report.evidence.trials[0].simulation_id
    assert action(remote, binding, {"kind": "run_get", "id": child}).result.record.simulation.id == child
    highlights = action(remote, binding, {"kind": "highlights", "id": child}).result.highlights
    frame = highlights.highlights[0].frame
    assert action(remote, binding, {"kind": "frame_get", "id": child, "frame": frame}).result.frame.frame == frame
    other = replace(binding, owner_id=uuid4(), generation=2)
    current[0] = other
    assert action(remote, other, {"kind": "plan_get", "id": plan.id}).code == "run_not_found"
    assert action(remote, other, {"kind": "run_get", "id": child}).code == "run_not_found"
    assert action(remote, binding, {"kind": "plan_get", "id": plan.id}).code == "permission_denied"


@pytest.mark.parametrize("change", ["budget", "agent", "graphics", "map_reload", "ticks", "duration"])
def test_remote_plan_limits_reject_before_saving_or_executing(lab, change):
    runtime, binding, current, remote = lab
    value = remote_proposal()
    if change == "budget": value.budget.max_executions = 13
    elif change == "agent": value.goal.agent_id = uuid4()
    elif change == "graphics": value.options.carla_quality = "Epic"
    elif change == "map_reload": value.options.allow_map_reload = True
    elif change == "ticks": value.options.max_ticks = 10001
    else: current[1] = current[1].model_copy(update={"max_duration_s": .05})
    reply = action(remote, binding, {"kind": "plan_propose", "proposal": value})
    assert reply.kind == "rejected" and reply.code == "policy_denied"
    assert runtime._optimizer is None
    assert action(remote, binding, {"kind": "plans"}).result.items == []


def test_remote_evidence_consent_and_explicit_mode_are_required(lab):
    runtime, binding, current, remote = lab
    current[1] = current[1].model_copy(update={"allow_evidence_read": False})
    assert action(remote, binding, {"kind": "gallery"}).code == "permission_denied"
    current[1] = current[1].model_copy(update={"allow_evidence_read": True})
    created = action(remote, binding, {"kind": "plan_propose", "proposal": remote_proposal()}).result.plan
    reply = action(remote, binding, {"kind": "plan_approve", "id": created.id,
        "approval": {"expected_revision": 1, "digest": created.digest, "approve": True}}, mode="carla")
    assert reply.code == "policy_denied" and runtime._optimizer is None


def test_revoked_local_approval_stops_future_autonomous_trials(settings):
    entered, release = Event(), Event()
    class Held(ContactAdapter):
        def open(self, recipe):
            entered.set()
            assert release.wait(15)
            return super().open(recipe)
    runtime = ApiRuntime(settings, runner_factory=lambda **kw: SimulationRunner(Held(), **kw))
    binding = RemoteBinding("https://coordinator.test", uuid4(), uuid4(), 1)
    current = [binding, DevicePolicy(scopes=list(PermissionScope), allow_evidence_read=True)]
    remote = RemoteService(runtime.worker(), lambda: tuple(current), optimizer_factory=runtime.optimizer)
    try:
        plan = action(remote, binding, {"kind": "plan_propose", "proposal": remote_proposal()}).result.plan
        accepted = action(remote, binding, {"kind": "plan_approve", "id": plan.id,
            "approval": {"expected_revision": 1, "digest": plan.digest, "approve": True}}, mode="test")
        assert accepted.kind == "lab" and entered.wait(5)
        with remote.lock:
            current[0] = replace(binding, generation=2)
        release.set()
        final = wait(lambda: (v if (v := runtime.optimizer().get(plan.id)).operation.status not in {"queued", "running"} else None))
        assert final.operation.status == "failed" and len(final.trials) == 1
        assert not runtime.worker().status()["pending"]
    finally:
        release.set()
        assert runtime.close()


def test_coordinator_negotiates_lab_and_never_persists_its_evidence(web):
    client, app, device, credential, alice, bob = web
    with app.state.store.transaction() as connection:
        policy = DevicePolicy(scopes=["status:read", "run:execute:test", "run:cancel", "result:read"], allow_evidence_read=True)
        connection.execute("UPDATE devices SET policy_json=? WHERE id=?", (policy.model_dump_json(), str(device)))
    request = LabCommand(request_id=uuid4(), action={"kind": "gallery"})
    url = f"/v1/devices/{device}/experimentation"
    assert client.post(url, json=request.model_dump(mode="json"), headers=bob).status_code == 404
    assert client.post(url, json=request.model_dump(mode="json")).status_code == 403
    with client.websocket_connect("wss://coordinator.test/v1/device/connect", headers={
        "Authorization": "Bearer " + credential, "X-Poltergeist-Lab": "v1"}) as socket:
        hello = socket.receive_json()
        assert hello["lab_supported"] is True
        envelope = {key: hello[key] for key in ("device_id", "ownership_generation", "connection_generation")}
        socket.send_text(canonical_json(ClientReady(**envelope, remote_execution_enabled=True, lab_supported=True)))
        await_available(app, device)
        assert client.get("/v1/browser/devices", headers=alice).json()[0]["connection"]["lab_supported"]
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(client.post, url, json=request.model_dump(mode="json"), headers=alice)
            delivered = parse_message(socket.receive_text())
            assert delivered.command == request
            reply = LabReply(request_id=request.request_id, result=LabGalleryResult(items=[]))
            socket.send_text(canonical_json(RelayReply(**envelope, reply=reply)))
            response = pending.result(timeout=10)
            assert response.status_code == 200 and response.json()["result"]["kind"] == "gallery"
        with app.state.store.transaction(write=False) as connection:
            assert connection.execute("SELECT count(*) FROM commands").fetchone()[0] == 0
        assert app.state.relay.transfer_count == 0
    # The durable command endpoint cannot be used to sidestep channel negotiation.
    assert client.post(f"/v1/devices/{device}/commands", json=request.model_dump(mode="json"), headers=alice).status_code == 400


def test_remote_suite_ownership_survives_definition_deletion(lab):
    runtime, binding, current, remote = lab
    scenario = remote_proposal()
    plan = action(remote, binding, {"kind": "plan_propose", "proposal": scenario}).result.plan
    definition = {"kind": "suite", "name": "Reusable braking checks", "mode": "test", "options": {"max_ticks": 10000},
        "cases": [{"name": "Braking approach", "scenario": plan.scenarios[0]}]}
    saved = action(remote, binding, {"kind": "suite_save", "save": {"definition": definition}}).result.suite
    identity = uuid4()
    launch = {"kind": "suite_run", "id": saved.id, "launch": {"request_id": identity, "expected_revision": 1}}
    accepted = action(remote, binding, launch, mode="test", identity=identity)
    assert accepted.kind == "lab", accepted
    done = wait(lambda: (v if (v := runtime.optimizer().get(identity)).operation.status == "completed" else None))
    history = action(remote, binding, {"kind": "suite_runs", "id": saved.id, "limit": 1}).result
    assert [row.id for row in history.items] == [identity] and history.next_offset is None
    stranger = replace(binding, owner_id=uuid4(), generation=2)
    current[0] = stranger
    assert action(remote, stranger, {"kind": "suite_runs", "id": saved.id}).code == "run_not_found"
    current[0] = binding
    assert action(remote, binding, {"kind": "suite_delete", "id": saved.id, "expected_revision": 1}).kind == "lab"
    assert action(remote, binding, {"kind": "suite_report", "id": identity}).result.report.evidence.operation.id == identity
    assert action(remote, binding, {"kind": "run_get", "id": done.trials[0].simulation_id}).kind == "lab"
    current[0] = other = replace(binding, owner_id=uuid4(), generation=2)
    assert action(remote, other, {"kind": "suite_report", "id": identity}).code == "run_not_found"


def test_lab_request_identity_cannot_be_reused_as_an_ordinary_execute(lab):
    from shared.models.remote import ExecuteCommand
    runtime, binding, _, remote = lab
    identity = uuid4()
    plan = action(remote, binding, {"kind": "plan_propose", "proposal": remote_proposal()}, identity=identity).result.plan
    reply = remote.dispatch(binding, ExecuteCommand(request_id=identity, scenario=plan.scenarios[0], mode="test"))
    assert reply.code == "request_conflict"
    assert runtime.worker().status()["pending"] == 0


def test_remote_zero_waiting_limit_delays_discovery_without_submitting_a_child(settings):
    entered, release = Event(), Event()
    class Held(ContactAdapter):
        def open(self, recipe):
            entered.set()
            assert release.wait(15)
            return super().open(recipe)
    runtime = ApiRuntime(settings, runner_factory=lambda **kw: SimulationRunner(Held(), **kw))
    binding = RemoteBinding("https://coordinator.test", uuid4(), uuid4(), 1)
    current = [binding, DevicePolicy(scopes=list(PermissionScope), allow_evidence_read=True, max_waiting_runs=0)]
    remote = RemoteService(runtime.worker(), lambda: tuple(current), optimizer_factory=runtime.optimizer)
    try:
        plan = action(remote, binding, {"kind": "plan_propose", "proposal": remote_proposal()}).result.plan
        from shared.models.remote import ExecuteCommand
        accepted = remote.dispatch(binding, ExecuteCommand(request_id=uuid4(), scenario=plan.scenarios[0], mode="test"))
        assert accepted.kind == "accepted" and entered.wait(5)
        assert action(remote, binding, {"kind": "plan_approve", "id": plan.id,
            "approval": {"expected_revision": 1, "digest": plan.digest, "approve": True}}, mode="test").kind == "lab"
        wait(lambda: runtime.optimizer().get(plan.id).operation.status == "running")
        # Synchronize with an actual blocked admission instead of sleeping.
        with remote.lock:
            assert runtime.optimizer().get(plan.id).trials == []
            assert runtime.worker().status()["pending"] == 0
        runtime.optimizer().cancel(plan.id)
        wait(lambda: runtime.optimizer().get(plan.id).operation.status == "cancelled")
        assert runtime.optimizer().get(plan.id).trials == []
    finally:
        release.set()
        assert runtime.close()


def test_remote_document_pages_freeze_creation_cutoff_and_return_valid_cursors(lab):
    runtime, binding, _, remote = lab
    first = action(remote, binding, {"kind": "plan_propose", "proposal": remote_proposal()}).result.plan
    page = action(remote, binding, {"kind": "plans", "limit": 1}).result
    assert page.items == [first] and page.next_offset is None
    second = action(remote, binding, {"kind": "plan_propose", "proposal": remote_proposal()}).result.plan
    frozen = action(remote, binding, {"kind": "plans", "limit": 2, "before": page.snapshot_at}).result
    assert frozen.items == [first] and frozen.next_offset is None
    all_values = action(remote, binding, {"kind": "plans", "limit": 1}).result
    assert all_values.items == [second] and all_values.next_offset == 1
    rest = action(remote, binding, {"kind": "plans", "limit": 1, "offset": 1, "before": all_values.snapshot_at}).result
    assert rest.items == [first] and rest.next_offset is None


def test_guided_acceptance_receipt_failure_rolls_back_child_and_retry_is_exact(lab, monkeypatch):
    from backend.experiment_repository import ExperimentRepository
    from backend.persistence_errors import ConflictError
    from backend.remote_receipts import RemoteReceiptRepository
    from backend.repositories import SimulationRepository
    from backend.services.experimentation_service import gallery
    runtime, binding, _, remote = lab
    command = LabCommand(request_id=uuid4(), mode="test", action={"kind": "run_launch", "launch": {
        "mode": "test", "scenario": gallery()[0].scenario.model_copy(update={"duration_s": 8.}), "options": {"max_ticks": 10000}}})
    original = ExperimentRepository.create
    def unavailable_receipt(repository, value):
        if repository.kind == "receipt":
            raise ConflictError("Fixture receipt capacity exhausted")
        return original(repository, value)
    with monkeypatch.context() as patch:
        patch.setattr(ExperimentRepository, "create", unavailable_receipt)
        assert remote.dispatch(binding, command).kind == "rejected"
    with runtime.persistence().database.session(write=False) as session:
        assert SimulationRepository(session).list() == []
        assert RemoteReceiptRepository(session).lookup(binding, command.request_id) is None
    assert runtime.worker().status()["pending"] == 0
    accepted = remote.dispatch(binding, command)
    assert accepted.kind == "lab", accepted
    identity = accepted.result.record.simulation.id
    wait(lambda: runtime.worker().get(identity).result)
    assert remote.dispatch(binding, command) == accepted
    with runtime.persistence().database.session(write=False) as session:
        assert len(SimulationRepository(session).list()) == 1
