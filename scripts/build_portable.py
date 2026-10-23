"""Build the pinned Windows x64 S1 private portable prototype (build machine only).

Never copy an editable environment or runtime data. Hash-verified upstream
archives and wheels plus a fresh non-editable project wheel are the only inputs.
"""

import argparse
import base64
import csv
from datetime import UTC, datetime
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "packaging" / "windows-x64" / "inputs.json"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


def fetch(entry, cache, *, offline=False):
    filename = entry["filename"]
    if Path(filename).name != filename or ":" in filename or "\\" in filename or not entry["url"].startswith("https://"):
        raise ValueError("Unsafe pinned input")
    path = cache / filename
    if not path.exists():
        if offline:
            raise ValueError(f"Missing cached input in offline mode: {filename}")
        print(f"Downloading pinned {filename}", flush=True)
        with urllib.request.urlopen(entry["url"], timeout=90) as response, path.open("xb") as stream:
            shutil.copyfileobj(response, stream)
    if sha256(path) != entry["sha256"]:
        raise ValueError(f"Input checksum mismatch: {filename}. Remove the damaged cached file before retrying.")
    return path


def remove_owned(path, root):
    """Only prune freshly created bundle/build paths under our known output root."""
    path, root = Path(path), Path(root).resolve()
    if not path.resolve().is_relative_to(root) or path.resolve() == root:
        raise ValueError("Refusing to prune outside the fresh build output")
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def extract_runtime(archive, output, bundle):
    import zstandard
    unpacked = output / "upstream-python"
    unpacked.mkdir()
    with archive.open("rb") as stream, zstandard.ZstdDecompressor().stream_reader(stream) as decoded:
        with tarfile.open(fileobj=decoded, mode="r|") as tar:
            for member in tar:
                if member.issym() or member.islnk() or not (unpacked / member.name).resolve().is_relative_to(unpacked.resolve()):
                    raise ValueError("Unsafe upstream runtime archive member")
                tar.extract(member, path=unpacked, filter="data")
    python = unpacked / "python"
    metadata = json.loads((python / "PYTHON.json").read_text(encoding="utf-8"))
    if metadata["target_triple"] != "x86_64-pc-windows-msvc" or not (python / "install" / "python.exe").is_file():
        raise ValueError("Upstream archive is not the pinned Windows x64 runtime")
    runtime = bundle / "runtime"
    if not runtime.resolve().is_relative_to(output.resolve()):
        raise ValueError("Runtime destination escaped build output")
    (python / "install").rename(runtime)
    notices = bundle / "notices" / "python"
    notices.mkdir(parents=True)
    shutil.copy2(python / "PYTHON.json", notices / "PYTHON.json")
    for license_file in python.rglob("*"):
        if license_file.is_file() and ("license" in license_file.name.lower() or "licenses" in license_file.parts):
            relative = license_file.relative_to(python)
            target = notices / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(license_file, target)
    if not any(p.is_file() for p in notices.rglob("*") if p.name != "PYTHON.json"):
        raise ValueError("Upstream runtime license texts are missing")
    return runtime


def pe_imports(path):
    """Read regular PE import names; inventory only, not a loader/support proof."""
    data = Path(path).read_bytes()
    if data[:2] != b"MZ":
        return []
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError(f"Invalid PE image: {path}")
    sections, optional_size = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
    optional = pe + 24
    directory = optional + (112 if struct.unpack_from("<H", data, optional)[0] == 0x20b else 96)
    import_rva = struct.unpack_from("<I", data, directory + 8)[0]
    section_table = optional + optional_size
    def offset(rva):
        for index in range(sections):
            size, address, raw_size, raw = struct.unpack_from("<IIII", data, section_table + index * 40 + 8)
            if address <= rva < address + max(size, raw_size):
                return raw + rva - address
        raise ValueError(f"Invalid import RVA in {path}")
    if not import_rva:
        return []
    descriptor = offset(import_rva)
    result = []
    while any(data[descriptor:descriptor + 20]):
        name_rva = struct.unpack_from("<I", data, descriptor + 12)[0]
        start = offset(name_rva)
        end = data.index(b"\0", start, start + 4096)
        result.append(data[start:end].decode("ascii"))
        descriptor += 20
        if len(result) > 1024:
            raise ValueError("Unbounded PE import table")
    return sorted(set(result), key=lambda name: (name.lower(), name))


def native_inventory(bundle):
    native = [p for p in bundle.rglob("*") if p.suffix.lower() in {".dll", ".pyd", ".exe"}]
    included = {p.name.lower() for p in native}
    images = {p.relative_to(bundle).as_posix(): pe_imports(p) for p in sorted(native)}
    external = sorted({name.lower() for names in images.values() for name in names if name.lower() not in included})
    return {"schema_version": "poltergeist-native-imports-v1", "images": images, "not_bundled": external,
            "scope": "Regular PE import table inventory; delay-loaded/dynamically loaded libraries and actual loader resolution require runtime validation."}


def dependency_notices(site):
    result = []
    for distribution in sorted(importlib.metadata.distributions(path=[str(site)]), key=lambda d: d.metadata["Name"].lower()):
        metadata = distribution.metadata
        texts = [str(path) for path in distribution.files or [] if any(word in str(path).lower() for word in ("license", "notice", "copying"))]
        result.append({"name": metadata["Name"], "version": distribution.version,
            "license_expression": metadata.get("License-Expression"), "license_metadata": metadata.get("License"),
            "project_urls": metadata.get_all("Project-URL") or [], "home_page": metadata.get("Home-page"),
            "bundled_notice_files": texts})
    return result


def normalize_records(site, bundle):
    """Drop pruned entry points/direct_url hashes; retain valid installed records.

    Otherwise RECORD can retain a hash of a removed build-directory-specific
    direct_url.json, making identical builds differ after relocation.
    """
    for record in site.glob("*.dist-info/RECORD"):
        with record.open(encoding="utf-8", newline="") as stream:
            names = [row[0] for row in csv.reader(stream)]
        rows = []
        for name in names:
            path = (site / name).resolve()
            if not path.is_relative_to(bundle.resolve()) or not path.is_file():
                continue
            if path == record.resolve():
                rows.append([name, "", ""])
            else:
                digest = base64.urlsafe_b64encode(bytes.fromhex(sha256(path))).rstrip(b"=").decode("ascii")
                rows.append([name, "sha256=" + digest, str(path.stat().st_size)])
        with record.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream, lineterminator="\n").writerows(sorted(rows))


def validate_dependencies(site):
    """Validate wheel metadata closure, including requested extras, without imports."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    installed = {canonicalize_name(d.metadata["Name"]): d for d in importlib.metadata.distributions(path=[str(site)])}
    extras = {name: set() for name in installed}
    changed = True
    while changed:
        changed = False
        for name, distribution in installed.items():
            for text in distribution.requires or []:
                requirement = Requirement(text)
                if requirement.marker and not any(requirement.marker.evaluate({"extra": extra}) for extra in {"", *extras[name]}):
                    continue
                key = canonicalize_name(requirement.name)
                if key not in installed or installed[key].version not in requirement.specifier:
                    raise ValueError(f"Portable dependency closure failed: {name} requires {requirement}")
                before = len(extras[key])
                extras[key].update(requirement.extras)
                changed |= len(extras[key]) != before


def deterministic_zip(bundle, archive, epoch):
    date = datetime.fromtimestamp(epoch, UTC).timetuple()[:6]
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        for path in sorted(p for p in bundle.rglob("*") if p.is_file()):
            info = zipfile.ZipInfo(bundle.name + "/" + path.relative_to(bundle).as_posix(), date_time=date)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zipped.writestr(info, path.read_bytes())


def build(output, cache, *, offline=False):
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"} or sys.version_info[:2] != (3, 12):
        raise ValueError("Build this prototype with Windows x64 Python 3.12; other targets are unvalidated")
    inputs = json.loads(INPUTS.read_text(encoding="utf-8"))
    if inputs["schema_version"] != "poltergeist-portable-inputs-v1":
        raise ValueError("Unsupported portable input lock")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    cache = cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    entries = [inputs["runtime"], *inputs["wheels"], *inputs["notices"]]
    files = {entry["filename"]: fetch(entry, cache, offline=offline) for entry in entries}
    tools = output / "build-tools"
    tools.mkdir()
    # Bootstrap only verified, pinned build wheels; no resolver/build isolation
    # can silently select a different package version.
    for entry in inputs["wheels"]:
        if entry["role"] == "build":
            with zipfile.ZipFile(files[entry["filename"]]) as wheel:
                for name in wheel.namelist():
                    if not (tools / name).resolve().is_relative_to(tools):
                        raise ValueError("Unsafe build wheel")
                wheel.extractall(tools)
    sys.path.insert(0, str(tools))
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment["PYTHONPATH"] = str(tools)
    environment["SOURCE_DATE_EPOCH"] = str(inputs["source_date_epoch"])
    environment["PIP_CONFIG_FILE"] = os.devnull
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    wheel_dir = output / "project-wheel"
    wheel_dir.mkdir()
    metadata_dir = output / "project-metadata"
    metadata_dir.mkdir()
    subprocess.run([sys.executable, "-c", "import sys; from setuptools import setup; setup(script_args=['egg_info','--egg-base',sys.argv[1],'build','--build-base',sys.argv[2],'bdist_wheel','--bdist-dir',sys.argv[3],'--dist-dir',sys.argv[4]])",
                    str(metadata_dir), str(output / "source-build"), str(output / "wheel-staging"), str(wheel_dir)],
                   cwd=ROOT, env=environment, check=True)
    project_wheels = list(wheel_dir.glob("poltergeist-*.whl"))
    if len(project_wheels) != 1:
        raise ValueError("Expected one non-editable project wheel")
    project_wheel = project_wheels[0]
    bundle = output / "Poltergeist-S1-windows-x64"
    bundle.mkdir()
    runtime = extract_runtime(files[inputs["runtime"]["filename"]], output, bundle)
    site = runtime / "Lib" / "site-packages"
    remove_owned(site, output)
    site.mkdir()
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--no-compile", "--no-warn-script-location",
                    "--target", str(site), *[str(files[e["filename"]]) for e in inputs["wheels"] if e["role"] == "runtime"], str(project_wheel)],
                   cwd=output, env=environment, check=True)
    for path in [runtime / "Scripts", site / "bin", runtime / "Lib" / "ensurepip"]:
        remove_owned(path, output)
    # Windows treats Scripts and scripts as the same directory. Keep our scripts
    # Python package; prune only generated pip entry-point executables there.
    for path in list((site / "scripts").glob("*.exe")):
        remove_owned(path, output)
    for path in list(runtime.rglob("*.pdb")):
        remove_owned(path, output)
    for path in list(bundle.rglob("__pycache__")):
        remove_owned(path, output)
    for path in list(site.glob("*.dist-info/direct_url.json")):
        remove_owned(path, output)
    normalize_records(site, bundle)
    validate_dependencies(site)
    required = [runtime / "python.exe", runtime / "pythonw.exe",
                runtime / "DLLs" / "_tkinter.pyd", runtime / "tcl" / "tcl8.6" / "init.tcl",
                runtime / "tcl" / "tk8.6" / "tk.tcl", site / "companion" / "ui.py",
                site / "scripts" / "companion.py", site / "scripts" / "portable.py", site / "backend" / "main.py",
                site / "shared" / "models" / "runner.py", site / "simulator" / "agents" / "worker.py",
                site / "simulator" / "agents" / "reference_ai" / "LICENSE.plant2",
                *[site / "scenarios" / "templates" / (name + ".yaml")
                  for name in ("intersection", "pedestrian_crossing", "occluded_pedestrian", "sudden_braking")]]
    if any(not path.is_file() for path in required):
        raise ValueError("Portable runtime is missing a required package/resource")
    # ._pth and -I make the interpreter relocatable and reject ambient user-site,
    # PYTHONPATH and current-directory source imports, including in SDK workers.
    (runtime / "python312._pth").write_text("Lib\nDLLs\nLib/site-packages\n.\nimport site\n", encoding="ascii")
    for name in ("starter", "sensor_pilot", "portable_probe"):
        source = ROOT / "examples" / "agents" / name
        shutil.copytree(source, bundle / "examples" / "agents" / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env", ".venv"))
    (bundle / "examples" / "scenarios").mkdir(parents=True)
    shutil.copy2(ROOT / "examples" / "scenarios" / "installation_check.json", bundle / "examples" / "scenarios")
    for filename in ("Poltergeist.cmd", "Poltergeist Companion.vbs", "README-PORTABLE.txt", "DISTRIBUTION-NOTICES.md"):
        shutil.copy2(ROOT / "packaging" / "windows-x64" / filename, bundle / filename)
    shutil.copy2(INPUTS, bundle / "notices" / "inputs.json")
    for entry in inputs["notices"]:
        shutil.copy2(files[entry["filename"]], bundle / "notices" / entry["filename"])
    write_json(bundle / "notices" / "python-packages.json", dependency_notices(site))
    write_json(bundle / "notices" / "native-imports.json", native_inventory(bundle))
    manifest = {"schema_version": "poltergeist-portable-v1", "prototype": "S1-S2-private-windows-x64",
        "source_date_epoch": inputs["source_date_epoch"], "inputs_sha256": sha256(INPUTS),
        "project_wheel": {"filename": project_wheel.name, "sha256": sha256(project_wheel)},
        "runtime": inputs["runtime"], "remote_execution_enabled": False,
        "files": {p.relative_to(bundle).as_posix(): sha256(p) for p in sorted(bundle.rglob("*")) if p.is_file()}}
    write_json(bundle / "bundle.json", manifest)
    archive = output / f"{bundle.name}.zip"
    deterministic_zip(bundle, archive, inputs["source_date_epoch"])
    (output / f"{archive.name}.sha256").write_text(f"{sha256(archive)}  {archive.name}\n", encoding="ascii")
    write_json(output / "build-evidence.json", {"built_at": datetime.now(UTC).isoformat(), "build_python": sys.version,
        "platform": platform.platform(), "archive": str(archive), "sha256": sha256(archive), "bytes": archive.stat().st_size,
        "scope": "Build evidence only. Relocation, live CARLA and external clean-machine gates require separate evidence."})
    return archive


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New build directory; existing directories are refused")
    parser.add_argument("--cache", type=Path, default=ROOT / "validation-evidence" / "portable-inputs")
    parser.add_argument("--offline", action="store_true", help="Require every hash-verified input in the cache")
    args = parser.parse_args(argv)
    archive = build(args.output, args.cache, offline=args.offline)
    print(json.dumps({"archive": str(archive), "sha256": sha256(archive), "bytes": archive.stat().st_size}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
