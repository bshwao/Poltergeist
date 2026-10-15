"""Exercise cross-component inputs, wire round trips, and invalid boundary data."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
import yaml
from pydantic import ValidationError

from scripts.export_contracts import SCHEMA_PATH, render_schema
from shared.models import (
    ActorState,
    ArtifactReference,
    ControlInput,
    FailureReport,
    SafetyMetrics,
    Scenario,
    Simulation,
    SimulationResult,
    TelemetryFrame,
    Vector3,
    Weather,
)

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "scenarios" / "templates"
CREATED = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def scenario_data():
    return {
        "name": "pedestrian_crossing",
        "seed": 42,
        "actors": [
            {"id": "ego", "type": "ego_vehicle", "speed_mps": 10},
            {"id": "pedestrian", "type": "pedestrian", "speed_mps": 1.4},
        ],
    }


@pytest.mark.parametrize("template_path", sorted(TEMPLATES_DIR.glob("*.yaml")), ids=lambda path: path.stem)
def test_starter_templates_validate_and_round_trip(template_path):
    scenario = Scenario.model_validate(yaml.safe_load(template_path.read_text(encoding="utf-8")))

    restored = Scenario.model_validate_json(scenario.model_dump_json())

    assert restored == scenario
    assert restored.name == template_path.stem
    assert sum(actor.type == "ego_vehicle" for actor in restored.actors) == 1
    assert restored.fixed_delta_seconds == 0.05
    assert restored.seed == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": " "},
        {"schema_version": "2.0"},
        {"seed": -1},
        {"seed": 2**31},
        {"seed": True},
        {"seed": 0.5},
        {"seed": "42"},
        {"duration_s": 0},
        {"duration_s": 0.01},
        {"duration_s": float("nan")},
        {"fixed_delta_seconds": 0.2},
        {"actors": []},
        {"actors": [{"type": "vehicle"}]},
        {"actors": [{"type": "ego_vehicle"}, {"type": "ego_vehicle"}]},
        {"actors": [{"id": "same", "type": "ego_vehicle"}, {"id": "same", "type": "pedestrian"}]},
        {"actors": [{"type": "ego_vehicle", "speed_mps": -1}]},
        {"actors": [{"type": "ego_vehicle", "speed_mps": True}]},
        {"parameters": {"rain": float("inf")}},
        {"parameters": {"delay": {"value": 3}}},
        {"weather": {"precipitation": 101}},
        {"weather": {"sun_altitude_deg": -91}},
        {"occlusion": "false"},
        {"unknown_field": 3},
    ],
)
def test_invalid_scenario_boundary_values_are_rejected(scenario_data, overrides):
    with pytest.raises(ValidationError):
        Scenario.model_validate(scenario_data | overrides)


def test_defaults_are_independent_and_seed_is_preserved(scenario_data):
    first = Scenario.model_validate(scenario_data)
    second = Scenario.model_validate(scenario_data)
    first.parameters["delay_s"] = 2.5
    first.weather.cloudiness = 50

    assert second.parameters == {}
    assert second.weather.cloudiness == 0
    assert json.loads(second.model_dump_json())["seed"] == 42


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan"), True, "12"])
def test_physical_numbers_and_scores_reject_non_numeric_or_nonfinite_data(value):
    for model, field in [(Vector3, "x"), (Weather, "cloudiness"), (SafetyMetrics, "severity")]:
        with pytest.raises(ValidationError):
            model.model_validate({field: value})


@pytest.mark.parametrize(
    "values",
    [
        {"collision_count": -1},
        {"traffic_violation_count": True},
        {"min_ttc_s": -0.1},
        {"min_ttc_s": float("inf")},
        {"min_clearance_m": float("nan")},
        {"max_deceleration_mps2": -1},
        {"severity": 101},
    ],
)
def test_invalid_safety_metrics_are_rejected(values):
    with pytest.raises(ValidationError):
        SafetyMetrics.model_validate(values)


def test_unavailable_metrics_use_json_null():
    payload = SafetyMetrics().model_dump_json()

    assert json.loads(payload)["min_ttc_s"] is None
    assert "Infinity" not in payload
    assert "NaN" not in payload


def test_telemetry_round_trip_preserves_signed_acceleration():
    frame = TelemetryFrame(
        frame=10,
        time_s=0.5,
        ego=ActorState(actor_id="ego", velocity_mps=Vector3(x=10), acceleration_mps2=Vector3(x=-4)),
        actors=[ActorState(actor_id="pedestrian")],
        control=ControlInput(brake=0.7),
        collisions=[{"other_actor_id": "pedestrian", "impulse_n_s": 120}],
        traffic_violations=[{"rule": "speeding", "actor_id": "ego"}],
    )

    restored = TelemetryFrame.model_validate_json(frame.model_dump_json())

    assert restored == frame
    assert restored.ego.acceleration_mps2.x == -4


@pytest.mark.parametrize(
    "overrides",
    [
        {"frame": -1},
        {"frame": True},
        {"time_s": -1},
        {"actors": [{"actor_id": "ego"}]},
        {"actors": [{"actor_id": "other"}, {"actor_id": "other"}]},
        {"control": {"brake": 1.1}},
        {"control": {"steer": -1.1}},
        {"control": {"reverse": "false"}},
        {"ego": {"actor_id": "ego", "bounding_box_extent": {"x": -1, "y": 1, "z": 1}}},
    ],
)
def test_invalid_telemetry_is_rejected(overrides):
    with pytest.raises(ValidationError):
        TelemetryFrame.model_validate({"frame": 0, "time_s": 0, "ego": {"actor_id": "ego"}} | overrides)


@pytest.mark.parametrize(
    "values",
    [
        {"status": "running", "started_at": CREATED, "progress": 0.5},
        {"status": "completed", "started_at": CREATED, "finished_at": CREATED + timedelta(seconds=1), "progress": 1},
        {"status": "failed", "finished_at": CREATED, "error": "Could not spawn ego"},
        {"status": "cancelled", "finished_at": CREATED},
    ],
)
def test_valid_lifecycle_records_round_trip(scenario_data, values):
    simulation = Simulation(scenario=Scenario.model_validate(scenario_data), created_at=CREATED, **values)
    assert Simulation.model_validate_json(simulation.model_dump_json()) == simulation


@pytest.mark.parametrize(
    "values",
    [
        {"status": "queued", "started_at": CREATED},
        {"status": "queued", "progress": 0.1},
        {"status": "running"},
        {"status": "running", "started_at": CREATED, "finished_at": CREATED},
        {"status": "running", "started_at": CREATED, "progress": 1},
        {"status": "completed", "started_at": CREATED, "finished_at": CREATED},
        {"status": "completed", "finished_at": CREATED, "progress": 1},
        {"status": "cancelled"},
        {"status": "failed", "finished_at": CREATED},
        {"error": "unexpected error on queued run"},
        {"status": "running", "started_at": CREATED - timedelta(seconds=1)},
        {"status": "cancelled", "finished_at": CREATED - timedelta(seconds=1)},
        {"created_at": datetime(2026, 1, 1)},
        {"progress": True},
    ],
)
def test_inconsistent_lifecycle_is_rejected(scenario_data, values):
    with pytest.raises(ValidationError):
        Simulation.model_validate({"scenario": scenario_data, "created_at": CREATED} | values)


def test_results_and_failure_snapshots_round_trip(scenario_data):
    simulation = Simulation(
        id=UUID("00000000-0000-4000-8000-000000000001"),
        scenario=Scenario.model_validate(scenario_data),
        status="completed",
        created_at=CREATED,
        started_at=CREATED,
        finished_at=CREATED + timedelta(seconds=1),
        progress=1,
    )
    result = SimulationResult(
        simulation=simulation,
        metrics=SafetyMetrics(collision_count=1, severity=90, min_ttc_s=0),
        telemetry_frames=20,
        measured_duration_s=1,
        artifacts=[{"kind": "telemetry", "path": "telemetry/run.jsonl", "media_type": "application/x-ndjson"}],
    )
    failure = FailureReport(
        simulation_id=simulation.id,
        scenario=simulation.scenario,
        kind="collision",
        severity=90,
        summary="Ego collided with crossing pedestrian",
    )

    assert SimulationResult.model_validate_json(result.model_dump_json()) == result
    assert FailureReport.model_validate_json(failure.model_dump_json()) == failure
    assert failure.scenario.seed == 42
    assert failure.reproduced is None


def test_results_cannot_reference_an_active_run(scenario_data):
    with pytest.raises(ValidationError, match="terminal simulation"):
        SimulationResult(
            simulation=Simulation(scenario=Scenario.model_validate(scenario_data)),
            metrics=SafetyMetrics(), telemetry_frames=0, measured_duration_s=0,
        )


@pytest.mark.parametrize("path", ["/tmp/result.json", "../result.json", "reports/../../result.json", "C:\\result.json", "https://example.com/result", "\\\\server\\share"])
def test_artifact_references_cannot_escape_storage(path):
    with pytest.raises(ValidationError):
        ArtifactReference(kind="report", path=path, media_type="application/json")


def test_committed_schema_matches_python_models():
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render_schema(), "Run python -m scripts.export_contracts"
