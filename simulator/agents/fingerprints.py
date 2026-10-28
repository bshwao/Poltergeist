"""Static identity checks for trusted local agent registrations.

No agent module, interpreter, model loader, or dependency installer is run here.
These hashes detect changes in the declared local execution inputs. They do not
snapshot drivers, environment variables, or hardware. An explicitly pinned
managed runtime manifest additionally verifies its complete file inventory.
"""

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from uuid import UUID

from shared.models import (
    AgentExternalFileFingerprint, AgentFileFingerprint, AgentManifest,
    AgentRegistration, AgentSnapshot,
)

from .package import inspect_agent_package


class AgentFingerprintError(ValueError):
    """Declared files cannot be safely inspected, or their identity changed."""


_EXCLUDED_DIRECTORIES = {
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".venv", "venv", "node_modules",
}
_RUNTIME_PACKAGES = ("backend", "evaluation", "optimization", "scenarios", "shared", "simulator")
_MAX_FILES = 20000


def _link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise AgentFingerprintError(f"A regular local file is required: {path}")
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            after = os.fstat(stream.fileno())
        current = path.stat()
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
        if identity(before) != identity(after) or identity(after) != identity(current):
            raise AgentFingerprintError(f"File changed during fingerprint inspection: {path}")
    except (OSError, RuntimeError) as error:
        raise AgentFingerprintError(f"Cannot inspect local file: {path}") from error
    return digest.hexdigest(), after.st_size


def _file(path: Path, *, relative_to: Path | None = None):
    digest, size = _hash_file(path)
    if relative_to is not None:
        return AgentFileFingerprint(path=path.relative_to(relative_to).as_posix(), sha256=digest, size_bytes=size)
    return AgentExternalFileFingerprint(path=str(path), sha256=digest, size_bytes=size)


def _walk(directory: Path, *, python_only: bool = False) -> list[Path]:
    files = []
    def fail(error):
        raise AgentFingerprintError(f"Cannot enumerate local package: {directory}") from error

    for parent, directories, names in os.walk(directory, followlinks=False, onerror=fail):
        base = Path(parent)
        for name in directories:
            if _link(base / name):
                raise AgentFingerprintError(f"Package directory links and junctions are unsupported: {base / name}")
        directories[:] = sorted(name for name in directories if name not in _EXCLUDED_DIRECTORIES)
        for name in sorted(names):
            path = base / name
            if _link(path):
                raise AgentFingerprintError(f"Package file links are unsupported: {path}")
            if python_only and path.suffix != ".py":
                continue
            if not path.is_file() or not path.resolve().is_relative_to(directory):
                raise AgentFingerprintError(f"Package inputs must be regular files inside their directory: {path}")
            files.append(path)
            if len(files) > _MAX_FILES:
                raise AgentFingerprintError("Package contains too many files for a bounded registration")
    return files


def _canonical_file(value: str) -> Path:
    if value.startswith(("\\\\", "//")):
        raise AgentFingerprintError("Network share model files are unsupported; select local files")
    try:
        lexical = Path(value).expanduser()
        if not lexical.is_absolute():
            lexical = Path.cwd() / lexical
        # Preserve the lexical components until links have been checked. In
        # particular, normalizing alias/../weights before this check could hide
        # a parent link while agent configuration still opens the mutable alias.
        for component in (lexical, *lexical.parents):
            if _link(component):
                raise AgentFingerprintError(f"Declared model files must not use symlinks or junctions: {component}")
        path = lexical.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise AgentFingerprintError(f"Declared model file is unavailable: {value}") from error
    if not path.is_file():
        raise AgentFingerprintError(f"Declared model file must be a regular file: {path}")
    return path


def _runtime_files(root=None) -> list[AgentExternalFileFingerprint]:
    root = Path(__file__).resolve().parents[2] if root is None else Path(root)
    if not root.is_absolute() or ".." in root.parts:
        raise AgentFingerprintError("Runtime provenance requires an absolute local root")
    for component in (root, *root.parents):
        if _link(component):
            raise AgentFingerprintError("Linked runtime provenance was refused")
    paths = []
    for package in _RUNTIME_PACKAGES:
        directory = root / package
        if not directory.is_dir():
            raise AgentFingerprintError(f"First-party runtime package is unavailable: {directory}")
        paths.extend(_walk(directory, python_only=True))
    return sorted((_file(path) for path in paths), key=lambda item: item.path)


def build_agent_snapshot(request: AgentRegistration, registration_id: UUID, *, _framework_root=None) -> AgentSnapshot:
    """Resolve declarations and hash bytes without executing user code."""
    request = AgentRegistration.model_validate(request.model_dump(mode="python"))
    if request.package_path.startswith(("\\\\", "//")) or (
        request.python_executable is not None and request.python_executable.startswith(("\\\\", "//"))
    ):
        raise AgentFingerprintError("Network share execution paths are unsupported; select local files")
    package = inspect_agent_package(request.package_path)
    if package.manifest.observation_mode == "sensors" and request.agent_control_mode != "authoritative":
        raise AgentFingerprintError("Sensor agents require authoritative control mode")
    directory = package.directory
    files = {path.relative_to(directory).as_posix(): path for path in _walk(directory)}
    for relative in ["agent.json", *package.manifest.assets, *(
        [package.manifest.requirements_file] if package.manifest.requirements_file else []
    )]:
        candidate = directory / relative
        for part in [candidate, *candidate.parents]:
            if part == directory:
                break
            if _link(part):
                raise AgentFingerprintError(f"Declared package input must not use links: {candidate}")
        files[relative] = candidate
    if len(files) > _MAX_FILES:
        raise AgentFingerprintError("Package contains too many files for a bounded registration")
    package_files = [_file(files[name], relative_to=directory) for name in sorted(files)]
    canonical = json.dumps([item.model_dump(mode="json") for item in package_files], sort_keys=True, separators=(",", ":"))
    package_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # Do not resolve a venv interpreter symlink: its lexical path chooses the
    # environment even when its bytes come from a shared system executable.
    from .interpreter import worker_interpreter
    try:
        executable = worker_interpreter(request.python_executable)
    except ValueError as error:
        raise AgentFingerprintError(str(error)) from error
    if not executable.is_file():
        raise AgentFingerprintError(f"Agent Python executable is unavailable: {executable}")
    python_sha256, python_size_bytes = _hash_file(executable)
    configuration = package.manifest.configuration | request.configuration
    model_paths = {_canonical_file(value) for value in request.model_files}
    if package.manifest.agent_id == "poltergeist-reference-ai":
        from .reference_ai.checkpoint import DEFAULT_CHECKPOINT

        value = configuration.get("checkpoint_path", str(DEFAULT_CHECKPOINT))
        if not isinstance(value, str) or not value.strip():
            raise AgentFingerprintError("Reference AI checkpoint_path must be a local file path")
        checkpoint = _canonical_file(value)
        configuration["checkpoint_path"] = str(checkpoint)
        model_paths.add(checkpoint)
    manifest = AgentManifest.model_validate(package.manifest.model_dump(mode="python") | {"configuration": configuration})
    isolated = request.isolated_runtime
    framework_root, framework_files = None, []
    if isolated is not None:
        prefix = Path(isolated.prefix)
        if prefix != prefix.resolve() or ".." in prefix.parts:
            raise AgentFingerprintError("Select the canonical independent runtime prefix")
        runtime_files = _runtime_files(prefix / "Lib" / "site-packages")
        framework_root = Path(__file__).resolve().parents[2] if _framework_root is None else Path(_framework_root)
        framework_files = _runtime_files(framework_root)
        if isolated.manifest_sha256 is not None:
            from .runtime_manifest import verify_runtime_manifest
            try:
                verify_runtime_manifest(prefix, isolated.manifest_sha256)
            except (ValueError, OSError, RuntimeError) as error:
                raise AgentFingerprintError("The managed runtime seal is unavailable or changed") from error
    else:
        if _framework_root is not None:
            raise AgentFingerprintError("Separate framework provenance requires an isolated runtime")
        runtime_files = _runtime_files()
    return AgentSnapshot(
        registration_id=registration_id, package_path=str(directory), python_executable=str(executable),
        python_sha256=python_sha256, python_size_bytes=python_size_bytes,
        manifest=manifest, configuration=configuration, observation_mode=manifest.observation_mode,
        files=package_files, model_files=sorted((_file(path) for path in model_paths), key=lambda item: item.path),
        runtime_files=runtime_files, package_sha256=package_sha256,
        isolated_runtime=isolated, framework_runtime_root=None if framework_root is None else str(framework_root),
        framework_runtime_files=framework_files,
        process_options=request.process_options, agent_control_mode=request.agent_control_mode,
    )


def _verify_worker_origin(snapshot):
    prefix = Path(snapshot.isolated_runtime.prefix)
    if (os.name != "nt" or sys.version_info[:2] != (3, 12) or not sys.flags.isolated
            or not sys.flags.ignore_environment or Path(sys.prefix).resolve() != prefix
            or Path(sys.base_prefix).resolve() != prefix
            or Path(sys.executable).resolve() != prefix / "python.exe"):
        raise AgentFingerprintError("Worker is not using its declared isolated Windows Python runtime")
    site = prefix / "Lib" / "site-packages"
    allowed = {Path(item.path).resolve() for item in snapshot.runtime_files}
    for name, module in tuple(sys.modules.items()):
        package = name.split(".", 1)[0]
        if package in _RUNTIME_PACKAGES:
            location = getattr(module, "__file__", None)
            if (location is None or not Path(location).resolve().is_relative_to(site / package)
                    or Path(location).resolve() not in allowed):
                raise AgentFingerprintError("Worker imported framework code outside its isolated runtime")


def verify_agent_snapshot(snapshot: AgentSnapshot, *, worker=False) -> None:
    """Reinspect the exact queued version; activation does not rewrite history."""
    snapshot = AgentSnapshot.model_validate(snapshot.model_dump(mode="python"))
    request = AgentRegistration(
        package_path=snapshot.package_path, python_executable=snapshot.python_executable,
        configuration=snapshot.configuration, model_files=[item.path for item in snapshot.model_files],
        process_options=snapshot.process_options, agent_control_mode=snapshot.agent_control_mode,
        isolated_runtime=snapshot.isolated_runtime,
        trusted_local_code=True,
    )
    try:
        if worker and snapshot.isolated_runtime is not None:
            _verify_worker_origin(snapshot)
        current = build_agent_snapshot(request, snapshot.registration_id,
            _framework_root=snapshot.framework_runtime_root if worker and snapshot.isolated_runtime is not None else None)
    except (ValueError, OSError, RuntimeError) as error:
        raise AgentFingerprintError(f"Registered agent inputs are unavailable or changed: {error}") from error
    expected = snapshot.model_dump(mode="json")
    actual = current.model_dump(mode="json")
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if canonical(actual) != canonical(expected):
        fields = [name for name in AgentSnapshot.model_fields if canonical(actual.get(name)) != canonical(expected.get(name))]
        raise AgentFingerprintError("Registered agent inputs changed; register a new version: " + ", ".join(fields))
