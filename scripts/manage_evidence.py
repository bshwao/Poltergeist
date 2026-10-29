"""Export or explicitly remove a finished run's evidence while the Companion is closed."""

import argparse
import json
from pathlib import Path
from uuid import UUID
from zipfile import ZipFile, ZIP_STORED

from backend.services.persistence_service import PersistenceService
from backend.worker_lock import WorkerLease
from companion.profile import instance_lease, load_config, local_settings, profile_path


def export_evidence(artifacts, result, export):
    export = Path(export).absolute()
    if export.resolve().is_relative_to(artifacts.root.resolve()):
        raise ValueError("Export outside evidence storage")
    with export.open("xb") as target, ZipFile(target, "w", compression=ZIP_STORED) as archive:
        archive.writestr("result.json", result.model_dump_json(indent=2))
        for ref in result.artifacts:
            directory = artifacts.validate_reference(ref, result.simulation.id).parent
            for path in directory.iterdir():
                artifacts._contained(path, exists=True)
                if not path.is_file() or path.stat().st_nlink != 1:
                    raise ValueError("Export requires ordinary artifact files")
                archive.write(path, path.relative_to(artifacts.root).as_posix())
    return export


def manage(profile, simulation_id, *, export=None, delete=False):
    profile = profile_path(profile)
    desktop = instance_lease(profile)
    desktop.acquire()
    persistence = None
    worker = None
    try:
        persistence = PersistenceService.from_settings(local_settings(profile, load_config(profile)))
        worker = WorkerLease(persistence.database)
        worker.acquire()
        persistence.initialize()
        stored = persistence.get_simulation(simulation_id)
        if stored.result is None or stored.simulation.status.value not in ("completed", "cancelled", "failed"):
            raise ValueError("Evidence maintenance requires a finalized run")
        artifacts = persistence.artifacts
        refs = stored.result.artifacts
        if export is not None:
            export = Path(export).absolute()
            if export.resolve().is_relative_to(profile):
                raise ValueError("Export outside the Companion profile")
            export_evidence(artifacts, stored.result, export)
        if delete:
            for ref in refs:
                try:
                    artifacts.delete_evidence(ref, simulation_id)
                except FileNotFoundError:
                    # Repeating an interrupted retention operation is safe.
                    continue
        return {"simulation_id": str(simulation_id), "exported": export is not None,
                "deleted": delete, "capacity": artifacts.capacity()}
    finally:
        if persistence is not None:
            persistence.database.dispose()
        if worker is not None:
            worker.release()
        desktop.release()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--run", type=UUID, required=True)
    parser.add_argument("--export", type=Path)
    parser.add_argument("--delete", action="store_true")
    args = parser.parse_args(argv)
    if args.export is None and not args.delete:
        parser.error("Choose --export and/or --delete")
    print(json.dumps(manage(args.data_dir, args.run, export=args.export, delete=args.delete), indent=2))


if __name__ == "__main__":
    main()
