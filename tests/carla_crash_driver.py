"""Opt-in real CARLA fault witnesses, run against an owned test server.

The caller owns simulator startup/restart and independently checks RPC state.
Stages use real companion profiles and normal CARLA runners. Witness hooks
pause at native progress or finalization, or exit the process after durable
running state.
No hosted account or native GUI acceptance is implied by this local driver.
"""

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
from threading import Event
from time import monotonic
from uuid import uuid4

if "--bundle" not in sys.argv:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.dependencies import ApiRuntime
from companion.controller import CompanionController
from companion.remote_service import RemoteService
from backend.remote_receipts import RemoteBinding
from scenarios import build_scenario
from shared.models.coordination import DevicePolicy, PermissionScope
from shared.models.remote import CancelCommand, ExecuteCommand, ResultCommand


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def wait_result(worker, simulation):
    deadline = monotonic() + 40
    while monotonic() < deadline:
        stored = worker.get(simulation)
        if stored.result is not None:
            return stored
        Event().wait(.02)
    raise AssertionError("The owned native recovery run did not finalize")


def bundled_provenance(bundle):
    if bundle is None:
        return None
    assert sys.flags.isolated and Path(sys.executable).resolve() == bundle / "runtime/python.exe"
    assert all(Path(path).resolve().is_relative_to(bundle) for path in sys.path)
    prefixes = {"backend", "shared", "simulator", "scenarios", "evaluation", "optimization", "scripts", "companion", "carla"}
    imported = {name: str(Path(module.__file__).resolve()) for name, module in sys.modules.copy().items()
                if name.split(".")[0] in prefixes and getattr(module, "__file__", None)}
    assert imported and all(Path(path).is_relative_to(bundle) for path in imported.values())
    return {"bundle_manifest_sha256": hashlib.sha256((bundle / "bundle.json").read_bytes()).hexdigest(),
            "interpreter": str(Path(sys.executable).resolve()), "imported_product_paths": imported}


def cancel_and_verify(controller, remote, binding, command, acceptance, holding, release, *, finalizing):
    """Cancel at a held native tick or after the native runner built its output."""
    worker = controller._worker
    if not holding.wait(30):
        simulation = worker.get(acceptance.simulation_id).simulation
        raise AssertionError(f"Native cancellation boundary was not reached: {simulation.status}: {simulation.error}")
    assert worker.get(acceptance.simulation_id).result is None
    next_command = ExecuteCommand(request_id=uuid4(), mode="carla",
        scenario=command.scenario.model_copy(update={"name": "Independent native follow-up"}))
    next_acceptance = remote.dispatch(binding, next_command)
    assert next_acceptance.kind == "accepted"
    cancel = CancelCommand(request_id=uuid4(), simulation_id=acceptance.simulation_id)
    acknowledgement = remote.dispatch(binding, cancel)
    assert acknowledgement.kind == "cancel_requested" and acknowledgement.terminal is False
    release.set()
    first = wait_result(worker, acceptance.simulation_id)
    following = wait_result(worker, next_acceptance.simulation_id)
    expected = "completed" if finalizing else "cancelled"
    assert first.simulation.status == expected, first.simulation.error
    assert following.simulation.status == "completed", following.simulation.error
    assert first.result.telemetry_frames == (11 if finalizing else 2)
    assert following.result.telemetry_frames == 11
    for stored in (first, following):
        assert stored.result.runtime.adapter == "carla" and stored.result.runtime.server_version == "0.9.16"
        assert stored.result.artifacts
        for reference in stored.result.artifacts:
            worker.persistence.artifacts.validate_reference(reference, simulation_id=stored.simulation.id)
    first_read = ResultCommand(request_id=uuid4(), simulation_id=acceptance.simulation_id)
    next_read = ResultCommand(request_id=uuid4(), simulation_id=next_acceptance.simulation_id)
    first_public = remote.dispatch(binding, first_read)
    next_public = remote.dispatch(binding, next_read)
    assert first_public.kind == next_public.kind == "result"
    assert first_public.summary.cancel_requested is True
    assert next_public.summary.cancel_requested is False
    assert remote.dispatch(binding, cancel) == acknowledgement
    assert controller.close()
    controller.open()
    controller.start()
    restarted = RemoteService(controller._worker, remote.approved_binding)
    for request, original in ((command, acceptance), (next_command, next_acceptance), (cancel, acknowledgement),
                              (first_read, first_public), (next_read, next_public)):
        assert restarted.dispatch(binding, request) == original
    late_cancel = restarted.dispatch(binding, CancelCommand(request_id=uuid4(), simulation_id=acceptance.simulation_id))
    assert late_cancel.kind == "cancel_requested" and late_cancel.terminal is True
    from sqlalchemy import func, select
    from backend.models import SimulationJobRecord, SimulationRecord
    with controller._worker.persistence.database.session(write=False) as session:
        assert session.scalar(select(func.count()).select_from(SimulationRecord)) == 2
        assert session.scalar(select(func.count()).select_from(SimulationJobRecord)) == 2
    return {"cancel_ack": acknowledgement.model_dump(mode="json"), "after_commit_cancel_terminal": True,
            "summary": first_public.summary.model_dump(mode="json"),
            "follow_up_summary": next_public.summary.model_dump(mode="json"), "job_count": 2,
            "both_artifact_sets_verified": True, "exact_ack_and_results_preserved_after_reopen": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--bundle", type=Path, help="Require the isolated bundled interpreter and bundled product imports")
    parser.add_argument("--stage", choices=["crash", "reconcile", "blocked", "recovered", "verify-original",
        "hold-running", "active-blocked", "cancel-active", "cancel-finalizing"], required=True)
    args = parser.parse_args()
    bundle = None
    if args.bundle is not None:
        bundle = args.bundle.resolve()
        bundled_provenance(bundle)
        from scripts.portable import verify_bundle
        print(json.dumps({"stage": args.stage, "checking_bundle_integrity": True}), flush=True)
        integrity = verify_bundle(bundle)
        print(json.dumps({"stage": args.stage, "bundle_integrity": integrity}), flush=True)
    root = args.root.resolve()
    if not args.root.is_absolute() or not 1 <= args.port <= 65535:
        raise ValueError("Use an absolute owned evidence root and a valid test port")
    if args.stage == "crash":
        root.mkdir()
        binding = RemoteBinding("https://native-fixture.example", uuid4(), uuid4(), 1)
        command = ExecuteCommand(request_id=uuid4(), mode="carla", scenario=build_scenario("sudden_braking",
            name="Native interruption witness", map_name="Town10HD_Opt", duration_s=3., seed=7,
            parameters={"ego_speed_mps": 2., "lead_speed_mps": 0., "lead_distance_m": 35.,
                        "brake_delay_s": 0., "lead_deceleration_mps2": 3.}))
        save(root / "plan.json", {"port": args.port, "binding": {key: str(value) if key != "generation" else value
            for key, value in asdict(binding).items()}, "command": command.model_dump(mode="json")})
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    assert plan["port"] == args.port
    binding = RemoteBinding(**plan["binding"])
    command = ExecuteCommand.model_validate(plan["command"])
    original = args.stage in ("crash", "reconcile", "verify-original")
    profile = root / ("original" if original else args.stage)

    holding, release = Event(), Event()
    def runtime(settings):
        service = ApiRuntime(settings)
        if args.stage in ("crash", "hold-running", "cancel-active"):
            normal_factory = service.runner_factory
            def factory(**kwargs):
                runner = normal_factory(**kwargs)
                run = runner.run
                def witnessed_run(*arguments, **options):
                    callback = options["on_progress"]
                    def progress(simulation):
                        callback(simulation)  # The worker commits running first.
                        if (simulation.status == "running" and not holding.is_set()
                                and (args.stage != "cancel-active" or simulation.progress > 0)):
                            signal = {"process_id": os.getpid(), "simulation_id": str(simulation.id),
                                "status": simulation.status, "started_at": simulation.started_at.isoformat(),
                                "adapter": runner.adapter.mode, "episode": runner.adapter.world.id,
                                "frame": runner.adapter.world.get_snapshot().frame,
                                "native_actor_ids": sorted(runner.adapter.actor_manager.actor_ids),
                                "bundled_provenance": bundled_provenance(bundle)}
                            filename = {"crash": "crash-signal.json", "hold-running": "hold-signal.json",
                                        "cancel-active": "cancel-active-signal.json"}[args.stage]
                            save(root / filename, signal | {"progress": simulation.progress})
                            print(json.dumps(signal), flush=True)
                            if args.stage == "crash":
                                os._exit(79)
                            holding.set()
                            if not release.wait(120):
                                raise RuntimeError("The native contention witness was not released")
                    options["on_progress"] = progress
                    return run(*arguments, **options)
                runner.run = witnessed_run
                return runner
            service.runner_factory = factory
        return service

    controller = CompanionController(profile, runtime_factory=runtime)
    controller.open()
    controller.configure(controller.config.model_copy(update={"carla_port": args.port}))
    policy = DevicePolicy(scopes=list(PermissionScope))
    try:
        controller.start()
        worker = controller._worker
        if not original:
            binding = RemoteBinding(binding.origin, uuid4(), uuid4(), 1)
            recipe = command.scenario.model_copy(update={"duration_s": .5,
                "name": {"blocked": "Quarantine must refuse", "active-blocked": "Another active profile must refuse",
                         "hold-running": "Native endpoint owner", "cancel-active": "Cancel after a native tick",
                         "cancel-finalizing": "Cancel during native output publication"}.get(args.stage, "After explicit CARLA restart")})
            command = ExecuteCommand(request_id=uuid4(), mode="carla", scenario=recipe)
        if args.stage == "cancel-finalizing":
            persist = worker.persistence.persist_output
            def held_persist(output, **kwargs):
                if output.result.simulation.scenario.name == command.scenario.name:
                    assert output.result.simulation.status == "completed" and output.result.telemetry_frames == 11
                    save(root / "cancel-finalizing-signal.json", {
                        "stage": "after_native_runner_cleanup_before_persistence",
                        "simulation_id": str(output.result.simulation.id),
                        "frames": output.result.telemetry_frames})
                    holding.set()
                    assert release.wait(120), "The native finalization witness was not released"
                return persist(output, **kwargs)
            worker.persistence.persist_output = held_persist
        remote = RemoteService(worker, lambda: (binding, policy))
        acceptance = remote.dispatch(binding, command)
        assert acceptance.kind == "accepted"
        if args.stage in ("cancel-active", "cancel-finalizing"):
            proof = cancel_and_verify(controller, remote, binding, command, acceptance, holding, release,
                                      finalizing=args.stage == "cancel-finalizing")
            save(root / (args.stage + ".json"), proof | {"bundled_provenance": bundled_provenance(bundle)})
            print(json.dumps({"stage": args.stage, "status": proof["summary"]["status"], "jobs": 2}))
            return
        if args.stage == "crash":
            Event().wait(40)
            raise AssertionError("The running-progress crash hook was not reached")
        if args.stage == "hold-running":
            assert holding.wait(30), "The native owner did not reach running state"
            print(json.dumps({"stage": args.stage, "waiting_for": "release"}), flush=True)
            assert sys.stdin.readline().strip() == "release", "Release only this owned native test run"
            release.set()
        stored = wait_result(worker, acceptance.simulation_id)
        with worker.persistence.database.session(write=False) as session:
            from sqlalchemy import func, select
            from backend.models import SimulationJobRecord, SimulationRecord
            assert session.scalar(select(func.count()).select_from(SimulationRecord)) == 1
            assert session.scalar(select(func.count()).select_from(SimulationJobRecord)) == 1
        assert remote.dispatch(binding, command) == acceptance
        if args.stage in ("reconcile", "verify-original"):
            signal = json.loads((root / "crash-signal.json").read_text(encoding="utf-8"))
            assert str(acceptance.simulation_id) == signal["simulation_id"]
            assert stored.simulation.status == "failed" and "interrupted" in stored.simulation.error
            assert stored.simulation.started_at is not None and stored.result.telemetry_frames == 0
        elif args.stage == "blocked":
            assert stored.simulation.status == "failed" and "cleanup is unverified" in stored.simulation.error
            assert stored.result.telemetry_frames == 0
        elif args.stage == "active-blocked":
            assert stored.simulation.status == "failed" and "Another Poltergeist execution owns" in stored.simulation.error
            assert stored.result.telemetry_frames == 0
        else:
            assert stored.simulation.status == "completed", stored.simulation.error
            assert stored.result.runtime.adapter == "carla" and stored.result.runtime.server_version == "0.9.16"
            assert stored.result.telemetry_frames == 11
        public = remote.dispatch(binding, ResultCommand(request_id=uuid4(), simulation_id=acceptance.simulation_id))
        assert public.kind == "result"
        save(root / (args.stage + ".json"), {"stage": args.stage, "job_count": 1,
            "acceptance": acceptance.model_dump(mode="json"), "summary": public.summary.model_dump(mode="json"),
            "bundled_provenance": bundled_provenance(bundle)})
        print(json.dumps({"stage": args.stage, "status": public.summary.status, "jobs": 1,
                          "simulation_id": str(acceptance.simulation_id)}))
    finally:
        release.set()
        assert controller.close()


if __name__ == "__main__":
    main()
