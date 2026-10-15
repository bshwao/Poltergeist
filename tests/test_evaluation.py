"""Analytic complete traces, missing observations, scoring, and invalid inputs."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from evaluation.safety_score import calculate_severity, evaluate_safety
from evaluation.validation import EvaluationError, validate_frames
from shared.models import (
    ActorState, EvaluationCoverage, EvaluationSettings, SafetyMetrics, TelemetryFrame,
)


def actor(actor_id="ego", *, x=0, y=0, speed=10, acceleration=0):
    return {
        "actor_id": actor_id,
        "transform": {"position": {"x": x, "y": y}},
        "velocity_mps": {"x": speed},
        "acceleration_mps2": {"x": acceleration},
        "bounding_box_extent": {"x": 2, "y": 1, "z": 1},
    }


def frame(index=0, *, time=None, ego=None, actors=()):
    return {
        "frame": index, "time_s": index * 0.1 if time is None else time,
        "ego": actor() if ego is None else ego, "actors": list(actors),
        "collisions": [], "traffic_violations": [],
    }


def complete_metrics(**overrides):
    return SafetyMetrics(
        collision_count=0, traffic_violation_count=overrides.pop("traffic_violation_count", 0),
        max_deceleration_mps2=overrides.pop("max_deceleration_mps2", 0),
        coverage=EvaluationCoverage(
            total_frames=1, collision_frames=1, traffic_rule_frames=1,
            braking_frames=1, clearance_frames=1, ttc_frames=1,
        ),
        **overrides,
    )


def test_empty_or_unobserved_trace_never_implies_safe_zero():
    for trace in [[], [TelemetryFrame(frame=0, time_s=0, ego=ActorState(actor_id="ego"))]]:
        metrics = evaluate_safety(trace)
        assert metrics.collision_count is None
        assert metrics.traffic_violation_count is None
        assert metrics.max_deceleration_mps2 is None
        assert metrics.hard_braking_count is None
        assert metrics.severity is None
        assert not metrics.coverage.complete


def test_observed_empty_scene_is_distinct_from_missing_actor_feed():
    observed = evaluate_safety([frame()])
    assert observed.coverage.complete
    assert observed.collision_count == observed.traffic_violation_count == 0
    assert observed.max_deceleration_mps2 == observed.severity == 0
    assert observed.min_ttc_s is None
    assert observed.min_clearance_m is None

    missing = frame()
    missing["actors"] = None
    metrics = evaluate_safety([missing])
    assert metrics.coverage.clearance_frames == metrics.coverage.ttc_frames == 0
    assert metrics.severity is None


def test_following_pair_known_clearance_and_ttc():
    metrics = evaluate_safety([frame(actors=[actor("lead", x=14, speed=5)])])
    assert metrics.min_clearance_m == pytest.approx(10)
    assert metrics.min_ttc_s == pytest.approx(2)
    assert metrics.severity == pytest.approx(15)
    assert metrics.coverage.complete
    assert metrics.coverage.actor_pairs == metrics.coverage.ttc_pairs == 1


def test_parallel_miss_is_evaluated_without_a_false_collision_time():
    metrics = evaluate_safety([frame(actors=[actor("adjacent", x=14, y=5, speed=5)])])
    assert metrics.min_ttc_s is None
    assert metrics.min_clearance_m > 2
    assert metrics.coverage.ttc_pairs == 1
    assert metrics.severity == 0


def test_missing_boxes_do_not_become_point_geometry():
    other = actor("lead", x=14, speed=5)
    other["bounding_box_extent"] = None
    metrics = evaluate_safety([frame(actors=[other])])
    assert metrics.min_clearance_m is None
    assert metrics.min_ttc_s is None
    assert metrics.coverage.actor_pairs == 1
    assert metrics.coverage.clearance_pairs == metrics.coverage.ttc_pairs == 0
    assert metrics.severity is None


def test_missing_velocity_preserves_clearance_but_not_ttc_coverage():
    other = actor("lead", x=14)
    other["velocity_mps"] = None
    metrics = evaluate_safety([frame(actors=[other])])
    assert metrics.min_clearance_m == 10
    assert metrics.coverage.clearance_pairs == 1
    assert metrics.coverage.ttc_pairs == 0
    assert metrics.severity is None


def test_geometry_overlap_is_not_invented_sensor_collision():
    other = actor("lead", x=1)
    other["velocity_mps"] = None
    metrics = evaluate_safety([frame(actors=[other])])
    assert metrics.min_clearance_m == metrics.min_ttc_s == 0
    assert metrics.collision_count == 0
    assert metrics.coverage.complete
    assert metrics.severity == 70


def test_collision_is_severe_even_with_other_channels_missing():
    trace = [{"frame": 0, "time_s": 0, "ego": {"actor_id": "ego"},
              "collisions": [{"other_actor_id": "wall", "impulse_n_s": 5}]}]
    metrics = evaluate_safety(trace)
    assert metrics.collision_count == 1
    assert metrics.severity == 100
    assert not metrics.coverage.complete


def test_metric_minima_and_episode_counts_aggregate_the_window():
    trace = [
        frame(0, actors=[actor("lead", x=14, speed=5)]),
        frame(1, ego=actor(x=1, acceleration=-6), actors=[actor("lead", x=14.5, speed=5)]),
        frame(2, ego=actor(x=2, acceleration=-6), actors=[actor("lead", x=15, speed=5)]),
    ]
    for sample in trace:
        sample["traffic_violations"] = [{"actor_id": "ego", "rule": "red_light", "source_id": "light-1"}]
    metrics = evaluate_safety(iter(trace))
    assert metrics.min_clearance_m == pytest.approx(9)
    assert metrics.min_ttc_s == pytest.approx(1.8)
    assert metrics.max_deceleration_mps2 == 6
    assert metrics.hard_braking_count == 1
    assert metrics.traffic_violation_count == 1
    assert metrics.severity == pytest.approx(18 + 7.5 + 5)


def test_derived_speeding_does_not_claim_other_rules_were_checked():
    sample = frame()
    sample["ego"]["speed_limit_mps"] = 5
    sample["traffic_violations"] = None
    metrics = evaluate_safety([sample])
    assert metrics.traffic_violation_count == 1
    assert metrics.coverage.traffic_rule_frames == 0
    assert metrics.severity is None


@pytest.mark.parametrize("trace", [
    [frame(0), frame(0, time=0.1)],
    [frame(0), frame(1, time=0)],
    [frame(1), frame(0)],
    [frame(0), frame(1, ego=actor("different-ego"))],
])
def test_ambiguous_trace_order_or_identity_is_rejected(trace):
    with pytest.raises(EvaluationError):
        evaluate_safety(trace)


@pytest.mark.parametrize("trace", [[frame(0), frame(2)], [frame(0), frame(1, time=1)]])
def test_gaps_retain_observed_metrics_without_claiming_complete_score(trace):
    metrics = evaluate_safety(trace)
    assert metrics.coverage.has_gaps
    assert not metrics.coverage.complete
    assert metrics.collision_count == 0
    assert metrics.severity is None


def test_malformed_and_mutated_input_cannot_bypass_validation():
    sample = TelemetryFrame.model_validate(frame())
    sample.ego.velocity_mps.x = "bad"
    with pytest.raises(EvaluationError):
        evaluate_safety([sample])
    with pytest.raises(EvaluationError):
        validate_frames({"frame": 0})


def test_inputs_are_not_changed_and_settings_are_saved_in_json():
    trace = [frame(actors=[actor("lead", x=14, speed=5)])]
    original = deepcopy(trace)
    settings = EvaluationSettings(ttc_warning_s=4)
    metrics = evaluate_safety(trace, settings=settings)
    assert metrics.severity == pytest.approx(22.5)
    assert trace == original
    assert metrics.evaluation_settings == settings
    assert metrics.evaluation_settings is not settings
    assert SafetyMetrics.model_validate_json(metrics.model_dump_json()) == metrics


def test_custom_horizon_does_not_exclude_warning_window():
    with pytest.raises(ValidationError, match="horizon"):
        EvaluationSettings(ttc_horizon_s=2, ttc_warning_s=3)


def test_scoring_thresholds_weights_and_collision_priority():
    assert calculate_severity(complete_metrics(min_ttc_s=3, min_clearance_m=2, max_deceleration_mps2=4)) == 0
    assert calculate_severity(complete_metrics(min_ttc_s=1.5, min_clearance_m=1, max_deceleration_mps2=6, traffic_violation_count=1)) == pytest.approx(47.5)
    assert calculate_severity(complete_metrics(min_ttc_s=0, min_clearance_m=0, max_deceleration_mps2=8, traffic_violation_count=3)) == 99
    assert calculate_severity(SafetyMetrics(collision_count=1)) == 100
    assert calculate_severity(SafetyMetrics()) is None


def test_large_incident_counts_saturate_without_numeric_overflow():
    assert calculate_severity(complete_metrics(traffic_violation_count=10**500)) == 15


def test_completed_measured_stop_is_included_in_aggregate_metrics():
    trace = [
        frame(0, time=0, ego=actor(speed=4, acceleration=-4)),
        frame(1, time=0.5, ego=actor(speed=2, acceleration=-4)),
        frame(2, time=1, ego=actor(speed=0, acceleration=0)),
    ]
    for sample in trace:
        sample["control"] = {"brake": 1}
    metrics = evaluate_safety(trace)
    assert metrics.max_stopping_time_s == 1
    assert metrics.max_stopping_distance_m == 2
    assert metrics.hard_braking_count == 1
    assert metrics.max_deceleration_mps2 == 4
    assert metrics.coverage.complete


@pytest.mark.parametrize("overrides", [
    {"ttc_weight": 10}, {"ttc_weight": True}, {"ttc_warning_s": 0},
    {"collision_episode_gap_s": -1}, {"max_sample_gap_s": float("nan")},
    {"braking_full_severity_mps2": 4}, {"violations_for_full_severity": 0},
    {"violations_for_full_severity": True},
])
def test_invalid_evaluation_settings_are_rejected(overrides):
    with pytest.raises(ValidationError):
        EvaluationSettings(**overrides)


def test_coverage_counts_cannot_exceed_observations():
    with pytest.raises(ValidationError):
        EvaluationCoverage(total_frames=1, collision_frames=2)
    with pytest.raises(ValidationError):
        EvaluationCoverage(actor_pairs=1, clearance_pairs=0, ttc_pairs=1)
