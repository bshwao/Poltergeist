"""A queued cancellation receipt must survive process loss before cleanup."""

import json
import subprocess
import sys
from threading import Event
from time import monotonic
from uuid import uuid4

from sqlalchemy import func, select

from backend.database import Database
from backend.models import RemoteCommandReceiptRecord, SimulationJobRecord, SimulationRecord
from backend.remote_receipts import RemoteBinding
from backend.services.persistence_service import PersistenceService
from backend.services.simulation_service import SimulationService
from backend.storage import ArtifactStore
from companion.remote_service import RemoteService
from shared.models.coordination import DevicePolicy, PermissionScope
from shared.models.remote import CancelCommand, ExecuteCommand, ResultCommand
from simulator import SimulationRunner
from simulator.test_adapter import TestSimulationAdapter


CHILD = r'''
import json,os,sys
from pathlib import Path
from threading import Event,Timer
# End the actual child on fixture failure, including through a venv launcher.
watchdog=Timer(30,lambda:os._exit(81))
watchdog.daemon=True
watchdog.start()
from backend.database import Database
from backend.job_repository import JobRepository
from backend.remote_receipts import RemoteBinding,RemoteReceiptRepository
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
binding=RemoteBinding(data['origin'],data['device'],data['owner'],1)
policy=DevicePolicy(scopes=list(PermissionScope))
entered=Event()
class Held(TestSimulationAdapter):
 def open(self,scenario):
  entered.set()
  Event().wait(60)
  raise RuntimeError('The fixture should exit before releasing its active run')
persistence=PersistenceService(Database('sqlite:///'+(root/'runs.db').as_posix()),ArtifactStore(root/'artifacts'))
worker=SimulationService(persistence,runner_factory=lambda **kw:SimulationRunner(Held(),**kw))
worker.start()
service=RemoteService(worker,lambda:(binding,policy))
first=service.dispatch(binding,ExecuteCommand.model_validate(data['first']))
assert first.kind=='accepted' and entered.wait(10)
second=service.dispatch(binding,ExecuteCommand.model_validate(data['second']))
assert second.kind=='accepted'
cancel=CancelCommand(request_id=data['cancel_request'],simulation_id=second.simulation_id)
def exit_before_queued_finalization(stored,*args,**kwargs):
 assert stored.simulation.id==second.simulation_id
 with persistence.database.session(write=False) as session:
  receipt=RemoteReceiptRepository(session).lookup(binding,cancel.request_id)
  assert receipt is not None and receipt.cancel_terminal is False
  assert JobRepository(session).get(second.simulation_id).cancel_requested
  ack=service._ack(receipt)
 print(json.dumps({'first':first.model_dump(mode='json'),'second':second.model_dump(mode='json'),
  'cancel':cancel.model_dump(mode='json'),'cancel_ack':ack.model_dump(mode='json')}),flush=True)
 os._exit(79)
worker._persist_empty=exit_before_queued_finalization
service.dispatch(binding,cancel)
raise AssertionError('The pre-finalization crash hook was not reached')
'''


def test_queued_cancel_receipt_crash_before_cleanup_recovers_exact_ack_and_final_result(tmp_path):
    binding = RemoteBinding("https://coordinator.example", uuid4(), uuid4(), 1)
    policy = DevicePolicy(scopes=list(PermissionScope))
    recipe = {"name": "Queued cancellation crash", "duration_s": .1,
              "actors": [{"id": "ego", "type": "ego_vehicle"}]}
    first = ExecuteCommand(request_id=uuid4(), scenario=recipe, mode="test")
    second = ExecuteCommand(request_id=uuid4(), scenario=recipe, mode="test")
    child = subprocess.run([sys.executable, "-u", "-c", CHILD], input=json.dumps({
        "root": str(tmp_path), "origin": binding.origin, "device": str(binding.device_id),
        "owner": str(binding.owner_id), "first": first.model_dump(mode="json"),
        "second": second.model_dump(mode="json"), "cancel_request": str(uuid4())}),
        capture_output=True, text=True, timeout=40)
    assert child.returncode == 79, child.stderr
    proof = json.loads(child.stdout)
    persistence = PersistenceService(Database(f"sqlite:///{(tmp_path / 'runs.db').as_posix()}"), ArtifactStore(tmp_path / "artifacts"))
    worker = SimulationService(persistence, runner_factory=lambda **kw: SimulationRunner(TestSimulationAdapter(), **kw))
    service = RemoteService(worker, lambda: (binding, policy))
    try:
        worker.start()
        first_id, cancelled_id = proof["first"]["simulation_id"], proof["second"]["simulation_id"]
        deadline = monotonic() + 15
        while worker.get(cancelled_id).result is None and monotonic() < deadline:
            Event().wait(.01)
        assert worker.get(first_id).simulation.status == "failed"
        assert worker.get(cancelled_id).simulation.status == "cancelled"
        assert worker.get(cancelled_id).result.telemetry_frames == 0
        assert service.dispatch(binding, first).model_dump(mode="json") == proof["first"]
        assert service.dispatch(binding, second).model_dump(mode="json") == proof["second"]
        assert service.dispatch(binding, CancelCommand.model_validate(proof["cancel"])).model_dump(mode="json") == proof["cancel_ack"]
        assert proof["cancel_ack"]["terminal"] is False
        result = service.dispatch(binding, ResultCommand(request_id=uuid4(), simulation_id=cancelled_id))
        assert result.summary.status == "cancelled" and result.summary.cancel_requested
        with persistence.database.session(write=False) as session:
            assert session.scalar(select(func.count()).select_from(SimulationRecord)) == 2
            assert session.scalar(select(func.count()).select_from(SimulationJobRecord)) == 2
            assert session.scalar(select(func.count()).select_from(RemoteCommandReceiptRecord)) == 3
    finally:
        assert worker.close()
        persistence.database.dispose()
