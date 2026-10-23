"""Installer directories identify complete verified runtimes, not just app code."""

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from scripts import build_installer


def bundle(root, *, dependency_version="1"):
    files = {
        "runtime/python.exe": b"disposable native runtime fixture",
        "runtime/Lib/site-packages/scripts/install_check.py": b"# S3 fixture\n",
        "runtime/Lib/site-packages/dependency.py": dependency_version.encode(),
        f"runtime/Lib/site-packages/dependency-{dependency_version}.dist-info/METADATA":
            f"Name: dependency\nVersion: {dependency_version}\n".encode(),
    }
    for relative, contents in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    manifest = {
        "schema_version": "poltergeist-portable-v1",
        "project_wheel": {"sha256": "a" * 64},
        "files": {name: hashlib.sha256(contents).hexdigest() for name, contents in files.items()},
    }
    (root / "bundle.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return root


@pytest.fixture
def compiler(monkeypatch, tmp_path):
    """Capture actual compiler inputs; never execute or install native code."""
    calls = []
    version = json.loads((build_installer.ROOT / "packaging/windows-x64/installer-toolchain.json").read_bytes())["version"]
    monkeypatch.setattr(build_installer.sys, "platform", "win32")
    monkeypatch.setattr(build_installer.subprocess, "check_output", lambda *args, **kwargs: version + "\n")

    def compile_installer(arguments, **kwargs):
        definitions = dict(argument.removeprefix("--define=").split("=", 1)
                           for argument in arguments if argument.startswith("--define="))
        calls.append(definitions)
        (Path(definitions["OutputDir"]) / "Poltergeist-Companion-Private-Setup.exe").write_bytes(
            b"disposable compiled installer fixture")

    monkeypatch.setattr(build_installer.subprocess, "run", compile_installer)
    return tmp_path / "ISCC.exe", calls


def compile_bundle(compiler, bundle_path, output, **kwargs):
    executable, calls = compiler
    installer = build_installer.build(bundle_path, output, executable, **kwargs)
    return calls[-1], json.loads((installer.parent / "installer-build.json").read_bytes())


def test_dependency_only_change_uses_a_separate_runtime_directory(tmp_path, compiler):
    first = bundle(tmp_path / "dependency-one", dependency_version="1")
    second = bundle(tmp_path / "dependency-two", dependency_version="2")
    assert json.loads((first / "bundle.json").read_bytes())["project_wheel"] == json.loads(
        (second / "bundle.json").read_bytes())["project_wheel"]

    first_defines, first_report = compile_bundle(compiler, first, tmp_path / "first-output")
    second_defines, second_report = compile_bundle(compiler, second, tmp_path / "second-output")

    assert first_defines["BuildId"] != second_defines["BuildId"]
    for source, defines, report in ((first, first_defines, first_report), (second, second_defines, second_report)):
        digest = hashlib.sha256((source / "bundle.json").read_bytes()).hexdigest()
        assert report["bundle_manifest_sha256"] == digest
        assert report["build_id"] == defines["BuildId"] == "private-" + digest[:16]


def test_identical_bundle_keeps_its_identity_after_relocation(tmp_path, compiler):
    original = bundle(tmp_path / "original")
    relocated = tmp_path / "relocated bundle"
    shutil.copytree(original, relocated)

    first_defines, first_report = compile_bundle(compiler, original, tmp_path / "first-output")
    second_defines, second_report = compile_bundle(compiler, relocated, tmp_path / "second-output")

    assert first_defines["BundleDir"] != second_defines["BundleDir"]
    assert first_defines["BuildId"] == second_defines["BuildId"]
    assert first_report["bundle_manifest_sha256"] == second_report["bundle_manifest_sha256"]


@pytest.mark.parametrize("validation", [False, True])
def test_metadata_records_the_compiled_validation_profile(tmp_path, compiler, validation):
    source = bundle(tmp_path / "bundle")
    profile = tmp_path / "disposable user data" / "CompanionPrototype" if validation else None
    definitions, report = compile_bundle(compiler, source, tmp_path / "output", validation_profile=profile)

    assert report["validation_only"] is validation
    assert report["validation_profile"] == (str(profile) if validation else None)
    if validation:
        assert definitions["ProfileDir"] == report["validation_profile"]
        assert not profile.exists()
    else:
        assert "ProfileDir" not in definitions


def test_damaged_bundle_is_refused_before_invoking_the_compiler(tmp_path, compiler):
    source = bundle(tmp_path / "bundle")
    (source / "runtime/Lib/site-packages/dependency.py").write_bytes(b"unverified change")

    with pytest.raises(ValueError, match="integrity check failed"):
        build_installer.build(source, tmp_path / "output", compiler[0])

    assert not compiler[1]
    assert not (tmp_path / "output").exists()
