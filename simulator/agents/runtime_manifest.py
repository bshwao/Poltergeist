"""Read-only verification of an optional complete managed environment seal.

The snapshot supplies the expected manifest hash; a file cannot assert its own
trust. Parent verification runs before spawning a worker while its use lease is
held, so third-party imports cannot precede this payload check.
"""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat


def _plain(path):
    for item in (path, *path.parents):
        if item.is_symlink() or item.is_junction() or (item.exists() and getattr(item.stat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("Linked managed runtime inputs were refused")
    if path.exists() and not path.is_dir():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Managed runtime inputs must be ordinary unlinked files")


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate managed runtime manifest fields")
        value[key] = item
    return value


def verify_runtime_manifest(prefix, expected_sha256):
    prefix = Path(prefix)
    root = prefix.parent
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("A pinned runtime manifest is required")
    marker = root / "capsule.json"
    _plain(marker)
    with marker.open("rb") as stream:
        raw = stream.read(16 * 1024**2 + 1)
    if len(raw) > 16 * 1024**2 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("Managed runtime manifest changed")
    manifest = json.loads(raw, object_pairs_hook=_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite runtime manifest")))
    if (not isinstance(manifest, dict) or manifest.get("schema") != "poltergeist-model-capsule-v1"
            or manifest.get("target") != "windows-x86_64-cpython312" or prefix != root / "runtime"):
        raise ValueError("Unsupported sealed runtime identity")
    files = manifest.get("files")
    if not isinstance(files, dict) or not 0 < len(files) <= 100000:
        raise ValueError("Invalid sealed runtime inventory")
    if not {"runtime/python.exe", "agent/agent.py", "agent/agent.json"} <= files.keys():
        raise ValueError("Incomplete sealed runtime inventory")
    total, folded = 0, set()
    for name, entry in files.items():
        if not isinstance(name, str) or len(name) > 1024 or "\\" in name or ":" in name:
            raise ValueError("Invalid sealed runtime path")
        path = PurePosixPath(name)
        if (path.is_absolute() or name != path.as_posix() or ".." in path.parts
                or len(path.parts) < 2 or path.parts[0] not in {"runtime", "agent", "notices"}
                or any(part.endswith((".", " ")) for part in path.parts) or name.casefold() in folded):
            raise ValueError("Unsafe or aliased sealed runtime path")
        folded.add(name.casefold())
        if (not isinstance(entry, dict) or set(entry) != {"size", "sha256"}
                or type(entry["size"]) is not int or not 0 <= entry["size"] <= 2 * 1024**3
                or not isinstance(entry["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
            raise ValueError("Invalid sealed runtime fingerprint")
        total += entry["size"]
    if total > 8 * 1024**3:
        raise ValueError("Sealed runtime exceeds the supported size")
    optional = {"capsule.json", ".installation.json", ".runtime-use.worker.lock"}
    expected_directories = {str(parent) for name in files for parent in PurePosixPath(name).parents if str(parent) != "."}
    found = set()
    for parent, directories, names in os.walk(root, followlinks=False):
        for name in directories:
            directory = Path(parent) / name
            _plain(directory)
            if directory.relative_to(root).as_posix() not in expected_directories:
                raise ValueError("Unrecognized directory in a sealed runtime")
        for name in names:
            file = Path(parent) / name
            _plain(file)
            relative = file.relative_to(root).as_posix()
            if relative not in files and relative not in optional:
                raise ValueError("Unrecognized files in a sealed runtime")
            found.add(relative)
            if len(found) > 100003:
                raise ValueError("Sealed runtime file count exceeds its limit")
    if found - optional != set(files):
        raise ValueError("Sealed runtime files are missing")
    for name, entry in files.items():
        path = root / name
        _plain(path)
        if not path.is_file() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size != entry["size"]:
            raise ValueError("Sealed runtime file size changed")
        digest, size = hashlib.sha256(), 0
        with path.open("rb") as stream:
            while block := stream.read(1024**2):
                size += len(block)
                if size > entry["size"]:
                    raise ValueError("Sealed runtime file grew during verification")
                digest.update(block)
        if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
            raise ValueError("Sealed runtime file hash changed")
