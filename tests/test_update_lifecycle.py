"""Desktop/helper boundaries with fixture signatures; installers never execute."""

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event

import pytest

from backend.services.persistence_service import PersistenceService
from companion.controller import CompanionController
from companion.profile import CompanionConfig, local_settings
from companion.update_lifecycle import wait_for_process_exit
from companion import update_delivery, updates
from scripts import update_companion
from test_update_delivery import candidate, POLICY


@pytest.fixture
def installed(candidate, monkeypatch):
    profile, state, requests, signatures, options = candidate
    install = profile.parent / "app"
    bundle = install / "versions/private-original"
    bundle.mkdir(parents=True)
    (install / "installation.ini").write_text("[Installation]\nAppId=Poltergeist.Private.Companion\n"
        f"Build={bundle.name}\nVersion=0.1.0\nProfile={profile}\n", encoding="utf-8")
    persistence = PersistenceService.from_settings(local_settings(profile, CompanionConfig()))
    persistence.initialize()
    original_check, original_download, original_verify = update_delivery.check_update, update_delivery.download_update, updates.verify_release
    monkeypatch.setattr(updates, "load_policy", lambda: POLICY)
    monkeypatch.setattr(update_delivery, "check_update", lambda target, **kwargs: original_check(target, **(options | kwargs)))
    monkeypatch.setattr(update_delivery, "download_update", lambda target, manifest, **kwargs: original_download(target, manifest, **(options | kwargs)))
    monkeypatch.setattr(updates, "verify_release", lambda *args, **kwargs: original_verify(*args, **kwargs, _verify=options["_verify"]))
    controller = CompanionController(profile, bundle=bundle)
    controller.open()
    yield controller, persistence, state, options, install
    assert controller.close()
    persistence.database.dispose()


def test_controller_review_download_and_prepare_do_not_launch_an_installer(installed):
    controller, persistence, state, options, install = installed
    assert controller.update_status()["enabled"] and controller.update_status()["feed_enabled"]
    assert controller.check_updates(Event())["downloaded"] is False
    assert controller.download_checked_update(Event())["downloaded"] is True
    args = controller.prepare_update_install()
    assert args[:5] == [str(controller._bundle / "runtime/python.exe"), "-I", "-B", "-m", "scripts.update_companion"]
    assert args[args.index("--wait-for-pid") + 1] == str(os.getpid()) and "--notify" in args
    assert Path(args[args.index("--install-root") + 1]) == install
    assert controller._owned and not list(controller.profile.glob("updates/*/receipt.json"))


def test_stopped_runtime_still_refuses_durable_queued_work_before_closing(installed):
    controller, persistence, state, options, install = installed
    controller.check_updates(Event())
    controller.download_checked_update(Event())
    persistence.create_simulation({"name": "Keep queued work", "actors": [{"type": "ego_vehicle"}]}, mode="test")
    with pytest.raises(ValueError, match="queued"):
        controller.prepare_update_install()
    assert controller._owned and controller._update_installer.is_file()
    assert not list(controller.profile.glob("updates/*/receipt.json"))


def test_cached_candidate_can_be_selected_after_reopen_and_removed_explicitly(installed):
    controller, persistence, state, options, install = installed
    controller.check_updates(Event())
    controller.download_checked_update(Event())
    installer, manifest = controller._update_installer, controller._update_offer.manifest
    assert controller.close()
    reopened = CompanionController(controller.profile, bundle=controller._bundle)
    reopened.open()
    try:
        view = reopened.select_signed_update(installer, manifest)
        assert view["downloaded"] and view["from_feed"] and not view["rollback"]
        assert "--rollback" not in reopened.prepare_update_install()
        reopened.clear_update()
        assert not manifest.parent.exists()
    finally:
        assert reopened.close()


def test_expired_cache_can_be_removed_without_accepting_it_as_an_update(installed):
    from datetime import UTC, datetime, timedelta
    controller, persistence, state, options, install = installed
    controller.check_updates(Event())
    controller.download_checked_update(Event())
    installer, manifest = controller._update_installer, controller._update_offer.manifest
    controller._update_offer = controller._update_installer = None
    expired = state["release"].model_copy(update={"created_at": datetime.now(UTC) - timedelta(days=3),
        "expires_at": datetime.now(UTC) - timedelta(days=1)})
    manifest.write_text("# Poltergeist release metadata v1\n# " + expired.model_dump_json() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="validity"):
        controller.select_signed_update(installer, manifest)
    assert controller.remove_cached_update(manifest)["candidate"] is None
    assert not manifest.parent.exists()


def test_explicit_signed_rollback_preserves_selected_local_files(installed):
    controller, persistence, state, options, install = installed
    release = state["release"].model_copy(update={"version": "0.0.9"})
    installer, manifest = install / "SavedSetup.exe", install / "SavedRelease.ps1"
    installer.write_bytes(state["payload"])
    manifest.write_text("# Poltergeist release metadata v1\n# " + release.model_dump_json() + "\n", encoding="utf-8")
    view = controller.select_signed_rollback(installer, manifest)
    assert view["rollback"] and view["downloaded"] and not view["from_feed"]
    assert "--rollback" in controller.prepare_update_install()
    controller.clear_update()
    assert installer.is_file() and manifest.is_file() and controller._update_offer is None


@pytest.mark.skipif(os.name != "nt", reason="Windows process handle lifecycle")
def test_native_helper_waits_for_the_exact_closing_process():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(.3)"], creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        with pytest.raises(ValueError, match="did not exit"):
            wait_for_process_exit(child.pid, timeout=.01)
        wait_for_process_exit(child.pid, timeout=5)
        assert child.wait(timeout=1) == 0
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
    with pytest.raises(ValueError, match="Invalid"):
        wait_for_process_exit(os.getpid())


def test_helper_wait_failure_never_applies_or_reopens_a_release(monkeypatch, tmp_path):
    calls = []
    def wait(*_):
        calls.append("wait")
        raise ValueError("Companion still open")
    monkeypatch.setattr("companion.update_lifecycle.wait_for_process_exit", wait)
    monkeypatch.setattr(updates, "apply_update", lambda *_a, **_k: calls.append("install"))
    monkeypatch.setattr(update_companion, "notify_failure", lambda *_: calls.append("notify"))
    monkeypatch.setattr(update_companion, "reopen_updated_companion", lambda *_: calls.append("reopen"))
    result = update_companion.main(["--data-dir", str(tmp_path), "--installer", str(tmp_path / "Setup.exe"),
        "--manifest", str(tmp_path / "release.ps1"), "--install-root", str(tmp_path / "app"), "--wait-for-pid", "123", "--notify"])
    assert result == 1 and calls == ["wait", "notify"]


def test_reopen_requires_the_verified_installed_build_identity(monkeypatch, tmp_path):
    contents = b'{"fixture":"verified new bundle"}'
    digest = sha256(contents).hexdigest()
    build = "private-" + digest[:16]
    bundle = tmp_path / "app/versions" / build
    bundle.mkdir(parents=True)
    (bundle / "bundle.json").write_bytes(contents)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"state": "installed", "target_build": build, "bundle_manifest_sha256": digest}))
    calls = []
    monkeypatch.setattr("companion.update_lifecycle.launch_update_helper", lambda arguments: calls.append(arguments))
    update_companion.reopen_updated_companion(receipt, tmp_path / "app", tmp_path / "profile")
    assert calls == [[str(bundle / "runtime/pythonw.exe"), "-I", "-B", "-m", "scripts.companion", "--data-dir", str(tmp_path / "profile")]]
    (bundle / "bundle.json").write_bytes(b"Changed after the update")
    with pytest.raises(ValueError, match="identity changed"):
        update_companion.reopen_updated_companion(receipt, tmp_path / "app", tmp_path / "profile")
    assert len(calls) == 1


def test_desktop_entrypoint_launches_helper_after_releasing_its_profile(monkeypatch, tmp_path):
    import tempfile
    from types import SimpleNamespace
    from scripts import companion
    from companion.profile import instance_lease
    profile = tmp_path / "profile"
    observed, created = [], []
    monkeypatch.chdir(tmp_path)
    for name in ("TEMP", "TMP"):
        monkeypatch.setenv(name, os.environ.get(name, str(tmp_path)))
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    monkeypatch.setattr("tkinter.Tk", lambda: SimpleNamespace(mainloop=lambda: None))
    def window(root, controller, logger, **kwargs):
        created.append(controller)
        return SimpleNamespace(_pending_update=["reviewed fixture helper arguments"])
    monkeypatch.setattr("companion.ui.CompanionWindow", window)
    def launch(arguments):
        assert created[0]._owned is False
        lease = instance_lease(profile)
        lease.acquire()
        lease.release()
        observed.append(arguments)
    monkeypatch.setattr("companion.update_lifecycle.launch_update_helper", launch)
    assert companion.main(["--development", "--data-dir", str(profile)]) == 0
    assert observed == [["reviewed fixture helper arguments"]]
