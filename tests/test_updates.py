"""Release policy, safe points and retained rollback evidence (no real signing key)."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from backend.services.persistence_service import PersistenceService
from companion.profile import instance_lease, local_settings, CompanionConfig
from companion.signatures import verify_signature, signature_details
from companion.updates import ReleaseManifest, apply_update, verify_release, file_digest
from scripts.release import signing_inputs


PIN = "a" * 64
POLICY = {"schema_version": "poltergeist-release-policy-v1", "channel": "private-beta",
          "publishers_sha256": [PIN], "max_installer_bytes": 1024 * 1024}


@pytest.fixture
def release(tmp_path):
    installer = tmp_path / "Setup.exe"
    installer.write_bytes(b"Unsigned disposable installer fixture; never executed")
    bundle = b'{"fixture":"new immutable bundle"}'
    now = datetime.now(UTC)
    values = dict(version="0.2.0", created_at=now - timedelta(minutes=1), expires_at=now + timedelta(days=2),
        profile_schema_max=7,
        installer_bytes=installer.stat().st_size, installer_sha256=file_digest(installer),
        bundle_manifest_sha256=sha256(bundle).hexdigest())
    manifest = tmp_path / "release.ps1"
    def write(**changes):
        value = ReleaseManifest(**(values | changes))
        manifest.write_text("# Poltergeist release metadata v1\n# " + value.model_dump_json() + "\n", encoding="utf-8")
        return value
    write()
    verified = []
    def verify(path, pins):
        assert pins == [PIN]
        verified.append(Path(path))
        return PIN
    return installer, manifest, bundle, write, verify, verified


def test_updates_are_disabled_without_an_independently_installed_publisher(release):
    installer, manifest, _, _, verify, verified = release
    with pytest.raises(ValueError, match="disabled"):
        verify_release(installer, manifest, POLICY | {"publishers_sha256": []}, current_version="0.1.0", _verify=verify)
    assert verified == []
    assert verify_release(installer, manifest, POLICY, current_version="0.1.0", _verify=verify).version == "0.2.0"
    assert verified == [manifest, installer]
    installer.write_bytes(installer.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="integrity"):
        verify_release(installer, manifest, POLICY, current_version="0.1.0", _verify=verify)


def test_release_replay_expiry_and_implicit_downgrades_are_denied(release):
    installer, manifest, _, write, verify, _ = release
    for current in ("0.2.0", "0.3.0"):
        with pytest.raises(ValueError, match="version"):
            verify_release(installer, manifest, POLICY, current_version=current, _verify=verify)
    assert verify_release(installer, manifest, POLICY, current_version="0.3.0", rollback=True, _verify=verify)
    with pytest.raises(ValueError, match="eligible"):
        verify_release(installer, manifest, POLICY, current_version="0.1.0", rollback=True, _verify=verify)
    now = datetime.now(UTC)
    write(created_at=now - timedelta(days=3), expires_at=now - timedelta(days=1))
    with pytest.raises(ValueError, match="validity"):
        verify_release(installer, manifest, POLICY, current_version="0.1.0", _verify=verify)


def profile(root):
    directory = root / "profile"
    persistence = PersistenceService.from_settings(local_settings(directory, CompanionConfig()))
    persistence.initialize()
    install = root / "app"
    install.mkdir()
    (install / "installation.ini").write_text("[Installation]\nAppId=Poltergeist.Private.Companion\n"
        f"Profile={directory}\nBuild=private-previous\nVersion=0.1.0\n", encoding="utf-8")
    return directory, install, persistence


def test_success_backs_up_wal_and_retains_results_without_mutating_agent_identity(tmp_path, release):
    installer, manifest, bundle, write, verify, _ = release
    directory, install, persistence = profile(tmp_path)
    try:
        scenario = persistence.save_scenario({"name": "Keep this recipe", "actors": [{"type": "ego_vehicle"}]})
        calls = []
        def run(arguments, **kwargs):
            calls.append(arguments)
            if len(calls) == 1:
                path = install / "versions" / ("private-" + sha256(bundle).hexdigest()[:16])
                path.mkdir(parents=True)
                (path / "bundle.json").write_bytes(bundle)
                (install / "installation.ini").write_text("[Installation]\nAppId=Poltergeist.Private.Companion\n"
                    f"Profile={directory}\nBuild={path.name}\nVersion=0.2.0\n", encoding="utf-8")
            else:
                # The real migration path is still transactionally idempotent.
                persistence.database.initialize()
        receipt = apply_update(directory, installer, manifest, install, current_version="0.1.0",
                               _policy=POLICY, _verify=verify, _run=run)
        record = json.loads(receipt.read_bytes())
        assert record["state"] == "installed" and record["backup_sha256"] == file_digest(receipt.parent / "database.sqlite")
        assert record["target_build"] == "private-" + sha256(bundle).hexdigest()[:16]
        assert "Version=0.1.0" in (receipt.parent / "installation.ini").read_text()
        with sqlite3.connect(receipt.parent / "database.sqlite") as backup:
            assert backup.execute("SELECT id FROM scenarios").fetchone()[0] == str(scenario.id)
        assert len(calls) == 2 and calls[0][1:3] == ["/VERYSILENT", "/NORESTART"]
        lease = instance_lease(directory)
        lease.acquire()
        lease.release()
    finally:
        persistence.database.dispose()


def test_open_profile_queued_work_and_schema_incompatible_rollback_are_refused(tmp_path, release):
    installer, manifest, _, write, verify, _ = release
    directory, install, persistence = profile(tmp_path)
    lease = instance_lease(directory)
    calls = []
    def attempt(**options):
        return apply_update(directory, installer, manifest, install, current_version="0.1.0",
                            _policy=POLICY, _verify=verify, _run=lambda *a, **k: calls.append(a), **options)
    try:
        lease.acquire()
        with pytest.raises(Exception, match="lease|owns"):
            attempt()
        lease.release()
        persistence.create_simulation({"name": "Queued work", "actors": [{"type": "ego_vehicle"}]}, mode="test")
        with pytest.raises(ValueError, match="queued"):
            attempt()
        write(version="0.0.9", profile_schema_min=6, profile_schema_max=6)
        with pytest.raises(ValueError, match="schema"):
            attempt(rollback=True)
        assert calls == [] and not (directory / "updates").exists()
    finally:
        lease.release()
        persistence.database.dispose()


def test_installer_failure_retains_backup_and_error_receipt(tmp_path, release):
    installer, manifest, _, write, verify, _ = release
    directory, install, persistence = profile(tmp_path)
    persistence.database.dispose()
    def fail(arguments, **kwargs):
        raise subprocess.CalledProcessError(1, arguments)
    with pytest.raises(subprocess.CalledProcessError):
        apply_update(directory, installer, manifest, install, current_version="0.1.0",
                     _policy=POLICY, _verify=verify, _run=fail)
    receipts = list((directory / "updates").glob("*/receipt.json"))
    assert len(receipts) == 1 and json.loads(receipts[0].read_bytes())["state"] == "failed_preserved"
    with sqlite3.connect(receipts[0].parent / "database.sqlite") as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_installer_must_activate_the_exact_release_before_a_migration_probe(tmp_path, release):
    installer, manifest, bundle, write, verify, _ = release
    directory, install, persistence = profile(tmp_path)
    calls = []
    def wrong_activation(arguments, **kwargs):
        calls.append(arguments)
        path = install / "versions" / ("private-" + sha256(bundle).hexdigest()[:16])
        path.mkdir(parents=True)
        (path / "bundle.json").write_bytes(bundle)
        # A zero process exit with an unchanged active marker is not success.
    try:
        with pytest.raises(ValueError, match="activate"):
            apply_update(directory, installer, manifest, install, current_version="0.1.0",
                _policy=POLICY, _verify=verify, _run=wrong_activation)
        assert len(calls) == 1
        receipt = next((directory / "updates").glob("*/receipt.json"))
        assert json.loads(receipt.read_bytes())["state"] == "failed_preserved"
        assert "Version=0.1.0" in (receipt.parent / "installation.ini").read_text()
    finally:
        persistence.database.dispose()


@pytest.mark.skipif(os.name != "nt", reason="Windows Authenticode native check")
def test_native_authenticode_rejects_unsigned_fixture(tmp_path):
    path = tmp_path / "unsigned.ps1"
    path.write_text("# unsigned fixture\n", encoding="utf-8")
    assert signature_details(path)["status"] == "NotSigned"
    with pytest.raises(ValueError, match="signature"):
        verify_signature(path, [PIN])


@pytest.mark.parametrize("url", ["http://timestamp.test", "https://good.test/\";bad", "https://user:password@good.test", "https://good.test/$f"])
def test_signing_command_configuration_refuses_unsafe_substitution(url):
    with pytest.raises(ValueError):
        signing_inputs("signtool.exe", "0" * 40, url, PIN)


def test_new_release_metadata_advertises_current_schema_without_reinterpreting_old_manifests(tmp_path, monkeypatch):
    from scripts import release as producer
    from backend.migrations import CURRENT_REVISION
    installer, output = tmp_path / "Setup.exe", tmp_path / "release.ps1"
    installer.write_bytes(b"disposable signed-release metadata fixture")
    monkeypatch.setattr(producer, "verify_signature", lambda *args: PIN)
    monkeypatch.setattr(producer, "powershell", lambda *args, **kwargs: None)
    producer.signed_metadata(installer, "b" * 64, output, version="0.2.0", thumbprint="0" * 40,
        timestamp_url="http://timestamp.test", publisher=PIN)
    value = ReleaseManifest.model_validate_json(output.read_text(encoding="utf-8-sig").splitlines()[1][2:])
    assert value.profile_schema_min == 6 and value.profile_schema_max == CURRENT_REVISION
    legacy = value.model_dump(exclude={"profile_schema_min", "profile_schema_max"})
    assert ReleaseManifest.model_validate(legacy).profile_schema_max == 6
