"""Migrate and validate a stopped profile after its updater has backed it up."""

import argparse
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args(argv)
    from scripts.portable import bundle_root, verify_bundle
    from companion.profile import load_config, local_settings
    from backend.services.persistence_service import PersistenceService
    verify_bundle(bundle_root())
    service = PersistenceService.from_settings(local_settings(args.profile, load_config(args.profile)))
    try:
        service.initialize()
    finally:
        service.database.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
