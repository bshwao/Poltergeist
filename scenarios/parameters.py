"""Numeric parameters shared by template generation and failure searches.

Speeds are metres per second, distances are metres, and delays are seconds.
These are search bounds for the supplied templates, not limits imposed by CARLA.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import math
import random

from shared.models import ScenarioTemplate


class ParameterValidationError(ValueError):
    """A template parameter or requested sampling range is invalid."""


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    minimum: float | int
    maximum: float | int
    default: float | int
    unit: str
    description: str
    integer: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ParameterValidationError("Parameter name must be nonempty")
        if not isinstance(self.integer, bool):
            raise ParameterValidationError(f"{self.name}: integer must be a boolean")
        minimum = _number(self.name, self.minimum, integer=self.integer)
        maximum = _number(self.name, self.maximum, integer=self.integer)
        default = _number(self.name, self.default, integer=self.integer)
        if minimum > maximum:
            raise ParameterValidationError(f"{self.name}: minimum must not exceed maximum")
        if not minimum <= default <= maximum:
            raise ParameterValidationError(f"{self.name}: default must be within [{minimum}, {maximum}]")


def _number(name: str, value: object, *, integer: bool) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParameterValidationError(f"{name}: expected a finite number, got {value!r}")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ParameterValidationError(f"{name}: expected a finite number, got {value!r}")
    if integer:
        if isinstance(value, float) and not value.is_integer():
            raise ParameterValidationError(f"{name}: expected an integer, got {value!r}")
        return int(value)
    return float(value)


_COMMON = (
    ParameterSpec("ego_speed_mps", 1, 25, 12, "m/s", "Initial ego vehicle speed."),
    ParameterSpec("precipitation_pct", 0, 100, 0, "%", "Rain intensity and road wetness."),
    ParameterSpec("fog_density_pct", 0, 60, 0, "%", "Fog density."),
    ParameterSpec("sun_altitude_deg", -10, 90, 70, "deg", "Sun elevation above the horizon."),
)
_PEDESTRIAN = (
    ParameterSpec("pedestrian_speed_mps", 0.5, 3, 1.4, "m/s", "Pedestrian crossing speed."),
    ParameterSpec("pedestrian_distance_m", 15, 45, 18, "m", "Crossing position ahead of the ego vehicle."),
    ParameterSpec("pedestrian_lateral_offset_m", 2, 8, 4, "m", "Pedestrian starting distance from the ego path."),
    ParameterSpec("crossing_delay_s", 0, 8, 1, "s", "Time before the pedestrian starts crossing."),
)
_SPECS: dict[ScenarioTemplate, dict[str, ParameterSpec]] = {
    ScenarioTemplate.PEDESTRIAN_CROSSING: {
        spec.name: spec for spec in (*_COMMON, *_PEDESTRIAN)
    },
    ScenarioTemplate.OCCLUDED_PEDESTRIAN: {
        spec.name: spec for spec in (
            *_COMMON,
            *_PEDESTRIAN,
            ParameterSpec("occlusion_gap_m", 1, 8, 4, "m", "Longitudinal distance from the occluder to the pedestrian."),
        )
    },
    ScenarioTemplate.INTERSECTION: {
        spec.name: spec for spec in (
            *_COMMON,
            ParameterSpec("crossing_speed_mps", 1, 20, 10, "m/s", "Crossing vehicle speed."),
            ParameterSpec("intersection_distance_m", 10, 60, 30, "m", "Intersection distance ahead of the ego vehicle."),
            ParameterSpec("crossing_distance_m", 5, 40, 25, "m", "Crossing vehicle starting distance from the ego path."),
            ParameterSpec("crossing_delay_s", 0, 8, 0, "s", "Time before the crossing vehicle starts moving."),
        )
    },
    ScenarioTemplate.SUDDEN_BRAKING: {
        spec.name: spec for spec in (
            *_COMMON,
            ParameterSpec("lead_speed_mps", 1, 25, 12, "m/s", "Initial lead vehicle speed."),
            ParameterSpec("lead_distance_m", 5, 50, 20, "m", "Initial distance ahead of the ego vehicle."),
            ParameterSpec("brake_delay_s", 0.5, 8, 2, "s", "Time before the lead vehicle starts braking."),
            ParameterSpec("lead_deceleration_mps2", 1, 10, 6, "m/s^2", "Requested lead vehicle deceleration magnitude."),
        )
    },
}


def get_parameter_specs(template: ScenarioTemplate | str) -> dict[str, ParameterSpec]:
    """Return a fresh parameter registry for a built-in template."""
    try:
        template_id = ScenarioTemplate(template)
    except (TypeError, ValueError) as exc:
        choices = ", ".join(item.value for item in ScenarioTemplate)
        raise ParameterValidationError(f"Unknown template {template!r}; expected one of: {choices}") from exc
    return dict(_SPECS[template_id])


def _mapping(values: object, label: str) -> Mapping[str, object]:
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise ParameterValidationError(f"{label} must be a mapping of parameter names to values")
    return values


def _check_names(specs: Mapping[str, ParameterSpec], values: Mapping[str, object]) -> None:
    unknown = [name for name in values if name not in specs]
    if unknown:
        raise ParameterValidationError("Unknown parameter(s): " + ", ".join(repr(name) for name in unknown))


def _value(spec: ParameterSpec, value: object) -> float | int:
    result = _number(spec.name, value, integer=spec.integer)
    if not spec.minimum <= result <= spec.maximum:
        raise ParameterValidationError(
            f"{spec.name}: expected a value within [{spec.minimum}, {spec.maximum}], got {value!r}"
        )
    return result


def validate_parameters(
    template: ScenarioTemplate | str,
    values: Mapping[str, object] | None = None,
) -> dict[str, float | int]:
    """Validate supplied values, filling unspecified parameters with defaults."""
    specs = get_parameter_specs(template)
    supplied = _mapping(values, "parameters")
    _check_names(specs, supplied)
    return {name: _value(spec, supplied.get(name, spec.default)) for name, spec in specs.items()}


def sample_parameters(
    template: ScenarioTemplate | str,
    rng: random.Random,
    *,
    parameters: Mapping[str, object] | None = None,
    ranges: Mapping[str, object] | None = None,
) -> dict[str, float | int]:
    """Sample unfixed values using only the supplied local random generator.

    Custom ranges narrow the built-in bounds. Fixed values override sampling,
    and need only satisfy the built-in bounds; ranges are validated even for
    fixed parameters. All validation happens before any random draws.
    """
    specs = get_parameter_specs(template)
    fixed = _mapping(parameters, "parameters")
    supplied_ranges = _mapping(ranges, "ranges")
    _check_names(specs, fixed)
    _check_names(specs, supplied_ranges)
    validated_fixed = {name: _value(specs[name], value) for name, value in fixed.items()}
    bounds: dict[str, tuple[float | int, float | int]] = {}
    for name, spec in specs.items():
        requested = supplied_ranges.get(name, (spec.minimum, spec.maximum))
        if not isinstance(requested, (tuple, list)) or len(requested) != 2:
            raise ParameterValidationError(f"{name}: range must be a two-item tuple or list [minimum, maximum]")
        minimum, maximum = (_value(spec, endpoint) for endpoint in requested)
        if minimum > maximum:
            raise ParameterValidationError(f"{name}: range minimum must not exceed maximum")
        bounds[name] = (minimum, maximum)

    result: dict[str, float | int] = {}
    for name, spec in specs.items():
        if name in validated_fixed:
            result[name] = validated_fixed[name]
            continue
        minimum, maximum = bounds[name]
        result[name] = (
            rng.randint(int(minimum), int(maximum))
            if spec.integer
            else min(maximum, max(minimum, rng.uniform(minimum, maximum)))
        )
    return result
