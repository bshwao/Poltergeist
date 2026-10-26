"""Real SQLite writers order cancellation against finalized output publication."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Timer, get_ident, local
from uuid import uuid4

from time import monotonic, sleep

if sys.argv[1:] == ["--crash-child", "--source-checkout"]:
    assert not (Path(sys.executable).resolve().parent.parent / "bundle.json").is_file()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import event, func, select

from backend.database import Database
from backend.job_repository import JobRepository
from backend.models import RemoteCommandReceiptRecord, SimulationJobRecord, SimulationRecord
from backend.remote_receipts import RemoteBinding, RemoteReceiptRepository
from backend.repositories import SimulationRepository
from backend.services.persistence_service import PersistenceService
from backend.services.simulation_service import SimulationService
from backend.storage import ArtifactStore
from companion.remote_service import RemoteService
from shared.models.coordination import DevicePolicy, PermissionScope
from shared.models.remote import CancelCommand, ExecuteCommand, ResultCommand
from simulator import SimulationRunner
from simulator.test_adapter import TestSimulationAdapter
from scenarios import validate_scenario


def scene(name):
    return validate_scenario({"name": name, "duration_s": .2,
        "actors": [{"id": "ego", "type": "ego_vehicle", "speed_mps": 2}]})


def wait_for(predicate):
    deadline = monotonic() + 15
    while monotonic() < deadline:
        value = predicate()
        if value:
            return value
        sleep(.01)
    raise AssertionError("The finalization fixture did not finish")


def check_writer_order(tmp_path, monkeypatch, winner):
    assert winner in ("finalization", "cancellation")
    persistence = PersistenceService(Database(f"sqlite:///{(tmp_path / 'runs.db').as_posix()}"), ArtifactStore(tmp_path / "artifacts"))
    binding = RemoteBinding("https://coordinator.example", uuid4(), uuid4(), 1)
    policy = DevicePolicy(scopes=list(PermissionScope))
    output_ready, publish = Event(), Event()
    finalizer_ready, finalizer_attempted, finalizer_written = Event(), Event(), Event()
    allow_finalizer, allow_final_commit = Event(), Event()
    cancel_attempted, cancel_written, allow_cancel_commit = Event(), Event(), Event()
    next_running, finish_next = Event(), Event()
    target_finalizer, cancel_thread = local(), []
    runner_cancellations, workers = [], []
    cancel_request = []
    if winner == "finalization":
        allow_finalizer.set()
    else:
        allow_final_commit.set()

    class Adapter(TestSimulationAdapter):
        def open(self, scenario):
            if scenario.name == "Next independent run":
                next_running.set()
                assert finish_next.wait(20), "The next-run fixture was not released"
            return super().open(scenario)

    class Runner(SimulationRunner):
        def run(self, source, **kwargs):
            self.run_name = source.scenario.name
            return super().run(source, **kwargs)

        def cancel(self):
            runner_cancellations.append(self.run_name)
            super().cancel()

    original_persist = persistence.persist_output
    def persist(output, **kwargs):
        selected = output.result.simulation.scenario.name == "Finalizing original run"
        if selected:
            output_ready.set()
            assert publish.wait(20), "The output-publication fixture was not released"
        target_finalizer.selected = selected
        try:
            return original_persist(output, **kwargs)
        finally:
            target_finalizer.selected = False
    monkeypatch.setattr(persistence, "persist_output", persist)

    original_complete = SimulationRepository.complete
    def complete(repository, result, **kwargs):
        stored = original_complete(repository, result, **kwargs)
        if getattr(target_finalizer, "selected", False):
            finalizer_written.set()
            assert allow_final_commit.wait(20), "The final-result writer was not released"
        return stored
    monkeypatch.setattr(SimulationRepository, "complete", complete)

    original_create = RemoteReceiptRepository.create
    def create(repository, bound, request, operation, digest, simulation_id, **kwargs):
        receipt = original_create(repository, bound, request, operation, digest, simulation_id, **kwargs)
        if cancel_request and request == cancel_request[0]:
            cancel_written.set()
            if winner == "cancellation":
                assert allow_cancel_commit.wait(20), "The cancellation writer was not released"
        return receipt
    monkeypatch.setattr(RemoteReceiptRepository, "create", create)

    def make():
        worker = SimulationService(persistence, runner_factory=lambda **kwargs: Runner(Adapter(), **kwargs))
        worker.start()
        workers.append(worker)
        return worker, RemoteService(worker, lambda: (binding, policy))

    worker, service = make()
    def before_sql(connection, cursor, statement, parameters, context, executemany):
        if statement != "BEGIN IMMEDIATE":
            return
        if getattr(target_finalizer, "selected", False):
            finalizer_ready.set()
            assert allow_finalizer.wait(20), "The finalizer BEGIN fixture was not released"
            finalizer_attempted.set()
        elif cancel_thread and get_ident() == cancel_thread[0]:
            cancel_attempted.set()
    event.listen(persistence.database.engine, "before_cursor_execute", before_sql)
    try:
        first_command = ExecuteCommand(request_id=uuid4(), scenario=scene("Finalizing original run"), mode="test")
        first = service.dispatch(binding, first_command)
        assert first.kind == "accepted" and output_ready.wait(10)
        second_command = ExecuteCommand(request_id=uuid4(), scenario=scene("Next independent run"), mode="test")
        second = service.dispatch(binding, second_command)
        assert second.kind == "accepted"
        command = CancelCommand(request_id=uuid4(), simulation_id=first.simulation_id)
        cancel_request.append(command.request_id)
        publish.set()
        assert finalizer_ready.wait(10)
        if winner == "finalization":
            assert finalizer_written.wait(10)

        def cancel():
            cancel_thread.append(get_ident())
            return service.dispatch(binding, command)

        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(cancel)
            try:
                if winner == "finalization":
                    assert cancel_attempted.wait(10)
                else:
                    assert cancel_written.wait(10)
                    allow_finalizer.set()
                    assert finalizer_attempted.wait(10)
                assert not pending.done()
                # Independent readers cannot see either uncommitted terminal
                # output or an uncommitted cancellation/receipt.
                with persistence.database.session(write=False) as session:
                    assert SimulationRepository(session).get(first.simulation_id).result is None
                    assert not JobRepository(session).get(first.simulation_id).cancel_requested
                    assert RemoteReceiptRepository(session).lookup(binding, command.request_id) is None
                allow_final_commit.set()
                allow_cancel_commit.set()
                ack = pending.result(timeout=10)
            finally:
                allow_finalizer.set()
                allow_final_commit.set()
                allow_cancel_commit.set()

        assert ack.kind == "cancel_requested" and ack.terminal is (winner == "finalization")
        assert next_running.wait(10)
        assert service.dispatch(binding, command) == ack
        # A new cancellation for the completed original is a terminal no-op,
        # even though the next run currently owns the worker.
        late = service.dispatch(binding, CancelCommand(request_id=uuid4(), simulation_id=first.simulation_id))
        assert late.kind == "cancel_requested" and late.terminal is True
        with persistence.database.session(write=False) as session:
            assert not JobRepository(session).get(second.simulation_id).cancel_requested
        assert runner_cancellations == (["Finalizing original run"] if winner == "cancellation" else [])
        finish_next.set()
        wait_for(lambda: worker.get(second.simulation_id).result)
        for accepted in (first, second):
            result = service.dispatch(binding, ResultCommand(request_id=uuid4(), simulation_id=accepted.simulation_id))
            assert result.summary.status == "completed" and result.summary.metrics.coverage.total_frames == 5
            assert result.summary.cancel_requested is (accepted == first and winner == "cancellation")
            for ref in worker.get(accepted.simulation_id).result.artifacts:
                persistence.artifacts.validate_reference(ref, simulation_id=accepted.simulation_id)
        assert worker.close()
        restarted, service = make()
        assert service.dispatch(binding, first_command) == first
        assert service.dispatch(binding, second_command) == second
        assert service.dispatch(binding, command) == ack
        with persistence.database.session(write=False) as session:
            for model, expected in ((SimulationRecord, 2), (SimulationJobRecord, 2), (RemoteCommandReceiptRecord, 4)):
                assert session.scalar(select(func.count()).select_from(model)) == expected
        assert restarted.status()["accepting"]
        return {"first_writer": winner, "original_cancel_terminal": ack.terminal,
            "original_final_status": "completed", "original_cancel_requested": winner == "cancellation",
            "next_run_status": "completed", "next_run_cancel_requested": False,
            "runner_cancellation_targets": list(runner_cancellations),
            "frames_per_run": 5, "runs": 2, "jobs": 2, "receipts": 4,
            "uncommitted_writes_hidden_from_independent_reader": True,
            "original_ack_unchanged_after_next_run_and_restart": True,
            "artifacts_verified": True, "worker_accepting_after_restart": True}
    finally:
        publish.set()
        allow_finalizer.set()
        allow_final_commit.set()
        allow_cancel_commit.set()
        finish_next.set()
        event.remove(persistence.database.engine, "before_cursor_execute", before_sql)
        for item in workers:
            assert item.close()
        persistence.database.dispose()


class Patch:
    """Minimal reversible fixture patches when pytest is absent from a bundle."""
    def __init__(self):
        self.changes = []

    def setattr(self, target, name, value):
        original = vars(target).get(name)
        self.changes.append((target, name, name in vars(target), original))
        setattr(target, name, value)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        for target, name, existed, original in reversed(self.changes):
            if existed:
                setattr(target, name, original)
            else:
                delattr(target, name)


def crash_child():
    data = json.loads(sys.stdin.buffer.read(4097))
    assert data["boundary"] in ("before_commit", "after_commit")
    watchdog = Timer(45, lambda: os._exit(81))
    watchdog.daemon = True
    watchdog.start()
    root = Path(data["root"])
    binding = RemoteBinding("https://coordinator.example", data["device"], data["owner"], 1)
    policy = DevicePolicy(scopes=list(PermissionScope))
    persistence = PersistenceService(Database(f"sqlite:///{(root / 'runs.db').as_posix()}"), ArtifactStore(root / "artifacts"))
    worker = SimulationService(persistence, runner_factory=lambda **kwargs: SimulationRunner(TestSimulationAdapter(), **kwargs))
    service = RemoteService(worker, lambda: (binding, policy))
    output_ready, publish = Event(), Event()
    state = {}

    def crash(result):
        assert state and result.artifacts
        for reference in result.artifacts:
            persistence.artifacts.validate_reference(reference, simulation_id=result.simulation.id)
        receipt = dict(state, boundary=data["boundary"], actual_process_id=os.getpid(), python_executable=sys.executable,
            published_artifacts=[ref.model_dump(mode="json") for ref in result.artifacts],
            modules={name: sys.modules[name].__file__ for name in
                ("backend.services.simulation_service", "companion.remote_service", "simulator.simulation_runner")})
        (root / "crash.json").write_text(json.dumps(receipt) + "\n", encoding="utf-8")
        # No Python finally block, worker close or destructor can finalize this.
        os._exit(79)

    original_persist = persistence.persist_output
    def persist(output, **kwargs):
        if output.result.simulation.scenario.name != "Crashing finalizer":
            return original_persist(output, **kwargs)
        output_ready.set()
        assert publish.wait(20)
        saved = original_persist(output, **kwargs)
        if data["boundary"] == "after_commit":
            crash(saved.result)
        return saved
    persistence.persist_output = persist
    original_complete = SimulationRepository.complete
    def complete(repository, result, **kwargs):
        saved = original_complete(repository, result, **kwargs)
        if result.simulation.scenario.name == "Crashing finalizer" and data["boundary"] == "before_commit":
            # Artifacts and SQL writes exist, but the owning transaction has
            # not returned from this hook and therefore cannot have committed.
            crash(result)
        return saved
    SimulationRepository.complete = complete
    worker.start()
    first = service.dispatch(binding, ExecuteCommand(request_id=data["first"], scenario=scene("Crashing finalizer"), mode="test"))
    assert first.kind == "accepted" and output_ready.wait(20)
    second = service.dispatch(binding, ExecuteCommand(request_id=data["second"], scenario=scene("Surviving queued run"), mode="test"))
    assert second.kind == "accepted"
    command = CancelCommand(request_id=data["cancel"], simulation_id=first.simulation_id)
    ack = service.dispatch(binding, command)
    assert ack.kind == "cancel_requested" and ack.terminal is False
    state.update(first=first.model_dump(mode="json"), second=second.model_dump(mode="json"),
        cancel=command.model_dump(mode="json"), cancel_ack=ack.model_dump(mode="json"))
    publish.set()
    Event().wait(40)
    raise AssertionError("The finalizer crash hook was not reached")


def check_crash_boundary(root, boundary):
    from companion.enrollment_process import _ProcessTree
    from shared.models import ArtifactReference

    root.mkdir(parents=True, exist_ok=False)
    data = {"root": str(root), "boundary": boundary, "device": str(uuid4()), "owner": str(uuid4()),
        "first": str(uuid4()), "second": str(uuid4()), "cancel": str(uuid4())}
    payload = json.dumps(data).encode()
    assert len(payload) < 1024
    tree = process = None
    with (root / "child.log").open("wb") as log:
        try:
            source_arguments = [] if (Path(sys.executable).resolve().parent.parent / "bundle.json").is_file() else ["--source-checkout"]
            process = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--crash-child", *source_arguments],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log,
                creationflags=(subprocess.CREATE_NO_WINDOW | 0x4) if os.name == "nt" else 0, start_new_session=os.name != "nt")
            tree = _ProcessTree(process)
            tree.resume()
            process.communicate(input=payload, timeout=60)
            assert process.returncode == 79, "See the owned finalizer child.log"
        finally:
            if tree is not None:
                tree.request_stop()
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
            if tree is not None:
                assert tree.finish(monotonic() + 5), "The owned finalizer process tree did not exit"
    proof = json.loads((root / "crash.json").read_bytes())
    assert proof["boundary"] == boundary and Path(proof["python_executable"]).resolve() == Path(sys.executable).resolve()
    assert all(Path(path).resolve() == Path(sys.modules[name].__file__).resolve() for name, path in proof["modules"].items())
    binding = RemoteBinding("https://coordinator.example", data["device"], data["owner"], 1)
    policy = DevicePolicy(scopes=list(PermissionScope))
    persistence = PersistenceService(Database(f"sqlite:///{(root / 'runs.db').as_posix()}"), ArtifactStore(root / "artifacts"))
    workers = []
    def make():
        worker = SimulationService(persistence, runner_factory=lambda **kwargs: SimulationRunner(TestSimulationAdapter(), **kwargs))
        worker.start()
        workers.append(worker)
        return worker, RemoteService(worker, lambda: (binding, policy))
    try:
        worker, service = make()
        first_id, second_id = proof["first"]["simulation_id"], proof["second"]["simulation_id"]
        wait_for(lambda: worker.get(second_id).result)
        first = worker.get(first_id)
        expected = "failed" if boundary == "before_commit" else "completed"
        assert first.simulation.status == expected and first.result.telemetry_frames == (0 if expected == "failed" else 5)
        assert worker.get(second_id).simulation.status == "completed" and worker.get(second_id).result.telemetry_frames == 5
        published = [ArtifactReference.model_validate(value) for value in proof["published_artifacts"]]
        for ref in (*published, *first.result.artifacts, *worker.get(second_id).result.artifacts):
            persistence.artifacts.validate_reference(ref, simulation_id=first_id if ref in published or ref in first.result.artifacts else second_id)
        original_paths = {ref.path for ref in published}
        recovered_paths = {ref.path for ref in first.result.artifacts}
        assert (original_paths == recovered_paths) if boundary == "after_commit" else original_paths.isdisjoint(recovered_paths)
        commands = [ExecuteCommand(request_id=data[key], scenario=scene(name), mode="test")
            for key, name in (("first", "Crashing finalizer"), ("second", "Surviving queued run"))]
        for command, key in zip(commands, ("first", "second")):
            assert service.dispatch(binding, command).model_dump(mode="json") == proof[key]
        cancel = CancelCommand.model_validate(proof["cancel"])
        assert service.dispatch(binding, cancel).model_dump(mode="json") == proof["cancel_ack"]
        summary = service.dispatch(binding, ResultCommand(request_id=uuid4(), simulation_id=first_id)).summary
        assert summary.status == expected and summary.cancel_requested
        revision = first.revision
        assert worker.close()
        restarted, service = make()
        assert restarted.get(first_id).revision == revision
        assert service.dispatch(binding, cancel).model_dump(mode="json") == proof["cancel_ack"]
        with persistence.database.session(write=False) as session:
            for model, expected_count in ((SimulationRecord, 2), (SimulationJobRecord, 2), (RemoteCommandReceiptRecord, 3)):
                assert session.scalar(select(func.count()).select_from(model)) == expected_count
        return {"boundary": boundary, "actual_child_exit_code": 79, "owned_process_tree_exited": True,
            "child_product_imports_match_parent": True,
            "original_status": expected, "original_frames": first.result.telemetry_frames,
            "surviving_waiter_status": "completed", "surviving_waiter_frames": 5, "original_cancel_terminal": False,
            "cancel_ack_replayed_unchanged": True, "result_revision_unchanged_on_second_restart": True,
            "runs": 2, "jobs": 2, "receipts": 3, "published_artifacts_verified": len(published),
            "uncommitted_original_artifacts_retained_outside_recovered_result": boundary == "before_commit"}
    finally:
        for worker in workers:
            assert worker.close()
        persistence.database.dispose()


def main():
    import argparse
    from scripts.portable import verify_bundle

    parser = argparse.ArgumentParser(description="Private runtime finalization-order proof; synthetic only")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    bundle, output = args.bundle.resolve(), args.output.resolve()
    assert bundle.is_relative_to(root / "validation-evidence/portable-dist")
    assert Path(sys.executable).resolve() == bundle / "runtime/python.exe"
    assert sys.flags.isolated and sys.flags.dont_write_bytecode and not sys.flags.optimize
    assert output.is_relative_to(root / "validation-evidence") and not output.is_relative_to(bundle)
    output.mkdir(parents=True, exist_ok=False)
    receipt = {"schema_version": "poltergeist-private-finalization-order-v1", "passed": False,
        "started_at": datetime.now(UTC).isoformat(), "bundle_manifest_sha256": hashlib.sha256((bundle / "bundle.json").read_bytes()).hexdigest(),
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "python_version": sys.version.split()[0],
        "scope": "Direct RemoteService with test-approved binding, synthetic adapter and disposable databases; no GUI consent, TLS, CARLA, physical sleep or external-machine claim."}
    try:
        receipt["integrity_before"] = verify_bundle(bundle)
        site = bundle / "runtime/Lib/site-packages"
        names = set()
        for package in ("backend", "shared", "simulator", "scenarios", "evaluation", "optimization", "scripts", "companion"):
            for source in (root / package).rglob("*.py"):
                relative = source.relative_to(root)
                if relative.as_posix() == "scripts/verify_coordinator.py":
                    continue
                names.add(relative.as_posix())
                assert source.read_bytes() == (site / relative).read_bytes()
        receipt["source_python_files_matched"] = len(names)
        receipt["cases"] = []
        for winner in ("finalization", "cancellation"):
            with Patch() as patch:
                receipt["cases"].append(check_writer_order(output / winner, patch, winner))
        receipt["process_loss"] = [check_crash_boundary(output / ("crash-" + boundary), boundary)
            for boundary in ("before_commit", "after_commit")]
        receipt["provenance"] = {name: str(Path(module.__file__).resolve()) for name, module in list(sys.modules.items())
            if name.split(".")[0] in ("backend", "shared", "simulator", "scenarios", "evaluation", "optimization", "scripts", "companion")
            and getattr(module, "__file__", None)}
        assert receipt["provenance"] and all(Path(path).is_relative_to(site) for path in receipt["provenance"].values())
        receipt["integrity_after"] = verify_bundle(bundle)
        receipt["passed"] = True
        return 0
    except BaseException as error:
        receipt["error_type"] = type(error).__name__
        return 1
    finally:
        receipt["finished_at"] = datetime.now(UTC).isoformat()
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"passed": receipt["passed"], "receipt": str(output / "receipt.json")}), flush=True)


if __name__ == "__main__":
    raise SystemExit(crash_child() if sys.argv[1:] in (["--crash-child"], ["--crash-child", "--source-checkout"]) else main())
