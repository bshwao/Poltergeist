"""The installed acceptance driver must never reuse a real user's profile."""

import hashlib
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("packaged_consent_driver", Path(__file__).with_name("packaged_companion_consent.py"))
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
IDENTITY = "Poltergeist.Private.Companion.Validation"


@pytest.fixture
def installation(tmp_path):
    root = tmp_path / "checkout"
    app = root / "validation-evidence/portable-dist/test-app"
    build = "private-" + hashlib.sha256(b"{}").hexdigest()[:16]
    bundle = app / "versions" / build
    bundle.mkdir(parents=True)
    (bundle / "bundle.json").write_bytes(b"{}")
    profile = root / "validation-evidence/test-profile/CompanionPrototype"
    profile.mkdir(parents=True)
    (app / "installation.ini").write_text(
        f"[Installation]\nAppId={IDENTITY}\nBuild={build}\nProfile={profile}\n", encoding="utf-8")
    (profile / "installation-owner.ini").write_text(
        f"[Owner]\nAppId={IDENTITY}\nProfile={profile}\n", encoding="utf-8")
    return root, app, bundle, profile


def test_accepts_only_the_unused_compiled_profile_without_writing_it(installation):
    root, app, bundle, profile = installation
    before = (profile / "installation-owner.ini").read_bytes()
    selected, receipt = driver.installed_validation_profile(root, bundle, app)
    assert selected == profile
    assert receipt["compiled_profile_used"] is True
    assert receipt["shortcut_invoked"] is False
    assert list(profile.iterdir()) == [profile / "installation-owner.ini"]
    assert (profile / "installation-owner.ini").read_bytes() == before


@pytest.mark.parametrize("field,value,error", [
    ("AppId", "Poltergeist.Private.Companion", "installed_identity_or_build_mismatch"),
    ("Build", "private-foreign", "installed_identity_or_build_mismatch"),
])
def test_refuses_normal_installer_or_another_build(installation, field, value, error):
    root, app, bundle, profile = installation
    metadata = app / "installation.ini"
    text = metadata.read_text(encoding="utf-8")
    lines = [f"{field}={value}" if line.startswith(field + "=") else line for line in text.splitlines()]
    metadata.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(RuntimeError, match=error):
        driver.installed_validation_profile(root, bundle, app)


@pytest.mark.parametrize("existing", ["config.json", "database", "remote-enrollment.bin"])
def test_refuses_any_previously_used_profile(installation, existing):
    root, app, bundle, profile = installation
    (profile / existing).write_bytes(b"existing user data must remain intact")
    with pytest.raises(RuntimeError, match="installed_profile_already_used"):
        driver.installed_validation_profile(root, bundle, app)
    assert (profile / existing).read_bytes() == b"existing user data must remain intact"


def test_refuses_a_foreign_profile_owner(installation):
    root, app, bundle, profile = installation
    (profile / "installation-owner.ini").write_text("[Owner]\nAppId=foreign\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="installed_profile_ownership_mismatch"):
        driver.installed_validation_profile(root, bundle, app)


def test_refuses_a_runtime_not_named_by_the_installer(installation):
    root, app, bundle, profile = installation
    foreign = app / "other-runtime"
    foreign.mkdir()
    (foreign / "bundle.json").write_bytes((bundle / "bundle.json").read_bytes())
    with pytest.raises(RuntimeError, match="installed_runtime_mismatch"):
        driver.installed_validation_profile(root, foreign, app)


def test_accepts_inno_unicode_metadata(installation):
    root, app, bundle, profile = installation
    for path in (app / "installation.ini", profile / "installation-owner.ini"):
        text = path.read_text(encoding="utf-8")
        path.write_text(text, encoding="utf-16")
    assert driver.installed_validation_profile(root, bundle, app)[0] == profile


def test_refuses_a_profile_outside_validation_evidence(installation, tmp_path):
    root, app, bundle, profile = installation
    metadata = app / "installation.ini"
    metadata.write_text(metadata.read_text(encoding="utf-8").replace(str(profile), str(tmp_path / "real-user/CompanionPrototype")),
        encoding="utf-8")
    with pytest.raises(RuntimeError, match="installed_path_outside_owned_validation"):
        driver.installed_validation_profile(root, bundle, app)


def test_refuses_a_relative_install_root(installation):
    root, app, bundle, profile = installation
    with pytest.raises(RuntimeError, match="installed_path_must_be_absolute"):
        driver.installed_validation_profile(root, bundle, app.relative_to(root))


def test_refuses_a_linked_owner_marker(installation):
    root, app, bundle, profile = installation
    marker = profile / "installation-owner.ini"
    original = root / "outside-marker.ini"
    marker.rename(original)
    try:
        marker.symlink_to(original)
    except OSError:
        pytest.skip("Native symlink creation is unavailable")
    with pytest.raises(RuntimeError, match="installed_path_outside_owned_validation|installed_path_is_linked"):
        driver.installed_validation_profile(root, bundle, app)


def test_scans_logs_in_the_separate_installed_profile(tmp_path):
    base, profile = tmp_path / "fixture", tmp_path / "compiled-profile"
    base.mkdir()
    (profile / "logs").mkdir(parents=True)
    (base / "coordinator.log").write_text("ordinary fixture output", encoding="utf-8")
    log = profile / "logs/companion.log"
    log.write_text("private sentinel-credential must be detected", encoding="utf-8")
    with pytest.raises(RuntimeError, match="^secret_in_owned_log$"):
        driver.scan_owned_logs(base, profile, ["sentinel-credential"])
    log.write_text("safe companion output", encoding="utf-8")
    receipt = driver.scan_owned_logs(base, profile, ["sentinel-credential"])
    assert receipt["files"] == 2 and receipt["profile_log_files"] == 1
    assert receipt["all_profile_logs_scanned"] is True


def test_nested_portable_profile_logs_are_scanned_once(tmp_path):
    profile = tmp_path / "CompanionPrototype"
    profile.mkdir()
    (profile / "companion.log").write_text("safe output", encoding="utf-8")
    receipt = driver.scan_owned_logs(tmp_path, profile, ["sentinel-credential"])
    assert receipt["files"] == receipt["profile_log_files"] == 1
