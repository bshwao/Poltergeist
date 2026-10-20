"""Analyze validated saved evidence without a queue, CARLA or agent import."""

from uuid import UUID

from evaluation.behavior_analysis import DecisionEvidence, analyze_behavior
from shared.models.behavior import BehaviorSettings


def build_behavior_analysis(persistence, simulation_id: UUID, *, settings: BehaviorSettings | None = None):
    decisions = DecisionEvidence()
    # read_output validates the complete timeline checksum and raw references
    # before anything is returned. The callback retains only compact controls.
    output = persistence.read_output(simulation_id, on_decision=decisions)
    decisions.available = any(ref.kind == "decisions" for ref in output.result.artifacts)
    return analyze_behavior(output.result, output.telemetry, decisions=decisions, settings=settings)
