"""External agents drive real runner steps without changing the baseline path."""

from copy import deepcopy
import json
import os
import sys
from threading import Event, Thread
from time import monotonic

import pytest

from scenarios import save_scenario, validate_scenario
from shared.models import ControlInput
from simulator import SimulationRunner, run_simulation
from simulator.agents.process import AgentProcessOptions, ProcessAgent
from simulator.simulation_runner import main
from simulator.test_adapter import TestSimulationAdapter


def scene(**changes):
    return validate_scenario({
        "name": "external-agent", "map": "Town03", "seed": 17,
        "duration_s": 0.2, "fixed_delta_seconds": 0.05,
        "actors": [{"id": "ego", "type": "ego_vehicle", "speed_mps": 2, "spawn_point_index": 0}],
    } | changes)


@pytest.fixture
def package(tmp_path):
    root = tmp_path / "agent-package"
    root.mkdir()
    log = tmp_path / "worker-events.jsonl"
    source = '''
import json
import os
import time
from pathlib import Path
from simulator.agents import AutonomousAgent

class RunnerAgent(AutonomousAgent):
    def record(self, event, **values):
        with Path(self.configuration["log"]).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, "pid": os.getpid(), **values}) + "\\n")

    def load(self, configuration):
        self.configuration = configuration
        self.record("load")
        if configuration.get("fail_load"):
            raise RuntimeError("agent load exploded")

    def reset(self, context):
        self.record("reset", context=context.model_dump(mode="json"))
        if self.configuration.get("fail_reset"):
            raise RuntimeError("agent reset exploded")

    def step(self, observation):
        self.record("step", observation=observation.model_dump(mode="json"))
        if self.configuration.get("sleep_step"):
            time.sleep(self.configuration["sleep_step"])
        if self.configuration.get("fail_step"):
            raise RuntimeError("agent step exploded")
        if self.configuration.get("exit_step"):
            os._exit(23)
        return {
            "frame": observation.frame + self.configuration.get("frame_offset", 0),
            "control": self.configuration.get("control", {"brake": 0.7, "steer": 0.1}),
        }

    def close(self):
        self.record("close")
        if self.configuration.get("fail_close"):
            raise RuntimeError("agent cleanup exploded")
'''
    (root / "agent.py").write_text(source, encoding="utf-8")

    def build(**configuration):
        manifest = {
            "agent_id": "runner-test-agent", "name": "Runner test agent", "version": "0.1.0",
            "entry_point": "agent:RunnerAgent", "configuration": {"log": str(log), **configuration},
        }
        (root / "agent.json").write_text(json.dumps(manifest), encoding="utf-8")
        return root, log

    return build


def events(log):
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


class SpyAdapter(TestSimulationAdapter):
    def __init__(self):
        super().__init__()
        self.open_calls = self.close_calls = 0
        self.controls = []

    def open(self, scenario):
        self.open_calls += 1
        return super().open(scenario)

    def step(self, control, commands):
        self.controls.append(deepcopy(control))
        return super().step(control, commands)

    def close(self):
        self.close_calls += 1
        super().close()


def test_external_process_controls_each_frame_and_receives_episode_context(package):
    path, log = package()
    agent = ProcessAgent(path, python_executable=sys.executable)
    adapter = SpyAdapter()
    output = SimulationRunner(adapter).run(scene(), agent=agent)
    assert output.result.simulation.status == "completed", output.result.simulation.error
    assert output.result.simulation.agent == "custom"
    assert adapter.controls == [ControlInput(brake=0.7, steer=0.1)] * 4
    assert output.telemetry[1].ego.velocity_mps.x < output.telemetry[0].ego.velocity_mps.x
    calls = events(log)
    assert [call["event"] for call in calls] == ["load", "reset", "step", "step", "step", "step", "close"]
    assert len({call["pid"] for call in calls}) == 1
    assert calls[0]["pid"] != os.getpid()
    reset = calls[1]["context"]
    assert (reset["seed"], reset["map_name"], reset["ego_actor_id"], reset["fixed_delta_seconds"]) == (17, "Town03", "ego", 0.05)
    assert [call["observation"]["frame"] for call in calls if call["event"] == "step"] == [0, 1, 2, 3]
    assert [call["observation"]["time_s"] for call in calls if call["event"] == "step"] == pytest.approx([0, 0.05, 0.1, 0.15])
    assert agent.stats.steps == 4
    assert agent.stats.worker_pid == calls[0]["pid"]
    assert agent.stats.round_trip_total_s >= agent.stats.worker_step_total_s >= 0
    assert adapter.close_calls == 1
    assert not agent.is_running


def test_bridge_preserves_unknown_actor_observations_and_actual_frame_ids(package):
    class UnknownActors(SpyAdapter):
        def _snapshot(self, control, collisions):
            frame = super()._snapshot(control, collisions)
            frame.actors = None
            frame.frame += 100
            return frame

    path, log = package()
    agent = ProcessAgent(path)
    result = SimulationRunner(UnknownActors()).run(scene(), agent=agent).result
    assert result.simulation.status == "completed", result.simulation.error
    observations = [entry["observation"] for entry in events(log) if entry["event"] == "step"]
    assert [observation["frame"] for observation in observations] == [100, 101, 102, 103]
    assert all(observation["actors"] is None for observation in observations)
    assert all(observation["traffic_light_red"] is False for observation in observations)
    assert all(observation["route_heading_deg"] == 0 for observation in observations)


@pytest.mark.parametrize("configuration,expected,opened,frames", [
    ({"fail_load": True}, "agent load exploded", 0, 0),
    ({"fail_reset": True}, "agent reset exploded", 1, 1),
    ({"fail_step": True}, "agent step exploded", 1, 1),
    ({"exit_step": True}, "", 1, 1),
    ({"frame_offset": 1}, "frame", 1, 1),
    ({"control": {"throttle": 2}}, "", 1, 1),
])
def test_agent_failures_fail_run_without_fallback_and_close_resources(package, configuration, expected, opened, frames):
    path, _ = package(**configuration)
    agent = ProcessAgent(path)
    adapter = SpyAdapter()
    result = SimulationRunner(adapter).run(scene(), agent=agent).result
    assert result.simulation.status == "failed"
    assert expected in result.simulation.error
    assert result.telemetry_frames == frames
    assert adapter.open_calls == opened
    assert adapter.close_calls == 1
    assert adapter.controls == []
    assert not agent.is_running


def test_slow_agent_hits_step_deadline_and_worker_is_terminated(package):
    path, _ = package(sleep_step=10)
    agent = ProcessAgent(path, options=AgentProcessOptions(step_timeout_s=0.1, close_timeout_s=0.1))
    adapter = SpyAdapter()
    started = monotonic()
    result = SimulationRunner(adapter).run(scene(), agent=agent).result
    assert result.simulation.status == "failed"
    assert "timeout" in result.simulation.error.lower() or "deadline" in result.simulation.error.lower()
    assert monotonic() - started < 8
    assert adapter.controls == []
    assert not agent.is_running
    assert adapter.close_calls == 1


def test_pre_cancelled_run_starts_neither_agent_nor_world(package):
    path, log = package()
    agent = ProcessAgent(path)
    adapter = SpyAdapter()
    runner = SimulationRunner(adapter)
    runner.cancel()
    result = runner.run(scene(), agent=agent).result
    assert result.simulation.status == "cancelled"
    assert not events(log)
    assert not agent.is_running
    assert adapter.open_calls == 0
    assert adapter.close_calls == 1


def test_cancellation_interrupts_waiting_agent_and_cleans_both_resources(package):
    path, log = package(sleep_step=10)
    cancellation = Event()
    agent = ProcessAgent(path, options=AgentProcessOptions(step_timeout_s=15, close_timeout_s=0.1))
    adapter = SpyAdapter()
    stop_watch = Event()

    def cancel_when_step_begins():
        while not stop_watch.wait(0.02):
            if any(entry["event"] == "step" for entry in events(log)):
                cancellation.set()
                return

    watcher = Thread(target=cancel_when_step_begins, daemon=True)
    watcher.start()
    started = monotonic()
    try:
        result = SimulationRunner(adapter).run(scene(), agent=agent, cancellation=cancellation).result
    finally:
        stop_watch.set()
        watcher.join(timeout=2)
    assert result.simulation.status == "cancelled", result.simulation.error
    assert result.simulation.error is None
    assert monotonic() - started < 8
    assert not agent.is_running
    assert adapter.controls == []
    assert adapter.close_calls == 1


def test_adapter_initialization_failure_closes_loaded_agent(package):
    class BrokenAdapter(SpyAdapter):
        def open(self, scenario):
            self.open_calls += 1
            raise RuntimeError("world failed to open")

    path, log = package()
    agent = ProcessAgent(path)
    adapter = BrokenAdapter()
    result = SimulationRunner(adapter).run(scene(), agent=agent).result
    assert result.simulation.status == "failed"
    assert "world failed to open" in result.simulation.error
    assert [call["event"] for call in events(log)] == ["load", "close"]
    assert not agent.is_running
    assert adapter.close_calls == 1


def test_agent_cleanup_failure_preserves_primary_error_and_adapter_cleanup(package):
    path, _ = package(fail_step=True, fail_close=True)
    agent = ProcessAgent(path)
    adapter = SpyAdapter()
    result = SimulationRunner(adapter).run(scene(), agent=agent).result
    assert result.simulation.status == "failed"
    assert "agent step exploded" in result.simulation.error
    assert "agent cleanup exploded" in result.simulation.error
    assert not agent.is_running
    assert adapter.close_calls == 1


def test_existing_scripted_ego_brake_override_remains_explicit_phase3_work(package):
    path, _ = package(control={"throttle": 1, "steer": 0.2})
    result = run_simulation(scene(events=[{
        "action": "brake", "actor_id": "ego", "trigger_time_s": 0, "deceleration_mps2": 4,
    }]), mode="test", agent=ProcessAgent(path))
    assert result.result.simulation.status == "completed"
    assert result.telemetry[1].control == ControlInput(throttle=0, brake=0.5, steer=0.2)


def test_cli_external_agent_keeps_result_stdout_and_puts_timings_on_stderr(package, tmp_path, capsys):
    path, _ = package()
    scenario_path = tmp_path / "scenario.yaml"
    save_scenario(scene(), scenario_path)
    assert main([
        "--scenario", str(scenario_path), "--mode", "test", "--agent-package", str(path),
        "--agent-python", sys.executable, "--agent-load-timeout", "10", "--agent-step-timeout", "1",
        "--agent-close-timeout", "1",
    ]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["simulation"]["agent"] == "custom"
    assert payload["simulation"]["status"] == "completed"
    assert json.loads(captured.err)["agent_process"]["steps"] == 4


@pytest.mark.parametrize("flag,value", [
    ("--agent-python", sys.executable),
    ("--agent-load-timeout", "10"),
    ("--agent-step-timeout", "1"),
    ("--agent-close-timeout", "1"),
])
def test_cli_agent_options_require_package(flag, value, capsys):
    with pytest.raises(SystemExit) as error:
        main(["--template", "intersection", "--mode", "test", flag, value])
    assert error.value.code == 2
    assert "require --agent-package" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_cli_rejects_nonpositive_or_nonfinite_deadline(package, value, capsys):
    path, _ = package()
    with pytest.raises(SystemExit) as error:
        main(["--template", "intersection", "--mode", "test", "--agent-package", str(path), "--agent-step-timeout", value])
    assert error.value.code in (1, 2)
    assert "Simulation failed" in capsys.readouterr().err
