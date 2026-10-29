"""Repeatable pinned bundle -> signed installer -> signed metadata; no publishing."""

import argparse
import json
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--compiler", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--signtool", type=Path, required=True)
    parser.add_argument("--certificate-thumbprint", required=True)
    parser.add_argument("--publisher-sha256", required=True)
    parser.add_argument("--timestamp-url", required=True)
    parser.add_argument("--metadata-timestamp-url", required=True)
    parser.add_argument("--installer-url")
    parser.add_argument("--rollout-percent", type=int, default=100)
    parser.add_argument("--rollout-salt", default="")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    from scripts.build_portable import build as portable, sha256
    from scripts.build_installer import build as installer
    from scripts.release import signed_metadata, signing_inputs
    from companion.updates import load_policy
    signing = dict(tool=args.signtool, thumbprint=args.certificate_thumbprint,
        timestamp_url=args.timestamp_url, publisher=args.publisher_sha256)
    signing_inputs(**signing)
    if args.publisher_sha256 not in load_policy()["publishers_sha256"]:
        parser.error("Configure the public publisher pin in companion/release-policy.json before building")
    args.output.mkdir(parents=True, exist_ok=False)
    archive = portable(args.output / "portable", args.cache, offline=args.offline)
    bundles = [p for p in archive.parent.iterdir() if p.is_dir() and (p / "bundle.json").is_file()]
    if len(bundles) != 1:
        raise ValueError("Expected exactly one complete portable bundle")
    bundle = bundles[0]
    setup = installer(bundle, args.output / "installer", args.compiler, signing=signing, release_version=args.version)
    metadata = signed_metadata(setup, sha256(bundle / "bundle.json"), args.output / "release.ps1", version=args.version,
        thumbprint=args.certificate_thumbprint, timestamp_url=args.metadata_timestamp_url, publisher=args.publisher_sha256,
        installer_url=args.installer_url, rollout_percent=args.rollout_percent, rollout_salt=args.rollout_salt)
    print(json.dumps({"installer": str(setup), "metadata": str(metadata), "published": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
