"""Safe, deterministic scenario loading independent of CARLA and the working directory."""

from collections.abc import Mapping
from copy import deepcopy
import json
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError

from shared.models import ActorSpec, ActorType, Scenario, ScenarioTemplate


class ScenarioLoadError(ValueError):
    """A scenario could not be decoded, normalized, or validated."""


_ACTOR_DEFAULTS = {
    ActorType.EGO_VEHICLE: ("ego", "vehicle.tesla.model3"),
    ActorType.LEAD_VEHICLE: ("lead", "vehicle.tesla.model3"),
    ActorType.CROSSING_VEHICLE: ("crossing", "vehicle.audi.tt"),
    ActorType.VEHICLE: ("vehicle", "vehicle.tesla.model3"),
    ActorType.PEDESTRIAN: ("pedestrian", "walker.pedestrian.0001"),
}


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader with duplicate mapping keys rejected, including merged keys."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in result
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    "found an unhashable mapping key", key_node.start_mark,
                ) from exc
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    f"duplicate mapping key {key!r}", key_node.start_mark,
                )
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ScenarioLoadError(f"duplicate mapping key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ScenarioLoadError(f"non-finite JSON number {value!r} is not allowed")


def _materialize_actor_defaults(data: dict[str, Any]) -> None:
    actors = data.get("actors")
    if not isinstance(actors, (list, tuple)):
        return  # Leave shape errors to the shared contract validator.
    normalized = [
        actor.model_dump(mode="python") if isinstance(actor, ActorSpec)
        else dict(actor) if isinstance(actor, Mapping)
        else actor
        for actor in actors
    ]
    # Reserve every explicit ID first, even IDs later in the actor list.
    reserved = {
        actor["id"].strip()
        for actor in normalized
        if isinstance(actor, dict) and isinstance(actor.get("id"), str)
    }
    for actor in normalized:
        if not isinstance(actor, dict):
            continue
        try:
            actor_type = ActorType(actor.get("type"))
        except (ValueError, TypeError):
            continue
        role, blueprint = _ACTOR_DEFAULTS[actor_type]
        if actor.get("id") is None:
            candidate, suffix = role, 2
            while candidate in reserved:
                candidate = f"{role}_{suffix}"
                suffix += 1
            actor["id"] = candidate
            reserved.add(candidate)
        if actor.get("blueprint") is None:
            actor["blueprint"] = blueprint
    data["actors"] = normalized


def validate_scenario(data: Mapping[str, Any] | Scenario) -> Scenario:
    """Return a fresh, fully validated scenario with stable actor defaults.

    Even existing Scenario instances are revalidated, since callers can mutate
    Pydantic models and their nested lists after constructing them.
    """
    if not isinstance(data, (Mapping, Scenario)):
        raise ScenarioLoadError("scenario root must be a mapping")
    try:
        payload = deepcopy(data.model_dump(mode="python") if isinstance(data, Scenario) else dict(data))
        _materialize_actor_defaults(payload)
        if payload.get("template") is not None:
            from .parameters import validate_parameters

            payload["parameters"] = validate_parameters(payload["template"], payload.get("parameters"))
        scenario = Scenario.model_validate(payload)
        # A mapping can itself contain mutated nested contract instances, which
        # Pydantic otherwise accepts without revalidating their existing fields.
        scenario = Scenario.model_validate(scenario.model_dump(mode="python"))
        if scenario.template is not None:
            from .template_rules import validate_template_scenario

            validate_template_scenario(scenario)
        return scenario
    except (ValueError, TypeError) as exc:
        raise ScenarioLoadError(f"invalid scenario: {exc}") from exc


def loads_scenario(text: str, *, format: str = "yaml", source: str = "<string>") -> Scenario:
    """Read one YAML or JSON document, rejecting unsafe tags and duplicate keys."""
    try:
        if not isinstance(text, str):
            raise ScenarioLoadError("scenario text must be a string")
        if format in ("yaml", "yml"):
            data = yaml.load(text, Loader=_UniqueKeySafeLoader)
        elif format == "json":
            data = json.loads(text, object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant)
        else:
            raise ScenarioLoadError(f"unsupported scenario format {format!r}; use yaml or json")
        return validate_scenario(data)
    except (ValueError, TypeError, yaml.YAMLError) as exc:
        raise ScenarioLoadError(f"{source}: {exc}") from exc


def _path_and_format(path: str | Path) -> tuple[Path, str]:
    try:
        resolved = Path(path)
    except TypeError as exc:
        raise ScenarioLoadError(f"invalid scenario path: {exc}") from exc
    suffix = resolved.suffix.lower()
    if suffix not in (".yaml", ".yml", ".json"):
        raise ScenarioLoadError(f"{resolved}: unsupported scenario extension {suffix!r}; use .yaml, .yml, or .json")
    return resolved, suffix[1:]


def load_scenario(path: str | Path) -> Scenario:
    """Load a scenario file with path-specific decoding and validation errors."""
    scenario_path, format = _path_and_format(path)
    try:
        text = scenario_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError, ValueError) as exc:
        raise ScenarioLoadError(f"{scenario_path}: could not read scenario: {exc}") from exc
    return loads_scenario(text, format=format, source=str(scenario_path))


def list_templates() -> tuple[ScenarioTemplate, ...]:
    """Return bundled template names in their stable declaration order."""
    return tuple(ScenarioTemplate)


def load_template(template: ScenarioTemplate | str) -> Scenario:
    """Load an enum-selected bundled template without filesystem interpolation."""
    try:
        kind = ScenarioTemplate(template)
    except (ValueError, TypeError) as exc:
        raise ScenarioLoadError(f"unknown scenario template {template!r}") from exc
    source = f"template:{kind.value}"
    try:
        text = resources.files("scenarios").joinpath("templates", f"{kind.value}.yaml").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ScenarioLoadError(f"{source}: could not read bundled template: {exc}") from exc
    scenario = loads_scenario(text, source=source)
    if scenario.template != kind:
        raise ScenarioLoadError(f"{source}: bundled template must declare template={kind.value!r}")
    return scenario


def save_scenario(scenario: Mapping[str, Any] | Scenario, path: str | Path) -> Path:
    """Validate and write a complete UTF-8 scenario, replacing an explicit path."""
    scenario_path, format = _path_and_format(path)
    try:
        data = validate_scenario(scenario).model_dump(mode="json")
        if format == "json":
            text = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        else:
            text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        scenario_path.write_text(text, encoding="utf-8")
    except (OSError, ValueError, TypeError) as exc:
        raise ScenarioLoadError(f"{scenario_path}: could not save scenario: {exc}") from exc
    return scenario_path
