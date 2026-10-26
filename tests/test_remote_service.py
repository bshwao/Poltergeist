"""Remote acceptance uses real SQLite/worker transactions and safe projections."""

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier, Event
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from backend.database import Database
from backend.models import SimulationJobRecord, SimulationRecord
from backend.persistence_errors import ConflictError
from backend.remote_receipts import RemoteBinding, RemoteReceiptRepository
from backend.services.persistence_service import PersistenceService
from backend.services.simulation_service import SimulationService
from backend.storage import ArtifactStore
from companion.remote_service import RemoteService
from scenarios import validate_scenario
from shared.models.coordination import DevicePolicy, PermissionScope
from shared.models.remote import (CancelCommand, CommandRejected, ExecuteAccepted,
    ExecuteCommand, RemoteRunOptions, ResultCommand, RunResultReply, StatusCommand,
    canonical_json)
from simulator import SimulationRunner
from simulator.test_adapter import TestSimulationAdapter


def scene(name="Owned remote experiment", duration=.2):
    return validate_scenario({"name": name, "duration_s": duration,
        "actors": [{"id": "ego", "type": "ego_vehicle", "speed_mps": 2}]})


def execute(**kwargs):
    return ExecuteCommand(request_id=uuid4(), scenario=scene(), mode="test", **kwargs)


def wait_for(predicate):
    deadline = monotonic() + 15
    while monotonic() < deadline:
        result = predicate()
        if result:
            return result
        Event().wait(.01)
    raise AssertionError("The synthetic worker did not finish")


@pytest.fixture
def harness(tmp_path):
    persistence = PersistenceService(Database(f"sqlite:///{(tmp_path / 'runs.db').as_posix()}"),
                                     ArtifactStore(tmp_path / "artifacts"))
    binding = RemoteBinding("https://coordinator.example", uuid4(), uuid4(), 1)
    current = [binding, DevicePolicy(scopes=list(PermissionScope))]
    workers = []
    release = Event()

    def make(*, block=False, max_pending=10):
        entered = Event()

        class BlockingAdapter(TestSimulationAdapter):
            def open(self, scenario):
                entered.set()
                if not release.wait(15):
                    raise RuntimeError("Synthetic fixture was not released")
                return super().open(scenario)

        def factory(**kwargs):
            return SimulationRunner(BlockingAdapter() if block else TestSimulationAdapter(), **kwargs)

        worker = SimulationService(persistence, runner_factory=factory, max_pending=max_pending)
        worker.start()
        workers.append(worker)
        service = RemoteService(worker, lambda: tuple(current))
        return worker, service, entered

    yield binding, current, persistence, make, release
    release.set()
    for worker in workers:
        assert worker.close()
    persistence.database.dispose()


def count(persistence, model):
    with persistence.database.session(write=False) as session:
        return session.scalar(select(func.count()).select_from(model))


def test_lost_acceptance_reply_reuses_one_run_and_scoped_result(harness):
    binding, current, persistence, make, release = harness
    worker, service, _ = make()
    command = execute()
    first = service.dispatch(binding, command)
    assert isinstance(first, ExecuteAccepted)
    wait_for(lambda: worker.get(first.simulation_id).result)
    assert service.dispatch(binding, command) == first
    assert count(persistence, SimulationRecord) == count(persistence, SimulationJobRecord) == 1
    result_command = ResultCommand(request_id=uuid4(), simulation_id=first.simulation_id)
    result = service.dispatch(binding, result_command)
    assert isinstance(result, RunResultReply)
    assert result.summary.status == "completed" and result.summary.result_available
    assert result.summary.execute_request_id == command.request_id
    assert result.summary.evidence.state == "local_only"
    assert result.summary.evidence.artifact_reference_count > 0
    assert result.summary.metrics.coverage.total_frames > 0
    assert service.dispatch(binding, result_command).summary == result.summary
    serialized = canonical_json(result)
    assert str(persistence.artifacts.root) not in serialized
    assert "frames.ndjson" not in serialized and "agent_snapshot" not in serialized


def test_browser_integer_template_parameters_accept_once_with_the_normalized_recipe(harness):
    from scenarios.stress_presets import get_stress_preset
    from shared.models.remote import parse_message, payload_digest
    binding, current, persistence, make, release = harness
    worker, service, _ = make()
    recipe = get_stress_preset("sudden_braking-low").scenario().model_dump(mode="json")
    # JSON.stringify has no representation for an integral float. The browser
    # sends these parameters as integers even though the template uses floats.
    recipe["parameters"] = {key: int(value) if isinstance(value, float) and value.is_integer() else value
                            for key, value in recipe["parameters"].items()}
    request = {"kind": "execute", "request_id": str(uuid4()), "mode": "test", "scenario": recipe}
    command = parse_message(json.dumps(request), relay=False)
    first = service.dispatch(binding, command)
    assert isinstance(first, ExecuteAccepted)
    stored = worker.get(first.simulation_id)
    assert payload_digest(stored.simulation.scenario) == payload_digest(command.scenario)
    assert service.dispatch(binding, parse_message(json.dumps(request), relay=False)) == first
    assert count(persistence, SimulationRecord) == count(persistence, SimulationJobRecord) == 1
    wait_for(lambda: worker.get(first.simulation_id).result)


def test_changed_request_content_conflicts_without_another_job(harness):
    binding, current, persistence, make, release = harness
    worker, service, _ = make()
    command = execute()
    accepted = service.dispatch(binding, command)
    changed = command.model_copy(update={"scenario": scene("Changed recipe")})
    reply = service.dispatch(binding, changed)
    assert reply.kind == "rejected" and reply.code == "request_conflict"
    assert count(persistence, SimulationRecord) == 1
    assert service.dispatch(binding, CancelCommand(request_id=command.request_id,
        simulation_id=accepted.simulation_id)).code == "request_conflict"


def test_finalized_metadata_without_artifact_references_stays_missing(harness):
    binding, current, persistence, make, release = harness
    worker, service, _ = make()
    accepted = service.dispatch(binding, execute())
    finalized = wait_for(lambda: worker.get(accepted.simulation_id).result)
    assert finalized.artifacts and finalized.telemetry_frames > 0
    # Simulate an older/incomplete persisted metadata record. Existing bytes
    # aren't probed through guessed paths or presented as available evidence.
    with persistence.database.session() as session:
        row = session.get(SimulationRecord, str(accepted.simulation_id))
        payload = json.loads(row.result)
        payload["artifacts"] = []
        row.result = json.dumps(payload)
    reply = service.dispatch(binding, ResultCommand(request_id=uuid4(), simulation_id=accepted.simulation_id))
    assert reply.kind == "result"
    assert reply.summary.evidence.state == "missing"
    assert reply.summary.evidence.telemetry_frames == finalized.telemetry_frames
    assert reply.summary.evidence.artifact_reference_count == 0


def test_arbitrary_local_uuid_and_swapped_binding_cannot_read_or_cancel(harness):
    binding, current, persistence, make, release = harness
    worker, service, _ = make(block=True)
    local = worker.submit(scene("Local only"), mode="test")
    for command in (CancelCommand(request_id=uuid4(), simulation_id=local.simulation.id),
                    ResultCommand(request_id=uuid4(), simulation_id=local.simulation.id)):
        assert service.dispatch(binding, command).code == "run_not_found"
    assert worker.job_info(local.simulation.id)["cancel_requested"] is False
    foreign = RemoteBinding(binding.origin, binding.device_id, uuid4(), binding.generation)
    assert service.dispatch(foreign, execute()).code == "permission_denied"
    assert count(persistence, SimulationRecord) == 1


@pytest.mark.parametrize("scope", list(PermissionScope))
def test_each_operation_rechecks_approved_local_scope(harness, scope):
    binding, current, persistence, make, release = harness
    worker, service, _ = make()
    current[1] = DevicePolicy(scopes=[s for s in PermissionScope if s != scope])
    command = {PermissionScope.STATUS: StatusCommand(request_id=uuid4()),
        PermissionScope.RESULT: ResultCommand(request_id=uuid4(), simulation_id=uuid4()),
        PermissionScope.CANCEL: CancelCommand(request_id=uuid4(), simulation_id=uuid4()),
        PermissionScope.EXECUTE_TEST: execute(),
        PermissionScope.EXECUTE_CARLA: execute().model_copy(update={"mode": "carla"})}[scope]
    assert service.dispatch(binding, command).code == "permission_denied"
    assert count(persistence, SimulationRecord) == 0


def test_zero_waiting_policy_only_accepts_idle_slot_and_replays_while_busy(harness):
    binding, current, persistence, make, release = harness
    current[1] = DevicePolicy(scopes=list(PermissionScope), max_waiting_runs=0)
    worker, service, entered = make(block=True)
    command = execute()
    first = service.dispatch(binding, command)
    assert first.kind == "accepted" and entered.wait(5)
    assert service.dispatch(binding, execute()).code == "queue_full"
    assert service.dispatch(binding, command) == first
    assert count(persistence, SimulationRecord) == 1


def test_quota_excludes_active_and_cancelled_waiters_and_cancel_sequence_persists(harness):
    binding, current, persistence, make, release = harness
    current[1] = DevicePolicy(scopes=list(PermissionScope), max_waiting_runs=1)
    worker, service, entered = make(block=True)
    active = service.dispatch(binding, execute())
    assert entered.wait(5)
    waiting = service.dispatch(binding, execute())
    assert waiting.kind == "accepted"
    assert service.dispatch(binding, execute()).code == "queue_full"
    status = service.dispatch(binding, StatusCommand(request_id=uuid4()))
    assert status.waiting_remote_runs == 1 and status.active
    before = service.dispatch(binding, ResultCommand(request_id=uuid4(), simulation_id=active.simulation_id))
    cancel = CancelCommand(request_id=uuid4(), simulation_id=active.simulation_id)
    reply = service.dispatch(binding, cancel)
    assert reply.kind == "cancel_requested" and reply.terminal is False
    after = service.dispatch(binding, ResultCommand(request_id=uuid4(), simulation_id=active.simulation_id))
    assert after.summary.revision == before.summary.revision
    assert after.summary.cancel_requested is True
    assert after.summary.receipt_revision > before.summary.receipt_revision
    queued_cancel = CancelCommand(request_id=uuid4(), simulation_id=waiting.simulation_id)
    # The durable ACK predates queued-run cleanup; replay never substitutes the
    # later terminal state. Read the separate result for that final outcome.
    assert service.dispatch(binding, queued_cancel).terminal is False
    assert service.dispatch(binding, queued_cancel).terminal is False
    assert service.dispatch(binding, ResultCommand(request_id=uuid4(), simulation_id=waiting.simulation_id)).summary.status == "cancelled"
    assert service.dispatch(binding, execute()).kind == "accepted"


def test_policy_rejects_duration_and_exact_runner_ticks_before_queue_write(harness):
    binding, current, persistence, make, release = harness
    current[1] = DevicePolicy(scopes=list(PermissionScope), max_duration_s=.15, max_ticks=3)
    worker, service, _ = make()
    request = execute(options=RemoteRunOptions(max_ticks=3))
    assert service.dispatch(binding, request).code == "policy_denied"
    current[1] = DevicePolicy(scopes=list(PermissionScope), max_duration_s=1., max_ticks=3)
    assert service.dispatch(binding, request).code == "policy_denied"
    assert count(persistence, SimulationRecord) == 0


def test_competing_lost_ack_rolls_back_losing_tentative_run(harness):
    binding, current, persistence, make, release = harness
    worker, first_service, entered = make(block=True, max_pending=1)
    second_service = RemoteService(worker, lambda: tuple(current))
    barrier = Barrier(2)
    command = execute()
    for service in (first_service, second_service):
        original = service._lookup
        seen = [False]

        def lookup(*args, original=original, seen=seen):
            result = original(*args)
            if not seen[0]:
                seen[0] = True
                barrier.wait(5)
            return result

        service._lookup = lookup
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda s: s.dispatch(binding, command), (first_service, second_service)))
    assert all(reply.kind == "accepted" for reply in replies)
    assert replies[0] == replies[1]
    assert count(persistence, SimulationRecord) == count(persistence, SimulationJobRecord) == 1


def test_receipt_callback_failure_rolls_back_job_and_run(harness, monkeypatch):
    binding, current, persistence, make, release = harness
    worker, service, _ = make()
    original = RemoteReceiptRepository.create

    def failed(*args, **kwargs):
        original(*args, **kwargs)
        raise ConflictError("Injected post-insert failure with private local details")

    monkeypatch.setattr(RemoteReceiptRepository, "create", failed)
    assert service.dispatch(binding, execute()).code == "request_conflict"
    assert count(persistence, SimulationRecord) == count(persistence, SimulationJobRecord) == 0
    assert worker.status()["pending"] == 0


def test_acceptance_survives_worker_restart_and_scope_loss_denies_old_result(harness):
    binding, current, persistence, make, release = harness
    worker, service, _ = make()
    command = execute()
    accepted = service.dispatch(binding, command)
    wait_for(lambda: worker.get(accepted.simulation_id).result)
    assert worker.close()
    restarted, new_service, _ = make()
    assert new_service.dispatch(binding, command) == accepted
    assert count(persistence, SimulationRecord) == 1
    current[1] = DevicePolicy(scopes=[PermissionScope.STATUS])
    assert new_service.dispatch(binding, command).code == "permission_denied"
    assert new_service.dispatch(binding, ResultCommand(request_id=uuid4(),
        simulation_id=accepted.simulation_id)).code == "permission_denied"
