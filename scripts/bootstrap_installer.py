"""Prepare the pinned build-only Inno Setup compiler in an isolated directory."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

from scripts.build_portable import ROOT, fetch, write_json

LOCK = ROOT / "packaging" / "windows-x64" / "installer-toolchain.json"


def bootstrap(destination, cache, *, offline=False):
    if sys.platform != "win32":
        raise ValueError("The installer toolchain targets Windows x64")
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    if lock["schema_version"] != "poltergeist-installer-toolchain-v1":
        raise ValueError("Unsupported installer toolchain lock")
    destination, cache = Path(destination).resolve(), Path(cache).resolve()
    if destination.exists():
        raise ValueError("Choose a new build-tool directory")
    cache.mkdir(parents=True, exist_ok=True)
    installer = fetch(lock, cache, offline=offline)
    # Upstream's portable switch suppresses app registration and an uninstaller.
    # Never install the build tool globally or change PATH/file associations.
    result = subprocess.run([str(installer), "/PORTABLE=1", "/CURRENTUSER", "/VERYSILENT", "/SP-",
        "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS", "/TASKS=", f"/DIR={destination}"],
        check=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    compiler = destination / "ISCC.exe"
    if not compiler.is_file():
        raise RuntimeError("Pinned compiler was not extracted")
    write_json(destination.parent / f"{destination.name}-bootstrap.json", {
        "version": lock["version"], "input_sha256": lock["sha256"], "portable": True,
        "exit_code": result.returncode, "compiler": str(compiler)})
    return compiler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=ROOT / "validation-evidence" / "installer-inputs")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    print(bootstrap(args.destination, args.cache, offline=args.offline))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
