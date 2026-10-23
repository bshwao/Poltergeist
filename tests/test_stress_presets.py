from collections import Counter
import hashlib
import json

import pytest

from scenarios.stress_presets import STRESS_PRESETS, get_stress_preset
from scenarios import load_scenario, validate_scenario
from scripts.export_stress_presets import export_presets
from shared.models import ScenarioTemplate


def test_twelve_distinct_recipes_cover_three_levels_of_every_family():
    assert len(STRESS_PRESETS) == len({p.id for p in STRESS_PRESETS}) == 12
    assert Counter(p.family for p in STRESS_PRESETS) == {family: 3 for family in ScenarioTemplate}
    for family in ScenarioTemplate:
        assert {p.intended_stress for p in STRESS_PRESETS if p.family == family} == {"low", "medium", "high"}
    recipes = [p.scenario().model_dump_json() for p in STRESS_PRESETS]
    assert len(set(recipes)) == 12


@pytest.mark.parametrize("preset", STRESS_PRESETS, ids=lambda preset: preset.id)
def test_fixed_presets_are_valid_repeatable_and_do_not_override_ego_controls(preset):
    first, second = preset.scenario(seed=11), preset.scenario(seed=11)
    assert first == second == validate_scenario(first)
    assert first.seed == 11 and first.map == "Town10HD_Opt"
    assert first.duration_s == 8. and first.fixed_delta_seconds == .05
    ego = next(actor for actor in first.actors if actor.type == "ego_vehicle")
    assert all(event.actor_id != ego.id for event in first.events)
    assert preset.scenario(seed=19).parameters == first.parameters
    first.parameters["ego_speed_mps"] = 0.
    assert preset.scenario(seed=11) == second


def test_unknown_preset_is_rejected():
    with pytest.raises(ValueError, match="Unknown collision stress preset"):
        get_stress_preset("not-a-preset")


def test_exported_presets_round_trip_and_preserve_existing_output(tmp_path):
    output = tmp_path / "recipes"
    index = export_presets(output, seed=19)
    assert len(index["presets"]) == 12
    for entry in index["presets"]:
        path = output / entry["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        assert load_scenario(path) == get_stress_preset(entry["id"]).scenario(seed=19)
    before = (output / "index.json").read_bytes()
    with pytest.raises(FileExistsError):
        export_presets(output, seed=23)
    assert (output / "index.json").read_bytes() == before
    assert json.loads(before)["seed"] == 19
