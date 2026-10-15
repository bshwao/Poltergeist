"""Versioned severity scoring and end-to-end telemetry evaluation."""

from collections.abc import Iterable, Mapping
from typing import Any

from shared.models import EvaluationCoverage, EvaluationSettings, SafetyMetrics, TelemetryFrame

from .braking_metrics import summarize_braking
from .collision_detector import summarize_collisions
from .distance_metrics import bounding_box_clearance
from .traffic_rules import summarize_traffic_rules
from .ttc_calculator import time_to_collision
from .validation import EvaluationError, _plain, validate_frames


def _settings(settings: EvaluationSettings | None) -> EvaluationSettings:
    try:
        return EvaluationSettings() if settings is None else EvaluationSettings.model_validate(_plain(settings))
    except (ValueError, TypeError, RecursionError, AttributeError) as error:
        raise EvaluationError(f"Invalid evaluation settings: {error}") from error


def calculate_severity(metrics: SafetyMetrics, settings: EvaluationSettings | None = None) -> float | None:
    """Score observed collisions at 100; require complete coverage otherwise.

    Noncollision severity is a weighted, capped [0, 99] search heuristic. It is
    not an estimate of crash probability. Missing channels cannot count as zero.
    """

    metrics = SafetyMetrics.model_validate(_plain(metrics))
    config = _settings(settings if settings is not None else metrics.evaluation_settings)
    if metrics.collision_count is not None and metrics.collision_count > 0:
        return 100.0
    if not metrics.coverage.complete or any(value is None for value in (
        metrics.collision_count, metrics.traffic_violation_count, metrics.max_deceleration_mps2,
    )):
        return None

    def clipped(value: float) -> float:
        return min(1.0, max(0.0, value))

    ttc_risk = 0.0 if metrics.min_ttc_s is None else clipped(1.0 - metrics.min_ttc_s / config.ttc_warning_s)
    clearance_risk = 0.0 if metrics.min_clearance_m is None else clipped(1.0 - metrics.min_clearance_m / config.clearance_warning_m)
    braking_risk = clipped(
        (metrics.max_deceleration_mps2 - config.hard_braking_threshold_mps2)
        / (config.braking_full_severity_mps2 - config.hard_braking_threshold_mps2)
    )
    # Saturate before dividing so even large valid integer counts stay finite.
    traffic_risk = min(metrics.traffic_violation_count, config.violations_for_full_severity) / config.violations_for_full_severity
    return min(99.0, (
        config.ttc_weight * ttc_risk + config.clearance_weight * clearance_risk
        + config.braking_weight * braking_risk + config.traffic_rule_weight * traffic_risk
    ))


def evaluate_safety(
    frames: Iterable[TelemetryFrame | Mapping[str, Any]],
    *,
    settings: EvaluationSettings | None = None,
) -> SafetyMetrics:
    """Evaluate an ordered observation window; retain nulls and coverage gaps.

    Counts refer to ego collision/rule episodes, rather than raw sensor messages.
    Each frame is validated, and duplicate/reordered frames are rejected.
    """

    trace = validate_frames(frames)
    config = _settings(settings)
    collisions = summarize_collisions(trace, episode_gap_s=config.collision_episode_gap_s)
    rules = summarize_traffic_rules(
        trace, episode_gap_s=config.violation_episode_gap_s,
        speeding_tolerance_mps=config.speeding_tolerance_mps,
    )
    braking = summarize_braking(
        trace, hard_braking_threshold_mps2=config.hard_braking_threshold_mps2,
        stopped_speed_mps=config.stopped_speed_mps, max_sample_gap_s=config.max_sample_gap_s,
    )
    coverage = EvaluationCoverage(
        total_frames=len(trace), collision_frames=collisions.observed_frames,
        traffic_rule_frames=rules.observed_frames, braking_frames=braking.observed_frames,
        has_gaps=any(
            current.frame != previous.frame + 1 or current.time_s - previous.time_s > config.max_sample_gap_s + 1e-9
            for previous, current in zip(trace, trace[1:])
        ),
    )
    min_clearance: float | None = None
    min_ttc: float | None = None
    for frame in trace:
        if frame.actors is None:
            continue
        clear_frame = ttc_frame = True
        for other in frame.actors:
            coverage.actor_pairs += 1
            clearance = bounding_box_clearance(frame.ego, other)
            if clearance is None:
                clear_frame = ttc_frame = False
                continue
            coverage.clearance_pairs += 1
            min_clearance = clearance if min_clearance is None else min(min_clearance, clearance)
            # An existing overlap is known even if velocity is unavailable.
            if clearance == 0 or (frame.ego.velocity_mps is not None and other.velocity_mps is not None):
                coverage.ttc_pairs += 1
                ttc = time_to_collision(frame.ego, other, horizon_s=config.ttc_horizon_s)
                if ttc is not None:
                    min_ttc = ttc if min_ttc is None else min(min_ttc, ttc)
            else:
                ttc_frame = False
        if clear_frame:
            coverage.clearance_frames += 1
        if ttc_frame:
            coverage.ttc_frames += 1

    metrics = SafetyMetrics(
        collision_count=collisions.count,
        traffic_violation_count=rules.count,
        min_clearance_m=min_clearance, min_ttc_s=min_ttc,
        max_deceleration_mps2=braking.max_deceleration_mps2,
        hard_braking_count=braking.hard_braking_count,
        max_stopping_distance_m=max((episode.distance_m for episode in braking.stopping_episodes), default=None),
        max_stopping_time_s=max((episode.duration_s for episode in braking.stopping_episodes), default=None),
        coverage=EvaluationCoverage.model_validate(coverage.model_dump()),
        evaluation_settings=config,
    )
    metrics.severity = calculate_severity(metrics)
    return metrics
