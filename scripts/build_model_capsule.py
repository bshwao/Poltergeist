"""Build a relocatable managed CPU environment from verified publisher inputs.

The supplied interpreter is never run. Runtime and package bytes are rebuilt
from the pinned upstream archive and wheels, not copied from an editable or
partially installed environment. End users only receive the resulting ZIP.
"""

import argparse
import base64
from contextlib import contextmanager
import csv
from datetime import UTC, datetime
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import tarfile
import urllib.request
import zipfile

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "poltergeist-model-capsule-v1"
TARGET = "windows-x86_64-cpython312"
EPOCH = 1791849600
MAX_FILES = 100000
MAX_FILE = 2 * 1024**3
MAX_TOTAL = 8 * 1024**3
MAX_MANIFEST = 16 * 1024**2
CHUNK = 1024**2
_DOS = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(CHUNK):
            value.update(block)
    return value.hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")


def safe_name(value):
    if not isinstance(value, str) or not value or value.startswith(("/", "\\")) or "\\" in value:
        raise ValueError("Unsafe capsule path")
    parts = value.split("/")
    for part in parts:
        if (part in {"", ".", ".."} or part.endswith((".", " "))
                or part.split(".", 1)[0].upper() in _DOS
                or any(ord(char) < 32 or ord(char) > 126 or char in '<>:"|?*' for char in part)):
            raise ValueError("Unsafe Windows capsule path")
    return value


def plain(path, *, directory=False):
    path = Path(path)
    for item in (path, *path.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError("Linked publisher inputs are unsupported")
    if not path.exists():
        raise ValueError("Publisher input is missing")
    info = path.stat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1):
        raise ValueError("Publisher inputs must be ordinary unlinked files")
    return path


class Names:
    def __init__(self):
        self.spelling, self.files, self.directories = {}, set(), set()

    def add(self, name):
        safe_name(name)
        parts = name.split("/")
        for index in range(1, len(parts) + 1):
            component = "/".join(parts[:index])
            folded = component.casefold()
            if folded in self.spelling and self.spelling[folded] != component:
                raise ValueError("Case aliases in capsule paths")
            self.spelling[folded] = component
            if index < len(parts):
                if folded in self.files:
                    raise ValueError("A capsule file is also used as a directory")
                self.directories.add(folded)
            else:
                if folded in self.files or folded in self.directories:
                    raise ValueError("Duplicate or conflicting capsule path")
                self.files.add(folded)


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("A SHA-256 input pin is required")
    return value


def _canonical_name(value):
    return re.sub(r"[-_.]+", "-", value).lower()


def _zip_entries(archive):
    names = Names()
    total = 0
    result = {}
    for item in archive.infolist():
        if item.is_dir():
            continue
        names.add(item.filename)
        mode = item.external_attr >> 16
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG) or item.flag_bits & 1:
            raise ValueError("Linked, special or encrypted wheel/capsule member")
        if not 0 <= item.file_size <= MAX_FILE:
            raise ValueError("Oversized archive member")
        total += item.file_size
        if len(result) >= MAX_FILES or total > MAX_TOTAL:
            raise ValueError("Archive inventory exceeds publisher bounds")
        result[item.filename] = item
    return result


@contextmanager
def _runtime_tar(path):
    with Path(path).open("rb") as stream:
        if str(path).endswith(".zst"):
            import zstandard  # Publisher's separately pinned build tool, never needed by the app.
            with zstandard.ZstdDecompressor().stream_reader(stream) as decoded:
                with tarfile.open(fileobj=decoded, mode="r|") as archive:
                    yield archive
        else:
            with tarfile.open(fileobj=stream, mode="r|*") as archive:
                yield archive


def _pruned(relative):
    parts = PurePosixPath(relative).parts
    lowered = [part.lower() for part in parts]
    if "__pycache__" in lowered or any(part in {".git", ".pytest_cache", ".cache"} for part in lowered):
        return True
    if relative.lower().endswith((".pyc", ".pyo")) or (parts and lowered[0] == "scripts"):
        return True
    if len(parts) >= 2 and lowered[:2] == ["lib", "ensurepip"]:
        return True
    if len(parts) >= 3 and lowered[:2] == ["lib", "site-packages"]:
        return lowered[2] == "pip" or lowered[2].startswith("pip-")
    return False


def _license_path(name):
    parts = PurePosixPath(name).parts
    return any(word in parts[-1].lower() for word in ("license", "licence", "notice", "copying")) or "licenses" in [part.lower() for part in parts]


def _wheel_target(name):
    parts = name.split("/")
    if parts[0].endswith(".data"):
        if len(parts) < 3:
            raise ValueError("Malformed wheel data path")
        if parts[1] == "scripts":
            return None
        if parts[1] in {"purelib", "platlib"}:
            return "runtime/Lib/site-packages/" + "/".join(parts[2:])
        if parts[1] == "data":
            return "runtime/" + "/".join(parts[2:])
        raise ValueError("Unsupported wheel installation scheme")
    return "runtime/Lib/site-packages/" + name


def _wheel_record(archive, entries):
    records = [name for name in entries if name.count("/") == 1 and name.endswith(".dist-info/RECORD")]
    metadata = [name for name in entries if name.count("/") == 1 and name.endswith(".dist-info/METADATA")]
    if len(records) != 1 or len(metadata) != 1 or records[0].split("/")[0] != metadata[0].split("/")[0]:
        raise ValueError("Wheel has no unique distribution metadata/RECORD")
    if entries[records[0]].file_size > MAX_MANIFEST or entries[metadata[0]].file_size > MAX_MANIFEST:
        raise ValueError("Oversized wheel metadata")
    rows = {}
    for row in csv.reader(io.StringIO(archive.read(records[0]).decode("utf-8"))):
        if len(row) != 3:
            raise ValueError("Invalid wheel RECORD")
        # Some upstream Windows wheels use native separators in RECORD while
        # their ZIP members use '/'. Normalize only this metadata reference;
        # archive names remain strict, and aliases/escapes still fail closed.
        name = row[0].replace("\\", "/")
        safe_name(name)
        if name in rows:
            raise ValueError("Duplicate wheel RECORD path alias")
        rows[name] = row[1:]
    if set(rows) != set(entries) or rows[records[0]] != ["", ""]:
        raise ValueError("Wheel RECORD does not describe its exact payload")
    for name in entries:
        if name == records[0]:
            continue
        expected, size = rows[name]
        if not expected.startswith("sha256=") or not size.isdecimal() or int(size) != entries[name].file_size:
            raise ValueError("Wheel RECORD lacks a bounded SHA-256 identity")
        token = expected.removeprefix("sha256=")
        try:
            decoded = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        except ValueError as error:
            raise ValueError("Invalid wheel RECORD digest") from error
        if len(decoded) != 32:
            raise ValueError("Invalid wheel RECORD digest")
        rows[name] = [decoded.hex(), int(size)]
    return records[0], BytesParser().parsebytes(archive.read(metadata[0])), rows


def _wheel_compatible(archive, entries):
    paths = [name for name in entries if name.count("/") == 1 and name.endswith(".dist-info/WHEEL")]
    if len(paths) != 1 or entries[paths[0]].file_size > MAX_MANIFEST:
        raise ValueError("Wheel has no bounded ABI declaration")
    headers = BytesParser().parsebytes(archive.read(paths[0]))
    for tag in headers.get_all("Tag", []):
        parts = tag.split("-")
        if len(parts) != 3:
            continue
        python, abi, platform = parts
        if python == "py3" and abi == "none" and platform == "any":
            return
        if platform == "win_amd64" and python == "cp312" and abi == "cp312":
            return
        if (platform == "win_amd64" and abi == "abi3" and python.startswith("cp3")
                and python[3:].isdecimal() and 2 <= int(python[3:]) <= 12):
            return
    raise ValueError("Wheel is incompatible with Windows x64 Python 3.12")


class PayloadWriter:
    def __init__(self, archive, *, epoch, tested_prefix=None):
        self.archive, self.names, self.files = archive, Names(), {}
        self.total = 0
        self.stamp = datetime.fromtimestamp(epoch, UTC).timetuple()[:6]
        if self.stamp[0] < 1980 or self.stamp[0] > 2107:
            raise ValueError("ZIP timestamp outside supported range")
        self.tested_prefix = None if tested_prefix is None else plain(tested_prefix, directory=True)

    def add(self, name, stream, size, *, expected=None, compare=True):
        self.names.add(name)
        if type(size) is not int or not 0 <= size <= MAX_FILE or self.total + size > MAX_TOTAL or len(self.files) >= MAX_FILES:
            raise ValueError("Capsule payload exceeds publisher bounds")
        info = zipfile.ZipInfo(name, self.stamp)
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        value, count = hashlib.sha256(), 0
        with self.archive.open(info, "w", force_zip64=size > 2**31) as output:
            while block := stream.read(CHUNK):
                count += len(block)
                if count > size:
                    raise ValueError("Publisher input grew beyond its declared size")
                output.write(block)
                value.update(block)
        if count != size or (expected is not None and value.hexdigest() != expected):
            raise ValueError("Publisher input does not match its pinned file identity")
        item = {"sha256": value.hexdigest(), "size": size}
        self.files[name] = item
        self.total += size
        if self.tested_prefix is not None and compare and name.startswith("runtime/"):
            source = plain(self.tested_prefix / name.removeprefix("runtime/"))
            if source.stat().st_size != size or sha256(source) != item["sha256"]:
                raise ValueError("Tested prefix differs from the verified publisher inputs")
        return item

    def add_bytes(self, name, value, **kwargs):
        return self.add(name, io.BytesIO(value), len(value), **kwargs)


def _runtime_payload(writer, archive_path, spec):
    metadata = None
    licenses = []
    with _runtime_tar(archive_path) as source:
        for item in source:
            name = safe_name(item.name.rstrip("/"))
            if not item.isfile() and not item.isdir():
                raise ValueError("Linked or special upstream runtime member")
            if item.isdir():
                continue
            if not 0 <= item.size <= MAX_FILE:
                raise ValueError("Oversized upstream runtime member")
            if name == "python/PYTHON.json":
                if item.size > MAX_MANIFEST:
                    raise ValueError("Oversized upstream runtime metadata")
                data = source.extractfile(item).read()
                metadata = json.loads(data)
                writer.add_bytes("notices/python/PYTHON.json", data)
            elif name.startswith("python/install/"):
                relative = name.removeprefix("python/install/")
                if not _pruned(relative):
                    writer.add("runtime/" + relative, source.extractfile(item), item.size)
            elif name.startswith("python/licenses/"):
                target = "notices/python/" + name.removeprefix("python/")
                writer.add(target, source.extractfile(item), item.size)
                licenses.append(target)
    if (metadata is None or metadata.get("target_triple") != "x86_64-pc-windows-msvc"
            or metadata.get("python_version") != spec["version"] or not licenses):
        raise ValueError("Runtime target/version/license metadata does not match its pin")
    if "runtime/python.exe" not in writer.files:
        raise ValueError("Runtime interpreter is missing")


def _package_payload(writer, path, package, supplements):
    with zipfile.ZipFile(path) as wheel:
        entries = _zip_entries(wheel)
        _wheel_compatible(wheel, entries)
        record_name, metadata, rows = _wheel_record(wheel, entries)
        if (_canonical_name(metadata.get("Name", "")) != _canonical_name(package["name"])
                or metadata.get("Version") != package["version"]):
            raise ValueError("Pinned wheel distribution identity does not match metadata")
        installed = []
        notice_files = []
        for name in sorted(entries):
            if name == record_name:
                continue
            target = _wheel_target(name)
            if target is None or _pruned(target.removeprefix("runtime/")):
                continue
            item = writer.add(target, wheel.open(entries[name]), entries[name].file_size, expected=rows[name][0])
            installed.append((target, item))
            if _license_path(name):
                notice = "notices/packages/" + _canonical_name(package["name"]) + "/" + name
                writer.add(notice, wheel.open(entries[name]), entries[name].file_size, expected=rows[name][0], compare=False)
                notice_files.append(notice)
        for supplement in supplements:
            if _canonical_name(supplement["package"]) != _canonical_name(package["name"]):
                continue
            path = plain(supplement["path"])
            expected = _digest(supplement["sha256"])
            target = "notices/packages/" + _canonical_name(package["name"]) + "/supplemental/" + safe_name(supplement["filename"])
            with path.open("rb") as stream:
                writer.add(target, stream, path.stat().st_size, expected=expected, compare=False)
            notice_files.append(target)
        if not notice_files:
            raise ValueError("Distribution has no license/notice texts; supply a pinned upstream supplement: " + package["name"])
        record_target = _wheel_target(record_name)
        data = io.StringIO(newline="")
        output = csv.writer(data, lineterminator="\n")
        for name, item in sorted(installed):
            token = base64.urlsafe_b64encode(bytes.fromhex(item["sha256"])).rstrip(b"=").decode("ascii")
            output.writerow([posixpath.relpath(name, "runtime/Lib/site-packages"), "sha256=" + token, item["size"]])
        output.writerow([record_name, "", ""])
        writer.add_bytes(record_target, data.getvalue().encode("utf-8"), compare=False)
        return {**package, "license": metadata.get("License-Expression") or metadata.get("License"),
                "notice_files": sorted(notice_files), "record_rebuilt": True}


def _dependency_closure(checked, python_version):
    """Validate the supplied pinned graph without resolving or downloading code."""
    environment = {**default_environment(), "os_name": "nt", "sys_platform": "win32",
                   "platform_system": "Windows", "platform_machine": "AMD64",
                   "platform_python_implementation": "CPython", "implementation_name": "cpython",
                   "python_version": "3.12", "python_full_version": python_version,
                   "implementation_version": python_version}
    metadata = {}
    for name, item, path in checked:
        with zipfile.ZipFile(path) as archive:
            entries = _zip_entries(archive)
            _, headers, _ = _wheel_record(archive, entries)
        if headers.get("Requires-Python") and Version(python_version) not in SpecifierSet(headers["Requires-Python"]):
            raise ValueError("Wheel requires a different Python version: " + name)
        metadata[name] = headers
    versions = {name: Version(item["version"]) for name, item, _ in checked}
    extras = {name: {""} for name in versions}
    pending = list(versions)
    while pending:
        name = pending.pop()
        for raw in metadata[name].get_all("Requires-Dist", []):
            requirement = Requirement(raw)
            if requirement.marker and not any(requirement.marker.evaluate({**environment, "extra": extra}) for extra in extras[name]):
                continue
            target = _canonical_name(requirement.name)
            if requirement.url or target not in versions or versions[target] not in requirement.specifier:
                raise ValueError("Pinned dependency graph is incomplete or incompatible: " + name + " -> " + raw)
            added = requirement.extras - extras[target]
            if added:
                extras[target].update(added)
                pending.append(target)


def build(output, *, runtime_archive, runtime_spec, wheels_dir, packages_manifest,
          source_revision, capsule_id="plant2-cpu-win312-r1", supplemental_notices=(),
          source_date_epoch=EPOCH, tested_prefix=None):
    if not re.fullmatch(r"[a-z0-9-]{1,80}", capsule_id) or not re.fullmatch(r"[0-9a-f]{40}", source_revision):
        raise ValueError("A versioned capsule ID and source revision are required")
    runtime_archive = plain(runtime_archive)
    wheels_dir = plain(wheels_dir, directory=True)
    if sha256(runtime_archive) != _digest(runtime_spec["sha256"]):
        raise ValueError("Runtime archive hash mismatch")
    if not str(runtime_spec["version"]).startswith("3.12."):
        raise ValueError("Only the Windows Python 3.12 capsule target is supported")
    document = json.loads(plain(packages_manifest).read_bytes())
    packages = document.get("packages")
    if not isinstance(packages, list) or not packages or len(packages) > 256:
        raise ValueError("A bounded pinned package manifest is required")
    seen, checked = set(), []
    for item in packages:
        if set(item) != {"name", "version", "filename", "sha256", "bytes"}:
            raise ValueError("Invalid pinned package fields")
        name = _canonical_name(item["name"])
        filename = safe_name(item["filename"])
        if "/" in filename or not filename.endswith(".whl") or name in seen:
            raise ValueError("Duplicate package or unsafe wheel filename")
        seen.add(name)
        path = plain(wheels_dir / filename)
        if type(item["bytes"]) is not int or item["bytes"] <= 0 or path.stat().st_size != item["bytes"] or sha256(path) != _digest(item["sha256"]):
            raise ValueError("Pinned wheel bytes/hash mismatch")
        checked.append((name, item, path))
    sdk = [item for name, item, path in checked if name == "poltergeist"]
    if len(sdk) != 1:
        raise ValueError("Exactly one pinned Poltergeist SDK wheel is required")
    versions = {name: item["version"] for name, item, path in checked}
    required = {"torch", "torchvision", "transformers", "timm", "omegaconf", "numpy", "scipy"}
    if not required <= set(versions) or not all(versions[name].endswith("+cpu") for name in ("torch", "torchvision")):
        raise ValueError("The PlanT capsule requires its supported CPU dependency family")
    _dependency_closure(checked, runtime_spec["version"])
    output = Path(output).absolute()
    for item in (output, *output.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError("Linked build output refused")
    output.mkdir(parents=True, exist_ok=False)
    archive_path = output / "model-capsule.zip"
    temporary = output / "model-capsule.zip.partial"
    with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        writer = PayloadWriter(archive, epoch=source_date_epoch, tested_prefix=tested_prefix)
        _runtime_payload(writer, runtime_archive, runtime_spec)
        installed = [_package_payload(writer, path, item, supplemental_notices) for name, item, path in sorted(checked)]
        agent = {"protocol_version": "1.2", "agent_id": "poltergeist-reference-ai", "name": "PlanT 2.0 (managed CPU)",
                 "version": capsule_id, "entry_point": "agent:ReferenceAgent", "observation_mode": "simulator_state",
                 "control_mode": "vehicle_control", "navigation_mode": "provided_route", "required_features": ["structured_scene"],
                 "sensors": [], "assets": [], "configuration": {"device": "cpu", "torch_threads": 2, "cuda_graphs": False}}
        writer.add_bytes("agent/agent.json", json_bytes(agent))
        writer.add_bytes("agent/agent.py", b'"""Managed, pinned first-party PlanT entry point."""\nfrom simulator.agents.reference_ai import ReferenceAIAgent\n\nclass ReferenceAgent(ReferenceAIAgent):\n    pass\n')
        writer.add_bytes("notices/dependencies.json", json_bytes(installed))
        manifest = {"schema": SCHEMA, "id": capsule_id, "target": TARGET, "python_version": runtime_spec["version"],
                    "python_executable": "runtime/python.exe", "agent_package": "agent", "sdk_wheel_sha256": sdk[0]["sha256"],
                    "source_revision": source_revision, "packages": installed,
                    "runtime_archive_sha256": runtime_spec["sha256"], "files": dict(sorted(writer.files.items())),
                    "notice_scope": "Collected upstream runtime and wheel/supplement license texts; redistribution and transitive binary license review is not performed by this builder."}
        raw = json_bytes(manifest)
        if len(raw) > MAX_MANIFEST:
            raise ValueError("Capsule manifest exceeds supported size")
        writer.add_bytes("capsule.json", raw, compare=False)
    temporary.replace(archive_path)
    descriptor = inspect_capsule(archive_path)
    (output / "build-evidence.json").write_bytes(json_bytes({key: value for key, value in descriptor.items() if key != "manifest"}))
    return descriptor


def inspect_capsule(path, *, verify_files=True):
    path = plain(path)
    with zipfile.ZipFile(path) as archive:
        if any(item.is_dir() for item in archive.infolist()):
            raise ValueError("Capsules contain only regular file entries")
        entries = _zip_entries(archive)
        if "capsule.json" not in entries or entries["capsule.json"].file_size > MAX_MANIFEST:
            raise ValueError("Capsule has no bounded manifest")
        raw = archive.read("capsule.json")
        manifest = json.loads(raw)
        files = manifest.get("files")
        if (manifest.get("schema") != SCHEMA or manifest.get("target") != TARGET or not isinstance(files, dict)
                or set(entries) != set(files) | {"capsule.json"} or not {"runtime/python.exe", "agent/agent.json", "agent/agent.py"} <= set(files)):
            raise ValueError("Capsule manifest does not describe its exact supported payload")
        for name, item in files.items():
            safe_name(name)
            if name.split("/", 1)[0] not in {"runtime", "agent", "notices"} or name.endswith("/.installation.json"):
                raise ValueError("Unexpected capsule root or installation state")
            if set(item) != {"sha256", "size"} or type(item["size"]) is not int or item["size"] != entries[name].file_size:
                raise ValueError("Invalid capsule file declaration")
            _digest(item["sha256"])
            if verify_files:
                value = hashlib.sha256()
                with archive.open(entries[name]) as stream:
                    while block := stream.read(CHUNK):
                        value.update(block)
                if value.hexdigest() != item["sha256"]:
                    raise ValueError("Capsule payload hash mismatch")
    return {"id": manifest["id"], "target": manifest["target"], "sha256": sha256(path), "bytes": path.stat().st_size,
            "manifest_sha256": hashlib.sha256(raw).hexdigest(), "manifest": manifest}


def fetch_notices(document, cache, *, offline=False):
    """Fetch only publisher-pinned supplemental notice texts, not code."""
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    result = []
    for item in document.get("notices", []):
        filename = safe_name(item["filename"])
        if "/" in filename or not item["url"].startswith("https://"):
            raise ValueError("Unsafe supplemental notice input")
        path = cache / filename
        if not path.exists():
            if offline:
                raise ValueError("Missing cached supplemental notice: " + filename)
            with urllib.request.urlopen(item["url"], timeout=30) as response:
                data = response.read(1024**2 + 1)
            if len(data) > 1024**2 or hashlib.sha256(data).hexdigest() != _digest(item["sha256"]):
                raise ValueError("Supplemental notice hash/size mismatch")
            with path.open("xb") as stream:
                stream.write(data)
        if sha256(plain(path)) != _digest(item["sha256"]):
            raise ValueError("Cached supplemental notice hash mismatch")
        result.append({**item, "path": str(path.absolute())})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-archive", type=Path, required=True)
    parser.add_argument("--runtime-inputs", type=Path, default=ROOT / "packaging/windows-x64/inputs.json")
    parser.add_argument("--wheels-dir", type=Path, required=True)
    parser.add_argument("--packages-manifest", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--capsule-id", default="plant2-cpu-win312-r1")
    parser.add_argument("--notice-inputs", type=Path, default=ROOT / "packaging/windows-x64/model-cpu-notices.json")
    parser.add_argument("--notice-cache", type=Path, required=True)
    parser.add_argument("--tested-prefix", type=Path)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    spec = json.loads(args.runtime_inputs.read_bytes())
    notices = fetch_notices(json.loads(args.notice_inputs.read_bytes()), args.notice_cache, offline=args.offline)
    result = build(args.output, runtime_archive=args.runtime_archive, runtime_spec=spec["runtime"], wheels_dir=args.wheels_dir,
                   packages_manifest=args.packages_manifest, source_revision=args.source_revision, capsule_id=args.capsule_id,
                   supplemental_notices=notices, tested_prefix=args.tested_prefix, source_date_epoch=spec.get("source_date_epoch", EPOCH))
    print(json.dumps({key: value for key, value in result.items() if key != "manifest"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
