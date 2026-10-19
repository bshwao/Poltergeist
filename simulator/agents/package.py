"""Inspect local agent packages; execute their code only on explicit loading.

Package loading is for trusted local Python code. It is not a security sandbox
or a dependency installer. Imported modules are scoped to one loaded package.
"""

from dataclasses import dataclass
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType
from uuid import uuid4

from shared.models import AgentManifest

from .sdk import AgentCompatibilityError, AgentSDKError, AutonomousAgent, check_agent_capabilities


class AgentPackageError(AgentCompatibilityError):
    """An agent package is incomplete or cannot be loaded as declared."""


@dataclass(frozen=True)
class AgentPackage:
    directory: Path
    manifest: AgentManifest
    module_path: Path


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AgentPackageError(f"Duplicate JSON key in agent manifest: {key}")
        result[key] = value
    return result


def _file(directory: Path, relative: str, label: str) -> Path:
    try:
        candidate = (directory / relative).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise AgentPackageError(f"Missing or inaccessible {label}: {relative}") from error
    if not candidate.is_relative_to(directory):
        raise AgentPackageError(f"{label} must stay inside the agent package: {relative}")
    if not candidate.is_file():
        raise AgentPackageError(f"{label} must be a file: {relative}")
    return candidate


def inspect_agent_package(path: str | Path) -> AgentPackage:
    """Check metadata and declared files without importing code or model weights."""
    try:
        directory = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise AgentPackageError(f"Agent package directory is unavailable: {path}") from error
    if not directory.is_dir():
        raise AgentPackageError("Pass the directory containing agent.json")
    manifest_path = _file(directory, "agent.json", "agent manifest")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        manifest = check_agent_capabilities(payload)
    except AgentPackageError:
        raise
    except (OSError, UnicodeError, ValueError, AgentSDKError) as error:
        raise AgentPackageError(f"Cannot read agent.json: {error}") from error
    for asset in manifest.assets:
        _file(directory, asset, "model asset")
    if manifest.requirements_file is not None:
        _file(directory, manifest.requirements_file, "requirements file")

    module_name = manifest.entry_point.split(":")[0]
    parts = module_name.split(".")
    # Inspect package initializers too; inspection must not allow a parent
    # initializer symlink to resolve outside the selected package directory.
    for length in range(1, len(parts) + 1):
        initializer = "/".join(parts[:length]) + "/__init__.py"
        if (directory / initializer).exists() or (directory / initializer).is_symlink():
            _file(directory, initializer, "module initializer")
    relative_module = "/".join(parts)
    package_module = relative_module + "/__init__.py"
    if (directory / package_module).is_file():
        module_path = _file(directory, package_module, "entry-point module")
    else:
        module_path = _file(directory, relative_module + ".py", "entry-point module")
    return AgentPackage(directory, manifest, module_path)


def _purge_modules(namespace: str) -> None:
    for name in tuple(sys.modules):
        if name == namespace or name.startswith(namespace + "."):
            del sys.modules[name]


class _PackageAgent(AutonomousAgent):
    def __init__(self, agent: AutonomousAgent, namespace: str):
        self._agent, self._namespace = agent, namespace
        self._closed = False

    def load(self, configuration):
        return self._agent.load(configuration)

    def reset(self, context):
        return self._agent.reset(context)

    def step(self, observation):
        return self._agent.step(observation)

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._agent.close()
        finally:
            _purge_modules(self._namespace)


def load_agent_package(package: AgentPackage) -> AutonomousAgent:
    """Import and construct a trusted package; call close to release its modules.

    Local imports should be relative (``from .helpers import ...``). Dependencies
    must already be installed. This function never installs requirements or
    changes sys.path, the working directory, or simulator settings.
    """
    current = inspect_agent_package(package.directory)
    if current.manifest != package.manifest or current.module_path != package.module_path:
        raise AgentPackageError("Agent package declarations changed; inspect the package again")
    namespace = "_poltergeist_agent_" + uuid4().hex
    module = ModuleType(namespace)
    module.__path__ = [str(package.directory)]
    module.__package__ = namespace
    sys.modules[namespace] = module
    module_name, class_name = package.manifest.entry_point.split(":")
    try:
        imported = importlib.import_module(f"{namespace}.{module_name}")
        agent_class = getattr(imported, class_name, None)
        if not isinstance(agent_class, type) or not issubclass(agent_class, AutonomousAgent):
            raise AgentPackageError("Entry-point class must implement AutonomousAgent")
        agent = agent_class()
    except Exception as error:
        _purge_modules(namespace)
        if isinstance(error, AgentPackageError):
            raise
        raise AgentPackageError(
            f"Cannot load entry point {package.manifest.entry_point}: {type(error).__name__}: {error}"
        ) from error
    except BaseException:
        _purge_modules(namespace)
        raise
    return _PackageAgent(agent, namespace)
