"""Parameter bounds and reproducible search spaces for the scenario engine."""

from dataclasses import FrozenInstanceError
import random

import pytest

from scenarios.parameters import (
    ParameterSpec,
    ParameterValidationError,
    get_parameter_specs,
    sample_parameters,
    validate_parameters,
)
from shared.models import ScenarioTemplate


@pytest.mark.parametrize("template", list(ScenarioTemplate))
def test_defaults_and_endpoints_are_valid(template):
    specs = get_parameter_specs(template)
    assert validate_parameters(template) == {name: spec.default for name, spec in specs.items()}
    for endpoint in ("minimum", "maximum"):
        supplied = {name: getattr(spec, endpoint) for name, spec in specs.items()}
        assert validate_parameters(template.value, supplied) == supplied


def test_registries_are_independent_and_specs_are_immutable():
    specs = get_parameter_specs("sudden_braking")
    spec = specs.pop("ego_speed_mps")
    assert "ego_speed_mps" in get_parameter_specs("sudden_braking")
    with pytest.raises(FrozenInstanceError):
        spec.default = 10


@pytest.mark.parametrize("value", [True, False, "12", None, float("nan"), float("inf"), -float("inf"), 0.9, 25.1, 10**500])
def test_invalid_values_name_the_parameter(value):
    with pytest.raises(ParameterValidationError, match="ego_speed_mps"):
        validate_parameters("intersection", {"ego_speed_mps": value})


@pytest.mark.parametrize("function", [validate_parameters, get_parameter_specs])
def test_unknown_template_is_a_parameter_error(function):
    with pytest.raises(ParameterValidationError, match="Unknown template.*missing"):
        function("missing")


def test_unknown_parameter_is_rejected():
    with pytest.raises(ParameterValidationError, match="ego_speed_typo"):
        validate_parameters("intersection", {"ego_speed_typo": 12})


def test_non_mapping_parameters_are_rejected():
    with pytest.raises(ParameterValidationError, match="mapping"):
        validate_parameters("intersection", [])


@pytest.mark.parametrize("template", list(ScenarioTemplate))
def test_sampling_is_repeatable_and_always_within_bounds(template):
    first_rng, second_rng = random.Random(820), random.Random(820)
    first = [sample_parameters(template, first_rng) for _ in range(30)]
    assert first == [sample_parameters(template, second_rng) for _ in range(30)]
    assert first[0] != first[1]
    for sample in first:
        assert validate_parameters(template, sample) == sample


def test_sampling_uses_only_the_supplied_generator():
    before = random.getstate()
    sample_parameters("intersection", random.Random(28))
    assert random.getstate() == before


def test_custom_ranges_and_fixed_overrides_are_respected_without_mutation():
    fixed = {"ego_speed_mps": 24}
    ranges = {"ego_speed_mps": [3, 4], "crossing_speed_mps": (7, 9), "crossing_delay_s": (2, 2)}
    for seed in range(20):
        sample = sample_parameters("intersection", random.Random(seed), parameters=fixed, ranges=ranges)
        assert sample["ego_speed_mps"] == 24
        assert 7 <= sample["crossing_speed_mps"] <= 9
        assert sample["crossing_delay_s"] == 2
    assert fixed == {"ego_speed_mps": 24}
    assert ranges == {"ego_speed_mps": [3, 4], "crossing_speed_mps": (7, 9), "crossing_delay_s": (2, 2)}


@pytest.mark.parametrize("bounds", [None, "1,2", [], [2], [1, 2, 3], [5, 4], [0, 2], [1, 26], [True, 2], [1, float("nan")], [1, float("inf")]])
def test_all_ranges_are_validated_even_for_fixed_values(bounds):
    rng = random.Random(90)
    before = rng.getstate()
    with pytest.raises(ParameterValidationError, match="ego_speed_mps"):
        sample_parameters("intersection", rng, parameters={"ego_speed_mps": 12}, ranges={"ego_speed_mps": bounds})
    assert rng.getstate() == before


@pytest.mark.parametrize("argument", ["parameters", "ranges"])
def test_sampling_rejects_unknown_parameters_before_random_draws(argument):
    rng = random.Random(3)
    before = rng.getstate()
    with pytest.raises(ParameterValidationError, match="typo"):
        sample_parameters("intersection", rng, **{argument: {"typo": 5}})
    assert rng.getstate() == before


def test_integer_specs_accept_integral_values_and_sample_discrete_bounds(monkeypatch):
    import scenarios.parameters as parameters_module

    spec = ParameterSpec("count", 1, 4, 2, "actors", "Actor count.", integer=True)
    monkeypatch.setattr(parameters_module, "get_parameter_specs", lambda template: {"count": spec})
    assert validate_parameters("unused", {"count": 3.0}) == {"count": 3}
    assert isinstance(validate_parameters("unused", {"count": 3.0})["count"], int)
    samples = [sample_parameters("unused", random.Random(seed), ranges={"count": [2, 3]})["count"] for seed in range(20)]
    assert set(samples) == {2, 3}
    assert all(isinstance(value, int) for value in samples)
    with pytest.raises(ParameterValidationError, match="count.*integer"):
        validate_parameters("unused", {"count": 2.5})
    with pytest.raises(ParameterValidationError, match="count.*integer"):
        sample_parameters("unused", random.Random(1), ranges={"count": [1, 3.5]})


@pytest.mark.parametrize("kwargs", [
    {"minimum": float("nan")}, {"minimum": 4, "maximum": 1},
    {"default": 6}, {"default": True}, {"default": 2.5, "integer": True},
    {"minimum": 1.1, "integer": True}, {"maximum": 4.1, "integer": True},
])
def test_invalid_spec_definition_is_rejected(kwargs):
    definition = dict(name="count", minimum=1, maximum=4, default=2, unit="actors", description="Count.")
    definition.update(kwargs)
    with pytest.raises(ParameterValidationError, match="count"):
        ParameterSpec(**definition)
