"""Abrupt process loss must not replay claimed work or lose cancel receipts."""

import json
import subprocess
import sys
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from backend.database import Database
from backend.models import SimulationJobRecord, SimulationRecord
from backend.remote_receipts import RemoteBinding
from backend.services.persistence_service import PersistenceService
from backend.services.simulation_service import SimulationService
from backend.storage import ArtifactStore
from companion.remote_service import RemoteService
from shared.models.coordination import DevicePolicy, PermissionScope
from shared.models.remote import CancelCommand, ExecuteCommand, ResultCommand, RunResultReply
from simulator import SimulationRunner
from simulator.test_adapter import TestSimulationAdapter


CHILD = r'''
import json,os,sys
from pathlib import Path
from threading import Event
from backend.database import Database
from backend.remote_receipts import RemoteBinding
from backend.services.persistence_service import PersistenceService
from backend.services.simulation_service import SimulationService
from backend.storage import ArtifactStore
from companion.remote_service import RemoteService
from shared.models.coordination import DevicePolicy,PermissionScope
from shared.models.remote import CancelCommand,ExecuteCommand
from simulator import SimulationRunner
from simulator.test_adapter import TestSimulationAdapter
data=json.load(sys.stdin)
root=Path(data['root'])
binding=RemoteBinding(origin=data['origin'],device_id=__import__('uuid').UUID(data['device']),owner_id=__import__('uuid').UUID(data['owner']),generation=1)
policy=DevicePolicy(scopes=list(PermissionScope))
entered=Event()
class Held(TestSimulationAdapter):
 def open(self,scenario):
  if data['phase']=='opening':
   self.hold()
  return super().open(scenario)
 def step(self,control,commands):
  self.hold()
 def hold(self):
  entered.set()
  Event().wait(60)
  raise RuntimeError('Crash fixture was not terminated')
persistence=PersistenceService(Database('sqlite:///'+(root/'runs.db').as_posix()),ArtifactStore(root/'artifacts'))
worker=SimulationService(persistence,runner_factory=lambda **kwargs:SimulationRunner(Held(),**kwargs))
worker.start()
service=RemoteService(worker,lambda:(binding,policy))
first=ExecuteCommand.model_validate(data['first'])
second=ExecuteCommand.model_validate(data['second'])
accepted=service.dispatch(binding,first)
assert accepted.kind=='accepted'
assert entered.wait(20)
assert worker.get(accepted.simulation_id).simulation.status==('queued' if data['phase']=='opening' else 'running')
waiting=service.dispatch(binding,second)
assert waiting.kind=='accepted'
cancel=CancelCommand(request_id=data['cancel_request'],simulation_id=waiting.simulation_id if data['cancel_waiting'] else accepted.simulation_id)
cancellation=service.dispatch(binding,cancel)
assert cancellation.kind=='cancel_requested'
assert data['cancel_waiting'] or cancellation.terminal is False
print(json.dumps({'first':accepted.model_dump(mode='json'),'second':waiting.model_dump(mode='json'),
 'cancel':cancel.model_dump(mode='json'),'cancel_reply':cancellation.model_dump(mode='json')}),flush=True)
# This exits the actual Python worker, including through a Windows venv launcher.
# No Python finally block, service close, or object destructor can finalize it.
os._exit(79)
'''


@pytest.mark.parametrize("cancel_waiting", [False, True])
@pytest.mark.parametrize("phase", ["opening", "running"])
def test_actual_process_loss_fails_claimed_run_retains_waiter_and_replays_original_receipts(tmp_path, cancel_waiting, phase):
    binding = RemoteBinding("https://coordinator.example", uuid4(), uuid4(), 1)
    policy = DevicePolicy(scopes=list(PermissionScope))
    first = ExecuteCommand(request_id=uuid4(), scenario={"name": "Interrupted owned run", "duration_s": .1,
        "actors": [{"id": "ego", "type": "ego_vehicle"}]}, mode="test")
    second = ExecuteCommand(request_id=uuid4(), scenario={"name": "Durable waiting run", "duration_s": .1,
        "actors": [{"id": "ego", "type": "ego_vehicle"}]}, mode="test")
    data = {"root": str(tmp_path), "origin": binding.origin, "owner": str(binding.owner_id),
        "device": str(binding.device_id), "first": first.model_dump(mode="json"), "second": second.model_dump(mode="json"),
        "cancel_request": str(uuid4()), "cancel_waiting": cancel_waiting, "phase": phase}
    child = subprocess.run([sys.executable, "-u", "-c", CHILD], input=json.dumps(data),
        capture_output=True, text=True, timeout=45)
    assert child.returncode == 79, child.stderr
    receipts = json.loads(child.stdout)
    persistence = PersistenceService(Database(f"sqlite:///{(tmp_path / 'runs.db').as_posix()}"), ArtifactStore(tmp_path / "artifacts"))
    worker = SimulationService(persistence, runner_factory=lambda **kwargs: SimulationRunner(TestSimulationAdapter(), **kwargs))
    service = RemoteService(worker, lambda: (binding, policy))
    try:
        worker.start()
        first_id, second_id = receipts["first"]["simulation_id"], receipts["second"]["simulation_id"]
        deadline = monotonic() + 20
        while worker.get(second_id).result is None and monotonic() < deadline:
            Event().wait(.01)
        interrupted, recovered = worker.get(first_id), worker.get(second_id)
        assert interrupted.simulation.status == "failed"
        assert "interrupted" in interrupted.simulation.error
        assert interrupted.result.telemetry_frames == 0
        assert (interrupted.simulation.started_at is not None) is (phase == "running")
        assert recovered.simulation.status == ("cancelled" if cancel_waiting else "completed")
        assert recovered.result is not None
        # Even a lost acceptance/cancel acknowledgement cannot create a new run
        # or relabel the interrupted run as a successful cancellation.
        assert service.dispatch(binding, first).model_dump(mode="json") == receipts["first"]
        assert service.dispatch(binding, second).model_dump(mode="json") == receipts["second"]
        assert service.dispatch(binding, CancelCommand.model_validate(receipts["cancel"])).model_dump(mode="json") == receipts["cancel_reply"]
        result = service.dispatch(binding, ResultCommand(request_id=uuid4(), simulation_id=first_id))
        assert isinstance(result, RunResultReply)
        assert result.summary.status == "failed" and result.summary.error.code == "execution_failed"
        assert result.summary.metrics.coverage.total_frames == 0
        assert result.summary.cancel_requested is (not cancel_waiting)
        with persistence.database.session(write=False) as session:
            assert session.scalar(select(func.count()).select_from(SimulationRecord)) == 2
            assert session.scalar(select(func.count()).select_from(SimulationJobRecord)) == 2
    finally:
        assert worker.close()
        persistence.database.dispose()
