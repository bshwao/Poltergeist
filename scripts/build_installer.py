"""Compile the unsigned private S3 Windows installer from a verified bundle."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from scripts.build_portable import ROOT, sha256, write_json
from scripts.portable import verify_bundle


def build(bundle, output, compiler, *, validation_profile=None):
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
    write_json(output / "installer-build.json", {"schema_version": "poltergeist-private-installer-v1",
        "signed": False, "compiler_version": version, "build_id": build_id,
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
    args = parser.parse_args(argv)
    print(build(args.bundle, args.output, args.compiler, validation_profile=args.validation_profile))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
