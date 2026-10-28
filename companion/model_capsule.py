"""Verify and materialize an app-pinned model runtime without executing it.

The catalogue pins both the archive and its inner manifest. Generations stay
immutable after activation; the one mutable pointer is replaced atomically only
after extraction and a complete read-back check. Callers must exclude local
execution before repair/removal, or hold ``use()`` for an entire use interval.
These ownership checks are not a sandbox against hostile code as the same user.
"""

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from types import SimpleNamespace
from uuid import uuid4
import zipfile

from backend.persistence_errors import ConflictError
from backend.worker_lock import WorkerLease
from simulator.agents.runtime_lease import runtime_use_lease
from .model_assets import AssetError, CHUNK, DownloadPaused

MAX_FILES = 100_000
MAX_FILE_BYTES = 2 * 1024**3
MAX_TOTAL_BYTES = 8 * 1024**3
MAX_MANIFEST_BYTES = 16 * 1024**2
MAX_CONTAINERS = 32
TARGET = "windows-x86_64-cpython312"
_DIGEST = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"[0-9a-f]{40}")
_GENERATION = re.compile(r"(staging|generation)-([0-9a-f]{32})")
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
             *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_ROOT_FILES = {"owner.json", "catalogue.json", "catalogue.pending.json", "active.json",
               "active.pending.json", "operation.worker.lock"}


class CapsuleError(AssetError):
    pass


def _component(value):
    if (not value or value in {".", ".."} or len(value) > 255 or value.endswith((".", " "))
            or any(ord(character) < 32 or ord(character) > 126 or character in '<>:"\\|?*' for character in value)
            or value.split(".", 1)[0].upper() in _RESERVED):
        raise CapsuleError("Capsule paths must use ordinary Windows-safe names")


def _relative(value):
    if not isinstance(value, str) or len(value) > 1024 or "\\" in value:
        raise CapsuleError("Invalid capsule file path")
    parts = value.split("/")
    if len(parts) > 32:
        raise CapsuleError("Capsule path is too deep")
    for part in parts:
        _component(part)
    if value != "capsule.json" and (len(parts) < 2 or parts[0] not in {"runtime", "agent", "notices"}):
        raise CapsuleError("Capsule file is outside its declared runtime, agent or notices")
    return PurePosixPath(value)


def _no_aliases(names):
    seen = {}
    files = set()
    for name in names:
        _relative(name)
        folded = name.casefold()
        if folded in files:
            raise CapsuleError("Capsule contains duplicate or case-aliased files")
        files.add(folded)
        components = name.split("/")
        for count in range(1, len(components) + 1):
            path = "/".join(components[:count])
            key = path.casefold()
            if key in seen and seen[key] != path:
                raise CapsuleError("Capsule contains case-aliased directories")
            seen[key] = path
    for name in names:
        if any(str(parent).casefold() in files for parent in PurePosixPath(name).parents if str(parent) != "."):
            raise CapsuleError("Capsule file conflicts with a directory")


def _plain(path, *, directory=False):
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        # One non-following query covers symlinks, junctions and other Windows
        # reparse points. Repeating several stat calls for every ancestor made
        # full model inventories needlessly expensive.
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise CapsuleError("Reparse points in capsule storage were refused")
        if component == path and (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
                or (not directory and info.st_nlink != 1)):
            raise CapsuleError("Capsule storage must contain ordinary unlinked files")


def _pause(cancelled):
    if cancelled():
        raise DownloadPaused()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CapsuleError("Duplicate JSON fields in capsule metadata")
        result[key] = value
    return result


def _json(data):
    try:
        return json.loads(data, object_pairs_hook=_pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(CapsuleError("Nonfinite capsule metadata")))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise CapsuleError("Capsule metadata is invalid") from error


def _read(path, limit):
    _plain(path)
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise CapsuleError("Capsule metadata exceeds its size limit")
    return value


def _write(path, data):
    _plain(path)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


@dataclass(frozen=True)
class CapsuleSpec:
    id: str
    size: int
    sha256: str
    manifest_sha256: str
    target: str = TARGET

    def __post_init__(self):
        if not isinstance(self.id, str) or not re.fullmatch(r"[a-z0-9-]{1,80}", self.id):
            raise ValueError("Invalid capsule ID")
        _component(self.id)
        if type(self.size) is not int or not 0 < self.size <= MAX_TOTAL_BYTES:
            raise ValueError("Invalid capsule archive size")
        if any(not isinstance(value, str) or not _DIGEST.fullmatch(value)
               for value in (self.sha256, self.manifest_sha256)):
            raise ValueError("Capsule catalogue must pin archive and manifest SHA-256")
        if self.target != TARGET:
            raise ValueError("Unsupported capsule target")


@dataclass(frozen=True)
class InstalledCapsule:
    root: Path
    generation: str
    manifest: dict
    manifest_sha256: str

    @property
    def interpreter(self):
        return self.root / "runtime" / "python.exe"

    @property
    def agent_package(self):
        return self.root / "agent"


def _manifest(data, spec):
    if hashlib.sha256(data).hexdigest() != spec.manifest_sha256:
        raise CapsuleError("Capsule manifest does not match the trusted catalogue")
    value = _json(data)
    if (not isinstance(value, dict) or value.get("schema") != "poltergeist-model-capsule-v1"
            or value.get("id") != spec.id or value.get("target") != spec.target):
        raise CapsuleError("Capsule identity does not match the trusted catalogue")
    if not isinstance(value.get("python_version"), str) or not re.fullmatch(r"3\.12\.\d{1,3}", value["python_version"]):
        raise CapsuleError("Unsupported capsule Python version")
    if (not isinstance(value.get("sdk_wheel_sha256"), str) or not _DIGEST.fullmatch(value["sdk_wheel_sha256"])
            or not isinstance(value.get("source_revision"), str) or not _REVISION.fullmatch(value["source_revision"])):
        raise CapsuleError("Capsule SDK provenance is missing")
    packages = value.get("packages")
    if not isinstance(packages, list) or not 0 < len(packages) <= 1000:
        raise CapsuleError("Capsule dependency inventory is invalid")
    for package in packages:
        if (not isinstance(package, dict) or any(not isinstance(package.get(key), str)
                or not 0 < len(package[key]) <= 128 for key in ("name", "version"))):
            raise CapsuleError("Capsule dependency identity is invalid")
    files = value.get("files")
    if not isinstance(files, dict) or not 0 < len(files) <= MAX_FILES:
        raise CapsuleError("Capsule file inventory is invalid")
    _no_aliases(files)
    total = 0
    for name, entry in files.items():
        if (name == "capsule.json" or not isinstance(entry, dict) or set(entry) != {"sha256", "size"}
                or not isinstance(entry["sha256"], str) or not _DIGEST.fullmatch(entry["sha256"])
                or type(entry["size"]) is not int or not 0 <= entry["size"] <= MAX_FILE_BYTES):
            raise CapsuleError("Capsule file fingerprint is invalid")
        total += entry["size"]
    if total > MAX_TOTAL_BYTES:
        raise CapsuleError("Capsule expanded size exceeds the supported limit")
    if not {"runtime/python.exe", "agent/agent.py", "agent/agent.json"} <= files.keys() or not any(name.startswith("notices/") for name in files):
        raise CapsuleError("Capsule interpreter, agent or licensing notices are missing")
    if value.get("python_executable", "runtime/python.exe") != "runtime/python.exe" or value.get("agent_package", "agent") != "agent":
        raise CapsuleError("Capsule execution paths are unsupported")
    return value


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


@contextmanager
def _archive(source, spec, cancelled, progress):
    source = Path(source)
    _plain(source)
    with source.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if before.st_size != spec.size:
            raise CapsuleError("Capsule archive size does not match the trusted catalogue")
        digest, count = hashlib.sha256(), 0
        while chunk := stream.read(CHUNK):
            _pause(cancelled)
            digest.update(chunk)
            count += len(chunk)
            if count > spec.size:
                raise CapsuleError("Capsule archive changed during verification")
            progress("verifying_capsule", count, spec.size)
        if count != spec.size or digest.hexdigest() != spec.sha256:
            raise CapsuleError("Capsule archive hash does not match the trusted catalogue")
        if _identity(before) != _identity(os.fstat(stream.fileno())):
            raise CapsuleError("Capsule archive changed during verification")
        stream.seek(0)
        try:
            with zipfile.ZipFile(stream) as archive:
                entries = archive.infolist()
                if not 0 < len(entries) <= MAX_FILES + 1:
                    raise CapsuleError("Capsule archive has too many files")
                _no_aliases([entry.filename for entry in entries])
                for entry in entries:
                    kind = stat.S_IFMT(entry.external_attr >> 16)
                    if (entry.is_dir() or kind not in (0, stat.S_IFREG) or entry.flag_bits & 1
                            or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                        raise CapsuleError("Capsule archive must contain unencrypted regular files")
                    if (entry.file_size > MAX_FILE_BYTES or entry.compress_size > spec.size
                            or entry.file_size > max(entry.compress_size, 1) * 10_000):
                        raise CapsuleError("Capsule archive member exceeds its supported limits")
                manifest_entry = archive.getinfo("capsule.json")
                if manifest_entry.file_size > MAX_MANIFEST_BYTES:
                    raise CapsuleError("Capsule manifest exceeds its supported limit")
                data = archive.read(manifest_entry)
                manifest = _manifest(data, spec)
                expected = set(manifest["files"]) | {"capsule.json"}
                if set(archive.namelist()) != expected:
                    raise CapsuleError("Capsule archive differs from its exact manifest inventory")
                for name, entry in manifest["files"].items():
                    if archive.getinfo(name).file_size != entry["size"]:
                        raise CapsuleError("Capsule member size does not match its manifest")
                yield archive, manifest, data
        except (zipfile.BadZipFile, KeyError, RuntimeError, NotImplementedError) as error:
            raise CapsuleError("Capsule ZIP structure or contents are invalid") from error
        _plain(source)
        if _identity(before) != _identity(os.fstat(stream.fileno())) or _identity(before) != _identity(source.stat()):
            raise CapsuleError("Capsule archive changed during use")


def _member(archive, name, entry, cancelled, progress, count, total, output=None):
    digest, size = hashlib.sha256(), 0
    with archive.open(name) as stream:
        while chunk := stream.read(min(CHUNK, entry["size"] - size + 1)):
            _pause(cancelled)
            size += len(chunk)
            if size > entry["size"]:
                raise CapsuleError("Capsule member exceeds its declared size")
            digest.update(chunk)
            if output is not None:
                output.write(chunk)
            progress("installing_environment" if output is not None else "verifying_capsule_contents", count + size, total)
    if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
        raise CapsuleError("Capsule member does not match its pinned fingerprint")
    _pause(cancelled)
    return size


def inspect_archive(source, spec, *, cancelled=lambda: False, progress=lambda *args: None):
    """Read-only archive validation, including every member's complete digest."""
    with _archive(source, spec, cancelled, progress) as (archive, manifest, _):
        count, total = 0, sum(item["size"] for item in manifest["files"].values())
        for name, entry in manifest["files"].items():
            count += _member(archive, name, entry, cancelled, progress, count, total)
        return manifest


class CapsuleStore:
    def __init__(self, profile, spec):
        self.spec = spec
        profile = Path(profile)
        if not profile.is_absolute() or str(profile).startswith(("\\\\", "//")) or ".." in profile.parts:
            raise ValueError("Capsules require an absolute local companion profile")
        self.directory = profile / "model-environments" / spec.id
        self._lease = WorkerLease(SimpleNamespace(file_path=self.directory / "operation"))

    def _owner(self):
        return {"schema": "poltergeist-model-environment-owner-v1", "id": self.spec.id,
                "archive_sha256": self.spec.sha256, "archive_size": self.spec.size,
                "manifest_sha256": self.spec.manifest_sha256, "target": self.spec.target}

    def _guard_root(self):
        _plain(self.directory, directory=True)
        if self.directory.exists():
            for item in self.directory.iterdir():
                if item.name in _ROOT_FILES:
                    _plain(item)
                elif _GENERATION.fullmatch(item.name):
                    _plain(item, directory=True)
                else:
                    raise CapsuleError("Environment folder contains unrecognized files; no user files were removed")

    def _own(self, *, create):
        self._guard_root()
        if not self.directory.exists():
            if not create:
                return False
            self.directory.mkdir(parents=True, exist_ok=False)
            _write(self.directory / "owner.json", _encoded(self._owner()))
        if _json(_read(self.directory / "owner.json", 8192)) != self._owner():
            raise CapsuleError("Environment folder has no matching ownership record")
        try:
            self._lease.acquire()
        except ConflictError:
            raise ConflictError("Another operation or running model owns this environment") from None
        try:
            self._guard_root()
        except BaseException:
            self._lease.release()
            raise
        return True

    def _catalogue(self):
        return _manifest(_read(self.directory / "catalogue.json", MAX_MANIFEST_BYTES), self.spec)

    def _replace_metadata(self, name, data):
        pending = self.directory / name.replace(".json", ".pending.json")
        _plain(pending)
        pending.unlink(missing_ok=True)
        try:
            _write(pending, data)
            self._guard_root()
            pending.replace(self.directory / name)
        finally:
            _plain(pending)
            pending.unlink(missing_ok=True)

    def _installation(self, generation):
        return {"schema": "poltergeist-model-generation-v1", "owner": self._owner(), "generation": generation}

    def _containers(self, manifest):
        result = []
        for path in sorted(self.directory.iterdir()):
            match = _GENERATION.fullmatch(path.name)
            if match is None:
                continue
            if len(result) >= MAX_CONTAINERS:
                raise CapsuleError("Too many retained model environment generations")
            marker = _json(_read(path / ".installation.json", 8192))
            if marker != self._installation(match[2]):
                raise CapsuleError("Model generation has no matching ownership record")
            self._tree(path, manifest, partial=True)
            result.append(path)
        return result

    def _tree(self, root, manifest, *, partial=False, cancelled=lambda: False):
        expected = set(manifest["files"]) | {"capsule.json", ".installation.json"}
        optional = {".runtime-use.worker.lock"}
        allowed = expected | optional
        directories = {str(parent) for name in expected for parent in PurePosixPath(name).parents if str(parent) != "."}
        found, folders = set(), []
        _plain(root, directory=True)
        for parent, child_dirs, files in os.walk(root, followlinks=False):
            _pause(cancelled)
            base = Path(parent)
            for name in child_dirs:
                path = base / name
                _plain(path, directory=True)
                relative = path.relative_to(root).as_posix()
                if relative not in directories:
                    raise CapsuleError("Model generation contains an unrecognized directory")
                folders.append(path)
            for name in files:
                _pause(cancelled)
                path = base / name
                _plain(path)
                relative = path.relative_to(root).as_posix()
                if relative not in allowed:
                    raise CapsuleError("Model generation contains unrecognized files")
                found.add(relative)
                if len(found) > MAX_FILES + 3:
                    raise CapsuleError("Model generation exceeds its file limit")
        if not partial and found - optional != expected:
            raise CapsuleError("Model generation has missing files")
        return found, folders

    def _verify(self, root, manifest, generation, cancelled, progress):
        self._tree(root, manifest, cancelled=cancelled)
        if _json(_read(root / ".installation.json", 8192)) != self._installation(generation):
            raise CapsuleError("Model generation ownership changed")
        if hashlib.sha256(_read(root / "capsule.json", MAX_MANIFEST_BYTES)).hexdigest() != self.spec.manifest_sha256:
            raise CapsuleError("Installed capsule manifest changed")
        count, total = 0, sum(item["size"] for item in manifest["files"].values())
        for name, entry in manifest["files"].items():
            _pause(cancelled)
            path = root / name
            _plain(path)
            if path.stat().st_size != entry["size"]:
                raise CapsuleError("Installed environment size changed; use Repair")
            digest, size = hashlib.sha256(), 0
            with path.open("rb") as stream:
                while chunk := stream.read(CHUNK):
                    _pause(cancelled)
                    size += len(chunk)
                    if size > entry["size"]:
                        raise CapsuleError("Installed environment changed during verification")
                    digest.update(chunk)
                    progress("verifying_environment", count + size, total)
            if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
                raise CapsuleError("Installed environment hash changed; use Repair")
            count += size
        _pause(cancelled)
        return InstalledCapsule(root, generation, manifest, self.spec.manifest_sha256)

    def _active(self, manifest, cancelled, progress, *, leased=False):
        path = self.directory / "active.json"
        if not path.exists():
            return None
        value = _json(_read(path, 8192))
        if not isinstance(value, dict) or set(value) != {"schema", "id", "generation", "manifest_sha256"}:
            raise CapsuleError("Environment activation record is invalid")
        generation = value.get("generation")
        if (value["schema"] != "poltergeist-model-environment-active-v1" or value["id"] != self.spec.id
                or value["manifest_sha256"] != self.spec.manifest_sha256 or not isinstance(generation, str)
                or not re.fullmatch(r"generation-[0-9a-f]{32}", generation)):
            raise CapsuleError("Environment activation identity is invalid")
        root = self.directory / generation
        if leased:
            return self._verify(root, manifest, generation.removeprefix("generation-"), cancelled, progress)
        with runtime_use_lease(root / "runtime"):
            return self._verify(root, manifest, generation.removeprefix("generation-"), cancelled, progress)

    def inspect(self, *, cancelled=lambda: False, progress=lambda *args: None):
        if not self._own(create=False):
            return None
        try:
            if not (self.directory / "catalogue.json").exists() and not (self.directory / "active.json").exists():
                if any(_GENERATION.fullmatch(path.name) for path in self.directory.iterdir()):
                    raise CapsuleError("Interrupted environment has no pinned inventory; reinstall from its capsule")
                return None
            manifest = self._catalogue()
            self._containers(manifest)
            return self._active(manifest, cancelled, progress)
        finally:
            self._lease.release()

    def _delete_contents(self, path, manifest):
        # Validate the exact absolute target before deleting anything and walk
        # only ordinary known files. Never delegate recursive deletion to a
        # second shell or follow a junction through shutil.rmtree.
        self._guard_root()
        if not _GENERATION.fullmatch(path.name) or path.parent != self.directory or not path.resolve().is_relative_to(self.directory.resolve()):
            raise CapsuleError("Refusing to remove outside an owned model generation")
        found, folders = self._tree(path, manifest, partial=True)
        for name in sorted(found - {".installation.json", ".runtime-use.worker.lock"}, reverse=True):
            candidate = path / name
            _plain(candidate)
            candidate.unlink()
        for folder in sorted(folders, key=lambda value: len(value.parts), reverse=True):
            _plain(folder, directory=True)
            folder.rmdir()
    def _finish_delete(self, path):
        # Payload has gone while its runtime lease was held. New workers refuse
        # a missing prefix, so Windows can now close/unlink the generated lock.
        self._guard_root()
        _plain(path, directory=True)
        if set(item.name for item in path.iterdir()) - {".installation.json", ".runtime-use.worker.lock"}:
            raise CapsuleError("Unrecognized files appeared during model removal")
        if (path / "runtime").exists():
            raise CapsuleError("Runtime is still present during final removal")
        for name in (".runtime-use.worker.lock", ".installation.json"):
            _plain(path / name)
            (path / name).unlink(missing_ok=True)
        path.rmdir()

    def install(self, source, *, repair=False, cancelled=lambda: False, progress=lambda *args: None):
        """Activate only fully verified bytes. Repair requires no active users.

        A source is an app catalogue/bundle input, never an arbitrary ZIP whose
        own manifest supplies its trust. Cancellation leaves inactive owned
        staging; the next attempt can safely discard that exact known subset.
        """
        with _archive(source, self.spec, cancelled, progress) as (archive, manifest, data):
            self._own(create=True)
            try:
                catalogue = self.directory / "catalogue.json"
                if catalogue.exists():
                    try:
                        self._catalogue()
                    except (CapsuleError, OSError):
                        if not repair:
                            raise CapsuleError("Saved environment inventory is damaged; use Repair") from None
                        self._replace_metadata("catalogue.json", data)
                else:
                    self._replace_metadata("catalogue.json", data)
                containers = self._containers(manifest)
                # Retrying extraction discards only inactive, inventoried
                # staging. Keep the previous active generation until the new
                # one is verified and selected.
                for container in containers:
                    if container.name.startswith("staging-"):
                        with runtime_use_lease(container / "runtime", allow_missing_prefix=True):
                            self._delete_contents(container, manifest)
                        self._finish_delete(container)
                containers = [path for path in containers if path.name.startswith("generation-")]
                retired = []
                with ExitStack() as owners:
                    for container in containers:
                        owners.enter_context(runtime_use_lease(container / "runtime", allow_missing_prefix=True))
                    if not repair:
                        installed = self._active(manifest, cancelled, progress, leased=True)
                        if installed is not None:
                            return installed
                    _pause(cancelled)
                    total = sum(item["size"] for item in manifest["files"].values())
                    if shutil.disk_usage(self.directory).free < total + CHUNK:
                        raise CapsuleError("There is not enough free space for this model environment")
                    generation = uuid4().hex
                    staging = self.directory / ("staging-" + generation)
                    staging.mkdir()
                    _write(staging / ".installation.json", _encoded(self._installation(generation)))
                    _write(staging / "capsule.json", data)
                    count = 0
                    for name, entry in manifest["files"].items():
                        _pause(cancelled)
                        target = staging / name
                        _plain(target)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with target.open("xb") as output:
                            count += _member(archive, name, entry, cancelled, progress, count, total, output)
                    self._verify(staging, manifest, generation, cancelled, progress)
                    _pause(cancelled)
                    destination = self.directory / ("generation-" + generation)
                    staging.rename(destination)
                    _pause(cancelled)
                    self._replace_metadata("active.json", _encoded({"schema": "poltergeist-model-environment-active-v1",
                        "id": self.spec.id, "generation": destination.name, "manifest_sha256": self.spec.manifest_sha256}))
                    for container in containers:
                        self._delete_contents(container, manifest)
                        retired.append(container)
                for container in retired:
                    self._finish_delete(container)
                return InstalledCapsule(destination, generation, manifest, self.spec.manifest_sha256)
            finally:
                self._lease.release()

    def remove(self):
        """Deactivate before deleting only the pinned, owned file inventory."""
        if not self._own(create=False):
            return
        try:
            manifest = self._catalogue()
            containers = self._containers(manifest)
            with ExitStack() as owners:
                for container in containers:
                    owners.enter_context(runtime_use_lease(container / "runtime", allow_missing_prefix=True))
                for name in ("active.json", "active.pending.json", "catalogue.pending.json"):
                    path = self.directory / name
                    _plain(path)
                    path.unlink(missing_ok=True)
                for container in containers:
                    self._delete_contents(container, manifest)
            for container in containers:
                self._finish_delete(container)
        finally:
            self._lease.release()

    @contextmanager
    def exclude_users(self):
        """Exclude every retained generation while related model bytes change."""
        if not self._own(create=False):
            yield
            return
        try:
            manifest = self._catalogue()
            containers = self._containers(manifest)
            with ExitStack() as owners:
                for container in containers:
                    owners.enter_context(runtime_use_lease(container / "runtime", allow_missing_prefix=True))
                yield
        finally:
            self._lease.release()

    @contextmanager
    def use(self, *, cancelled=lambda: False, progress=lambda *args: None):
        """Hold the OS lease while a caller uses the verified generation."""
        if not self._own(create=False):
            raise CapsuleError("The model environment is not installed")
        try:
            manifest = self._catalogue()
            self._containers(manifest)
            installed = self._active(manifest, cancelled, progress)
            if installed is None:
                raise CapsuleError("The model environment is not installed")
            with runtime_use_lease(installed.interpreter.parent):
                yield installed
        finally:
            self._lease.release()
