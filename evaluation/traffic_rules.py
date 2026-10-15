"""Summarize reported ego rule violations and measured speeding episodes.

Signal, stop-sign, lane, and sidewalk violations must come from the simulator's
map-aware detectors. Speeding can also be derived when an ego velocity and a
speed limit are both supplied. Unknown rule channels never imply compliance.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from math import hypot, isfinite
from numbers import Real
from typing import Any

from shared.models import TelemetryFrame, TrafficRule

from .validation import validate_frames


@dataclass(frozen=True)
class TrafficRuleEpisode:
    actor_id: str
    rule: TrafficRule
    source_id: str | None
    start_time_s: float
    end_time_s: float


@dataclass(frozen=True)
class TrafficRuleSummary:
    # Known when either the complete event channel or speeding was observed.
    # A known count can therefore be a lower bound under partial coverage.
    count: int | None
    observed_frames: int
    speeding_observed_frames: int
    episodes: tuple[TrafficRuleEpisode, ...]


def summarize_traffic_rules(
    frames: Iterable[TelemetryFrame | Mapping[str, Any]],
    *,
    episode_gap_s: float = 1.0,
    speeding_tolerance_mps: float = 0.5,
) -> TrafficRuleSummary:
    """Count ego violation episodes, preserving distinct signal/sign/zone IDs.

    An observed event list closes absent rules immediately. Unobserved samples
    leave episodes open, but the next active sample must be within the configured
    gap to continue one. Speed samples independently close or continue speeding
    episodes. Replaying the same event ID does not create a fresh observation.
    Historical speeding events cannot identify the current speed zone. A sole
    continuous anonymous episode acquires its first reported source, and a sole
    known source also absorbs anonymous callbacks while that episode continues.

    Speeding means speed *strictly exceeds* limit plus tolerance. Derivation is
    partial observation: ``observed_frames`` counts the full event channel only,
    while ``speeding_observed_frames`` counts usable velocity/limit pairs.
    """
    for name, value in (
        ("episode_gap_s", episode_gap_s),
        ("speeding_tolerance_mps", speeding_tolerance_mps),
    ):
        if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value) or value < 0:
            raise ValueError(f"{name} must be a finite non-negative number")

    validated = validate_frames(frames)
    episodes: list[TrafficRuleEpisode] = []
    active: dict[tuple[str, TrafficRule, str | None], int] = {}
    seen: dict[str, tuple[str, TrafficRule, str | None, str]] = {}
    observed_frames = 0
    partially_observed = False
    speeding_observed_frames = 0

    for frame in validated:
        # Dicts preserve sensor event ordering while deduplicating a frame.
        reported: dict[tuple[str, TrafficRule, str | None], None] = {}
        fresh: dict[tuple[str, TrafficRule, str | None], None] = {}
        if frame.traffic_violations is not None:
            checked = set(TrafficRule) if frame.rule_checks is None else set(frame.rule_checks)
            observed_frames += checked == set(TrafficRule)
            partially_observed = partially_observed or bool(checked)
            for event in frame.traffic_violations:
                replay = False
                if event.event_id is not None:
                    signature = (event.actor_id, event.rule, event.source_id, event.details)
                    if event.event_id in seen:
                        if seen[event.event_id] != signature:
                            raise ValueError(f"Traffic event_id {event.event_id!r} has inconsistent payloads")
                        replay = True
                    else:
                        seen[event.event_id] = signature
                if event.actor_id != frame.ego.actor_id:
                    continue
                partially_observed = True
                key = (event.actor_id, event.rule, event.source_id)
                reported[key] = None
                if not replay:
                    fresh[key] = None

        speeding: bool | None = None
        if frame.ego.velocity_mps is not None and frame.ego.speed_limit_mps is not None:
            speeding_observed_frames += 1
            velocity = frame.ego.velocity_mps
            speed = hypot(velocity.x, velocity.y, velocity.z)
            if not isfinite(speed):
                raise ValueError("Velocity magnitude exceeds the supported finite range")
            threshold = frame.ego.speed_limit_mps + speeding_tolerance_mps
            # Preserve decimal threshold boundaries (e.g. 1.3 == 1.0 + 0.3).
            # If the positive sum overflows, every supported finite speed is
            # below the mathematical threshold.
            speeding = isfinite(threshold) and speed > threshold

        current_speeding = [
            key for key, index in active.items()
            if key[1] == TrafficRule.SPEEDING
            and frame.time_s - episodes[index].end_time_s <= episode_gap_s
        ]
        explicit_speeding = [key for key in fresh if key[1] == TrafficRule.SPEEDING]
        known_sources = [key for key in explicit_speeding if key[2] is not None]
        anonymous = (frame.ego.actor_id, TrafficRule.SPEEDING, None)
        if anonymous in explicit_speeding:
            # An anonymous callback gives no evidence for an additional zone.
            # Resolve it only when one fresh/current named source is available.
            resolved = known_sources[0] if len(known_sources) == 1 else (
                current_speeding[0]
                if not known_sources and len(current_speeding) == 1 and current_speeding[0][2] is not None
                else None
            )
            if resolved is not None:
                fresh = {resolved if key == anonymous else key: None for key in fresh}
                explicit_speeding = list(dict.fromkeys(
                    resolved if key == anonymous else key for key in explicit_speeding
                ))

        if current_speeding == [anonymous] and len(explicit_speeding) == 1 and explicit_speeding[0][2] is not None:
            named = explicit_speeding[0]
            index = active.pop(anonymous)
            active[named] = index
            episodes[index] = replace(episodes[index], source_id=named[2])
            current_speeding = [named]

        # Replayed speed events are historical observations. They cannot select
        # a derived source or preserve an obsolete zone beside fresh evidence.
        reported = {key: None for key in reported if key[1] != TrafficRule.SPEEDING}
        for key in explicit_speeding:
            reported[key] = None
        if speeding:
            # Use explicit zones when available. Otherwise keep the current
            # speeding source while velocity provides continuous evidence.
            derived_keys = explicit_speeding or current_speeding or [anonymous]
            for key in derived_keys:
                reported[key] = None
                fresh[key] = None

        for key in list(active):
            if (
                frame.traffic_violations is not None
                and (frame.rule_checks is None or key[1] in frame.rule_checks)
                and key not in reported
            ):
                del active[key]
            elif speeding is False and key[1] == TrafficRule.SPEEDING and key not in reported:
                del active[key]

        for key in fresh:
            index = active.get(key)
            if index is not None and frame.time_s - episodes[index].end_time_s <= episode_gap_s:
                episodes[index] = replace(episodes[index], end_time_s=frame.time_s)
            else:
                active[key] = len(episodes)
                episodes.append(TrafficRuleEpisode(
                    actor_id=key[0],
                    rule=key[1],
                    source_id=key[2],
                    start_time_s=frame.time_s,
                    end_time_s=frame.time_s,
                ))

    return TrafficRuleSummary(
        count=len(episodes) if partially_observed or speeding_observed_frames else None,
        observed_frames=observed_frames,
        speeding_observed_frames=speeding_observed_frames,
        episodes=tuple(episodes),
    )
