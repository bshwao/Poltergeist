"""Driving tasks preserve control ownership throughout the real worker loop."""

import json
from pathlib import Path
from threading import Event

import pytest

from scenarios import load_scenario, validate_scenario
from shared.models import DrivingTask, Transform, Vector3
from simulator import SimulationRunner
from simulator.agents import ProcessAgent
from simulator.errors import SimulationError
from simulator.test_adapter import TestSimulationAdapter

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "agents" / "goal_following"


def pose(x=0.0, y=0.0, z=0.0):
    return Transform(position=Vector3(x=float(x), y=float(y), z=float(z)))


def recipe(*, task=None, events=None, initial_speed=2.0, duration=1.0):
    return validate_scenario({"name": "driving-goal", "map": "Town03", "duration_s": duration,
        "fixed_delta_seconds": 0.05,
        "actors": [{"id": "ego", "type": "ego_vehicle", "speed_mps": initial_speed,
                    "spawn": pose().model_dump()}], "events": events or [],
        "driving_task": task.model_dump() if task is not None else None})


class ObservedAdapter(TestSimulationAdapter):
    def __init__(self):
        super().__init__()
        self.open_count, self.close_count = 0, 0

    def open(self, scenario):
        self.open_count += 1
        return super().open(scenario)

    def observation(self, *args, **kwargs):
        raise AssertionError("Explicit tasks must not use legacy adapter route/speed hints")

    def close(self):
        self.close_count += 1
        super().close()


@pytest.fixture
def package(tmp_path):
    directory = tmp_path / "task-agent"
    directory.mkdir()
    (directory / "agent.py").write_text('''
from simulator.agents import AutonomousAgent
class Driver(AutonomousAgent):
    def load(self, configuration): self.configuration = configuration
    def reset(self, context): self.context = context
    def step(self, observation):
        return {"protocol_version": self.configuration["protocol"], "frame": observation.frame,
            "control": self.configuration.get("control", {}),
            "diagnostics": {"metadata": {"reset": self.context.model_dump(mode="json"),
                "observed_target_speed": observation.target_speed_mps,
                "observed_heading": observation.route_heading_deg,
                "scene_route": None if observation.scene is None else
                    [item.model_dump(mode="json") for item in observation.scene.route or []]}}}
    def close(self): pass
''', encoding="utf-8")

    def build(*, protocol="1.2", navigation="provided_route", control=None):
        data = {"protocol_version": protocol, "agent_id": "task-test", "name": "Task test", "version": "1",
            "entry_point": "agent:Driver", "navigation_mode": navigation,
            "required_features": ["structured_scene"] if protocol != "1.0" else [],
            "configuration": {"protocol": protocol, "control": control or {}}}
        (directory / "agent.json").write_text(json.dumps(data), encoding="utf-8")
        return ProcessAgent(directory)
    return build


def test_explicit_baseline_task_measures_progress_and_finishes_early():
    task = DrivingTask(destination=pose(0.4), target_speed_mps=2.0,
                       completion_radius_m=0.01, stop_on_completion=True)
    adapter = ObservedAdapter()
    output = SimulationRunner(adapter).run(recipe(task=task), mode="test")
    assert output.result.simulation.status == "completed"
    outcome = output.result.driving_outcome
    assert outcome.status == "reached" and outcome.termination_reason == "goal_reached"
    assert outcome.reached_at_s == pytest.approx(0.2)
    assert output.result.measured_duration_s == pytest.approx(0.2)
    assert outcome.initial_speed_mps == 2.0
    assert outcome.route_progress_m == pytest.approx(0.4)
    assert outcome.route_length_m == pytest.approx(0.4)
    assert adapter.close_count == 1


def test_completed_execution_can_have_timed_out_goal():
    task = DrivingTask(destination=pose(100), target_speed_mps=2.0, stop_on_completion=True)
    output = SimulationRunner(ObservedAdapter()).run(recipe(task=task, duration=0.2), mode="test")
    assert output.result.simulation.status == "completed"
    assert output.result.driving_outcome.status == "timed_out"
    assert output.result.driving_outcome.termination_reason == "duration_elapsed"
    assert output.result.measured_duration_s == 0.2
    assert len(output.telemetry) == 5


def test_goal_reach_can_be_measured_while_experiment_continues():
    task = DrivingTask(destination=pose(0.4), target_speed_mps=2.0, completion_radius_m=0.01)
    output = SimulationRunner(ObservedAdapter()).run(recipe(task=task, duration=0.5), mode="test")
    outcome = output.result.driving_outcome
    assert outcome.status == "reached" and outcome.termination_reason == "duration_elapsed"
    assert outcome.reached_at_s == pytest.approx(0.2)
    assert output.result.measured_duration_s == 0.5
    assert outcome.final_distance_m > task.completion_radius_m


def test_external_framework_task_receives_complete_route_and_optional_speed(package):
    task = DrivingTask(route=[pose(), pose(1), pose(1, 2)], target_speed_mps=None)
    decisions = []
    output = SimulationRunner(ObservedAdapter()).run(recipe(task=task, initial_speed=7, duration=0.1),
        mode="test", agent=package(), on_decision=decisions.append)
    assert output.result.simulation.status == "completed", output.result.simulation.error
    metadata = decisions[0]["agent_result"]["diagnostics"]["metadata"]
    assert len(metadata["reset"]["route"]) == 3
    assert metadata["reset"]["driving_task"]["route"] == []
    assert metadata["observed_target_speed"] is None
    assert metadata["observed_heading"] is not None
    assert output.result.control_audit.mode == "authoritative"
    assert output.result.driving_outcome.initial_speed_mps == 7.0


def test_agent_owned_task_has_no_route_heading_or_initial_speed_fallback(package):
    task = DrivingTask(route_owner="agent", destination=pose(10), target_speed_mps=None)
    decisions = []
    output = SimulationRunner(ObservedAdapter()).run(recipe(task=task, initial_speed=7, duration=0.1),
        mode="test", agent=package(navigation="agent_planner"), on_decision=decisions.append)
    assert output.result.simulation.status == "completed", output.result.simulation.error
    observation = decisions[0]["observation"]
    metadata = decisions[0]["agent_result"]["diagnostics"]["metadata"]
    assert metadata["reset"]["route"] == []
    assert metadata["observed_target_speed"] is None
    assert metadata["observed_heading"] is None
    assert observation["scene"]["route"] is None
    assert output.result.driving_outcome.route_progress_m is None
    assert output.result.control_audit.navigation == "agent_planner"


@pytest.mark.parametrize("kind", ["legacy_sdk", "missing_planner", "baseline_owner", "baseline_speed"])
def test_incompatible_ownership_is_rejected_before_open(package, kind):
    adapter = ObservedAdapter()
    task = DrivingTask(destination=pose(10), target_speed_mps=3.0)
    agent = None
    if kind == "legacy_sdk":
        agent = package(protocol="1.1")
    elif kind == "missing_planner":
        task.route_owner = "agent"
        agent = package()
    elif kind == "baseline_owner":
        task.route_owner = "agent"
    else:
        task.target_speed_mps = None
    with pytest.raises(SimulationError):
        SimulationRunner(adapter).run(recipe(task=task), mode="test", agent=agent)
    assert adapter.open_count == 0
    if agent is not None:
        assert agent.stats.worker_pid is None


@pytest.mark.parametrize("action", ["set_speed", "brake"])
def test_authoritative_agent_rejects_every_scripted_ego_action_before_open(package, action):
    adapter = ObservedAdapter()
    event = {"action": action, "actor_id": "ego", "trigger_time_s": 0.0}
    event.update({"speed_mps": 1.0} if action == "set_speed" else {"deceleration_mps2": 8.0})
    agent = package()
    with pytest.raises(SimulationError, match="rejects scripted ego"):
        SimulationRunner(adapter).run(recipe(events=[event]), mode="test", agent=agent)
    assert adapter.open_count == 0 and agent.stats.worker_pid is None


def test_explicit_scripted_mode_records_typed_speed_and_braking_interventions(package):
    task = DrivingTask(destination=pose(100), target_speed_mps=3.0)
    events = [{"action": "set_speed", "actor_id": "ego", "trigger_time_s": 0.0, "speed_mps": 1.0},
              {"action": "brake", "actor_id": "ego", "trigger_time_s": 0.05, "deceleration_mps2": 8.0}]
    output = SimulationRunner(ObservedAdapter()).run(recipe(task=task, events=events, duration=0.15),
        mode="test", agent=package(control={"throttle": 0.7}), agent_control_mode="scripted")
    assert output.result.simulation.status == "completed", output.result.simulation.error
    interventions = output.result.driving_outcome.interventions
    assert [item.kind for item in interventions] == [
        "scripted_ego_speed_target", "scripted_ego_braking", "scripted_ego_braking"]
    assert interventions[0].target_speed_mps == 1.0
    assert interventions[1].proposed_control.throttle == 0.7
    assert interventions[1].applied_control.throttle == 0.0
    assert interventions[1].applied_control.brake == 1.0
    assert output.result.control_audit.interventions == interventions


def test_cancellation_after_goal_reach_reports_cancelled_execution(package):
    stop = Event()
    task = DrivingTask(destination=pose(), target_speed_mps=None)
    def cancel_after_frame(frame):
        if frame.time_s > 0:
            stop.set()
    output = SimulationRunner(ObservedAdapter()).run(recipe(task=task), mode="test",
        agent=package(), cancellation=stop, on_frame=cancel_after_frame)
    assert output.result.simulation.status == "cancelled"
    assert output.result.driving_outcome.status == "cancelled"
    assert output.result.driving_outcome.reached_at_s is None


def test_already_at_goal_ends_without_any_controls(package):
    task = DrivingTask(destination=pose(), stop_on_completion=True)
    agent = package()
    output = SimulationRunner(ObservedAdapter()).run(recipe(task=task), mode="test", agent=agent)
    assert output.result.simulation.status == "completed", output.result.simulation.error
    assert output.result.driving_outcome.status == "reached"
    assert output.result.driving_outcome.reached_at_s == 0.0
    assert output.result.measured_duration_s == 0.0
    assert len(output.telemetry) == 1 and agent.stats.steps == 0


def test_goal_following_example_stops_and_holds_inside_goal():
    scenario = load_scenario(REPO / "examples" / "scenarios" / "goal_following.json")
    decisions = []
    output = SimulationRunner(ObservedAdapter()).run(scenario, mode="test",
        agent=ProcessAgent(EXAMPLE), on_decision=decisions.append)
    assert output.result.simulation.status == "completed", output.result.simulation.error
    outcome = output.result.driving_outcome
    assert outcome.status == "reached" and outcome.termination_reason == "goal_reached"
    assert output.result.measured_duration_s < scenario.duration_s
    assert outcome.initial_speed_mps == 1.0 and outcome.target_speed_mps == 4.0
    assert output.telemetry[-1].ego.velocity_mps.x <= 0.3
    assert output.result.control_audit.interventions == []
    assert decisions[0]["agent_result"]["diagnostics"]["metadata"]["speed_source"] == "task_advisory"


def test_goal_following_example_agent_navigation_is_explicitly_synthetic():
    task = DrivingTask(route_owner="agent", destination=pose(0.5), target_speed_mps=None)
    decisions = []
    output = SimulationRunner(ObservedAdapter()).run(recipe(task=task, duration=0.1), mode="test",
        agent=ProcessAgent(EXAMPLE), on_decision=decisions.append)
    assert output.result.simulation.status == "completed", output.result.simulation.error
    assert decisions[0]["agent_result"]["diagnostics"]["metadata"]["speed_source"] == "agent_configuration"

    class PretendCarlaScene(ObservedAdapter):
        def agent_context(self, frame):
            scene = super().agent_context(frame)
            scene.source = "carla_ground_truth"
            return scene
    output = SimulationRunner(PretendCarlaScene()).run(recipe(task=task, duration=0.1),
        mode="test", agent=ProcessAgent(EXAMPLE))
    assert output.result.simulation.status == "failed"
    assert "synthetic test mode only" in output.result.simulation.error
    assert len(output.telemetry) == 1  # The failure precedes any applied command.
