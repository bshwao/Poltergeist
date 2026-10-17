"""Deterministic greedy recipe reduction; native trials are owned by the caller.

This is a bounded local simplifier, not a proof of a globally minimal failure.
Every proposal preserves scenario validation and built-in parameter bindings.
Only the caller's repeated, positive failure observations may accept a proposal.
"""

from collections.abc import Iterator
import json
import math

from scenarios.parameters import get_parameter_specs
from scenarios.scenario_loader import load_template, validate_scenario
from scenarios.template_rules import apply_template_parameters
from shared.models import ActorType, Scenario, Weather


def _key(scenario: Scenario) -> str:
    return json.dumps(scenario.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


class FailureMinimizer:
    def __init__(self, scenario: Scenario):
        self._best = validate_scenario(scenario)
        self._seen = {_key(self._best)}
        self._pending: Scenario | None = None
        self._candidates = self._reductions(self._best)

    @property
    def best(self) -> Scenario:
        """Return an isolated copy of the last accepted recipe."""
        return self._best.model_copy(deep=True)

    @staticmethod
    def complexity(scenario: Scenario) -> tuple[int, int, int, float, float, float, float]:
        """Lexicographic cost, suitable for recording as a JSON number array.

        Actors and events dominate parameter deviations, then weather, custom
        numeric controls and duration. Template distance is normalized by each
        parameter's declared search range. Names and descriptive text carry no
        cost because changing them does not simplify executable behavior.
        """
        scenario = validate_scenario(scenario)
        deviations = []
        if scenario.template is not None:
            for name, spec in get_parameter_specs(scenario.template).items():
                deviations.append(abs(scenario.parameters[name] - spec.default) / (spec.maximum - spec.minimum or 1))
        weather = scenario.weather.model_dump()
        defaults = Weather().model_dump()
        weather_distance = sum(abs(weather[name] - defaults[name]) / (180 if name == "sun_altitude_deg" else 100) for name in weather)
        # Log magnitudes keep valid, unusually large finite custom controls
        # representable in the recorded JSON cost without overflowing a sum.
        controls = sum(math.log1p(actor.speed_mps) for actor in scenario.actors)
        for event in scenario.events:
            controls += math.log1p(event.trigger_time_s)
            controls += math.log1p(event.speed_mps if event.action == "set_speed" else event.deceleration_mps2)
        return (
            len(scenario.actors), len(scenario.events), sum(value > 0 for value in deviations),
            sum(deviations), weather_distance, controls, scenario.duration_s,
        )

    def propose(self) -> Scenario | None:
        """Return the next lower-cost recipe; observe it before asking again."""
        if self._pending is not None:
            raise RuntimeError("Observe the pending minimization proposal first")
        for payload in self._candidates:
            try:
                candidate = validate_scenario(payload)
            except ValueError:
                continue
            key = _key(candidate)
            if key in self._seen or self.complexity(candidate) >= self.complexity(self._best):
                continue
            self._seen.add(key)
            self._pending = candidate
            return candidate.model_copy(deep=True)
        return None

    def observe(self, accepted: bool) -> None:
        """Accept only a confirmed reduction; map inconclusive trials to false."""
        if type(accepted) is not bool:
            raise ValueError("accepted must be a boolean; inconclusive trials must be rejected")
        if self._pending is None:
            raise RuntimeError("No pending minimization proposal")
        if accepted:
            self._best = self._pending
            self._candidates = self._reductions(self._best)
        self._pending = None

    @staticmethod
    def _targets(value: float, default: float, *, minimum_step: float = 1e-6) -> Iterator[float]:
        if value == default:
            return
        yield default
        if abs(value - default) > minimum_step:
            midpoint = (value + default) / 2
            if midpoint != value and midpoint != default:
                yield midpoint

    @classmethod
    def _reductions(cls, scenario: Scenario) -> Iterator[dict]:
        payload = scenario.model_dump(mode="python")
        required = {actor.id for actor in load_template(scenario.template).actors} if scenario.template is not None else set()
        anchors = {actor.relative_spawn.anchor_actor_id for actor in scenario.actors if actor.relative_spawn is not None}
        # Remove only dependency leaves. Their own events are removed with them;
        # actors that depend on a surviving anchor retain their original pose.
        for index, actor in enumerate(scenario.actors):
            if actor.type == ActorType.EGO_VEHICLE or actor.id in required or actor.id in anchors:
                continue
            candidate = scenario.model_dump(mode="python")
            candidate["actors"].pop(index)
            candidate["events"] = [event for event in candidate["events"] if event["actor_id"] != actor.id]
            yield candidate
        if scenario.template is None:
            for index in range(len(scenario.events)):
                candidate = scenario.model_dump(mode="python")
                candidate["events"].pop(index)
                yield candidate
        else:
            for name, spec in get_parameter_specs(scenario.template).items():
                value = scenario.parameters[name]
                for target in cls._targets(value, spec.default, minimum_step=(spec.maximum - spec.minimum) / 1024):
                    parameters = dict(scenario.parameters)
                    parameters[name] = int(target) if spec.integer else target
                    try:
                        yield apply_template_parameters(scenario, parameters).model_dump(mode="python")
                    except ValueError:
                        # A default delay can be outside an already short run.
                        continue
        for index, actor in enumerate(scenario.actors):
            for target in cls._targets(actor.speed_mps, 0):
                candidate = scenario.model_dump(mode="python")
                candidate["actors"][index]["speed_mps"] = target
                yield candidate  # Built-in bound fields fail validation below.
        if scenario.template is None:
            for index, event in enumerate(scenario.events):
                for field, default in (("trigger_time_s", 0), ("speed_mps", 0)) if event.action == "set_speed" else (("trigger_time_s", 0), ("deceleration_mps2", 1)):
                    for target in cls._targets(getattr(event, field), default):
                        candidate = scenario.model_dump(mode="python")
                        candidate["events"][index][field] = target
                        yield candidate
        for field, default in Weather().model_dump().items():
            for target in cls._targets(getattr(scenario.weather, field), default):
                candidate = scenario.model_dump(mode="python")
                candidate["weather"][field] = target
                yield candidate
        # Preserve all event times: the shortest proposal contains the final
        # event and at least one tick. A partial shortening is also attempted.
        dt = scenario.fixed_delta_seconds
        last_event = max((event.trigger_time_s for event in scenario.events), default=0)
        minimum_ticks = max(1, math.floor(last_event / dt) + 1)
        current_ticks = math.ceil(scenario.duration_s / dt)
        for ticks in dict.fromkeys((minimum_ticks, (minimum_ticks + current_ticks) // 2)):
            if ticks >= current_ticks:
                continue
            candidate = dict(payload)
            candidate["duration_s"] = ticks * dt
            yield candidate
