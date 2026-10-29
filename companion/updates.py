"""Offline signed updates at a closed-profile safe point, with retained backups."""

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import AwareDatetime, Field, model_validator

from backend.worker_lock import WorkerLease
from shared.models.base import ContractModel
from .profile import instance_lease, profile_path
from .signatures import verify_signature


class ReleaseManifest(ContractModel):
    schema_version: Literal["poltergeist-release-v1"] = "poltergeist-release-v1"
    version: Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})$")]
    channel: Literal["private-beta"] = "private-beta"
    created_at: AwareDatetime
    expires_at: AwareDatetime
    installer_bytes: Annotated[int, Field(ge=1, le=4294967296, strict=True)]
    installer_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    bundle_manifest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    profile_schema_min: Annotated[int, Field(ge=6, le=1000, strict=True)] = 6
    profile_schema_max: Annotated[int, Field(ge=6, le=1000, strict=True)] = 6
    relay_protocol: Literal["poltergeist-relay-v1"] = "poltergeist-relay-v1"
    installer_url: Annotated[str, Field(max_length=2048)] | None = Field(default=None, exclude_if=lambda value: value is None)
    rollout_percent: Annotated[int, Field(ge=0, le=100, strict=True)] = 100
    rollout_salt: Annotated[str, Field(max_length=64, pattern=r"^[A-Za-z0-9_-]*$")] = ""

    @model_validator(mode="after")
    def consistency(self):
        if (self.expires_at <= self.created_at or (self.expires_at - self.created_at).total_seconds() > 30 * 86400
                or self.profile_schema_min > self.profile_schema_max):
            raise ValueError("Invalid release validity or schema window")
        return self


def load_policy():
    return json.loads(Path(__file__).with_name("release-policy.json").read_bytes())


def version_tuple(version):
    return tuple(int(part) for part in version.split("."))


def _plain(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise ValueError("Updates require ordinary local paths")
    if path.exists() and path.is_file() and path.stat().st_nlink != 1:
        raise ValueError("Updates cannot use hard-linked files")
    return path


def file_digest(path):
    digest = sha256()
    with _plain(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class UpdateNotEligible(ValueError):
    pass


def check_profile_state(source, release):
    schema = source.execute("SELECT max(revision) FROM schema_migrations").fetchone()[0]
    if schema is None or not release.profile_schema_min <= schema <= release.profile_schema_max:
        raise ValueError("This release cannot preserve the current profile schema")
    if (source.execute("SELECT count(*) FROM simulations WHERE status IN ('queued','running')").fetchone()[0]
            or source.execute("SELECT count(*) FROM optimization_operations WHERE status IN ('queued','running')").fetchone()[0]):
        raise ValueError("Finish or cancel queued and active work before updating")
    return schema


def verify_manifest(manifest, policy, *, current_version, rollback=False, now=None, _verify=verify_signature):
    pins = policy.get("publishers_sha256", [])
    if policy.get("schema_version") != "poltergeist-release-policy-v1" or not pins:
        raise ValueError("Signed updates are disabled until a release publisher is configured in the installed app")
    _verify(_plain(manifest), pins)
    with Path(manifest).open("rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("Release metadata is too large")
    # The signed .ps1 is data only. It is never executed or dot-sourced.
    lines = data.decode("utf-8-sig").splitlines()
    if len(lines) < 2 or lines[0] != "# Poltergeist release metadata v1" or not lines[1].startswith("# "):
        raise ValueError("Invalid signed release metadata")
    release = ReleaseManifest.model_validate_json(lines[1][2:])
    now = now or datetime.now(UTC)
    if not release.created_at <= now < release.expires_at or release.channel != policy.get("channel"):
        raise ValueError("Release channel or validity window is not accepted")
    import re
    if not re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}", current_version):
        raise ValueError("Invalid installed version")
    if (not rollback and version_tuple(release.version) <= version_tuple(current_version)
            or rollback and version_tuple(release.version) >= version_tuple(current_version)):
        raise UpdateNotEligible("Release version is not eligible for this operation")
    if release.installer_bytes > policy["max_installer_bytes"]:
        raise ValueError("Installer exceeds the installed update policy")
    return release


def verify_release(installer, manifest, policy, *, current_version, rollback=False, now=None, _verify=verify_signature):
    release = verify_manifest(manifest, policy, current_version=current_version, rollback=rollback, now=now, _verify=_verify)
    pins = policy["publishers_sha256"]
    installer = _plain(installer)
    if (installer.stat().st_size != release.installer_bytes or release.installer_bytes > policy["max_installer_bytes"]
            or file_digest(installer) != release.installer_sha256):
        raise ValueError("Installer size or integrity check failed")
    _verify(installer, pins)
    return release


def apply_update(profile, installer, manifest, install_root, *, current_version, rollback=False,
                 _policy=None, _verify=verify_signature, _run=subprocess.run):
    import configparser
    profile, installer, manifest, install_root = map(_plain, (profile_path(_plain(profile)), installer, manifest, install_root))
    policy = load_policy() if _policy is None else _policy
    release = verify_release(installer, manifest, policy, current_version=current_version, rollback=rollback, _verify=_verify)
    if install_root.is_relative_to(profile) or profile.is_relative_to(install_root):
        raise ValueError("Runtime and profile must be separate")
    ini = configparser.ConfigParser()
    ini.read(_plain(install_root / "installation.ini"), encoding="utf-8-sig")
    if (ini.get("Installation", "AppId", fallback="") != "Poltergeist.Private.Companion"
            or Path(ini.get("Installation", "Profile", fallback="")).resolve() != profile.resolve()
            or ini.get("Installation", "Version", fallback="") != current_version):
        raise ValueError("The update does not own this installation/profile")
    desktop = instance_lease(profile)
    desktop.acquire()
    worker = None
    try:
        database = _plain(profile / "database/poltergeist.db")
        from types import SimpleNamespace
        worker = WorkerLease(SimpleNamespace(file_path=database))
        worker.acquire()
        if not database.is_file():
            raise ValueError("Updates require an initialized Companion database")
        # Read-only SQLite backup includes committed WAL pages. No migration runs
        # before the backup and active/queued work checks have completed.
        source = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        try:
            check_profile_state(source, release)
            directory = _plain(profile / "updates" / (release.version + "-" + uuid4().hex))
            if shutil.disk_usage(profile).free < release.installer_bytes + database.stat().st_size * 2 + 512 * 1024**2:
                raise ValueError("Insufficient disk space for update staging and rollback backup")
            directory.mkdir(parents=True)
            with sqlite3.connect(directory / "database.sqlite") as target:
                source.backup(target)
        finally:
            source.close()
        for name in ("companion.json", "installation-owner.ini"):
            path = _plain(profile / name)
            if path.is_file():
                shutil.copyfile(path, directory / name)
        shutil.copyfile(_plain(install_root / "installation.ini"), directory / "installation.ini")
        staged = directory / "Setup.exe"
        shutil.copyfile(installer, staged)
        shutil.copyfile(manifest, directory / "release.ps1")
        # Reverify the staged bytes, preventing path replacement during copying.
        verify_release(staged, directory / "release.ps1", policy, current_version=current_version,
                       rollback=rollback, _verify=_verify)
        receipt = {"schema_version": "poltergeist-update-receipt-v1", "state": "prepared",
            "previous_version": current_version, "target_version": release.version, "rollback": rollback,
            "previous_build": ini.get("Installation", "Build"), "target_build": "private-" + release.bundle_manifest_sha256[:16],
            "bundle_manifest_sha256": release.bundle_manifest_sha256,
            "backup_sha256": file_digest(directory / "database.sqlite")}
        def record(state):
            receipt["state"] = state
            temporary = directory / ".receipt.tmp"
            temporary.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
            temporary.replace(directory / "receipt.json")
        record("prepared")
        try:
            _run([str(staged), "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES", f"/DIR={install_root}"],
                 check=True, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            build = "private-" + release.bundle_manifest_sha256[:16]
            bundle = _plain(install_root / "versions" / build)
            if file_digest(bundle / "bundle.json") != release.bundle_manifest_sha256:
                raise ValueError("Installed bundle identity differs from the signed release")
            active = configparser.ConfigParser()
            active.read(_plain(install_root / "installation.ini"), encoding="utf-8-sig")
            if (active.get("Installation", "AppId", fallback="") != "Poltergeist.Private.Companion"
                    or active.get("Installation", "Build", fallback="") != build
                    or active.get("Installation", "Version", fallback="") != release.version
                    or Path(active.get("Installation", "Profile", fallback="")).resolve() != profile):
                raise ValueError("Installer did not activate the expected release/profile")
            _run([str(bundle / "runtime/python.exe"), "-I", "-B", "-m", "scripts.update_probe", "--profile", str(profile)],
                 check=True, timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except BaseException:
            record("failed_preserved")
            raise
        record("installed")
        return directory / "receipt.json"
    finally:
        if worker is not None:
            worker.release()
        desktop.release()
