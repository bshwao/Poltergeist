"""Conservative objectives and failure evidence for automated experiments.

An unavailable objective is never converted to zero. Positive observations can
survive partial coverage; absence only establishes a negative when the relevant
channel was observed throughout the completed run.
"""

from dataclasses import dataclass
import math

from shared.models import EvaluationSettings, FailureKind, SimulationResult, SimulationStatus


@dataclass(frozen=True)
class ResultAssessment:
    score: float | None
    matches: bool | None
    reason: str
    failure_kind: FailureKind | None


_PRIORITY = (
    FailureKind.COLLISION, FailureKind.NEAR_MISS,
    FailureKind.HARD_BRAKING, FailureKind.TRAFFIC_VIOLATION,
)


def assess_result(
    result: SimulationResult, *, kind: FailureKind | None = None,
    minimum_severity: float = 0,
) -> ResultAssessment:
    """Assess a terminal result using its recorded evaluation settings.

    With no requested kind, choose the first observed failure in the documented
    priority order. An entirely observed run with no failure is a known negative.
    A partial run without positive evidence is inconclusive.
    """
    if isinstance(minimum_severity, bool) or not isinstance(minimum_severity, (int, float)):
        raise ValueError("minimum_severity must be a finite number between 0 and 100")
    if not 0 <= minimum_severity <= 100 or not math.isfinite(minimum_severity):
        raise ValueError("minimum_severity must be a finite number between 0 and 100")
    if kind is not None:
        kind = FailureKind(kind)
    # Revalidate mutated instances as the same immutable snapshots used by runs.
    result = SimulationResult.model_validate(result.model_dump(mode="python"))
    if result.simulation.status != SimulationStatus.COMPLETED:
        return ResultAssessment(None, None, "Run did not complete successfully", kind)
    metrics = result.metrics
    if metrics.severity is None:
        return ResultAssessment(None, None, "Completed run has no valid severity objective", kind)
    score = metrics.severity
    settings = metrics.evaluation_settings or EvaluationSettings()
    positives = {
        FailureKind.COLLISION: (metrics.collision_count or 0) > 0,
        FailureKind.NEAR_MISS: (
            metrics.min_ttc_s is not None and metrics.min_ttc_s < settings.ttc_warning_s
        ) or (
            metrics.min_clearance_m is not None and metrics.min_clearance_m < settings.clearance_warning_m
        ),
        FailureKind.HARD_BRAKING: (metrics.hard_braking_count or 0) > 0,
        FailureKind.TRAFFIC_VIOLATION: (metrics.traffic_violation_count or 0) > 0,
    }
    coverage = metrics.coverage
    contiguous = coverage.total_frames > 0 and not coverage.has_gaps
    complete = {
        FailureKind.COLLISION: contiguous and coverage.collision_frames == coverage.total_frames
        and metrics.collision_count is not None,
        FailureKind.HARD_BRAKING: contiguous and coverage.braking_frames == coverage.total_frames
        and metrics.hard_braking_count is not None,
        FailureKind.TRAFFIC_VIOLATION: contiguous and coverage.traffic_rule_frames == coverage.total_frames
        and metrics.traffic_violation_count is not None,
        FailureKind.NEAR_MISS: contiguous
        and coverage.clearance_frames == coverage.ttc_frames == coverage.total_frames
        and coverage.ttc_pairs == coverage.clearance_pairs == coverage.actor_pairs,
    }
    selected = kind or next((item for item in _PRIORITY if positives[item]), None)
    if selected is None:
        known = all(complete.values())
        return ResultAssessment(
            score, False if known else None,
            "No observed failure in fully observed channels" if known else "No positive failure evidence; coverage is incomplete",
            None,
        )
    if positives[selected] and score >= minimum_severity:
        return ResultAssessment(score, True, "Observed failure meets the severity threshold", selected)
    if complete[selected]:
        reason = "Observed failure is below the severity threshold" if positives[selected] else "Requested failure was not observed"
        return ResultAssessment(score, False, reason, selected)
    return ResultAssessment(score, None, "Requested failure or threshold is not established with complete coverage", selected)
