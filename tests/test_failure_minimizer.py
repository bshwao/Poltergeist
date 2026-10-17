"""Recipe validity and deterministic, conservative local minimization."""

import json

import pytest

from optimization.failure_minimizer import FailureMinimizer
from scenarios.scenario_generator import build_scenario
from scenarios.scenario_loader import validate_scenario
from shared.models import ScenarioTemplate


def custom_scenario():
    return validate_scenario({
        "name": "custom", "duration_s": 10.0, "seed": 123,
        "actors": [
            {"type": "ego_vehicle", "id": "ego", "speed_mps": 8.0},
            {"type": "vehicle", "id": "anchor", "speed_mps": 2.0,
             "relative_spawn": {"anchor_actor_id": "ego", "offset_m": {"x": 20.0}}},
            {"type": "pedestrian", "id": "leaf", "speed_mps": 1.0,
             "relative_spawn": {"anchor_actor_id": "anchor", "offset_m": {"y": 4.0}}},
        ],
        "events": [{"action": "set_speed", "actor_id": "leaf", "trigger_time_s": 2.0, "speed_mps": 2.0}],
        "weather": {"cloudiness": 90.0, "precipitation": 80.0, "sun_altitude_deg": 20.0},
    })


@pytest.mark.parametrize("template", list(ScenarioTemplate))
def test_template_proposals_preserve_binding_validation_and_source(template):
    source = build_scenario(template, parameters={"ego_speed_mps": 24.0, "precipitation_pct": 90.0})
    original = source.model_dump_json()
    minimizer = FailureMinimizer(source)
    proposals = []
    for _ in range(100):
        candidate = minimizer.propose()
        if candidate is None:
            break
        assert candidate.template == template
        assert candidate.seed == source.seed
        validate_scenario(candidate)
        assert minimizer.complexity(candidate) < minimizer.complexity(minimizer.best)
        proposals.append(candidate)
        minimizer.observe(False)
    assert proposals
    assert any(candidate.parameters["ego_speed_mps"] == 12 for candidate in proposals)
    assert source.model_dump_json() == original == minimizer.best.model_dump_json()


def test_removes_only_dependency_leaves_and_their_events():
    minimizer = FailureMinimizer(custom_scenario())
    first = minimizer.propose()
    assert {actor.id for actor in first.actors} == {"ego", "anchor"}
    assert first.events == []
    minimizer.observe(True)
    second = minimizer.propose()
    assert [actor.id for actor in second.actors] == ["ego"]
    validate_scenario(second)


def test_extra_builtin_actors_can_be_removed_but_template_roles_are_retained():
    source = build_scenario("occluded_pedestrian")
    payload = source.model_dump(mode="python")
    payload["actors"].append({"id": "extra", "type": "vehicle", "speed_mps": 1.0})
    minimizer = FailureMinimizer(validate_scenario(payload))
    candidate = minimizer.propose()
    assert {actor.id for actor in candidate.actors} == {actor.id for actor in source.actors}
    assert candidate.template == source.template


def test_custom_event_removal_then_control_reductions_can_be_rejected_independently():
    source = custom_scenario()
    minimizer = FailureMinimizer(source)
    seen = []
    while (candidate := minimizer.propose()) is not None:
        seen.append(candidate)
        minimizer.observe(False)
    assert any(len(candidate.actors) == 3 and not candidate.events for candidate in seen)
    assert any(len(candidate.events) == 1 and candidate.events[0].trigger_time_s == 0 for candidate in seen)
    assert any(candidate.weather.precipitation == 0 for candidate in seen)
    assert any(candidate.duration_s < source.duration_s for candidate in seen)
    assert all(minimizer.complexity(candidate) < minimizer.complexity(source) for candidate in seen)
    assert minimizer.best == source


def test_greedy_acceptance_strictly_decreases_cost_and_terminates():
    minimizer = FailureMinimizer(custom_scenario())
    previous = minimizer.complexity(minimizer.best)
    for attempts in range(100):
        candidate = minimizer.propose()
        if candidate is None:
            break
        cost = minimizer.complexity(candidate)
        assert cost < previous
        minimizer.observe(True)
        assert minimizer.complexity(minimizer.best) == cost
        previous = cost
    else:
        pytest.fail("All-accepted reduction should terminate quickly")
    assert len(minimizer.best.actors) == 1
    assert minimizer.best.actors[0].speed_mps == 0
    assert minimizer.best.duration_s == minimizer.best.fixed_delta_seconds
    json.dumps(previous, allow_nan=False)


def test_restart_replay_with_identical_decisions_is_deterministic():
    left = FailureMinimizer(custom_scenario())
    right = FailureMinimizer(custom_scenario())
    for index in range(40):
        left_candidate = left.propose()
        right_candidate = right.propose()
        assert left_candidate == right_candidate
        if left_candidate is None:
            break
        accept = index % 3 == 0
        left.observe(accept)
        right.observe(accept)
        assert left.best == right.best


def test_pending_and_best_copies_cannot_be_mutated_to_change_acceptance():
    minimizer = FailureMinimizer(custom_scenario())
    candidate = minimizer.propose()
    expected = candidate.model_dump_json()
    candidate.duration_s = 999.0
    minimizer.best.actors.clear()
    with pytest.raises(RuntimeError):
        minimizer.propose()
    minimizer.observe(True)
    assert minimizer.best.model_dump_json() == expected
    with pytest.raises(RuntimeError):
        minimizer.observe(False)


def test_unknown_observation_requires_explicit_rejection_and_preserves_pending():
    minimizer = FailureMinimizer(custom_scenario())
    source = minimizer.best
    minimizer.propose()
    with pytest.raises(ValueError):
        minimizer.observe(None)
    minimizer.observe(False)
    assert minimizer.best == source


def test_invalid_mutated_recipe_is_rejected_before_any_trial():
    source = build_scenario("sudden_braking")
    source.actors[1].speed_mps = 999
    with pytest.raises(ValueError):
        FailureMinimizer(source)


def test_duration_shortening_preserves_strict_event_boundary():
    source = custom_scenario()
    minimizer = FailureMinimizer(source)
    while (candidate := minimizer.propose()) is not None:
        assert all(event.trigger_time_s < candidate.duration_s for event in candidate.events)
        minimizer.observe(False)
