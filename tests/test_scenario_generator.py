"""Test saved/reproduced behavior, parameter binding, and seed independence."""

import random

import pytest

from scenarios.parameters import get_parameter_specs
from scenarios.scenario_generator import (
    ScenarioGenerationError,
    build_scenario,
    generate_scenario,
    generate_scenarios,
)
from scenarios.scenario_loader import load_scenario, load_template, save_scenario, validate_scenario
from shared.models import ScenarioTemplate


def actor_by_id(scenario, actor_id):
    return next(actor for actor in scenario.actors if actor.id == actor_id)


@pytest.mark.parametrize("template", list(ScenarioTemplate))
def test_build_with_defaults_matches_full_template(template):
    assert build_scenario(template) == load_template(template)


@pytest.mark.parametrize("template", list(ScenarioTemplate))
def test_same_seed_reproduces_complete_payload(template):
    first = generate_scenario(template, seed=1729)
    second = generate_scenario(template, seed=1729)

    assert first.model_dump_json() == second.model_dump_json()
    assert first.seed == 1729
    assert first.template == template
    assert first.name == f"{template.value}-1729"
    assert validate_scenario(first) == first


def test_generation_does_not_change_global_rng_or_caller_inputs():
    state = random.getstate()
    parameters = {"ego_speed_mps": 15}
    ranges = {"pedestrian_speed_mps": (1, 2)}

    generated = generate_scenario("pedestrian_crossing", seed=7, parameters=parameters, ranges=ranges)

    assert random.getstate() == state
    assert parameters == {"ego_speed_mps": 15}
    assert ranges == {"pedestrian_speed_mps": (1, 2)}
    assert actor_by_id(generated, "ego").speed_mps == 15
    assert 1 <= generated.events[0].speed_mps <= 2


def test_pedestrian_parameters_change_motion_geometry_and_weather():
    scenario = build_scenario("pedestrian_crossing", seed=4, parameters={
        "ego_speed_mps": 20, "pedestrian_speed_mps": 2.5,
        "pedestrian_distance_m": 25, "pedestrian_lateral_offset_m": 6,
        "crossing_delay_s": 3, "precipitation_pct": 75,
        "fog_density_pct": 20, "sun_altitude_deg": -5,
    })
    pedestrian = actor_by_id(scenario, "pedestrian")

    assert actor_by_id(scenario, "ego").speed_mps == 20
    assert pedestrian.speed_mps == 0
    assert pedestrian.relative_spawn.anchor_actor_id == "ego"
    assert pedestrian.relative_spawn.offset_m.x == 25
    assert pedestrian.relative_spawn.offset_m.y == 6
    assert pedestrian.relative_spawn.yaw_offset_deg == -90
    assert scenario.events[0].actor_id == "pedestrian"
    assert scenario.events[0].trigger_time_s == 3
    assert scenario.events[0].speed_mps == 2.5
    assert scenario.weather.precipitation == scenario.weather.wetness == 75
    assert scenario.weather.fog_density == 20
    assert scenario.weather.sun_altitude_deg == -5


def test_occluder_lies_between_initial_ego_and_pedestrian():
    scenario = build_scenario("occluded_pedestrian", parameters={
        "pedestrian_distance_m": 30, "pedestrian_lateral_offset_m": 6, "occlusion_gap_m": 5,
    })
    pedestrian = actor_by_id(scenario, "pedestrian").relative_spawn.offset_m
    occluder = actor_by_id(scenario, "occluder")
    position = occluder.relative_spawn.offset_m

    assert scenario.occlusion is True
    assert position.x == 25
    assert position.y == 5
    assert 0 < position.x < pedestrian.x
    assert position.y / position.x == pytest.approx(pedestrian.y / pedestrian.x)
    assert occluder.speed_mps == 0


def test_intersection_vehicle_heads_across_ego_route_after_delay():
    scenario = build_scenario("intersection", parameters={
        "intersection_distance_m": 40, "crossing_distance_m": 15,
        "crossing_speed_mps": 8, "crossing_delay_s": 2,
    })
    crossing = actor_by_id(scenario, "crossing")

    assert crossing.speed_mps == 0
    assert crossing.relative_spawn.offset_m.x == 40
    assert crossing.relative_spawn.offset_m.y == -15
    assert crossing.relative_spawn.yaw_offset_deg == 90
    assert scenario.events[0].speed_mps == 8
    assert scenario.events[0].trigger_time_s == 2


def test_sudden_braking_has_a_moving_lead_and_timed_brake():
    scenario = build_scenario("sudden_braking", parameters={
        "lead_speed_mps": 10, "lead_distance_m": 35,
        "brake_delay_s": 4, "lead_deceleration_mps2": 8,
    })
    lead = actor_by_id(scenario, "lead")

    assert lead.speed_mps == 10
    assert lead.relative_spawn.offset_m.x == 35
    assert scenario.events[0].action == "brake"
    assert scenario.events[0].actor_id == "lead"
    assert scenario.events[0].trigger_time_s == 4
    assert scenario.events[0].deceleration_mps2 == 8


@pytest.mark.parametrize("template", list(ScenarioTemplate))
def test_generated_values_stay_in_declared_bounds_across_seeds(template):
    scenarios = generate_scenarios(template, 10, seed=50)
    specs = get_parameter_specs(template)
    for scenario in scenarios:
        assert set(scenario.parameters) == set(specs)
        for name, spec in specs.items():
            assert spec.minimum <= scenario.parameters[name] <= spec.maximum
    assert scenarios[0].parameters != scenarios[1].parameters


def test_batch_preserves_prefix_and_wraps_unique_seeds():
    short = generate_scenarios("sudden_braking", 2, seed=2**31 - 1)
    long = generate_scenarios("sudden_braking", 3, seed=2**31 - 1)

    assert short == long[:2]
    assert [scenario.seed for scenario in long] == [2**31 - 1, 0, 1]
    assert long[1] == generate_scenario("sudden_braking", seed=0)
    assert generate_scenarios("intersection", 0) == []


@pytest.mark.parametrize("extension", ["yaml", "json"])
def test_generated_scenario_can_be_saved_and_loaded(tmp_path, extension):
    scenario = generate_scenario("occluded_pedestrian", seed=1234)
    path = tmp_path / f"scenario.{extension}"
    save_scenario(scenario, path)

    assert load_scenario(path) == scenario


@pytest.mark.parametrize("seed", [-1, 2**31, True, 3.5, "5"])
def test_invalid_generation_seeds_fail(seed):
    with pytest.raises(ScenarioGenerationError):
        generate_scenario("intersection", seed=seed)


@pytest.mark.parametrize("count", [-1, True, 1.5, 2**31 + 1])
def test_invalid_batch_count_fails(count):
    with pytest.raises(ScenarioGenerationError):
        generate_scenarios("intersection", count)


def test_bad_template_parameters_and_ranges_fail_with_context():
    for call in [
        lambda: build_scenario("unknown"),
        lambda: build_scenario("intersection", name=" "),
        lambda: generate_scenario("intersection", parameters={"crossing_speed_mps": 100}),
        lambda: generate_scenario("intersection", ranges={"crossing_speed_mps": (10, 2)}),
        lambda: generate_scenarios("intersection", 0, parameters={"typo": 1}),
    ]:
        with pytest.raises(ScenarioGenerationError):
            call()


def test_template_parameters_cannot_silently_disagree_with_materialized_fields():
    payload = build_scenario("pedestrian_crossing").model_dump(mode="python")
    payload["parameters"]["ego_speed_mps"] = 20

    with pytest.raises(ValueError, match="speed_mps"):
        validate_scenario(payload)


def test_template_events_and_roles_cannot_be_silently_replaced():
    payload = build_scenario("intersection").model_dump(mode="python")
    payload["events"][0]["speed_mps"] = 2
    with pytest.raises(ValueError, match="speed_mps"):
        validate_scenario(payload)

    payload = build_scenario("intersection").model_dump(mode="python")
    payload["actors"][1]["type"] = "pedestrian"
    with pytest.raises(ValueError, match="crossing_vehicle"):
        validate_scenario(payload)


def test_generations_do_not_share_mutable_template_state():
    first = build_scenario("pedestrian_crossing")
    first.actors[0].speed_mps = 999
    first.parameters["ego_speed_mps"] = 999
    second = build_scenario("pedestrian_crossing")

    assert second.actors[0].speed_mps == second.parameters["ego_speed_mps"] == 12
