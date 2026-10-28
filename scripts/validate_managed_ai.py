"""Exercise the Companion's managed CPU pack with retained execution evidence.

Run with the installed app interpreter, or a development environment. --prepare
explicitly downloads the supported pack. This command never installs into core.
"""

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
from threading import Event
from time import monotonic

from companion.controller import CompanionController
from companion.model_environment import digest, LOCK_PATH
from companion.ui import installation_recipe


def validate(profile, *, prepare=False, mode="carla", duration=.2):
    profile = Path(profile).resolve()
    report = {"schema": "poltergeist-managed-ai-validation-v1", "checked_at": datetime.now(UTC).isoformat(),
              "passed": False, "mode": mode, "catalogue_sha256": digest(LOCK_PATH),
              "scope": "Local managed installation, real pretrained inference, registration, execution and persistence; no independent-machine claim."}
    controller = CompanionController(profile)
    controller.open()
    evidence = profile / ("managed-ai-" + mode + "-validation.json")
    try:
        controller.prepare_model("prepare" if prepare else "check_environment")
        deadline = monotonic() + 1800
        while controller.model_status()["busy"]:
            if monotonic() > deadline:
                controller.pause_model_download()
                raise TimeoutError("Model preparation exceeded the validation budget; pause requested")
            Event().wait(.1)
        status = controller.model_status()
        if not status["inference_ready"]:
            raise RuntimeError(status["message"])
        report["readiness"] = controller._models._readiness
        registered = controller.enable_managed_model(trusted_local_code=True)
        report["registration_id"] = str(registered.id)
        from scripts.validate_installation import live_recipe
        recipe = live_recipe("127.0.0.1", controller.config.carla_port, duration)
        run = controller.submit(recipe, mode=mode, agent_id=registered.id)
        run_id = run.simulation.id
        report["run_id"] = str(run_id)
        deadline = monotonic() + 300
        while monotonic() < deadline:
            record = controller._worker.get(run_id)
            if record.result is not None:
                break
            Event().wait(.1)
        else:
            controller.cancel(run_id)
            raise TimeoutError("Managed execution timed out; cancellation requested")
        report["result"] = record.result.model_dump(mode="json")
        if record.simulation.status.value != "completed":
            raise RuntimeError("Managed execution did not complete: " + str(record.simulation.error))
        decisions = controller._runtime.persistence().read_decisions(run_id, limit=100)
        report["decisions"] = decisions.model_dump(mode="json")
        if not decisions.records:
            raise RuntimeError("Managed run has no persisted decisions")
        for decision in decisions.records:
            if decision.status == "applied":
                if decision.proposed_control != decision.applied_control:
                    raise RuntimeError("Managed controls were replaced")
        report["exports"] = [str(controller.export(run_id, format=kind)) for kind in ("json", "csv", "html")]
        if not controller.close():
            raise RuntimeError("Owned runtime did not stop")
        controller.open()
        controller.start()
        reopened = controller._runtime.persistence().get_simulation(run_id)
        if reopened.result != record.result:
            raise RuntimeError("Saved managed execution changed after reopening")
        # Submit the persisted registration after reopening: this reacquires the
        # capsule use lease even though this session has not run preparation.
        environment = controller._model_manager().environment
        controller._acquire_model_use()
        report["reopened"] = True
        report["environment_python"] = str(environment.python)
        report["passed"] = True
        return report
    except BaseException as error:
        report["error"] = str(error)
        raise
    finally:
        report["closed"] = controller.close()
        evidence.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--mode", choices=("carla",), default="carla")
    parser.add_argument("--duration", type=float, default=.2)
    args = parser.parse_args(argv)
    if not args.data_dir.is_absolute() or not .05 <= args.duration <= 10:
        parser.error("Choose an absolute data directory and a duration from .05 to 10 seconds")
    report = validate(args.data_dir, prepare=args.prepare, mode=args.mode, duration=args.duration)
    print(json.dumps({"passed": report["passed"], "run_id": report["run_id"], "closed": report["closed"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
