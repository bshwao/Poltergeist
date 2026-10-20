"""Analyze validated saved evidence without a queue, CARLA or agent import."""

from uuid import UUID

from evaluation.behavior_analysis import DecisionEvidence, analyze_behavior
from shared.models.behavior import BehaviorSettings


def read_behavior_analysis(persistence, simulation_id: UUID, *, settings: BehaviorSettings | None = None, on_decision=None):
    """Validate history once; optional consumers publish nothing until return."""
    decisions = DecisionEvidence()
    def consume(record):
        decisions(record)
        if on_decision is not None:
            on_decision(record)
    # read_output validates the complete timeline checksum and raw references
    # before anything is returned. Control evidence retains only compact controls;
    # optional consumers must likewise publish nothing before this read returns.
    output = persistence.read_output(simulation_id, on_decision=consume)
    decisions.available = any(ref.kind == "decisions" for ref in output.result.artifacts)
    return analyze_behavior(output.result, output.telemetry, decisions=decisions, settings=settings), output


def build_behavior_analysis(persistence, simulation_id: UUID, *, settings: BehaviorSettings | None = None):
    return read_behavior_analysis(persistence, simulation_id, settings=settings)[0]
