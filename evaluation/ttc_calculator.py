"""First predicted oriented-box contact under constant linear world velocity."""

import math

from shared.models import ActorState

from .geometry import (
    CONTACT_TOLERANCE_M,
    actor_box,
    dot,
    finite,
    norm,
    overlap,
    projections,
    relative_boxes,
    sub,
)
from .validation import validate_actor


def can_compute_ttc(ego: ActorState, other: ActorState) -> bool:
    """Whether TTC is observed, including overlap with unavailable velocity."""
    ego, other = validate_actor(ego), validate_actor(other)
    a, b = actor_box(ego), actor_box(other)
    if a is None or b is None:
        return False
    return (ego.velocity_mps is not None and other.velocity_mps is not None) or overlap(a, b)


def time_to_collision(ego: ActorState, other: ActorState, *, horizon_s: float = 10.0) -> float | None:
    """Predict first 3D box contact within an inclusive finite time horizon.

    Box orientations and velocities remain fixed; acceleration and angular
    velocity are ignored. SAT requires simultaneous overlap on every axis,
    avoiding false alarms from vehicles whose projected paths pass nearby.
    Current box overlap/touch returns 0 even if velocity is missing. Otherwise
    None means missing geometry/velocity or no contact within the horizon;
    use ``can_compute_ttc`` to distinguish measurement availability.
    """
    if isinstance(horizon_s, bool) or not isinstance(horizon_s, (int, float)):
        raise ValueError("TTC horizon_s must be a positive finite number")
    try:
        horizon_s = float(horizon_s)
    except OverflowError as exc:
        raise ValueError("TTC horizon_s must be a positive finite number") from exc
    if not math.isfinite(horizon_s) or horizon_s <= 0:
        raise ValueError("TTC horizon_s must be a positive finite number")
    ego, other = validate_actor(ego), validate_actor(other)
    a, b = actor_box(ego), actor_box(other)
    if a is None or b is None:
        return None
    a, b = relative_boxes(a, b)
    projected = projections(a, b)
    if all(abs(distance) <= finite(radius + CONTACT_TOLERANCE_M) for _, distance, radius in projected):
        return 0.0
    if ego.velocity_mps is None or other.velocity_mps is None:
        return None
    first, second = ego.velocity_mps, other.velocity_mps
    relative_velocity = sub((second.x, second.y, second.z), (first.x, first.y, first.z))
    entry, exit_ = 0.0, horizon_s
    time_tolerance = CONTACT_TOLERANCE_M / max(1.0, norm(relative_velocity))
    for axis, distance, radius in projected:
        speed = dot(relative_velocity, axis)
        # Trigonometric roundoff makes an orthogonal projection tiny instead
        # of exactly zero. Treat sub-tolerance travel over the whole horizon
        # as stationary, so an exactly grazing vehicle is not a false miss.
        if abs(speed) <= CONTACT_TOLERANCE_M / horizon_s:
            if abs(distance) > finite(radius + CONTACT_TOLERANCE_M):
                return None
            continue
        first_time = finite(finite(-radius - distance) / speed)
        last_time = finite(finite(radius - distance) / speed)
        axis_entry, axis_exit = sorted((first_time, last_time))
        entry, exit_ = max(entry, axis_entry), min(exit_, axis_exit)
        if finite(entry - exit_) > time_tolerance:
            return None
    return min(entry, horizon_s)
