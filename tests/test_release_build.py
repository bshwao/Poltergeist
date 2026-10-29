"""Signed build wiring uses public configuration; fixture signing is not a release."""

from hashlib import sha256
import json

import pytest

from scripts import build_installer, release
from test_installer_build import bundle, compiler, compile_bundle


PIN = "a" * 64
SIGNING = {"tool": "C:/tools/signtool.exe", "thumbprint": "B" * 40,
           "timestamp_url": "https://timestamp.example.test", "publisher": PIN}


def prepared_bundle(path, *, pins=None):
    root = bundle(path)
    files = {
        "runtime/Lib/site-packages/companion/release-policy.json": json.dumps({"publishers_sha256": [PIN] if pins is None else pins}).encode(),
        "runtime/Lib/site-packages/poltergeist-0.2.0.dist-info/METADATA": b"Name: poltergeist\nVersion: 0.2.0\n",
    }
    manifest = json.loads((root / "bundle.json").read_bytes())
    for name, contents in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)
        manifest["files"][name] = sha256(contents).hexdigest()
    (root / "bundle.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_signing_requires_bundled_publisher_and_matching_wheel_version(tmp_path, compiler):
    source = prepared_bundle(tmp_path / "bundle", pins=[])
    with pytest.raises(ValueError, match="pin"):
        build_installer.build(source, tmp_path / "untrusted", compiler[0], signing=SIGNING, release_version="0.2.0")
    source = prepared_bundle(tmp_path / "trusted")
    with pytest.raises(ValueError, match="wheel"):
        build_installer.build(source, tmp_path / "wrong-version", compiler[0], signing=SIGNING, release_version="0.3.0")
    assert compiler[1] == []


def test_signed_receipt_is_written_only_after_native_verification(tmp_path, compiler, monkeypatch):
    verified = []
    monkeypatch.setattr("companion.signatures.verify_signature", lambda path, pins: verified.append((path, pins)))
    source = prepared_bundle(tmp_path / "bundle")
    defines, report = compile_bundle(compiler, source, tmp_path / "output", signing=SIGNING, release_version="0.2.0")
    assert defines["SignedBuild"] == "1" and defines["ReleaseVersion"] == "0.2.0"
    assert report["signed"] is True and report["publisher_sha256"] == PIN and verified[0][1] == [PIN]
    def reject(*args):
        raise ValueError("Native signature verification failed")
    monkeypatch.setattr("companion.signatures.verify_signature", reject)
    with pytest.raises(ValueError, match="signature"):
        compile_bundle(compiler, source, tmp_path / "failed", signing=SIGNING, release_version="0.2.0")
    assert not (tmp_path / "failed/installer-build.json").exists()


def test_release_metadata_is_signed_as_data_using_environment_arguments(tmp_path, monkeypatch):
    installer = tmp_path / "Setup.exe"
    installer.write_bytes(b"disposable signed-operation fixture")
    output = tmp_path / "metadata's & file.ps1"
    calls = []
    monkeypatch.setattr(release, "verify_signature", lambda path, pins: calls.append((path, pins)))
    scripts = []
    monkeypatch.setattr(release, "powershell", lambda script, *, variables: scripts.append((script, variables)))
    release.signed_metadata(installer, "1" * 64, output, version="0.2.0", thumbprint="B" * 40,
                            timestamp_url="http://timestamp.example.test", publisher=PIN)
    lines = output.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == "# Poltergeist release metadata v1"
    assert json.loads(lines[1][2:])["installer_sha256"] == sha256(installer.read_bytes()).hexdigest()
    script, variables = scripts[0]
    assert str(output) not in script and variables["POLTERGEIST_SIGNATURE_FILE"] == str(output.absolute())
    assert [path for path, _ in calls] == [installer, output]
