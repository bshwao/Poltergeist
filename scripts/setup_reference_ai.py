"""Fetch and verify the one supported reference AI checkpoint.

This command never installs packages or starts CARLA. Dependencies belong in
the explicitly selected worker environment, as documented with the example.
"""

import argparse
import importlib.metadata
import json
from pathlib import Path
import sys
import tempfile
import urllib.error
import urllib.request

from simulator.agents.reference_ai.checkpoint import (
    CHECKPOINT_BYTES, CHECKPOINT_SHA256, CHECKPOINT_URL,
    DEFAULT_CHECKPOINT, UPSTREAM_REVISION, verify_checkpoint,
)


def download_checkpoint(destination: str | Path) -> Path:
    destination = Path(destination).expanduser().resolve()
    if destination.is_file():
        verify_checkpoint(destination)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="plant2-", suffix=".download", dir=destination.parent, delete=False,
        ) as output:
            temporary = Path(output.name)
            request = urllib.request.Request(CHECKPOINT_URL, headers={"User-Agent": "Poltergeist-reference-ai/0.1"})
            total = last_progress = 0
            with urllib.request.urlopen(request, timeout=60) as response:
                while data := response.read(1024 * 1024):
                    total += len(data)
                    if total > CHECKPOINT_BYTES:
                        raise ValueError("Download exceeds the pinned checkpoint size")
                    output.write(data)
                    if total - last_progress >= 32 * 1024 * 1024:
                        print(f"PlanT 2.0 checkpoint: {total / 1024**2:.0f} / {CHECKPOINT_BYTES / 1024**2:.0f} MiB", file=sys.stderr, flush=True)
                        last_progress = total
        verify_checkpoint(temporary)
        temporary.replace(destination)
        return destination
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Download the official 426 MiB checkpoint if absent")
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    args = parser.parse_args(argv)
    try:
        path = download_checkpoint(args.checkpoint) if args.download else args.checkpoint.expanduser().resolve()
        digest = verify_checkpoint(path)
        versions = {}
        for package in ("torch", "torchvision", "transformers", "timm", "omegaconf", "numpy", "scipy"):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = None
        print(json.dumps({
            "checkpoint": str(path), "sha256": digest,
            "upstream_revision": UPSTREAM_REVISION, "dependencies": versions,
            "dependencies_installed": all(versions.values()),
            "model_license": "Apache-2.0", "adapted_code_license": "MIT",
        }, indent=2))
        return 0
    except (OSError, ValueError, urllib.error.URLError) as error:
        print(f"Reference AI setup failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
