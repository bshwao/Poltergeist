"""Signed build wiring uses public configuration; fixture signing is not a release."""

from hashlib import sha256
import json
import tomllib

import pytest

from scripts import build_installer, build_portable, build_release, release
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


@pytest.mark.parametrize(("facts", "error"), [
    ({"present": False}, "not found"),
    ({"publisher_sha256": "b" * 64}, "publisher fingerprint"),
    ({"valid_now": False}, "validity period"),
    ({"allows_signing": False}, "does not allow code signing"),
    ({"has_private_key": False}, "no associated signing key"),
    ({}, None),
])
def test_signing_preflight_requires_exact_eligible_store_certificate(monkeypatch, facts, error):
    info = {"present": True, "publisher_sha256": PIN, "valid_now": True,
            "allows_signing": True, "has_private_key": True, **facts}

    def inspect(script, *, variables):
        assert SIGNING["thumbprint"] not in script
        assert variables == {"POLTERGEIST_SIGNING_THUMBPRINT": SIGNING["thumbprint"]}
        return json.dumps(info)

    monkeypatch.setattr(release, "powershell", inspect)
    if error:
        with pytest.raises(ValueError, match=error):
            release.check_signing_certificate(SIGNING["thumbprint"].lower(), PIN)
    else:
        release.check_signing_certificate(SIGNING["thumbprint"].lower(), PIN)


def test_release_preflight_runs_without_creating_or_signing_artifacts(tmp_path, monkeypatch, capsys):
    tool, compiler_path = tmp_path / "signtool.exe", tmp_path / "ISCC.exe"
    tool.touch()
    certificates, compilers = [], []
    monkeypatch.setattr(release, "check_signing_certificate", lambda *args: certificates.append(args))
    monkeypatch.setattr(build_installer, "compiler_version", compilers.append)
    monkeypatch.setattr("companion.updates.load_policy", lambda: {"publishers_sha256": [PIN]})
    monkeypatch.setattr(build_portable, "build", lambda *args, **kwargs: pytest.fail("Preflight started packaging"))
    version = tomllib.loads((build_portable.ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    arguments = ["--compiler", str(compiler_path), "--version", version, "--signtool", str(tool),
                 "--certificate-thumbprint", SIGNING["thumbprint"], "--publisher-sha256", PIN,
                 "--timestamp-url", SIGNING["timestamp_url"], "--metadata-timestamp-url"]
    assert build_release.main([*arguments, "http://timestamp.example.test", "--preflight"]) == 0
    assert json.loads(capsys.readouterr().out) == {"passed": True, "version": version, "built": False, "signed": False}
    assert certificates == [(SIGNING["thumbprint"], PIN)]
    assert compilers == [compiler_path.resolve()]

    output = tmp_path / "release-output"
    with pytest.raises(SystemExit) as stopped:
        build_release.main([*arguments, "https://timestamp.example.test", "--output", str(output), "--cache", str(tmp_path)])
    assert stopped.value.code == 2 and "http:// timestamp service" in capsys.readouterr().err
    assert not output.exists()
