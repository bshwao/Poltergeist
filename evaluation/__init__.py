"""CARLA-free evaluation of measured telemetry and observation coverage."""

from shared.models import EvaluationCoverage, EvaluationSettings, SafetyMetrics

from .braking_metrics import BrakingSummary, StopEpisode, summarize_braking
from .collision_detector import CollisionEpisode, CollisionSummary, summarize_collisions
from .distance_metrics import bounding_box_clearance, geometry_available
from .safety_score import calculate_severity, evaluate_safety
from .traffic_rules import TrafficRuleEpisode, TrafficRuleSummary, summarize_traffic_rules
from .ttc_calculator import can_compute_ttc, time_to_collision
from .validation import EvaluationError, validate_frames

__all__ = [
    "BrakingSummary", "CollisionEpisode", "CollisionSummary", "EvaluationCoverage",
    "EvaluationError", "EvaluationSettings", "SafetyMetrics", "StopEpisode",
    "TrafficRuleEpisode", "TrafficRuleSummary", "bounding_box_clearance",
    "calculate_severity", "can_compute_ttc", "evaluate_safety", "geometry_available",
    "summarize_braking", "summarize_collisions", "summarize_traffic_rules",
    "time_to_collision", "validate_frames",
]
