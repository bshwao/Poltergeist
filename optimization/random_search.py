"""Independent seeded scenario suggestions within the template's numeric bounds."""

from collections.abc import Mapping
from dataclasses import dataclass
import random

from scenarios import (
    ParameterSpec,
    generate_scenario,
    get_parameter_specs,
    sample_parameters,
    validate_parameters,
)
from shared.models import Scenario, ScenarioTemplate


@dataclass(frozen=True)
class SearchSpace:
    template: ScenarioTemplate
    specs: Mapping[str, ParameterSpec]
    parameters: Mapping[str, float | int]
    ranges: Mapping[str, tuple[float | int, float | int]]


def normalize_search_space(
    template: ScenarioTemplate | str,
    *,
    parameters: Mapping[str, object] | None = None,
    ranges: Mapping[str, object] | None = None,
) -> SearchSpace:
    """Validate the whole space before a sampler or simulation is created.

    Fixed values follow the scenario generator: they override sampling ranges
    and may use wider accepted input bounds. Range endpoints stay within the
    template's ordinary sampling bounds.
    """
    template = ScenarioTemplate(template)
    fixed = {} if parameters is None else parameters
    narrowed = {} if ranges is None else ranges
    sample_parameters(template, random.Random(0), parameters=fixed, ranges=narrowed)
    specs = get_parameter_specs(template)
    validated = validate_parameters(template, fixed)
    normalized_ranges = {}
    for name, spec in specs.items():
        low, high = narrowed.get(name, (spec.minimum, spec.maximum))
        # Reuse the scenario engine's finite/integer normalization rules.
        low = validate_parameters(template, {name: low})[name]
        high = validate_parameters(template, {name: high})[name]
        normalized_ranges[name] = (low, high)
    return SearchSpace(
        template=template,
        specs=specs,
        parameters={name: validated[name] for name in fixed},
        ranges=normalized_ranges,
    )


class RandomSearch:
    """Prefix-stable suggestions, each reproducible using its scenario seed."""

    version = "random-v1"

    def __init__(self, space: SearchSpace, seed: int) -> None:
        self._space = space
        self._seed = seed

    def suggest(self, index: int) -> Scenario:
        return generate_scenario(
            self._space.template,
            seed=(self._seed + index) % (2**31),
            parameters=self._space.parameters,
            ranges=self._space.ranges,
        )

    def observe(self, score: float | None) -> None:
        """Random suggestions are independent of scores, including unknown scores."""
