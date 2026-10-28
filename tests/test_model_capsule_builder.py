"""Publisher input identity, relocation and deterministic capsule reconstruction."""

import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import stat
import tarfile
import zipfile

import pytest

from scripts import build_model_capsule as builder


def digest(data):
    return hashlib.sha256(data).hexdigest()


def wheel(path, name, version, *, files=None, tag="py3-none-any", license=True, bad_record=False, requires=(), python_requires=None, native_record=False):
    package = name.replace("-", "_")
    dist = f"{package}-{version}.dist-info"
    dependencies = "".join("Requires-Dist: " + value + "\n" for value in requires)
    if python_requires:
        dependencies += "Requires-Python: " + python_requires + "\n"
    payload = {f"{package}/__init__.py": b"fixture only; never executed\n",
               f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nLicense: MIT\n{dependencies}\n".encode(),
               f"{dist}/WHEEL": f"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: {tag}\n".encode(),
               **(files or {})}
    if license:
        payload[f"{dist}/licenses/LICENSE.txt"] = b"fixture license text\n"
    record = io.StringIO(newline="")
    output = csv.writer(record, lineterminator="\n")
    for entry, data in sorted(payload.items()):
        token = base64.urlsafe_b64encode(bytes.fromhex(digest(data))).rstrip(b"=").decode()
        output.writerow([entry.replace("/", "\\") if native_record else entry, "sha256=" + token, str(len(data) + int(bad_record))])
    output.writerow([f"{dist}\\RECORD" if native_record else f"{dist}/RECORD", "", ""])
    payload[f"{dist}/RECORD"] = record.getvalue().encode()
    with zipfile.ZipFile(path, "x") as archive:
        for entry, data in payload.items():
            archive.writestr(entry, data)
    return {"name": name, "version": version, "filename": path.name, "sha256": builder.sha256(path), "bytes": path.stat().st_size}


def runtime(path, *, extra=None, version="3.12.15", target="x86_64-pc-windows-msvc"):
    files = {"python/PYTHON.json": json.dumps({"python_version": version, "target_triple": target}).encode(),
             "python/licenses/LICENSE.cpython.txt": b"runtime license\n", "python/install/python.exe": b"MZfixture",
             "python/install/Lib/os.py": b"# stdlib fixture\n", "python/install/Lib/ensurepip/__init__.py": b"# remove\n",
             "python/install/Lib/site-packages/pip/__init__.py": b"# remove\n", "python/install/Scripts/pip.exe": b"# remove\n",
             "python/install/Lib/__pycache__/os.pyc": b"# remove\n", **(extra or {})}
    with tarfile.open(path, "w") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return {"version": version, "sha256": builder.sha256(path)}


@pytest.fixture
def inputs(tmp_path):
    archive = tmp_path / "runtime.tar"
    spec = runtime(archive)
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    packages = []
    for name in ("poltergeist", "torch", "torchvision", "transformers", "timm", "omegaconf", "numpy", "scipy"):
        version = "1.0+cpu" if name in {"torch", "torchvision"} else "1.0"
        packages.append(wheel(wheels / f"{name}-{version}-py3-none-any.whl", name, version))
    manifest = tmp_path / "packages.json"
    manifest.write_text(json.dumps({"packages": packages}))
    return {"runtime_archive": archive, "runtime_spec": spec, "wheels_dir": wheels, "packages_manifest": manifest,
            "source_revision": "d" * 40}


def rewrite_package(inputs, name, **kwargs):
    manifest = inputs["packages_manifest"]
    values = json.loads(manifest.read_bytes())
    item = next(value for value in values["packages"] if value["name"] == name)
    path = inputs["wheels_dir"] / item["filename"]
    path.unlink()
    replacement = wheel(path, name, item["version"], **kwargs)
    values["packages"] = [replacement if value["name"] == name else value for value in values["packages"]]
    manifest.write_text(json.dumps(values))
    return replacement


def test_reconstruction_is_deterministic_and_omits_installer_tools(tmp_path, inputs):
    first = builder.build(tmp_path / "a", **inputs)
    second = builder.build(tmp_path / "b", **inputs)
    assert first == second
    capsule = tmp_path / "a/model-capsule.zip"
    assert first["sha256"] == builder.sha256(capsule)
    assert first["manifest_sha256"] == digest(builder.json_bytes(first["manifest"]))
    with zipfile.ZipFile(capsule) as archive:
        names = archive.namelist()
        assert "runtime/python.exe" in names
        assert "notices/python/licenses/LICENSE.cpython.txt" in names
        assert not any("__pycache__" in name or "/pip/" in name or "/Scripts/" in name or "/ensurepip/" in name for name in names)
        manifest = json.loads(archive.read("capsule.json"))
        assert set(names) == set(manifest["files"]) | {"capsule.json"}
        agent = json.loads(archive.read("agent/agent.json"))
        assert agent["configuration"] == {"device": "cpu", "torch_threads": 2, "cuda_graphs": False}
        assert "requirements_file" not in agent
        assert all(not info.is_dir() and stat.S_IFMT(info.external_attr >> 16) == stat.S_IFREG for info in archive.infolist())


def test_relocated_data_and_rebuilt_records_reference_installed_payload(tmp_path, inputs):
    rewrite_package(inputs, "numpy", files={"numpy-1.0.data/data/share/example.txt": b"data",
                                            "numpy-1.0.data/scripts/launcher": b"obsolete console path"})
    result = builder.build(tmp_path / "built", **inputs)
    with zipfile.ZipFile(tmp_path / "built/model-capsule.zip") as archive:
        assert archive.read("runtime/share/example.txt") == b"data"
        assert not any(".data/" in name for name in archive.namelist())
        record = archive.read("runtime/Lib/site-packages/numpy-1.0.dist-info/RECORD").decode()
        rows = list(csv.reader(io.StringIO(record)))
        assert [row for row in rows if row[0] == "../../share/example.txt"]
        assert not any("launcher" in row[0] for row in rows)
        assert result["manifest"]["packages"][0]["record_rebuilt"]


@pytest.mark.parametrize("change", ["runtime_hash", "wheel_hash", "wheel_size", "metadata_name", "record", "abi", "missing_dependency"])
def test_invalid_publisher_identity_cannot_publish_capsule(tmp_path, inputs, change):
    if change == "runtime_hash":
        inputs["runtime_spec"]["sha256"] = "0" * 64
    elif change in {"wheel_hash", "wheel_size", "missing_dependency"}:
        value = json.loads(inputs["packages_manifest"].read_bytes())
        if change == "wheel_hash":
            value["packages"][0]["sha256"] = "0" * 64
        elif change == "wheel_size":
            value["packages"][0]["bytes"] += 1
        else:
            value["packages"] = [item for item in value["packages"] if item["name"] != "torch"]
        inputs["packages_manifest"].write_text(json.dumps(value))
    elif change == "metadata_name":
        value = json.loads(inputs["packages_manifest"].read_bytes())
        value["packages"][0]["name"] = "claimed-sdk"
        inputs["packages_manifest"].write_text(json.dumps(value))
    elif change == "record":
        rewrite_package(inputs, "numpy", bad_record=True)
    elif change == "abi":
        rewrite_package(inputs, "numpy", tag="cp312-cp312-manylinux_2_28_x86_64")
    output = tmp_path / "built"
    with pytest.raises(ValueError):
        builder.build(output, **inputs)
    assert not (output / "model-capsule.zip").exists()


@pytest.mark.parametrize("path", ["../escape", "runtime/NUL.txt", "runtime/x:stream", "runtime/file.", "runtime/file ",
                                  "runtime/CONIN$", "runtime/a\\b", "runtime/../escape", "runtime/\u03b4.py"])
def test_windows_path_aliases_and_escapes_are_rejected(path):
    with pytest.raises(ValueError):
        builder.safe_name(path)


@pytest.mark.parametrize("names", [("runtime/Lib/a.py", "runtime/lib/b.py"), ("runtime/x", "runtime/x/a.py"),
                                  ("runtime/x/a.py", "runtime/x"), ("runtime/a.py", "runtime/a.py")])
def test_case_and_file_directory_collisions_are_refused(names):
    registry = builder.Names()
    registry.add(names[0])
    with pytest.raises(ValueError):
        registry.add(names[1])


def test_missing_notice_requires_a_verified_supplement(tmp_path, inputs):
    rewrite_package(inputs, "numpy", license=False)
    with pytest.raises(ValueError, match="license/notice"):
        builder.build(tmp_path / "missing", **inputs)
    notice = tmp_path / "upstream-license.txt"
    notice.write_bytes(b"upstream fixture license")
    supplement = {"package": "numpy", "filename": "LICENSE.txt", "path": notice, "sha256": builder.sha256(notice)}
    built = builder.build(tmp_path / "present", supplemental_notices=[supplement], **inputs)
    assert "notices/packages/numpy/supplemental/LICENSE.txt" in built["manifest"]["files"]
    supplement["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="identity"):
        builder.build(tmp_path / "bad-supplement", supplemental_notices=[supplement], **inputs)


def test_tested_prefix_is_compared_to_pins_instead_of_blessed(tmp_path, inputs):
    builder.build(tmp_path / "baseline", **inputs)
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    with zipfile.ZipFile(tmp_path / "baseline/model-capsule.zip") as archive:
        for name in archive.namelist():
            if name.startswith("runtime/"):
                target = prefix / name.removeprefix("runtime/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
    builder.build(tmp_path / "matching", tested_prefix=prefix, **inputs)
    (prefix / "python.exe").write_bytes(b"changed")
    with pytest.raises(ValueError, match="differs"):
        builder.build(tmp_path / "changed", tested_prefix=prefix, **inputs)


def test_inspection_rejects_an_extra_file_even_with_a_valid_manifest(tmp_path, inputs):
    builder.build(tmp_path / "built", **inputs)
    capsule = tmp_path / "built/model-capsule.zip"
    with zipfile.ZipFile(capsule, "a") as archive:
        archive.writestr("runtime/injected.py", b"extra")
    with pytest.raises(ValueError, match="exact"):
        builder.inspect_capsule(capsule)


def test_existing_output_is_never_overwritten(tmp_path, inputs):
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "user-file"
    sentinel.write_bytes(b"preserve")
    with pytest.raises(FileExistsError):
        builder.build(output, **inputs)
    assert sentinel.read_bytes() == b"preserve"


@pytest.mark.parametrize("requirement", ["missing>=1", "numpy>=2", "numpy @ https://example.invalid/code.whl",
                                          "missing; sys_platform == 'win32'"])
def test_incomplete_or_incompatible_transitive_graph_is_rejected(tmp_path, inputs, requirement):
    rewrite_package(inputs, "timm", requires=[requirement])
    with pytest.raises(ValueError, match="dependency graph"):
        builder.build(tmp_path / "bad-graph", **inputs)


def test_target_markers_and_transitive_extras_are_evaluated(tmp_path, inputs):
    rewrite_package(inputs, "timm", requires=["scipy[accelerated]", "missing; sys_platform == 'linux'"])
    rewrite_package(inputs, "scipy", requires=["missing; extra == 'accelerated'"])
    with pytest.raises(ValueError, match="dependency graph"):
        builder.build(tmp_path / "bad-extra", **inputs)
    rewrite_package(inputs, "scipy", requires=["numpy>=1; extra == 'accelerated'"], python_requires=">=3.12,<3.13")
    builder.build(tmp_path / "valid-graph", **inputs)
    rewrite_package(inputs, "scipy", python_requires=">=3.13")
    with pytest.raises(ValueError, match="Python version"):
        builder.build(tmp_path / "bad-python", **inputs)


def test_windows_record_references_are_normalized_without_weakening_archive_paths(tmp_path, inputs):
    rewrite_package(inputs, "torchvision", native_record=True)
    builder.build(tmp_path / "native-record", **inputs)
    rewrite_package(inputs, "torchvision", files={"../escape.py": b"no"}, native_record=True)
    with pytest.raises(ValueError, match="Unsafe"):
        builder.build(tmp_path / "unsafe-record", **inputs)


def test_vendored_metadata_is_payload_and_does_not_replace_root_identity(tmp_path, inputs):
    rewrite_package(inputs, "timm", files={"timm/_vendor/other-2.dist-info/METADATA": b"Name: other\n",
        "timm/_vendor/other-2.dist-info/RECORD": b"vendored record\n",
        "timm/_vendor/other-2.dist-info/WHEEL": b"Tag: cp310-cp310-linux_x86_64\n"})
    result = builder.build(tmp_path / "vendor", **inputs)
    assert "runtime/Lib/site-packages/timm/_vendor/other-2.dist-info/RECORD" in result["manifest"]["files"]


def test_curated_inference_pack_omits_compiler_inputs_and_dependency_tests(tmp_path, inputs):
    rewrite_package(inputs, "torch", files={"torch/include/ATen/Operators.h": b"compiler input",
        "torch/include/README": b"compiler input", "torch/share/cmake/TorchConfig.cmake": b"compiler input",
        "torch/lib/torch_cpu.pdb": b"debug symbols", "torch/lib/torch_cpu.dll": b"runtime fixture"})
    rewrite_package(inputs, "numpy", files={"numpy/tests/test_fixture.py": b"dependency test"})
    rewrite_package(inputs, "timm", files={"timm/layers/test_time_pool.py": b"runtime module"})
    result = builder.build(tmp_path / "curated", **inputs)
    files = result["manifest"]["files"]
    assert "runtime/Lib/site-packages/torch/lib/torch_cpu.dll" in files
    assert "runtime/Lib/site-packages/timm/layers/test_time_pool.py" in files
    assert not any("/tests/" in name or "/torch/include/" in name or name.endswith((".h", ".pdb", ".cmake")) for name in files)
