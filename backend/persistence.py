"""Persistence entry point. Initialize explicitly: python -m backend.persistence init."""

import argparse
import json

from backend.database import Database
from backend.persistence_errors import PersistenceError
from backend.services.persistence_service import PersistenceService
from backend.storage import ArtifactError, ArtifactStore

__all__ = ["ArtifactStore", "Database", "PersistenceService"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Initialize Poltergeist's local SQLite database and artifact storage")
    parser.add_argument("command", choices=["init"])
    parser.parse_args(argv)
    service = None
    try:
        service = PersistenceService.from_settings()
        service.initialize()
        print(json.dumps({"database": "initialized", "storage": str(service.artifacts.root)}))
        return 0
    except (PersistenceError, ArtifactError, OSError) as error:
        parser.exit(1, f"Persistence initialization failed: {error}\n")
    finally:
        if service is not None:
            service.database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
