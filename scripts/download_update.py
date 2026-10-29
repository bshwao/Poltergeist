"""Check the installed HTTPS feed; download only with explicit --download."""

import argparse
from importlib.metadata import version
import json
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args(argv)
    from companion.update_delivery import check_update, download_update
    current = version("poltergeist")
    offer = check_update(args.data_dir, current_version=current)
    if offer is None:
        print(json.dumps({"update": None, "installed_version": current}))
        return 0
    installer = download_update(args.data_dir, offer.manifest, current_version=current) if args.download else None
    print(json.dumps({"installed_version": current, "version": offer.release.version,
        "installer_bytes": offer.release.installer_bytes, "expires_at": offer.release.expires_at.isoformat(),
        "manifest": str(offer.manifest), "installer": str(installer) if installer else None,
        "installed": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
