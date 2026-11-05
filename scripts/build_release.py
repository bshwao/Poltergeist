"""Repeatable pinned bundle -> signed installer -> signed metadata; no publishing."""

import argparse
import json
from pathlib import Path
import subprocess
import tomllib


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cache", type=Path)
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
    parser.add_argument("--preflight", action="store_true", help="Check local signing inputs without building or signing")
    args = parser.parse_args(argv)
    if not args.preflight and (args.output is None or args.cache is None):
        parser.error("Supply --output and --cache when building a release")
    from scripts.build_portable import ROOT, build as portable, sha256
    from scripts.build_installer import build as installer, compiler_version
    from scripts.release import check_signing_certificate, metadata_signing_inputs, signed_metadata, signing_inputs
    from companion.updates import load_policy
    try:
        signing = signing_inputs(args.signtool, args.certificate_thumbprint, args.timestamp_url, args.publisher_sha256)
        metadata_signing_inputs(signing["thumbprint"], args.metadata_timestamp_url, signing["publisher"])
        with (ROOT / "pyproject.toml").open("rb") as stream:
            if args.version != tomllib.load(stream)["project"]["version"]:
                raise ValueError("Release version must match the project version in pyproject.toml")
        if args.installer_url is not None:
            from companion.update_delivery import delivery_origin
            delivery_origin(args.installer_url)
        check_signing_certificate(signing["thumbprint"], signing["publisher"])
        if signing["publisher"] not in load_policy()["publishers_sha256"]:
            raise ValueError("Configure the public publisher pin in companion/release-policy.json before building")
        if not signing["tool"].is_file():
            raise ValueError("The configured Windows SDK SignTool was not found")
        compiler_version(args.compiler.resolve())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    if args.preflight:
        print(json.dumps({"passed": True, "version": args.version, "built": False, "signed": False}))
        return 0
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
