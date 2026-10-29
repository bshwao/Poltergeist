"""Compile the unsigned private S3 Windows installer from a verified bundle."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from scripts.build_portable import ROOT, sha256, write_json
from scripts.portable import verify_bundle


def build(bundle, output, compiler, *, validation_profile=None, signing=None, release_version="0.1.0"):
    if sys.platform != "win32":
        raise ValueError("Build this installer on Windows x64")
    bundle, output, compiler = (Path(value).resolve() for value in (bundle, output, compiler))
    for path in (bundle, output, compiler, validation_profile):
        if path is not None and any(char in str(path) for char in ('"', '\r', '\n')):
            raise ValueError("Unsupported quote/newline in installer path")
    verify_bundle(bundle)
    if not (bundle / "runtime/Lib/site-packages/scripts/install_check.py").is_file():
        raise ValueError("Build a fresh S3 bundle containing install_check and onboarding first")
    lock = json.loads((ROOT / "packaging/windows-x64/installer-toolchain.json").read_bytes())
    version = subprocess.check_output([str(compiler), "--version"], text=True, timeout=10).strip()
    if version != lock["version"]:
        raise ValueError("Installer compiler version differs from the pinned toolchain")
    manifest = json.loads((bundle / "bundle.json").read_bytes())
    source_identity = manifest["project_wheel"]["sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", source_identity):
        raise ValueError("Invalid bundle source identity")
    # The project wheel excludes pinned Python/dependency inputs. Give every
    # complete bundle its own directory so dependency-only changes cannot
    # overlay a previous runtime and leave obsolete native/package files.
    identity = sha256(bundle / "bundle.json")
    build_id = "private-" + identity[:16]
    native_paths = [name for name in manifest["files"] if name.lower().endswith((".exe", ".pyd", ".dll"))]
    maximum_folder = min(128, 240 - len("\\versions\\" + build_id + "\\") - max(map(len, native_paths)))
    if maximum_folder < 48:
        raise ValueError("Native bundle paths leave too little room for a safe install folder")
    arguments = [str(compiler), "--quiet-progress", f"--define=BundleDir={bundle}",
                 f"--define=OutputDir={output}", f"--define=BuildId={build_id}", f"--define=MaxInstallPath={maximum_folder}"]
    if not re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}", release_version):
        raise ValueError("Invalid release version")
    arguments.append(f"--define=ReleaseVersion={release_version}")
    if signing is not None:
        if validation_profile is not None:
            raise ValueError("Do not sign validation-only installers for distribution")
        from scripts.release import signing_inputs
        signing = signing_inputs(**signing)
        policy = json.loads((bundle / "runtime/Lib/site-packages/companion/release-policy.json").read_bytes())
        if signing["publisher"] not in policy["publishers_sha256"]:
            raise ValueError("The bundled update policy must pin the release publisher before signing")
        metadata = list((bundle / "runtime/Lib/site-packages").glob("poltergeist-*.dist-info/METADATA"))
        if len(metadata) != 1 or f"Version: {release_version}" not in metadata[0].read_text(encoding="utf-8").splitlines():
            raise ValueError("Release version must match the packaged project wheel")
        arguments.extend(["--define=SignedBuild=1", "--signtool=poltergeist=$q" + str(signing["tool"]) +
            "$q sign /sha1 " + signing["thumbprint"] + " /fd SHA256 /tr " + signing["timestamp_url"] + " /td SHA256 $f"])
    if validation_profile is not None:
        profile = Path(validation_profile)
        if not profile.is_absolute() or profile.name != "CompanionPrototype":
            raise ValueError("Use an absolute isolated validation profile ending in CompanionPrototype")
        if profile.resolve().is_relative_to(output) or profile.resolve().is_relative_to(bundle):
            raise ValueError("Validation data must be outside the installed bundle/output")
        arguments.extend(["--define=AppIdentity=Poltergeist.Private.Companion.Validation",
                          "--define=ProductName=Poltergeist Companion Validation", f"--define=ProfileDir={profile}"])
    output.mkdir(parents=True, exist_ok=False)
    with (output / "compiler.log").open("w", encoding="utf-8") as stream:
        subprocess.run([*arguments, str(ROOT / "packaging/windows-x64/companion.iss")],
            check=True, stdout=stream, stderr=subprocess.STDOUT, timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    installer = output / "Poltergeist-Companion-Private-Setup.exe"
    if signing is not None:
        from companion.signatures import verify_signature
        verify_signature(installer, [signing["publisher"]])
    write_json(output / "installer-build.json", {"schema_version": "poltergeist-private-installer-v1",
        "signed": signing is not None, "version": release_version,
        "publisher_sha256": signing["publisher"] if signing else None,
        "compiler_version": version, "build_id": build_id,
        "bundle_manifest_sha256": identity, "installer_sha256": sha256(installer),
        "bytes": installer.stat().st_size, "max_install_folder_characters": maximum_folder,
        "validation_only": validation_profile is not None,
        "validation_profile": str(profile) if validation_profile is not None else None})
    return installer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compiler", type=Path, default=ROOT / "validation-evidence/installer-tools/inno-7.1.0/ISCC.exe")
    parser.add_argument("--validation-profile", type=Path)
    parser.add_argument("--release-version", default="0.1.0")
    parser.add_argument("--signtool", type=Path)
    parser.add_argument("--certificate-thumbprint")
    parser.add_argument("--timestamp-url")
    parser.add_argument("--publisher-sha256")
    args = parser.parse_args(argv)
    supplied = (args.signtool, args.certificate_thumbprint, args.timestamp_url, args.publisher_sha256)
    if any(supplied) and not all(supplied):
        parser.error("Supply all four signing arguments")
    signing = dict(tool=args.signtool, thumbprint=args.certificate_thumbprint,
        timestamp_url=args.timestamp_url, publisher=args.publisher_sha256) if all(supplied) else None
    print(build(args.bundle, args.output, args.compiler, validation_profile=args.validation_profile,
        signing=signing, release_version=args.release_version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
