"""Reference mode keeps learned controls authoritative and evidence associated."""

import json

import pytest

from scenarios import validate_scenario
from shared.models import AgentSceneContext, Transform, Vector3
from simulator import SimulationRunner
from simulator.agents import ProcessAgent
from simulator.errors import SimulationError
from simulator.test_adapter import TestSimulationAdapter


def recipe(events=None):
    return validate_scenario({
        "name": "reference-runner", "map": "Town03", "duration_s": .1,
        "fixed_delta_seconds": .05,
        "actors": [{"id": "ego", "type": "ego_vehicle", "speed_mps": 2, "spawn_point_index": 0}],
        "events": events or [],
    })


def test_cycle_timing_includes_all_decision_callback_work(monkeypatch):
    from simulator import simulation_runner as module
    clock = [100.0]
    monkeypatch.setattr(module, "perf_counter", lambda: clock[0])
    compute_times = []
    def evidence(record):
        compute_times.append(record["full_step_wall_time_s"])
        clock[0] += .2
    result = SimulationRunner(TestSimulationAdapter()).run(recipe(), mode="test", on_decision=evidence)
    assert result.result.simulation.status == "completed"
    assert compute_times == [0.0, 0.0]
    assert result.step_wall_times_s == pytest.approx((.2, .2))
    assert result.execution_wall_time_s == pytest.approx(.4)


@pytest.fixture
def agent_package(tmp_path):
    directory = tmp_path / "driver"
    directory.mkdir()
    log = tmp_path / "lifecycle.jsonl"
    (directory / "agent.json").write_text(json.dumps({
        "protocol_version": "1.1", "agent_id": "reference-test", "name": "Reference test",
        "version": "1", "entry_point": "agent:Driver", "required_features": ["structured_scene"],
        "configuration": {"log": str(log)},
    }))
    (directory / "agent.py").write_text('''
import json
from pathlib import Path
from simulator.agents import AutonomousAgent
class Driver(AutonomousAgent):
    def record(self, kind, data):
        with Path(self.log).open("a") as stream:
            stream.write(json.dumps({"kind":kind,"data":data})+"\\n")
    def load(self, configuration):
        self.log = configuration["log"]
        self.record("load", {})
    def reset(self, context): self.record("reset", context.model_dump(mode="json"))
    def step(self, observation):
        self.record("step", observation.model_dump(mode="json"))
        return {"protocol_version":"1.1", "frame":observation.frame, "control":{"throttle":.4}}
    def close(self): self.record("close", {})
''')
    return directory, log


class SceneAdapter(TestSimulationAdapter):
    def __init__(self):
        super().__init__()
        self.closed = False

    def open(self, scenario):
        return super().open(scenario).model_copy(update={"frame": 100})

    def step(self, control, commands):
        frame = super().step(control, commands)
        return frame.model_copy(update={"frame": frame.frame + 100})

    def agent_context(self, frame):
        return AgentSceneContext(source="synthetic", objects=[], speed_limit_mps=10,
                                 route=[Transform(), Transform(position=Vector3(x=20))])

    def close(self):
        self.closed = True
        super().close()


def test_authoritative_rejects_conflicting_ego_script_before_loading(agent_package):
    package, log = agent_package
    agent = ProcessAgent(package)
    with pytest.raises(SimulationError, match="ego-control"):
        SimulationRunner().run(recipe([{"actor_id": "ego", "action": "brake", "trigger_time_s": 0,
                                        "deceleration_mps2": 4}]), mode="test", agent=agent,
                               agent_control_mode="authoritative")
    assert not log.exists() and agent.pid is None
    agent.close()


def test_authoritative_rejects_non_sdk_controller():
    with pytest.raises(SimulationError, match="external SDK"):
        SimulationRunner().run(recipe(), mode="test", agent_control_mode="authoritative")


def test_scene_reset_observation_and_applied_controls_retain_frame_identity(agent_package):
    package, log = agent_package
    adapter = SceneAdapter()
    agent = ProcessAgent(package)
    decisions = []
    output = SimulationRunner(adapter).run(recipe(), mode="test", agent=agent,
        agent_control_mode="authoritative", on_decision=decisions.append)
    assert output.result.simulation.status == "completed", output.result.simulation.error
    assert adapter.closed and not agent.is_running
    assert output.setup_wall_time_s > 0 and output.execution_wall_time_s > 0
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    reset = next(call["data"] for call in calls if call["kind"] == "reset")
    assert reset["protocol_version"] == "1.1" and len(reset["route"]) == 2
    # A rolling scene horizon is guidance, not an actual episode destination.
    assert reset["destination"] is None
    assert [item["frame"] for item in decisions] == [100, 101]
    for item in decisions:
        assert item["observation"]["scene"]["source"] == "synthetic"
        assert item["agent_result"]["frame"] == item["frame"]
        assert item["resulting_frame"] == item["frame"] + 1
        assert item["applied_control"] == item["proposed_control"] == item["agent_result"]["control"]
        assert item["interventions"] == []
        assert item["worker_step_wall_time_s"] >= 0
        assert item["full_step_wall_time_s"] > 0


def test_missing_required_scene_fails_and_closes_worker(agent_package):
    package, log = agent_package
    adapter = SceneAdapter()
    adapter.agent_context = None
    agent = ProcessAgent(package)
    output = SimulationRunner(adapter).run(recipe(), mode="test", agent=agent,
                                           agent_control_mode="authoritative")
    assert output.result.simulation.status == "failed"
    assert "structured scene" in output.result.simulation.error
    assert adapter.closed and not agent.is_running
    assert json.loads(log.read_text().splitlines()[-1])["kind"] == "close"


def test_evidence_callback_failure_fails_run_and_cleans_up(agent_package):
    package, _ = agent_package
    adapter = SceneAdapter()
    agent = ProcessAgent(package)
    def broken(_):
        raise OSError("trace disk failure")
    output = SimulationRunner(adapter).run(recipe(), mode="test", agent=agent,
        agent_control_mode="authoritative", on_decision=broken)
    assert output.result.simulation.status == "failed"
    assert "trace disk failure" in output.result.simulation.error
    assert adapter.closed and not agent.is_running
