"""Bindings between built-in parameters and concrete scenario behavior.

The runner consumes the materialized actor, event, and weather fields. Keeping
those fields consistent with parameters makes saved scenarios reproducible.
"""

from collections.abc import Mapping
from math import isclose
from typing import Any

from shared.models import Scenario, ScenarioTemplate

from .parameters import validate_parameters


class TemplateValidationError(ValueError):
    """A built-in template's parameters and concrete behavior disagree."""


def _relative_spawn(x: float, y: float = 0, yaw: float = 0) -> dict[str, Any]:
    return {
        "anchor_actor_id": "ego",
        "offset_m": {"x": x, "y": y, "z": 0},
        "yaw_offset_deg": yaw,
    }


def _layout(template: ScenarioTemplate, parameters: Mapping[str, float | int]) -> dict[str, Any]:
    actors: dict[str, Any] = {
        "ego": {"type": "ego_vehicle", "speed_mps": parameters["ego_speed_mps"]},
    }
    layout: dict[str, Any] = {
        "actors": actors,
        "weather": {
            "precipitation": parameters["precipitation_pct"],
            "wetness": parameters["precipitation_pct"],
            "fog_density": parameters["fog_density_pct"],
            "sun_altitude_deg": parameters["sun_altitude_deg"],
        },
        "occlusion": template == ScenarioTemplate.OCCLUDED_PEDESTRIAN,
        "location": "intersection" if template == ScenarioTemplate.INTERSECTION else None,
        "event": "lead_vehicle_brakes" if template == ScenarioTemplate.SUDDEN_BRAKING else None,
    }
    if template in {ScenarioTemplate.PEDESTRIAN_CROSSING, ScenarioTemplate.OCCLUDED_PEDESTRIAN}:
        distance = parameters["pedestrian_distance_m"]
        lateral = parameters["pedestrian_lateral_offset_m"]
        actors["pedestrian"] = {
            "type": "pedestrian",
            "speed_mps": 0,
            "relative_spawn": _relative_spawn(distance, lateral, -90),
        }
        layout["events"] = [{
            "action": "set_speed", "actor_id": "pedestrian",
            "trigger_time_s": parameters["crossing_delay_s"],
            "speed_mps": parameters["pedestrian_speed_mps"],
        }]
        if template == ScenarioTemplate.OCCLUDED_PEDESTRIAN:
            occluder_distance = distance - parameters["occlusion_gap_m"]
            actors["occluder"] = {
                "type": "vehicle",
                "speed_mps": 0,
                # Put the parked vehicle along the initial ego-to-pedestrian ray.
                "relative_spawn": _relative_spawn(occluder_distance, lateral * (occluder_distance / distance)),
            }
    elif template == ScenarioTemplate.INTERSECTION:
        actors["crossing"] = {
            "type": "crossing_vehicle", "speed_mps": 0,
            "relative_spawn": _relative_spawn(
                parameters["intersection_distance_m"], -parameters["crossing_distance_m"], 90,
            ),
        }
        layout["events"] = [{
            "action": "set_speed", "actor_id": "crossing",
            "trigger_time_s": parameters["crossing_delay_s"],
            "speed_mps": parameters["crossing_speed_mps"],
        }]
    elif template == ScenarioTemplate.SUDDEN_BRAKING:
        actors["lead"] = {
            "type": "lead_vehicle", "speed_mps": parameters["lead_speed_mps"],
            "relative_spawn": _relative_spawn(parameters["lead_distance_m"]),
        }
        layout["events"] = [{
            "action": "brake", "actor_id": "lead",
            "trigger_time_s": parameters["brake_delay_s"],
            "deceleration_mps2": parameters["lead_deceleration_mps2"],
        }]
    return layout


def _actors_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {actor["id"]: actor for actor in payload["actors"] if actor["id"] is not None}


def _require_roles(actors: dict[str, Any], expected: dict[str, Any]) -> None:
    for actor_id, fields in expected.items():
        if actor_id not in actors:
            raise TemplateValidationError(f"Template requires an actor with id '{actor_id}'")
        if actors[actor_id]["type"] != fields["type"]:
            raise TemplateValidationError(f"Actor '{actor_id}' must have type '{fields['type']}'")


def _compare(actual: Any, expected: Any, path: str) -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise TemplateValidationError(f"{path} must contain the template's configured fields")
        for key, value in expected.items():
            _compare(actual.get(key), value, f"{path}.{key}")
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise TemplateValidationError(f"{path} must contain exactly one primary template event")
        for index, value in enumerate(expected):
            _compare(actual[index], value, f"{path}[{index}]")
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if not isinstance(actual, (int, float)) or not isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9):
            raise TemplateValidationError(f"{path} must equal {expected!r} according to template parameters")
    elif actual != expected:
        raise TemplateValidationError(f"{path} must equal {expected!r} according to the template")


def validate_template_scenario(scenario: Scenario) -> Scenario:
    """Reject built-in records whose saved parameters misdescribe their behavior."""

    if scenario.template is None:
        return scenario
    parameters = validate_parameters(scenario.template, scenario.parameters)
    payload = scenario.model_dump(mode="python")
    expected = _layout(scenario.template, parameters)
    actors = _actors_by_id(payload)
    _require_roles(actors, expected["actors"])
    for actor_id, fields in expected["actors"].items():
        _compare(actors[actor_id], fields, f"actors.{actor_id}")
    for field in ("weather", "events", "occlusion", "location", "event"):
        _compare(payload[field], expected[field], field)
    _compare(payload["parameters"], parameters, "parameters")
    return scenario


def apply_template_parameters(scenario: Scenario, parameters: Mapping[str, object]) -> Scenario:
    """Create a new validated record; never mutate the template or its collections."""

    if scenario.template is None:
        raise TemplateValidationError("Parameter bindings require a built-in scenario template")
    values = validate_parameters(scenario.template, parameters)
    payload = scenario.model_dump(mode="python")
    layout = _layout(scenario.template, values)
    actors = _actors_by_id(payload)
    _require_roles(actors, layout["actors"])
    for actor_id, fields in layout["actors"].items():
        actors[actor_id].update(fields)
        if "relative_spawn" in fields:
            actors[actor_id]["spawn"] = None
            actors[actor_id]["spawn_point_index"] = None
    payload["parameters"] = values
    payload["weather"].update(layout["weather"])
    for field in ("events", "occlusion", "location", "event"):
        payload[field] = layout[field]
    return validate_template_scenario(Scenario.model_validate(payload))
