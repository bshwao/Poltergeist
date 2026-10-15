"""Boundary checks for safe loading and reproducible scenario materialization."""

from copy import deepcopy
import json

import pytest

from scenarios.scenario_loader import (
    ScenarioLoadError,
    list_templates,
    load_scenario,
    load_template,
    loads_scenario,
    save_scenario,
    validate_scenario,
)
from shared.models import Scenario, ScenarioTemplate, Weather


def _custom_scenario():
    return {
        "name": "Custom déjà vu",
        "actors": [
            {"type": "ego_vehicle"},
            {"type": "vehicle"},
            {"type": "vehicle"},
            {"type": "pedestrian"},
            {"type": "lead_vehicle"},
            {"type": "crossing_vehicle"},
        ],
        "parameters": {"custom": 42, "enabled": True, "label": "whatever"},
    }


def test_defaults_are_stable_and_keep_map_locations_unresolved():
    scenario = validate_scenario(_custom_scenario())
    assert [actor.id for actor in scenario.actors] == [
        "ego", "vehicle", "vehicle_2", "pedestrian", "lead", "crossing",
    ]
    assert [actor.blueprint for actor in scenario.actors] == [
        "vehicle.tesla.model3", "vehicle.tesla.model3", "vehicle.tesla.model3",
        "walker.pedestrian.0001", "vehicle.tesla.model3", "vehicle.audi.tt",
    ]
    assert all(actor.spawn is None for actor in scenario.actors)
    assert scenario.parameters["custom"] == 42


def test_explicit_ids_are_reserved_before_default_ids_are_chosen():
    data = _custom_scenario()
    data["actors"] += [{"type": "vehicle", "id": " vehicle "}, {"type": "vehicle", "id": "vehicle_2"}]
    data["actors"][0]["id"] = "hero"
    data["actors"][0]["blueprint"] = "vehicle.audi.tt"
    scenario = validate_scenario(data)
    assert [actor.id for actor in scenario.actors] == [
        "hero", "vehicle_3", "vehicle_4", "pedestrian", "lead", "crossing", "vehicle", "vehicle_2",
    ]
    assert scenario.actors[0].blueprint == "vehicle.audi.tt"


def test_mapping_and_nested_inputs_are_never_mutated():
    data = _custom_scenario()
    original = deepcopy(data)
    scenario = validate_scenario(data)
    scenario.actors[0].speed_mps = 3.0
    scenario.parameters["custom"] = 20
    assert data == original


def test_scenario_instances_are_copied_and_revalidated():
    original = Scenario.model_validate(_custom_scenario())
    materialized = validate_scenario(original)
    assert original.actors[0].id is None
    assert materialized.actors[0].id == "ego"
    materialized.actors[0].speed_mps = 12.0
    assert original.actors[0].speed_mps == 0.0
    original.actors[0].speed_mps = -1.0
    with pytest.raises(ScenarioLoadError, match="speed_mps"):
        validate_scenario(original)


def test_nested_model_instances_inside_mappings_are_revalidated():
    data = _custom_scenario()
    data["weather"] = Weather()
    data["weather"].fog_density = -1.0
    with pytest.raises(ScenarioLoadError, match="fog_density"):
        validate_scenario(data)


def test_default_actor_ids_resolve_relative_spawns_and_events_before_validation():
    data = _custom_scenario()
    data["actors"][1]["relative_spawn"] = {"anchor_actor_id": "ego", "offset_m": {"x": 15.0}}
    data["events"] = [{"action": "set_speed", "actor_id": "vehicle", "trigger_time_s": 1.0, "speed_mps": 4.0}]
    scenario = validate_scenario(data)
    assert scenario.actors[1].relative_spawn.anchor_actor_id == scenario.actors[0].id
    assert scenario.events[0].actor_id == scenario.actors[1].id
    data["events"][0]["actor_id"] = "missing"
    with pytest.raises(ScenarioLoadError, match="unknown actor ID"):
        validate_scenario(data)


@pytest.mark.parametrize("text", ["", "null", "[]", "17", "just a string"])
def test_non_mapping_yaml_roots_are_rejected(text):
    with pytest.raises(ScenarioLoadError, match="scenario root must be a mapping"):
        loads_scenario(text)


@pytest.mark.parametrize("text", ["null", "[]", "17", '"just a string"'])
def test_non_mapping_json_roots_are_rejected(text):
    with pytest.raises(ScenarioLoadError, match="scenario root must be a mapping"):
        loads_scenario(text, format="json")


@pytest.mark.parametrize("text", [
    "name: first\nname: second\nactors: [{type: ego_vehicle}]",
    "name: test\nactors: [{type: ego_vehicle, speed_mps: 1, speed_mps: 2}]",
    "name: test\nactors: [{type: ego_vehicle}]\nparameters: {value: 1, value: 2}",
    "name: test\nactors: [{type: ego_vehicle}]\nparameters: {<<: &values {a: 1}, a: 2}",
])
def test_duplicate_yaml_keys_are_rejected_at_all_depths(text):
    with pytest.raises(ScenarioLoadError, match="duplicate mapping key"):
        loads_scenario(text)


@pytest.mark.parametrize("text", [
    '{"name":"one","name":"two","actors":[{"type":"ego_vehicle"}]}',
    '{"name":"one","actors":[{"type":"ego_vehicle","id":"a","id":"b"}]}',
    '{"name":"one","actors":[{"type":"ego_vehicle"}],"parameters":{"a":1,"a":2}}',
])
def test_duplicate_json_keys_are_rejected_at_all_depths(text):
    with pytest.raises(ScenarioLoadError, match="duplicate mapping key"):
        loads_scenario(text, format="json")


@pytest.mark.parametrize("text", [
    "!!python/object/apply:os.system ['echo unsafe']",
    "name: one\nactors: [{type: ego_vehicle}]\n---\nname: two",
    "actors: [unterminated",
    "? [complex, key]\n: value",
])
def test_invalid_or_unsafe_yaml_reports_the_source(text):
    with pytest.raises(ScenarioLoadError, match="case.yaml"):
        loads_scenario(text, source="case.yaml")


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonstandard_json_numbers_are_rejected(constant):
    text = '{"name":"test","actors":[{"type":"ego_vehicle"}],"duration_s":' + constant + "}"
    with pytest.raises(ScenarioLoadError, match="non-finite JSON number"):
        loads_scenario(text, format="json")


@pytest.mark.parametrize("extension", ["yaml", "yml", "json", "JSON"])
def test_complete_round_trip_with_utf8_and_materialized_defaults(tmp_path, extension):
    path = tmp_path / f"scenario.{extension}"
    scenario = validate_scenario(_custom_scenario())
    assert save_scenario(scenario, path) == path
    assert "déjà vu" in path.read_text(encoding="utf-8")
    assert load_scenario(str(path)).model_dump() == scenario.model_dump()
    assert "fixed_delta_seconds" in path.read_text(encoding="utf-8")
    assert "vehicle_2" in path.read_text(encoding="utf-8")


def test_save_validates_before_touching_an_existing_file(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text("keep me", encoding="utf-8")
    with pytest.raises(ScenarioLoadError, match="scenario.json"):
        save_scenario({"name": "broken", "actors": []}, path)
    assert path.read_text(encoding="utf-8") == "keep me"


def test_save_replaces_explicit_target(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text("replace me", encoding="utf-8")
    save_scenario(_custom_scenario(), path)
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "Custom déjà vu"


def test_file_errors_identify_the_path(tmp_path):
    missing = tmp_path / "missing.yaml"
    with pytest.raises(ScenarioLoadError, match="missing.yaml"):
        load_scenario(missing)
    broken = tmp_path / "broken.json"
    broken.write_text('{"name":', encoding="utf-8")
    with pytest.raises(ScenarioLoadError, match="broken.json"):
        load_scenario(broken)
    invalid = tmp_path / "invalid.yaml"
    invalid.write_bytes(b"\xff\xfe")
    with pytest.raises(ScenarioLoadError, match="invalid.yaml"):
        load_scenario(invalid)


def test_model_errors_are_wrapped_with_source(tmp_path):
    path = tmp_path / "model.yaml"
    path.write_text("name: test\nactors: [{type: unknown}]", encoding="utf-8")
    with pytest.raises(ScenarioLoadError, match="model.yaml.*invalid scenario"):
        load_scenario(path)


@pytest.mark.parametrize("operation", [load_scenario, lambda path: save_scenario(_custom_scenario(), path)])
def test_unsupported_extensions_are_rejected(tmp_path, operation):
    with pytest.raises(ScenarioLoadError, match="unsupported scenario extension"):
        operation(tmp_path / "scenario.txt")


def test_format_errors_and_invalid_input_are_wrapped():
    with pytest.raises(ScenarioLoadError, match="unsupported scenario format"):
        loads_scenario("{}", format="pickle")
    with pytest.raises(ScenarioLoadError, match="scenario text must be a string"):
        loads_scenario(None)
    with pytest.raises(ScenarioLoadError, match="scenario root must be a mapping"):
        validate_scenario([])


def test_template_listing_is_stable_and_enum_based():
    assert list_templates() == tuple(ScenarioTemplate)


@pytest.mark.parametrize("name", ["unknown", "../secrets", "intersection.yaml", "", None])
def test_template_names_cannot_address_arbitrary_files(name):
    with pytest.raises(ScenarioLoadError, match="unknown scenario template"):
        load_template(name)


def test_bundled_templates_are_fresh_and_independent_of_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in list_templates():
        first = load_template(name)
        second = load_template(name.value)
        assert first.template == name
        assert first.model_dump() == second.model_dump()
        assert first is not second
        assert first.actors[0] is not second.actors[0]
        assert all(actor.id and actor.blueprint for actor in first.actors)


def test_builtin_parameters_are_filled_and_checked_against_materialized_behavior():
    scenario = load_template("intersection")
    payload = scenario.model_dump(mode="json")
    payload["parameters"] = {}
    assert validate_scenario(payload).parameters == scenario.parameters
    payload["parameters"] = {"ego_speed_mps": scenario.parameters["ego_speed_mps"] + 1}
    with pytest.raises(ScenarioLoadError, match="actors.ego.speed_mps"):
        validate_scenario(payload)
    payload["parameters"] = {"surprise_parameter": 1}
    with pytest.raises(ScenarioLoadError, match="[Uu]nknown"):
        validate_scenario(payload)


def test_explicit_empty_blueprints_and_duplicate_ids_are_not_overwritten():
    data = _custom_scenario()
    data["actors"][0]["blueprint"] = ""
    with pytest.raises(ScenarioLoadError, match="blueprint"):
        validate_scenario(data)
    data["actors"][0]["blueprint"] = None
    data["actors"][0]["id"] = "same"
    data["actors"][1]["id"] = "same"
    with pytest.raises(ScenarioLoadError, match="unique"):
        validate_scenario(data)
