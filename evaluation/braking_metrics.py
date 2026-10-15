"""Observed braking and completed stopping episodes, in SI units.

Braking is acceleration opposite the direction of travel, so reversing works
and cornering acceleration does not count. When acceleration is unavailable,
adjacent velocity observations provide an interval-average deceleration.
Stopping distances integrate measured speeds; they are never predictions.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import fsum, hypot, isfinite
from typing import Any

from shared.models import TelemetryFrame

from .validation import validate_frames


@dataclass(frozen=True, slots=True)
class StopEpisode:
    start_time_s: float
    end_time_s: float
    duration_s: float
    distance_m: float


@dataclass(frozen=True, slots=True)
class BrakingSummary:
    max_deceleration_mps2: float | None
    hard_braking_count: int | None
    observed_frames: int
    stopping_episodes: tuple[StopEpisode, ...]


def _threshold(value: float, name: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not isfinite(result) or result < 0 or (result == 0 and not allow_zero):
        qualifier = "nonnegative" if allow_zero else "positive"
        raise ValueError(f"{name} must be finite and {qualifier}")
    return result


def summarize_braking(
    frames: Iterable[TelemetryFrame | Mapping[str, Any]],
    *,
    hard_braking_threshold_mps2: float = 4.0,
    stopped_speed_mps: float = 0.1,
    max_sample_gap_s: float = 0.5,
) -> BrakingSummary:
    """Summarize ego braking without assuming unobserved data is zero.

    ``observed_frames`` counts samples with calculable deceleration. Hard
    braking is counted once per continuous sequence at or above the threshold;
    unavailable measurements, skipped frame IDs, or excessive time gaps break
    that sequence. No calculable samples produce ``None`` for both metrics.

    A stop starts with a positive brake/handbrake command or observed
    deceleration while moving. Direct acceleration/brake observations start at
    the current sample;
    velocity-derived braking starts at the previous sample because it describes
    that entire interval. The trajectory is followed until speed reaches the
    stopped threshold. If the vehicle remains moving and both braking commands
    and measured deceleration cease, the pending stop is abandoned. Only
    completed, continuously observed episodes are returned. Missing velocity,
    skipped frames, or a time gap above ``max_sample_gap_s`` abandon a pending
    stop. An unfinished final episode is omitted.
    """
    hard_threshold = _threshold(hard_braking_threshold_mps2, "hard_braking_threshold_mps2")
    stopped_threshold = _threshold(stopped_speed_mps, "stopped_speed_mps", allow_zero=True)
    max_gap = _threshold(max_sample_gap_s, "max_sample_gap_s")
    samples = validate_frames(frames)

    maximum: float | None = None
    hard_count = 0
    observed = 0
    previous: TelemetryFrame | None = None
    previous_speed: float | None = None
    previous_hard = False
    stop_start: float | None = None
    stop_distance = 0.0
    stops: list[StopEpisode] = []

    for sample in samples:
        velocity = sample.ego.velocity_mps
        speed = None if velocity is None else hypot(velocity.x, velocity.y, velocity.z)
        if speed is not None and not isfinite(speed):
            raise ValueError("Velocity magnitude exceeds the supported finite range")
        dt = 0.0 if previous is None else sample.time_s - previous.time_s
        continuous = (
            previous is not None
            and sample.frame == previous.frame + 1
            and dt <= max_gap
            and speed is not None
            and previous_speed is not None
        )

        deceleration: float | None = None
        inferred = False
        acceleration = sample.ego.acceleration_mps2
        if speed is not None and velocity is not None and acceleration is not None:
            try:
                deceleration = 0.0 if speed <= stopped_threshold else max(
                    0.0,
                    -fsum((
                        acceleration.x * (velocity.x / speed),
                        acceleration.y * (velocity.y / speed),
                        acceleration.z * (velocity.z / speed),
                    )),
                )
            except OverflowError as exc:
                raise ValueError("Acceleration projection exceeds the supported finite range") from exc
        elif continuous:
            assert previous_speed is not None and speed is not None
            deceleration = max(0.0, (previous_speed - speed) / dt)
            inferred = True

        hard = False
        if deceleration is not None:
            if not isfinite(deceleration):
                raise ValueError("Deceleration exceeds the supported finite range")
            maximum = deceleration if maximum is None else max(maximum, deceleration)
            observed += 1
            hard = deceleration >= hard_threshold
            if hard and (not previous_hard or not continuous):
                hard_count += 1

        decelerating = deceleration is not None and deceleration > 0
        braking = sample.control.brake > 0 or sample.control.hand_brake or decelerating
        if not continuous or (speed is not None and speed > stopped_threshold and not braking):
            stop_start = None
            stop_distance = 0.0

        if stop_start is not None:
            assert speed is not None and previous_speed is not None
            stop_distance += (previous_speed / 2 + speed / 2) * dt
        elif speed is not None:
            if decelerating and inferred and previous_speed is not None and previous_speed > stopped_threshold:
                assert previous is not None
                stop_start = previous.time_s
                stop_distance = (previous_speed / 2 + speed / 2) * dt
            elif braking and speed > stopped_threshold:
                stop_start = sample.time_s
                stop_distance = 0.0

        if stop_start is not None and not isfinite(stop_distance):
            raise ValueError("Stopping distance exceeds the supported finite range")
        if stop_start is not None and speed is not None and speed <= stopped_threshold:
            stops.append(StopEpisode(
                start_time_s=stop_start,
                end_time_s=sample.time_s,
                duration_s=sample.time_s - stop_start,
                distance_m=stop_distance,
            ))
            stop_start = None
            stop_distance = 0.0

        previous = sample
        previous_speed = speed
        previous_hard = hard

    return BrakingSummary(
        max_deceleration_mps2=maximum,
        hard_braking_count=hard_count if observed else None,
        observed_frames=observed,
        stopping_episodes=tuple(stops),
    )
