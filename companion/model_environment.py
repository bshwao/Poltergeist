"""Pinned, application-local PlanT CPU runtime; no pip, venv or setup scripts.

The capsule has its own interpreter and dependency search path. A narrow import
finder exposes only Poltergeist's first-party packages from the installed app,
so worker provenance uses the same source paths as the parent without exposing
the core runtime's third-party site-packages.
"""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import tarfile
from types import SimpleNamespace
import zipfile

from backend.worker_lock import WorkerLease
from .model_assets import AssetError, AssetStore, ModelAsset, _check_pause, _plain

LOCK_PATH = Path(__file__).with_name("plant-cpu.lock.json")
APP_ROOT = Path(__file__).resolve().parents[1]
FIRST_PARTY = ("backend", "evaluation", "optimization", "scenarios", "shared", "simulator")


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _relative(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or ".." in path.parts or "\\" in name
            or ":" in name or any(part.rstrip(" .") != part for part in path.parts)):
        raise AssetError("Unsafe path in model environment")
    return path


def _tree(directory):
    _plain(directory, directory=True)
    files, pending = set(), [directory] if directory.exists() else []
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                # DirEntry.stat may report st_nlink=0 from cached Windows
                # enumeration data. os.stat obtains the real hardlink count.
                info = os.stat(entry.path, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) in {0xA0000003, 0xA000000C}:
                    raise AssetError("Linked model environment path")
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    files.add(Path(entry.path))
                else:
                    raise AssetError("Model environment requires ordinary unlinked files")
    return files


def _local_file(path):
    # Ancestors were checked once by _tree. Rechecking every ancestor for every
    # installed file makes large Torch packs needlessly slow on Windows.
    if path.is_symlink() or path.is_junction():
        raise AssetError("Linked model environment file")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise AssetError("Model environment requires ordinary unlinked files")


def unpack(archive, destination, kind, cancelled=lambda: False):
    """Install verified archive data, never execute package build/install code."""
    total = 0
    if kind == "antlr-source":
        with tarfile.open(archive, "r:gz") as source:
            for member in source:
                _check_pause(cancelled)
                path = _relative(member.name)
                if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                    raise AssetError("Linked/special source archive member")
                # This one pure-Python dependency has no upstream wheel. Only
                # its import sources and original notices are installed.
                parts = path.parts[1:]
                if parts[:2] == ("src", "antlr4") and member.isfile():
                    target = destination.joinpath(*parts[1:])
                elif len(parts) == 1 and member.isfile() and path.name in {"LICENSE.txt", "LICENSE", "PKG-INFO"}:
                    target = destination / "antlr4_python3_runtime-4.9.3.dist-info" / path.name
                else:
                    continue
                total += member.size
                if total > 32 * 1024**2:
                    raise AssetError("Source archive is too large")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as incoming, target.open("xb") as output:
                    shutil.copyfileobj(incoming, output)
        metadata = destination / "antlr4_python3_runtime-4.9.3.dist-info" / "METADATA"
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text("Metadata-Version: 2.1\nName: antlr4-python3-runtime\nVersion: 4.9.3\nLicense: BSD-3-Clause\n", encoding="utf-8")
        return
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            _check_pause(cancelled)
            path = _relative(member.filename)
            if stat.S_ISLNK(member.external_attr >> 16):
                raise AssetError("Linked archive member")
            parts = path.parts
            if parts[0].endswith(".data"):
                if len(parts) < 3 or parts[1] not in {"purelib", "platlib"}:
                    # Console scripts/headers are not needed in an inference
                    # runtime. Retain the original wheel in the download cache.
                    continue
                parts = parts[2:]
            if member.is_dir():
                continue
            total += member.file_size
            if total > 3 * 1024**3 or member.file_size > 2 * 1024**3:
                raise AssetError("Expanded model package exceeds its size budget")
            target = destination.joinpath(*parts)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise AssetError("Archive path escaped model environment")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(member) as incoming, target.open("xb") as output:
                shutil.copyfileobj(incoming, output)


class ModelEnvironment:
    def __init__(self, profile):
        self.profile = Path(profile)
        self.lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        self.directory = self.profile / "environments" / self.lock["id"]
        self.runtime = self.directory / "runtime"
        self.python = self.runtime / "python.exe"
        self.package = self.runtime / "agent"
        self.marker = self.directory / "owner.json"
        self.receipt = self.directory / "ready.json"
        self._lease = WorkerLease(SimpleNamespace(file_path=self.directory / "environment"))
        self.identity = {"schema": self.lock["schema"], "lock_sha256": digest(LOCK_PATH)}

    def supported(self):
        if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
            raise AssetError("The managed PlanT pack supports Windows x64 CPU only")

    @contextmanager
    def owned(self):
        _plain(self.directory, directory=True)
        if not self.directory.exists():
            self.directory.mkdir(parents=True)
            with self.marker.open("x", encoding="utf-8") as output:
                json.dump(self.identity, output)
        _plain(self.marker)
        if not self.marker.is_file() or self.marker.stat().st_size > 4096 or json.loads(self.marker.read_bytes()) != self.identity:
            raise AssetError("Model environment ownership does not match this catalogue")
        _plain(self.directory / "environment.worker.lock")
        self._lease.acquire()
        try:
            if {p.name for p in self.directory.iterdir()} - {"owner.json", "ready.json", "runtime", "staging", "environment.worker.lock"}:
                raise AssetError("Model environment contains unrecognized files")
            self._inventory = _tree(self.directory)
            yield
        finally:
            self._lease.release()

    def _delete(self, path):
        # Windows: validate absolute target and every link before recursive
        # removal. Only these two owned, fixed subdirectories may be pruned.
        if path not in {self.runtime, self.directory / "staging"} or path.resolve().parent != self.directory.resolve():
            raise AssetError("Refusing removal outside the managed environment")
        _tree(path)
        if path.exists():
            shutil.rmtree(path)

    def _verify(self, cancelled=lambda: False):
        _plain(self.receipt)
        if not self.receipt.is_file() or self.receipt.stat().st_size > 8 * 1024**2:
            raise AssetError("Prepare the model environment first")
        receipt = json.loads(self.receipt.read_bytes())
        if receipt.get("identity") != self.identity or receipt.get("app_root") != str(APP_ROOT):
            raise AssetError("Model environment was built for another app; use Repair")
        for name, expected in receipt.get("app_files", {}).items():
            _check_pause(cancelled)
            path = APP_ROOT / _relative(name)
            if not path.is_file() or digest(path) != expected:
                raise AssetError("The app changed since model preparation; use Repair environment")
        files = receipt.get("files")
        if not isinstance(files, dict) or not files or len(files) > 30000:
            raise AssetError("Invalid model environment manifest")
        actual = {p.relative_to(self.runtime).as_posix() for p in self._inventory if p.is_relative_to(self.runtime)}
        if actual != set(files):
            raise AssetError("Model environment has missing or unexpected files; use Repair")
        for name, expected in files.items():
            _check_pause(cancelled)
            path = self.runtime / _relative(name)
            if digest(path) != expected:
                raise AssetError("Model environment integrity check failed; use Repair")
        return receipt

    def verify(self, cancelled=lambda: False):
        with self.owned():
            return self._verify(cancelled)

    def prepare(self, weights, *, cancelled=lambda: False, progress=lambda *a: None, repair=False):
        self.supported()
        with self.owned():
            if self.receipt.exists() and not repair:
                self._verify(cancelled)
                return
            self.receipt.unlink(missing_ok=True)
            self._delete(self.directory / "staging")
            self._delete(self.runtime)
            entries = [self.lock["runtime"], *self.lock["packages"]]
            if shutil.disk_usage(self.directory).free < 3 * 1024**3:
                raise AssetError("Allow at least 3 GiB free disk space to prepare PlanT")
            archives = []
            all_bytes, done = sum(e["size"] for e in entries), 0
            for entry in entries:
                _check_pause(cancelled)
                asset = ModelAsset("env-" + entry["sha256"][:24], entry["filename"], entry["url"], entry["size"], entry["sha256"])
                store = AssetStore(self.profile / "environment-downloads", asset)
                def report(state, count, total):
                    progress("environment_download", done + count, all_bytes)
                archive = store.download(cancelled=cancelled, progress=report, repair=repair)
                archives.append((entry, archive))
                done += entry["size"]
            staging = self.directory / "staging"
            staging.mkdir()
            site = staging / "site-packages"
            site.mkdir()
            for index, (entry, archive) in enumerate(archives):
                progress("installing", index, len(archives))
                unpack(archive, staging if entry["kind"] == "runtime" else site, entry["kind"], cancelled)
            (staging / "python312._pth").write_text("python312.zip\n.\nsite-packages\nimport site\n", encoding="ascii")
            # No core third-party path is added to sys.path. Only these six
            # first-party package names can resolve from the installed app.
            bridge = ("import importlib.abc, importlib.util, pathlib, sys\n"
                      f"ROOT = pathlib.Path({str(APP_ROOT)!r})\n"
                      f"ALLOWED = {FIRST_PARTY!r}\n"
                      "class AppFinder(importlib.abc.MetaPathFinder):\n"
                      " def find_spec(self, fullname, path=None, target=None):\n"
                      "  if fullname in ALLOWED:\n"
                      "   p = ROOT / fullname / '__init__.py'\n"
                      "   return importlib.util.spec_from_file_location(fullname, p, submodule_search_locations=[str(p.parent)])\n"
                      "sys.meta_path.insert(0, AppFinder())\n")
            (site / "sitecustomize.py").write_text(bridge, encoding="utf-8")
            package = staging / "agent"
            package.mkdir()
            (package / "agent.py").write_text("from simulator.agents.reference_ai import ReferenceAIAgent\nclass ReferenceAgent(ReferenceAIAgent):\n    pass\n", encoding="utf-8")
            manifest = {"protocol_version": "1.2", "agent_id": "poltergeist-reference-ai", "name": "Managed PlanT 2.0 (CPU)",
                        "version": "0.1.0", "entry_point": "agent:ReferenceAgent", "observation_mode": "simulator_state",
                        "control_mode": "vehicle_control", "navigation_mode": "provided_route", "required_features": ["structured_scene"],
                        "sensors": [], "assets": [], "configuration": {"device": "cpu", "torch_threads": 2, "cuda_graphs": False, "checkpoint_path": str(weights)}}
            (package / "agent.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            notices = staging / "notices"
            notices.mkdir()
            shutil.copy2(LOCK_PATH, notices / "inputs.json")
            shutil.copy2(APP_ROOT / "simulator/agents/reference_ai/LICENSE.plant2", notices / "LICENSE.plant2")
            for name in ("LICENSE.plant-model", "LICENSE.antlr"):
                shutil.copy2(Path(__file__).with_name(name), notices / name)
            (notices / "MODEL.txt").write_text("PlanT 2.0: Apache-2.0 model, revision e81b261f8b92b20225d020fe9b7a0470c1434d96.\nAdapted integration: MIT (LICENSE.plant2). Dependency license texts remain in site-packages/*.dist-info.\nCPU pack; no CUDA or real-time performance guarantee.\n", encoding="utf-8")
            _check_pause(cancelled)
            staging.rename(self.runtime)
            progress("checking_environment", 0, 1)
            files = {}
            paths = [p for p in self.runtime.rglob("*") if p.is_file()]
            for index, path in enumerate(paths):
                _check_pause(cancelled)
                files[path.relative_to(self.runtime).as_posix()] = digest(path)
                if index % 100 == 0:
                    progress("checking_environment", index, len(paths))
            app_files = {p.relative_to(APP_ROOT).as_posix(): digest(p)
                         for name in FIRST_PARTY for p in (APP_ROOT / name).rglob("*.py")}
            value = {"identity": self.identity, "app_root": str(APP_ROOT), "files": files, "app_files": app_files}
            with self.receipt.open("x", encoding="utf-8") as output:
                json.dump(value, output, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())

    def remove(self):
        with self.owned():
            self.receipt.unlink(missing_ok=True)
            self._delete(self.runtime)
            self._delete(self.directory / "staging")
            for entry in [self.lock["runtime"], *self.lock["packages"]]:
                asset = ModelAsset("env-" + entry["sha256"][:24], entry["filename"], entry["url"], entry["size"], entry["sha256"])
                AssetStore(self.profile / "environment-downloads", asset).remove()

    @contextmanager
    def use(self, cancelled=lambda: False):
        """Hold through registration/execution, excluding repair and removal."""
        with self.owned():
            self._verify(cancelled)
            yield self
