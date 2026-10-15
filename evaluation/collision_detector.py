"""Summarize ego collision contacts without counting repeated sensor callbacks.

The simulator supplies collision events; position overlap alone is not evidence
of a collision. Events against the same actor within ``episode_gap_s`` are one
contact episode. Replayed event IDs never extend a contact's time window.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from math import isfinite
from numbers import Real
from typing import Any

from shared.models import TelemetryFrame

from .validation import validate_frames


@dataclass(frozen=True)
class CollisionEpisode:
    other_actor_id: str
    start_time_s: float
    end_time_s: float
    max_impulse_n_s: float


@dataclass(frozen=True)
class CollisionSummary:
    count: int | None
    observed_frames: int
    episodes: tuple[CollisionEpisode, ...]


def summarize_collisions(
    frames: Iterable[TelemetryFrame | Mapping[str, Any]],
    *,
    episode_gap_s: float = 1.0,
) -> CollisionSummary:
    """Count contact episodes, with ``None`` when the sensor was never observed.

    Empty observed frames do not end contact episodes immediately: CARLA can
    issue intermittent callbacks while actors remain in contact. Separate
    contacts require a gap strictly greater than ``episode_gap_s``. An explicit
    event ID must always identify the same actor and impulse; inconsistent
    replays raise ``ValueError`` instead of silently corrupting a result.
    """
    if isinstance(episode_gap_s, bool) or not isinstance(episode_gap_s, Real) or not isfinite(episode_gap_s) or episode_gap_s < 0:
        raise ValueError("episode_gap_s must be a finite non-negative number")

    validated = validate_frames(frames)
    episodes: list[CollisionEpisode] = []
    latest: dict[str, int] = {}
    seen: dict[str, tuple[str, float]] = {}
    observed_frames = 0

    for frame in validated:
        if frame.collisions is None:
            continue
        observed_frames += 1
        for event in frame.collisions:
            if event.event_id is not None:
                signature = (event.other_actor_id, event.impulse_n_s)
                if event.event_id in seen:
                    if seen[event.event_id] != signature:
                        raise ValueError(f"Collision event_id {event.event_id!r} has inconsistent payloads")
                    continue
                seen[event.event_id] = signature

            index = latest.get(event.other_actor_id)
            if index is not None and frame.time_s - episodes[index].end_time_s <= episode_gap_s:
                previous = episodes[index]
                episodes[index] = replace(
                    previous,
                    end_time_s=frame.time_s,
                    max_impulse_n_s=max(previous.max_impulse_n_s, event.impulse_n_s),
                )
            else:
                latest[event.other_actor_id] = len(episodes)
                episodes.append(CollisionEpisode(
                    other_actor_id=event.other_actor_id,
                    start_time_s=frame.time_s,
                    end_time_s=frame.time_s,
                    max_impulse_n_s=event.impulse_n_s,
                ))

    return CollisionSummary(
        count=len(episodes) if observed_frames else None,
        observed_frames=observed_frames,
        episodes=tuple(episodes),
    )
