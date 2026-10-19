"""Inspect trusted local packages without execution, then exercise explicit loading."""

import json
from pathlib import Path
import sys

import pytest

from scripts.check_agent import main
from shared.models import ActorState, AgentObservation, AgentReset
from simulator.agents.package import AgentPackageError, inspect_agent_package, load_agent_package
from simulator.agents.sdk import AgentSession, AutonomousAgent


AGENT_SOURCE = '''from pathlib import Path
from .helpers import THROTTLE
from simulator.agents.sdk import AutonomousAgent

class Driver(AutonomousAgent):
    def __init__(self):
        self.configuration = {}

    def load(self, configuration):
        self.configuration = configuration

    def reset(self, context):
        pass

    def step(self, observation):
        return {"frame": observation.frame, "control": {"throttle": THROTTLE}}

    def close(self):
        if "cleanup_file" in self.configuration:
            Path(self.configuration["cleanup_file"]).write_text("closed", encoding="utf-8")
        if self.configuration.get("fail_close"):
            raise RuntimeError("agent cleanup failed")
'''


def write_package(directory, *, source=AGENT_SOURCE, throttle=0.4, **changes):
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "agent_id": "test-package", "name": "Test package", "version": "1.0",
        "entry_point": "agent:Driver", **changes,
    }
    (directory / "agent.json").write_text(json.dumps(payload), encoding="utf-8")
    module = payload["entry_point"].split(":")[0]
    module_path = directory.joinpath(*module.split(".")).with_suffix(".py")
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(source, encoding="utf-8")
    (module_path.parent / "helpers.py").write_text(f"THROTTLE = {throttle!r}\n", encoding="utf-8")
    return directory


def observation():
    return AgentObservation(
        frame=0, time_s=0.0, dt_s=0.05, ego=ActorState(actor_id="ego"),
        target_speed_mps=12.0, actors=[],
    )


def reset_context():
    return AgentReset(seed=0, map_name="Town03", ego_actor_id="ego", fixed_delta_seconds=0.05)


def loaded_module_names(directory):
    result = set()
    for name, module in list(sys.modules.items()):
        module_file = getattr(module, "__file__", None)
        if module_file is not None:
            try:
                if Path(module_file).resolve().is_relative_to(directory.resolve()):
                    result.add(name)
            except (OSError, ValueError):
                pass
    return result


def test_inspection_reads_manifest_and_paths_without_executing_code(tmp_path):
    imported = tmp_path / "executed.txt"
    source = f"from pathlib import Path\nPath({str(imported)!r}).write_text('executed')\n" + AGENT_SOURCE
    directory = write_package(tmp_path / "my-agent", source=source)
    (directory / "weights.bin").write_bytes(b"model")
    (directory / "requirements.txt").write_text("", encoding="utf-8")
    payload = json.loads((directory / "agent.json").read_text(encoding="utf-8"))
    payload.update(assets=["weights.bin"], requirements_file="requirements.txt")
    (directory / "agent.json").write_text(json.dumps(payload), encoding="utf-8")
    original_path = list(sys.path)

    package = inspect_agent_package(directory)

    assert package.directory == directory.resolve()
    assert package.module_path == (directory / "agent.py").resolve()
    assert package.manifest.agent_id == "test-package"
    assert not imported.exists()
    assert loaded_module_names(directory) == set()
    assert sys.path == original_path


@pytest.mark.parametrize("raw", [
    '{"agent_id": "first", "agent_id": "second"}',
    "[]", "{invalid-json", '{"name": "Missing required fields"}',
])
def test_inspection_rejects_ambiguous_or_invalid_manifest(tmp_path, raw):
    directory = write_package(tmp_path / "bad-package")
    (directory / "agent.json").write_text(raw, encoding="utf-8")
    with pytest.raises(AgentPackageError):
        inspect_agent_package(directory)


@pytest.mark.parametrize("missing", ["directory", "manifest", "entry_module", "asset", "requirements"])
def test_missing_package_files_fail_before_execution(tmp_path, missing):
    directory = write_package(tmp_path / "incomplete")
    if missing == "directory":
        directory = tmp_path / "does-not-exist"
    elif missing == "manifest":
        (directory / "agent.json").unlink()
    elif missing == "entry_module":
        (directory / "agent.py").unlink()
    else:
        payload = json.loads((directory / "agent.json").read_text(encoding="utf-8"))
        payload.update({"assets": ["weights.bin"]} if missing == "asset" else {"requirements_file": "requirements.txt"})
        (directory / "agent.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AgentPackageError):
        inspect_agent_package(directory)


def test_sensor_declarations_are_inspected_but_rejected_as_unsupported(tmp_path):
    directory = write_package(
        tmp_path / "sensor-agent", observation_mode="sensors",
        sensors=[{"id": "front", "type": "sensor.camera.rgb"}],
    )
    with pytest.raises(AgentPackageError):
        inspect_agent_package(directory)
    assert loaded_module_names(directory) == set()


@pytest.mark.parametrize("field,value", [
    ("assets", ["../outside.bin"]),
    ("requirements_file", "../requirements.txt"),
    ("entry_point", "../outside:Driver"),
])
def test_manifest_cannot_reference_files_outside_package(tmp_path, field, value):
    directory = write_package(tmp_path / "escape")
    payload = json.loads((directory / "agent.json").read_text(encoding="utf-8"))
    payload[field] = value
    (directory / "agent.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AgentPackageError):
        inspect_agent_package(directory)


@pytest.mark.parametrize("target", ["agent.py", "weights.bin", "requirements.txt"])
def test_symlinks_cannot_escape_package(tmp_path, target):
    directory = write_package(tmp_path / "symlink-agent")
    outside = tmp_path / "outside.py"
    outside.write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
    destination = directory / target
    if destination.exists():
        destination.unlink()
    try:
        destination.symlink_to(outside)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"Symlinks unavailable on this system: {error}")
    payload = json.loads((directory / "agent.json").read_text(encoding="utf-8"))
    if target == "weights.bin":
        payload["assets"] = [target]
    elif target == "requirements.txt":
        payload["requirements_file"] = target
    (directory / "agent.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AgentPackageError):
        inspect_agent_package(directory)


@pytest.mark.parametrize("regular_package", [False, True])
def test_explicit_load_supports_dotted_modules_and_relative_helpers(tmp_path, regular_package):
    directory = write_package(tmp_path / "nested-agent", entry_point="driver.agent:Driver", throttle=0.7)
    if regular_package:
        (directory / "driver" / "__init__.py").write_text("", encoding="utf-8")
    original_path = list(sys.path)
    package = inspect_agent_package(directory)
    agent = load_agent_package(package)
    assert isinstance(agent, AutonomousAgent)
    try:
        assert loaded_module_names(directory)
        with AgentSession(agent, package.manifest) as session:
            session.load()
            session.reset(reset_context())
            assert session.step(observation()).control.throttle == 0.7
        assert loaded_module_names(directory) == set()
    finally:
        agent.close()
    assert sys.path == original_path


def test_same_module_names_in_two_packages_remain_independent(tmp_path):
    first = inspect_agent_package(write_package(tmp_path / "first", throttle=0.2))
    second = inspect_agent_package(write_package(tmp_path / "second", throttle=0.8))
    first_agent, second_agent = load_agent_package(first), load_agent_package(second)
    first_modules = loaded_module_names(first.directory)
    second_modules = loaded_module_names(second.directory)
    assert first_modules and second_modules and first_modules.isdisjoint(second_modules)
    try:
        with AgentSession(first_agent, first.manifest) as first_session, AgentSession(second_agent, second.manifest) as second_session:
            for session in (first_session, second_session):
                session.load()
                session.reset(reset_context())
            assert first_session.step(observation()).control.throttle == 0.2
            assert second_session.step(observation()).control.throttle == 0.8
        assert not loaded_module_names(first.directory)
        assert not loaded_module_names(second.directory)
    finally:
        first_agent.close()
        second_agent.close()


@pytest.mark.parametrize("source", [
    "class Driver: pass\n",
    "Driver = 42\n",
    "class AnotherClass: pass\n",
], ids=["not-agent", "not-class", "missing-class"])
def test_entry_point_must_be_an_instantiable_autonomous_agent(tmp_path, source):
    directory = write_package(tmp_path / "wrong-class", source=source)
    with pytest.raises(AgentPackageError):
        load_agent_package(inspect_agent_package(directory))
    assert loaded_module_names(directory) == set()


def test_constructor_failure_preserves_actionable_error_and_releases_imports(tmp_path):
    source = AGENT_SOURCE.replace("def __init__(self):", "def __init__(self, required_argument):")
    directory = write_package(tmp_path / "bad-constructor", source=source)
    with pytest.raises(AgentPackageError, match="required_argument") as caught:
        load_agent_package(inspect_agent_package(directory))
    assert isinstance(caught.value.__cause__, TypeError)
    assert loaded_module_names(directory) == set()


def test_import_failure_preserves_cause_and_releases_partial_imports(tmp_path):
    source = "from .helpers import THROTTLE\nraise RuntimeError('model dependency failed')\n" + AGENT_SOURCE
    directory = write_package(tmp_path / "bad-import", source=source)
    with pytest.raises(AgentPackageError, match="model dependency failed") as caught:
        load_agent_package(inspect_agent_package(directory))
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert loaded_module_names(directory) == set()


def test_package_modules_are_removed_even_when_agent_cleanup_fails(tmp_path):
    directory = write_package(tmp_path / "bad-cleanup", configuration={"fail_close": True})
    package = inspect_agent_package(directory)
    agent = load_agent_package(package)
    session = AgentSession(agent, package.manifest)
    session.load()
    assert loaded_module_names(directory)
    with pytest.raises(RuntimeError, match="agent cleanup failed"):
        session.close()
    assert loaded_module_names(directory) == set()
    session.close()


def test_default_cli_reports_compatibility_without_importing_agent(tmp_path, capsys):
    sentinel = tmp_path / "unexpected-import.txt"
    source = f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('imported')\n" + AGENT_SOURCE
    directory = write_package(tmp_path / "cli-agent", source=source)

    assert main([str(directory)]) == 0

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["compatible"] is True
    assert report["code_executed"] is False
    assert captured.err == ""
    assert not sentinel.exists()
    assert not loaded_module_names(directory)


def test_cli_smoke_test_runs_real_agent_steps_and_cleans_up(tmp_path, capsys):
    sentinel = tmp_path / "closed.txt"
    directory = write_package(
        tmp_path / "smoke-agent", throttle=0.6,
        configuration={"cleanup_file": str(sentinel)},
    )

    assert main([str(directory), "--smoke-test"]) == 0

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["compatible"] is True
    assert report["smoke_test"]["passed"] is True
    assert report["smoke_test"]["frames"] == [0, 1]
    assert [result["control"]["throttle"] for result in report["smoke_test"]["results"]] == [0.6, 0.6]
    assert captured.err == ""
    assert sentinel.read_text(encoding="utf-8") == "closed"
    assert loaded_module_names(directory) == set()


def test_failed_smoke_step_reports_error_and_still_closes_agent(tmp_path, capsys):
    sentinel = tmp_path / "closed-after-failure.txt"
    directory = write_package(
        tmp_path / "bad-smoke-agent", throttle=2.0,
        configuration={"cleanup_file": str(sentinel)},
    )

    assert main([str(directory), "--smoke-test"]) == 1

    captured = capsys.readouterr()
    assert captured.err
    assert "control" in captured.err.lower() or "throttle" in captured.err.lower()
    assert sentinel.read_text(encoding="utf-8") == "closed"
    assert loaded_module_names(directory) == set()


def test_cli_reports_missing_package_with_nonzero_status(tmp_path, capsys):
    assert main([str(tmp_path / "missing")]) == 1
    assert capsys.readouterr().err


def test_agent_python_logs_do_not_corrupt_the_cli_json_report(tmp_path, capsys):
    source = "print('loading agent module')\n" + AGENT_SOURCE
    source = source.replace(
        "def step(self, observation):",
        "def step(self, observation):\n        print('agent step')",
    )
    directory = write_package(tmp_path / "logging-agent", source=source)
    assert main([str(directory), "--smoke-test"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["smoke_test"]["passed"] is True
    assert "loading agent module" in captured.err
    assert "agent step" in captured.err
