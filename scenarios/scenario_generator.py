"""Build default or seeded built-in scenarios without connecting to CARLA."""

import random
from collections.abc import Mapping
from typing import Annotated

from pydantic import Field, TypeAdapter

from shared.models import Scenario, ScenarioTemplate
from shared.models.base import Seed

from .parameters import sample_parameters
from .scenario_loader import load_template
from .template_rules import apply_template_parameters

_SEED_ADAPTER = TypeAdapter(Seed)
_COUNT_ADAPTER = TypeAdapter(Annotated[int, Field(ge=0, le=2**31, strict=True)])


class ScenarioGenerationError(ValueError):
    """Invalid template, seed, parameter values, or sampling ranges."""


def build_scenario(
    template: ScenarioTemplate | str,
    *,
    parameters: Mapping[str, object] | None = None,
    seed: int = 0,
    name: str | None = None,
) -> Scenario:
    """Use template defaults for unspecified parameters; fixed values are exact.

    The result includes resolved IDs/blueprints and symbolic relative geometry.
    The CARLA adapter resolves map placement and physical validity at run time.
    """

    try:
        template = ScenarioTemplate(template)
        seed = _SEED_ADAPTER.validate_python(seed)
        base = load_template(template)
        scenario = apply_template_parameters(base, {} if parameters is None else parameters)
        payload = scenario.model_dump(mode="python")
        payload["seed"] = seed
        if name is not None:
            payload["name"] = name
        return Scenario.model_validate(payload)
    except (ValueError, TypeError) as error:
        raise ScenarioGenerationError(f"Cannot build scenario: {error}") from error


def generate_scenario(
    template: ScenarioTemplate | str,
    *,
    seed: int = 0,
    parameters: Mapping[str, object] | None = None,
    ranges: Mapping[str, tuple[float, float]] | None = None,
) -> Scenario:
    """Sample unset parameters from declared bounds using a local seeded RNG.

    ``parameters`` fixes selected values. ``ranges`` narrows sampling bounds for
    the remaining values. The global random state is never read or changed.
    """

    try:
        template = ScenarioTemplate(template)
        seed = _SEED_ADAPTER.validate_python(seed)
        values = sample_parameters(template, random.Random(seed), parameters=parameters, ranges=ranges)
        return build_scenario(template, parameters=values, seed=seed, name=f"{template.value}-{seed}")
    except (ValueError, TypeError) as error:
        raise ScenarioGenerationError(f"Cannot generate scenario: {error}") from error


def generate_scenarios(
    template: ScenarioTemplate | str,
    count: int,
    *,
    seed: int = 0,
    parameters: Mapping[str, object] | None = None,
    ranges: Mapping[str, tuple[float, float]] | None = None,
) -> list[Scenario]:
    """Generate a batch with consecutive seeds, wrapping at 2**31.

    Each item is independently reproducible with ``generate_scenario`` and its
    recorded seed. A longer batch preserves the same prefix; zero returns [].
    """

    try:
        template = ScenarioTemplate(template)
        seed = _SEED_ADAPTER.validate_python(seed)
        count = _COUNT_ADAPTER.validate_python(count)
        # Validate even a zero-length request, without touching global RNG state.
        sample_parameters(template, random.Random(seed), parameters=parameters, ranges=ranges)
        return [
            generate_scenario(template, seed=(seed + index) % (2**31), parameters=parameters, ranges=ranges)
            for index in range(count)
        ]
    except (ValueError, TypeError) as error:
        raise ScenarioGenerationError(f"Cannot generate scenario batch: {error}") from error
