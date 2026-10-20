"""Keep the browser's synthetic review fixture aligned with the actual engine."""

import json
from pathlib import Path

from evaluation.behavior_analysis import DecisionEvidence, analyze_behavior
from shared.models import SimulationResult, TelemetryFrame
from shared.models.behavior import BehaviorAnalysis
from shared.models.decision import DecisionRecord


def test_dashboard_fixture_is_reproducible_backend_evidence():
    path = Path(__file__).resolve().parents[1] / "frontend/tests/fixtures/behavior-review.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    recorded = BehaviorAnalysis.model_validate(data["analysis"])
    result = SimulationResult.model_validate(data["result"])
    frames = [TelemetryFrame.model_validate(item) for item in data["telemetry"]]
    decisions = DecisionEvidence()
    for item in data["records"]:
        decisions(DecisionRecord.model_validate(item))
    measured = analyze_behavior(result, frames, decisions=decisions, settings=recorded.settings)
    assert result.simulation.mode == "test"
    assert frames[0].frame == 100  # Deliberately distinct from pagination offsets.
    assert measured.model_dump(mode="json") == recorded.model_dump(mode="json")
