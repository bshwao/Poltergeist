"""Conservative experiment evidence, scoring and reproduction decisions."""

from datetime import UTC, datetime

import pytest

from optimization.objective_function import assess_result
from optimization.reproduction import summarize_reproduction
from scenarios.scenario_generator import build_scenario
from shared.models import (
    EvaluationCoverage, EvaluationSettings, FailureKind, SafetyMetrics,
    Simulation, SimulationResult, SimulationStatus,
)


def result(*, status=SimulationStatus.COMPLETED, severity=0.0, complete=True, **metrics):
    now = datetime.now(UTC)
    coverage = EvaluationCoverage(
        total_frames=3, collision_frames=3, traffic_rule_frames=3,
        braking_frames=3, clearance_frames=3, ttc_frames=3,
        actor_pairs=3, clearance_pairs=3, ttc_pairs=3,
    ) if complete else EvaluationCoverage(total_frames=3)
    values = dict(collision_count=0, traffic_violation_count=0, hard_braking_count=0,
                  severity=severity, coverage=coverage)
    values.update(metrics)
    return SimulationResult(
        simulation=Simulation(
            scenario=build_scenario("sudden_braking"), status=status,
            progress=1.0 if status == SimulationStatus.COMPLETED else 0.0,
            created_at=now, started_at=now, finished_at=now,
            error="test failure" if status == SimulationStatus.FAILED else None,
        ),
        metrics=SafetyMetrics(**values), telemetry_frames=3, measured_duration_s=0.1,
    )


@pytest.mark.parametrize("status", [SimulationStatus.FAILED, SimulationStatus.CANCELLED])
def test_failed_or_cancelled_collision_is_not_a_successful_objective(status):
    assessment = assess_result(result(status=status, severity=100, collision_count=1))
    assert assessment.score is None
    assert assessment.matches is None


def test_zero_is_a_valid_score_and_absence_is_known_only_with_coverage():
    assessment = assess_result(result())
    assert assessment.score == 0
    assert assessment.matches is False
    assert assessment.failure_kind is None
    assert assess_result(result(complete=False)).matches is None


def test_unscored_positive_failure_remains_unknown():
    assessment = assess_result(result(severity=None, hard_braking_count=1))
    assert assessment.score is assessment.matches is None


@pytest.mark.parametrize("kind,metric", [
    (FailureKind.COLLISION, {"collision_count": 1}),
    (FailureKind.NEAR_MISS, {"min_ttc_s": 1.0}),
    (FailureKind.NEAR_MISS, {"min_clearance_m": 1.0}),
    (FailureKind.HARD_BRAKING, {"hard_braking_count": 1}),
    (FailureKind.TRAFFIC_VIOLATION, {"traffic_violation_count": 1}),
])
def test_positive_evidence_accepted_even_with_partial_family_observation(kind, metric):
    assessment = assess_result(result(severity=100, complete=False, **metric), kind=kind, minimum_severity=90)
    assert assessment.matches is True
    assert assessment.failure_kind == kind


@pytest.mark.parametrize("kind", list(FailureKind))
def test_no_positive_evidence_unknown_when_family_is_partial(kind):
    assert assess_result(result(severity=100, complete=False), kind=kind).matches is None
    assert assess_result(result(), kind=kind).matches is False


@pytest.mark.parametrize("kind,metric", [
    (FailureKind.COLLISION, {"collision_count": 1}),
    (FailureKind.NEAR_MISS, {"min_clearance_m": 1.0}),
    (FailureKind.HARD_BRAKING, {"hard_braking_count": 1}),
    (FailureKind.TRAFFIC_VIOLATION, {"traffic_violation_count": 1}),
])
def test_threshold_and_inclusive_score_boundary(kind, metric):
    assert assess_result(result(severity=20, **metric), kind=kind, minimum_severity=20).matches is True
    assert assess_result(result(severity=20, **metric), kind=kind, minimum_severity=21).matches is False
    assert assess_result(result(severity=20, complete=False, **metric), kind=kind, minimum_severity=21).matches is None


def test_priority_and_explicit_family_are_independent():
    run = result(severity=100, collision_count=1, min_ttc_s=1.0, hard_braking_count=1, traffic_violation_count=1)
    assert assess_result(run).failure_kind == FailureKind.COLLISION
    run.metrics.collision_count = 0
    assert assess_result(run).failure_kind == FailureKind.NEAR_MISS
    run.metrics.min_ttc_s = None
    assert assess_result(run).failure_kind == FailureKind.HARD_BRAKING
    assert assess_result(run, kind=FailureKind.TRAFFIC_VIOLATION).matches is True


def test_near_miss_uses_recorded_warning_limits_and_strict_comparison():
    settings = EvaluationSettings(ttc_warning_s=1.0, clearance_warning_m=0.5)
    run = result(severity=10, min_ttc_s=1.0, min_clearance_m=0.5, evaluation_settings=settings)
    assert assess_result(run, kind=FailureKind.NEAR_MISS).matches is False
    run.metrics.min_ttc_s = 0.99
    assert assess_result(run, kind=FailureKind.NEAR_MISS).matches is True


def test_near_miss_negative_requires_complete_pair_channels_and_continuity():
    run = result()
    run.metrics.coverage.ttc_pairs = 2
    assert assess_result(run, kind=FailureKind.NEAR_MISS).matches is None
    run.metrics.coverage.ttc_pairs = 3
    run.metrics.coverage.has_gaps = True
    assert assess_result(run, kind=FailureKind.COLLISION).matches is None


@pytest.mark.parametrize("minimum", [True, -1, 101, 10**1000, float("nan"), float("inf"), "5"])
def test_invalid_objective_threshold(minimum):
    with pytest.raises(ValueError):
        assess_result(result(), minimum_severity=minimum)


@pytest.mark.parametrize("outcomes,total,required,expected", [
    ([True, True, None], 3, 2, True),
    ([False, False, None], 3, 2, False),
    ([True, False, None], 3, 2, None),
    ([None, None], 2, 2, None),
    ([], 3, 2, None),
    ([True], 3, 1, True),
    ([False], 3, 3, False),
    ([True, False], 3, 2, None),
    ([True, True, False], 3, 3, False),
])
def test_reproduction_decisions(outcomes, total, required, expected):
    assert summarize_reproduction(outcomes, total_repeats=total, required_successes=required) is expected


@pytest.mark.parametrize("outcomes,total,required", [
    ([], True, 1), ([], 0, 1), ([], 21, 1), ([], 2.0, 1),
    ([], 2, True), ([], 2, 0), ([], 2, 3), ([1], 1, 1),
    (["false"], 1, 1), ([True, False], 1, 1),
])
def test_invalid_reproduction_configuration(outcomes, total, required):
    with pytest.raises(ValueError):
        summarize_reproduction(outcomes, total_repeats=total, required_successes=required)
