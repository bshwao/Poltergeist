"""Runner behavior through real test-world dynamics and failing adapter boundaries."""

from copy import deepcopy
from threading import Event
import json

import pytest

from scenarios import load_template, save_scenario, validate_scenario
from shared.models import ControlInput, Simulation, SimulationStatus, TrafficRule
from simulator import SimulationOptions, SimulationRunner, run_simulation
from simulator.agents import BaselineAgent, CustomAgent
from simulator.errors import SimulationError
from simulator.protocols import SensorSample
from simulator.simulation_runner import main
from simulator.test_adapter import TestSimulationAdapter


def scene(**overrides):
    return validate_scenario({
        "name": "runner-test", "duration_s": 0.2, "fixed_delta_seconds": 0.05,
        "actors": [{"id": "ego", "type": "ego_vehicle", "speed_mps": 2, "spawn_point_index": 0}],
    } | overrides)


class SpyAdapter(TestSimulationAdapter):
    def __init__(self):
        super().__init__()
        self.open_calls = self.close_calls = self.finalize_calls = 0
        self.commands = []

    def open(self, scenario):
        self.open_calls += 1
        return super().open(scenario)

    def step(self, control, commands):
        self.commands.append((self._frame_index * 0.05, deepcopy(commands)))
        return super().step(control, commands)

    def finalize(self, frames):
        self.finalize_calls += 1
        return super().finalize(frames)

    def close(self):
        self.close_calls += 1
        super().close()


@pytest.mark.parametrize("template,duration", [
    ("intersection", 0.2), ("pedestrian_crossing", 1.2),
    ("occluded_pedestrian", 1.2), ("sudden_braking", 3.0),
])
def test_all_templates_run_and_evaluate_in_explicit_test_mode(template, duration):
    payload = load_template(template).model_dump()
    payload["duration_s"] = duration
    output = run_simulation(payload, mode="test")
    result = output.result
    assert result.simulation.status == "completed", result.simulation.error
    assert result.simulation.mode == result.runtime.adapter == "test"
    assert result.telemetry_frames == len(output.telemetry) == round(duration / 0.05) + 1
    assert result.measured_duration_s == pytest.approx(duration)
    assert result.metrics.coverage.complete
    assert result.metrics.severity is not None
    assert result.simulation.progress == 1
    assert result.simulation.created_at <= result.simulation.started_at <= result.simulation.finished_at
    assert result.runtime.resolved_spawns


def test_same_seed_has_identical_telemetry_and_metrics_without_mutating_input():
    scenario = scene()
    original = scenario.model_dump()
    first = run_simulation(scenario, mode="test")
    second = run_simulation(scenario, mode="test")
    assert first.telemetry == second.telemetry
    assert first.result.metrics == second.result.metrics
    assert first.result.runtime == second.result.runtime
    assert scenario.model_dump() == original


def test_wrapper_preserves_queued_simulation_mode():
    queued = Simulation(scenario=scene(), mode="test")
    result = run_simulation(queued).result
    assert result.simulation.id == queued.id
    assert result.simulation.status == "completed"
    assert result.runtime.adapter == "test"


@pytest.mark.parametrize("capture", ["camera", "lidar"])
def test_test_mode_rejects_unavailable_sensor_capture(capture):
    result = run_simulation(scene(), mode="test", options=SimulationOptions(**{capture: True})).result
    assert result.simulation.status == "failed"
    assert "require CARLA" in result.simulation.error
    assert result.telemetry_frames == 0
    assert result.metrics.severity is None


def test_sensor_samples_reach_subscriber_and_are_counted_in_runtime():
    class Capturing(SpyAdapter):
        def sensor_samples(self):
            if not self._active:
                return ()
            return (SensorSample("camera", self._frame_index, self._frame_index * 0.05, b"raw", {"width": 1}),)

        def finalize(self, frames):
            self._active = False
            return frames

    adapter = Capturing()
    samples = []
    result = SimulationRunner(adapter).run(scene(), on_sensor=samples.append).result
    assert result.simulation.status == "completed"
    assert [sample.frame for sample in samples] == list(range(5))
    assert result.runtime.sensor_samples_count == len(samples) == 5


def test_sensor_callback_failure_keeps_telemetry_and_closes_resources():
    class Capturing(SpyAdapter):
        def sensor_samples(self):
            return (SensorSample("camera", self._frame_index, 0, b"raw", {}),)

    def fail(sample):
        raise RuntimeError("capture subscriber failed")

    adapter = Capturing()
    result = SimulationRunner(adapter).run(scene(), on_sensor=fail).result
    assert result.simulation.status == "failed"
    assert "capture subscriber failed" in result.simulation.error
    assert result.telemetry_frames == 1
    assert adapter.close_calls == 1


def test_events_at_zero_and_between_ticks_change_targets_on_the_simulation_clock():
    scenario = scene(
        actors=[
            {"id": "ego", "type": "ego_vehicle", "speed_mps": 2, "spawn_point_index": 0},
            {"id": "walker", "type": "pedestrian", "spawn": {"position": {"x": 30, "y": 10}}},
        ],
        events=[
            {"action": "set_speed", "actor_id": "walker", "trigger_time_s": 0, "speed_mps": 1},
            {"action": "set_speed", "actor_id": "walker", "trigger_time_s": 0.075, "speed_mps": 2},
        ],
    )
    adapter = SpyAdapter()
    output = SimulationRunner(adapter).run(scenario)
    assert output.result.simulation.status == "completed"
    assert adapter.commands[0][1]["walker"].target_speed_mps == 1
    assert adapter.commands[1][1]["walker"].target_speed_mps == 1
    assert adapter.commands[2][0] == 0.1
    assert adapter.commands[2][1]["walker"].target_speed_mps == 2
    assert adapter.close_calls == adapter.finalize_calls == 1


def test_same_tick_event_order_is_stable_and_brake_can_be_replaced():
    adapter = SpyAdapter()
    output = SimulationRunner(adapter).run(scene(events=[
        {"action": "brake", "actor_id": "ego", "trigger_time_s": 0, "deceleration_mps2": 4},
        {"action": "set_speed", "actor_id": "ego", "trigger_time_s": 0, "speed_mps": 3},
    ]))
    assert output.result.simulation.status == "completed"
    assert adapter.commands[0][1]["ego"].target_speed_mps == 3
    assert adapter.commands[0][1]["ego"].deceleration_mps2 is None


def test_ego_braking_event_overrides_controller_and_is_measured():
    output = run_simulation(scene(events=[{
        "action": "brake", "actor_id": "ego", "trigger_time_s": 0, "deceleration_mps2": 4,
    }]), mode="test", agent=CustomAgent(lambda _: {"throttle": 1}))
    assert output.result.simulation.status == "completed"
    assert output.telemetry[1].control.brake == 0.5
    assert output.telemetry[1].ego.velocity_mps.x < output.telemetry[0].ego.velocity_mps.x


def test_ego_braking_preserves_drivers_steering_and_reverse():
    output = run_simulation(scene(events=[{
        "action": "brake", "actor_id": "ego", "trigger_time_s": 0, "deceleration_mps2": 4,
    }]), mode="test", agent=CustomAgent(lambda _: {"throttle": 1, "steer": 0.3, "reverse": True}))
    assert output.result.simulation.status == "completed"
    assert output.telemetry[1].control == ControlInput(throttle=0, steer=0.3, brake=0.5, reverse=True)


def test_pre_cancelled_run_never_opens_adapter_and_has_a_terminal_record():
    adapter = SpyAdapter()
    runner = SimulationRunner(adapter)
    runner.cancel()
    output = runner.run(scene())
    assert output.result.simulation.status == "cancelled"
    assert output.result.simulation.started_at is None
    assert output.result.telemetry_frames == 0
    assert output.result.metrics.severity is None
    assert adapter.open_calls == 0
    assert adapter.close_calls == 1


def test_cancellation_from_progress_keeps_partial_telemetry_and_cleans_up():
    adapter = SpyAdapter()
    runner = SimulationRunner(adapter)
    seen = []

    def progress(simulation):
        seen.append(simulation.status)
        if simulation.progress >= 0.25:
            runner.cancel()

    result = runner.run(scene(), on_progress=progress).result
    assert result.simulation.status == "cancelled"
    assert 0 < result.telemetry_frames < 5
    assert result.metrics.severity is None
    assert adapter.close_calls == adapter.finalize_calls == 1
    assert seen[0] == "queued" and seen[-1] == "cancelled"


def test_external_cancellation_event_is_respected():
    cancellation = Event()
    cancellation.set()
    result = run_simulation(scene(), mode="test", cancellation=cancellation).result
    assert result.simulation.status == "cancelled"


@pytest.mark.parametrize("boundary", ["open", "step", "finalize", "close"])
def test_adapter_failure_leaves_a_failed_record_and_always_attempts_cleanup(boundary):
    class Failing(SpyAdapter):
        def open(self, scenario):
            if boundary == "open":
                raise RuntimeError("open failed")
            return super().open(scenario)

        def step(self, *args):
            if boundary == "step":
                raise RuntimeError("step failed")
            return super().step(*args)

        def finalize(self, frames):
            if boundary == "finalize":
                raise RuntimeError("finalize failed")
            return super().finalize(frames)

        def close(self):
            super().close()
            if boundary == "close":
                raise RuntimeError("close failed")

    adapter = Failing()
    result = SimulationRunner(adapter).run(scene()).result
    assert result.simulation.status == "failed"
    assert boundary in result.simulation.error
    assert adapter.close_calls == 1
    assert result.metrics.severity is None


def test_custom_controller_and_subscriber_cannot_rewrite_trace_or_scenario():
    def controller(observation):
        observation.ego.actor_id = "modified"
        return ControlInput()

    def subscriber(frame):
        frame.ego.actor_id = "subscriber"

    result = run_simulation(scene(), mode="test", agent=CustomAgent(controller), on_frame=subscriber)
    assert result.result.simulation.status == "completed"
    assert result.result.simulation.agent == "custom"
    assert all(frame.ego.actor_id == "ego" for frame in result.telemetry)
    assert result.result.simulation.scenario.actors[0].id == "ego"


def test_keyboard_interrupt_during_finalization_still_closes_resources():
    class Interrupted(SpyAdapter):
        def finalize(self, frames):
            raise KeyboardInterrupt()

    adapter = Interrupted()
    output = SimulationRunner(adapter).run(scene())
    assert output.result.simulation.status == "cancelled"
    assert adapter.close_calls == 1


def test_keyboard_interrupt_in_terminal_callback_returns_cancelled_record():
    adapter = SpyAdapter()

    def progress(simulation):
        if simulation.status == "completed":
            raise KeyboardInterrupt()

    result = SimulationRunner(adapter).run(scene(), on_progress=progress).result
    assert result.simulation.status == "cancelled"
    assert result.metrics.severity is None
    assert adapter.close_calls == 1


def test_terminal_callback_error_preserves_an_earlier_adapter_failure():
    class Failing(SpyAdapter):
        def step(self, *args):
            raise RuntimeError("connection lost")

    def progress(simulation):
        if simulation.status == "failed":
            raise RuntimeError("subscriber failed")

    result = SimulationRunner(Failing()).run(scene(), on_progress=progress).result
    assert "connection lost" in result.simulation.error
    assert "subscriber failed" in result.simulation.error


def test_finalizer_cannot_delete_or_reorder_recorded_frames():
    class BadFinalizer(SpyAdapter):
        def finalize(self, frames):
            return frames[1:]

    result = SimulationRunner(BadFinalizer()).run(scene()).result
    assert result.simulation.status == "failed"
    assert "timeline" in result.simulation.error


def test_frame_gaps_fail_instead_of_becoming_successful_telemetry():
    class Skipped(SpyAdapter):
        def step(self, *args):
            frame = super().step(*args)
            frame.frame += 1
            return frame

    result = SimulationRunner(Skipped()).run(scene()).result
    assert result.simulation.status == "failed"
    assert "skipped a frame" in result.simulation.error


def test_runner_rejects_agent_mode_and_lifecycle_mismatches_before_execution():
    with pytest.raises(SimulationError, match="Adapter mode"):
        SimulationRunner(SpyAdapter()).run(scene(), mode="carla")
    with pytest.raises(SimulationError, match="custom"):
        SimulationRunner(SpyAdapter()).run(Simulation(scenario=scene(), mode="test", agent="custom"))
    with pytest.raises(SimulationError, match="Injected agent"):
        SimulationRunner(SpyAdapter()).run(Simulation(scenario=scene(), mode="test"), agent=CustomAgent(lambda _: {}))
    result = run_simulation(scene(), mode="test", agent=BaselineAgent()).result
    assert result.simulation.agent == "baseline"


def test_queued_record_keeps_its_identity_and_runner_is_single_use():
    simulation = Simulation(scenario=scene(), mode="test")
    runner = SimulationRunner(SpyAdapter())
    output = runner.run(simulation)
    assert output.result.simulation.id == simulation.id
    assert simulation.status == "queued"
    with pytest.raises(SimulationError, match="new SimulationRunner"):
        runner.run(scene())


def test_tick_limit_rejects_oversized_run_before_opening_resources():
    adapter = SpyAdapter()
    with pytest.raises(SimulationError, match="max_ticks"):
        SimulationRunner(adapter, options=SimulationOptions(max_ticks=1)).run(scene())
    assert adapter.open_calls == adapter.close_calls == 0


def test_cli_runs_saved_scenario_and_writes_structured_result(tmp_path, capsys):
    path = tmp_path / "scenario.yaml"
    save_scenario(scene(), path)
    assert main(["--scenario", str(path), "--mode", "test"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["simulation"]["mode"] == "test"
    assert payload["simulation"]["status"] == "completed"
    target = tmp_path / "result.json"
    assert main(["--scenario", str(path), "--mode", "test", "--output", str(target)]) == 0
    assert json.loads(target.read_text())["runtime"]["adapter"] == "test"


def test_cli_driving_task_override_keeps_initial_speed_separate_from_goal(tmp_path, capsys):
    path, task_path = tmp_path / "scenario.json", tmp_path / "task.json"
    save_scenario(scene(), path)
    task_path.write_text(json.dumps({"destination": {"position": {"x": 40.0}}, "target_speed_mps": 3.0}))
    assert main(["--scenario", str(path), "--mode", "test", "--driving-task", str(task_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["simulation"]["scenario"]["actors"][0]["speed_mps"] == 2
    assert payload["driving_outcome"]["initial_speed_mps"] == 2
    assert payload["driving_outcome"]["target_speed_mps"] == 3
    assert payload["driving_outcome"]["status"] == "timed_out"


def test_cli_invalid_driving_task_fails_before_writing_result(tmp_path):
    path, task_path, result_path = tmp_path / "scenario.json", tmp_path / "task.json", tmp_path / "result.json"
    save_scenario(scene(), path)
    task_path.write_text('{"route_owner":"agent", "route": []}')
    with pytest.raises(SystemExit) as error:
        main(["--scenario", str(path), "--mode", "test", "--driving-task", str(task_path), "--output", str(result_path)])
    assert error.value.code == 1
    assert not result_path.exists()


@pytest.mark.parametrize("bad_route", [[], [{"position": {"x": 0.0}}, {"position": {"x": 10.0}}], [{} , "invalid"]])
def test_task_adapter_invalid_route_is_a_failed_result_including_baseline(bad_route):
    class BadRoute(SpyAdapter):
        def prepare_driving_task(self, task, frame):
            return bad_route

    output = SimulationRunner(BadRoute()).run(scene(driving_task={
        "destination": {"position": {"x": 100.0}}, "target_speed_mps": 3.0,
    }), mode="test")
    assert output.result.simulation.status == "failed"
    assert output.result.driving_outcome.status == "failed"
    assert output.result.control_audit.interventions == []
    assert len(output.telemetry) == 1


def test_task_baseline_keeps_red_light_observation_without_legacy_route_fallback():
    class RedLight(SpyAdapter):
        def traffic_light_observation(self):
            return True

        def observation(self, *args):
            raise AssertionError("Task route must be independent of legacy fallback")

    output = SimulationRunner(RedLight()).run(scene(driving_task={
        "destination": {"position": {"x": 20.0}}, "target_speed_mps": 3.0,
    }), mode="test")
    assert output.result.simulation.status == "completed"
    assert all(frame.control.brake == 1 for frame in output.telemetry[1:])


def test_partial_rule_checks_cannot_claim_full_coverage_or_close_unchecked_rules():
    from evaluation import evaluate_safety, summarize_traffic_rules

    def sample(number, rules, events):
        return dict(
            frame=number, time_s=number * 0.1,
            ego={"actor_id": "ego", "velocity_mps": {"x": 0}, "acceleration_mps2": {"x": 0}},
            actors=[], collisions=[], rule_checks=rules, traffic_violations=events,
        )
    trace = [
        sample(0, [TrafficRule.RED_LIGHT], [{"actor_id": "ego", "rule": "red_light"}]),
        sample(1, [TrafficRule.SPEEDING], []),
        sample(2, [TrafficRule.RED_LIGHT], [{"actor_id": "ego", "rule": "red_light"}]),
    ]
    assert summarize_traffic_rules(trace).count == 1
    metrics = evaluate_safety(trace)
    assert metrics.traffic_violation_count == 1
    assert metrics.coverage.traffic_rule_frames == 0
    assert metrics.severity is None


def test_npc_only_rule_event_cannot_establish_ego_observation():
    from evaluation import evaluate_safety

    metrics = evaluate_safety([{
        "frame": 0, "time_s": 0,
        "ego": {"actor_id": "ego"}, "actors": [], "collisions": [],
        "rule_checks": [],
        "traffic_violations": [{"actor_id": "npc", "rule": "red_light"}],
    }])
    assert metrics.traffic_violation_count is None
    assert metrics.coverage.traffic_rule_frames == 0
    assert metrics.severity is None
