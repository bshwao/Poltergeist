"""Physical coverage, target attribution and failed-trial denominator checks."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from scenarios.stress_presets import get_stress_preset
from scripts import benchmark_stress as benchmark
from shared.models import CollisionEvent, EvaluationCoverage, SafetyMetrics, SimulationStatus
from simulator.simulation_runner import SimulationRunner


@pytest.fixture
def completed():
    recipe = get_stress_preset("pedestrian_crossing-low").scenario(seed=7).model_copy(update={"duration_s": .2})
    return SimulationRunner().run(recipe, mode="test")


def measured_metrics(*, collision_count=0, min_ttc_s=None, min_clearance_m=4., collision_frames=5):
    return SafetyMetrics(collision_count=collision_count, min_ttc_s=min_ttc_s, min_clearance_m=min_clearance_m,
                         coverage=EvaluationCoverage(total_frames=5, collision_frames=collision_frames,
                            clearance_frames=5, ttc_frames=5, actor_pairs=5, clearance_pairs=5, ttc_pairs=5))


def test_incomplete_road_rules_do_not_erase_complete_collision_evidence(completed):
    metrics = measured_metrics()
    assert not metrics.coverage.complete
    assert metrics.severity is None
    assert benchmark.observed_danger(completed.result, metrics) == "clear"


@pytest.mark.parametrize("changes,expected", [
    ({"collision_count": 1}, "collision"),
    ({"min_ttc_s": 1.5}, "critical_avoidance"),
    ({"min_clearance_m": 1.0}, "critical_avoidance"),
    ({"min_ttc_s": 1.51, "min_clearance_m": 1.01}, "clear"),
    ({"collision_frames": 4}, "unclassified"),
    ({"collision_count": None}, "unclassified"),
])
def test_operational_bins_retain_unknown_physical_feeds(completed, changes, expected):
    assert benchmark.observed_danger(completed.result, measured_metrics(**changes)) == expected


def test_contacts_with_roadside_geometry_do_not_become_pedestrian_contacts(completed):
    frames = [frame.model_copy(deep=True) for frame in completed.telemetry]
    frames[1].collisions = [CollisionEvent(other_actor_id="world:0", event_id="wall-contact")]
    frames[2].collisions = [CollisionEvent(other_actor_id="pedestrian", event_id="pedestrian-contact")]
    final_output = replace(completed, telemetry=tuple(frames))
    target, metrics = benchmark.interaction_metrics(final_output)
    assert target == "pedestrian" and metrics.collision_count == 1
    assert len(frames[1].collisions) == 1


def test_missing_primary_actor_is_not_counted_as_an_empty_safe_scene(completed):
    frames = [frame.model_copy(deep=True) for frame in completed.telemetry]
    frames[1].actors = []
    _, metrics = benchmark.interaction_metrics(replace(completed, telemetry=tuple(frames)))
    assert metrics.coverage.clearance_frames == len(frames) - 1
    assert benchmark.observed_danger(completed.result, metrics) == "unclassified"


def test_failed_and_missing_trials_are_not_safe_runs_in_the_denominator(tmp_path, completed):
    identifier = "pedestrian_crossing-low"
    benchmark.write_json(tmp_path / "manifest.json", {
        "schema_version": "poltergeist-stress-benchmark-v1", "catalog_version": "frozen-test-catalog",
        "phase": "benchmark", "presets": [identifier], "seeds": [7, 11, 19], "signals": "natural",
        "preset_metadata": {identifier: {"intended_stress": "low"}},
    })
    for seed in (7, 11):
        trial = tmp_path / f"{identifier}--seed-{seed}"
        trial.mkdir()
        result = completed.result.model_copy(deep=True)
        if seed == 11:
            result.simulation.status = SimulationStatus.FAILED
            result.simulation.error = "deliberate test failure"
        benchmark.write_json(trial / "result.json", result.model_dump(mode="json"))
        benchmark.write_json(trial / "trial.json", {"state_restored": True,
                             "interaction_metrics": measured_metrics().model_dump(mode="json")})
    summary = benchmark.summarize(tmp_path)
    row = summary["presets"][0]
    assert row["valid_completed"] == 1 and row["invalid_or_noncompleted"] == 1 and row["missing"] == 1
    assert row["collision_rate"] == 0. and not summary["complete"]
    assert row["collision_rate_wilson_95"][1] > .7
    assert row["medians"]["severity"] is None


def test_wilson_bounds_show_small_sample_uncertainty():
    assert benchmark.wilson_interval(0, 0) is None
    lower, upper = benchmark.wilson_interval(1, 1)
    assert lower < .25 and upper == 1.


def test_signal_confirmation_waits_for_delayed_native_snapshots():
    class Lights(list):
        def filter(self, pattern):
            return self
    class DelayedWorld:
        ticks = 0
        def get_actors(self):
            return Lights([SimpleNamespace(is_frozen=lambda: self.ticks >= 2, get_state=lambda: "Green")])
        def wait_for_tick(self, timeout):
            self.ticks += 1
    world = DelayedWorld()
    assert benchmark.wait_for_signal_flags(world, True, green="Green")
    assert world.ticks == 2


def test_aborted_batch_restores_signal_flags_and_requests_original_colors(tmp_path):
    class Lights(list):
        def filter(self, pattern):
            return self
    class Light:
        id = 9
        color = "Red"
        def is_frozen(self):
            return world.frozen
        def get_state(self):
            return self.color
        def set_state(self, value):
            self.color = value
    class World:
        frozen = False
        def get_actors(self):
            return Lights([light])
        def freeze_all_traffic_lights(self, value):
            self.frozen = value
    world, light = World(), Light()
    client = SimpleNamespace(get_world=lambda: world)
    carla = SimpleNamespace(TrafficLightState=SimpleNamespace(Green="Green"))
    with pytest.raises(RuntimeError, match="trial failed"):
        with benchmark.signal_protocol(client, carla, "green", tmp_path):
            assert world.frozen and light.color == "Green"
            raise RuntimeError("trial failed")
    assert not world.frozen and light.color == "Red"
