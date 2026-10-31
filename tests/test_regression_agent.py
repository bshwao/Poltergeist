"""Introduced SDK regressions, frozen versions and unavailable paired evidence."""

import json
from pathlib import Path
import shutil
import sys
from time import monotonic, sleep
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import update

from backend.config import Settings
from backend.main import create_app
from backend.models import SimulationRecord as SimulationRow
from backend.services import experimentation_service as evidence, regression_service as regression
from evaluation import evaluate_safety
from shared.models import CollisionEvent, TelemetryFrame
from shared.models.optimization import ComparisonAgent
from simulator import SimulationRunner
from simulator.test_adapter import TestSimulationAdapter


class ControlledContact(TestSimulationAdapter):
    def step(self, control, commands):
        frame = super().step(control, commands)
        if control.throttle > .5:
            frame.collisions = [CollisionEvent(other_actor_id="fixture:barrier")]
        return frame


def build_package(directory, version, throttle):
    directory.mkdir()
    (directory / "agent.json").write_text(json.dumps({"protocol_version": "1.3", "agent_id": "regression-fixture",
        "name": "Frozen regression fixture", "version": version, "entry_point": "agent:Driver",
        "observation_mode": "simulator_state"}), encoding="utf-8")
    (directory / "agent.py").write_text(
        "from simulator.agents import AutonomousAgent\n"
        "class Driver(AutonomousAgent):\n"
        " def load(self, configuration): pass\n"
        " def reset(self, context): pass\n"
        f" def step(self, observation): return {{'protocol_version':'1.3','frame':observation.frame,'control':{{'throttle':{throttle!r}}}}}\n"
        " def close(self): pass\n", encoding="utf-8")
    return directory


def register(client, directory):
    response = client.post("/agents", json={"package_path": str(directory), "python_executable": sys.executable,
        "trusted_local_code": True, "process_options": {"close_timeout_s": 10}})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(scope="module")
def frozen_suite(tmp_path_factory):
    root = tmp_path_factory.mktemp("frozen-regression")
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(root / 'runs.db').as_posix()}",
        storage_dir=root / "artifacts", simulation_shutdown_timeout_s=5.)
    app = create_app(settings, runner_factory=lambda **options: SimulationRunner(ControlledContact(), **options))
    with TestClient(app) as client:
        versions = [register(client, build_package(root / "reference", "1.0", .1)),
                    register(client, build_package(root / "candidate", "2.0", .9))]
        definition = {"kind": "suite", "name": "Introduced synthetic regression", "mode": "test",
            "agents": [item["id"] for item in versions], "seeds": [7, 11, 13],
            "cases": [{"name": "Controlled observed contact", "scenario": {"name": "Regression fixture",
                "duration_s": .1, "actors": [{"id": "ego", "type": "ego_vehicle", "speed_mps": 4., "spawn": {"position": {"x": 0}}},
                    {"id": "lead", "type": "lead_vehicle", "speed_mps": 0., "spawn": {"position": {"x": 12}}}]}}]}
        response = client.post("/experimentation/suites", json={"definition": definition})
        assert response.status_code == 201, response.text
        suite = response.json()
        identity = str(uuid4())
        response = client.post(f"/experimentation/suites/{suite['id']}/runs", json={"request_id": identity, "expected_revision": 1})
        assert response.status_code == 202, response.text
        deadline = monotonic() + 180
        while monotonic() < deadline:
            response = client.get(f"/experimentation/suite-runs/{identity}")
            assert response.status_code == 200, response.text
            journal = response.json()
            if journal["evidence"]["operation"]["status"] == "completed":
                break
            sleep(.1)
        else:
            raise AssertionError("Frozen fixture suite did not complete")
        assert len(journal["evidence"]["trials"]) == 6
    # Copy only after the original runtime and its SQLite writer have closed.
    return root, identity, journal, versions


@pytest.fixture
def client(frozen_suite, tmp_path):
    original, identity, journal, versions = frozen_suite
    clone = tmp_path / "closed-workspace-copy"
    shutil.copytree(original, clone)
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(clone / 'runs.db').as_posix()}", storage_dir=clone / "artifacts")
    app = create_app(settings)
    with TestClient(app) as api:
        yield api, identity, journal, versions, settings


def review(fixture, policy=None):
    client, identity, *_ = fixture
    response = client.get(f"/experimentation/regressions/{identity}") if policy is None else client.post(
        f"/experimentation/regressions/{identity}", json=policy)
    assert response.status_code == 200, response.text
    return response.json()


def changed_child(fixture, change):
    client, _, journal, *_ = fixture
    identity = next(row["simulation_id"] for row in journal["evidence"]["trials"] if row["agent_index"] == 1)
    persistence = client.app.state.runtime.persistence()
    record = persistence.get_simulation(identity)
    body = record.result.model_dump(mode="json")
    change(body)
    validated = type(record.result).model_validate(body)
    with persistence.database.session() as session:
        session.execute(update(SimulationRow).where(SimulationRow.id == identity).values(result=validated.model_dump_json()))


def rewrite_observations(fixture, change, *, agent_index=None):
    """Make internally consistent partial observations in this test's clone."""
    api, _, journal, *_ = fixture
    persistence = api.app.state.runtime.persistence()
    for trial in journal["evidence"]["trials"]:
        if agent_index is not None and trial["agent_index"] != agent_index:
            continue
        identity = trial["simulation_id"]
        result = persistence.get_simulation(identity).result
        reference = next(item for item in result.artifacts if item.kind == "telemetry")
        path = persistence.artifacts.validate_reference(reference, identity)
        frames = [TelemetryFrame.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines()]
        change(frames)
        result.metrics = evaluate_safety(frames, settings=result.metrics.evaluation_settings)
        path.write_text("".join(frame.model_dump_json() + "\n" for frame in frames), encoding="utf-8")
        with persistence.database.session() as session:
            session.execute(update(SimulationRow).where(SimulationRow.id == identity).values(result=result.model_dump_json()))


def test_unknown_initial_rules_do_not_hide_fully_observed_contact_regression(client):
    # CARLA's initial frame cannot yet establish interval-based traffic rules.
    rewrite_observations(client, lambda frames: setattr(frames[0], "rule_checks", []))
    value = review(client)
    assert value["state"] == "regression_observed"
    assert value["evidence_review"]["available_runs"] == 6
    contact, severity = (next(item for item in value["assessments"] if item["metric"] == name)
                         for name in ("collision_count", "severity"))
    assert contact["decision"] == "regression_observed" and contact["observed_pairs"] == contact["worsening_pairs"] == 3
    assert severity["decision"] == "inconclusive" and severity["observed_pairs"] == 0
    assert all(pair["comparable"] and pair["differences"] == {"severity": None, "collision_count": 1.0}
               and "severity" in pair["reason"] for pair in value["pairs"])
    assert review(client, {"minimum_pairs": 3, "thresholds": {"severity": 0.0}})["state"] == "inconclusive"
    # Clearing selected contacts does not clear the unselected, unknown rules.
    assert review(client, {"minimum_pairs": 3, "thresholds": {"collision_count": 1.0}})["state"] == "no_regression_observed"


@pytest.mark.parametrize("field,metric", [("collisions", "collision_count"), ("traffic_violations", "traffic_violation_count")])
def test_selected_event_metric_still_requires_every_frame_in_both_runs(client, field, metric):
    rewrite_observations(client, lambda frames: setattr(frames[0], field, None), agent_index=1)
    value = review(client, {"minimum_pairs": 3, "thresholds": {metric: 0.0}})
    assert value["state"] == "inconclusive"
    assert value["assessments"][0]["observed_pairs"] == 0
    assert all(not pair["comparable"] and pair["differences"][metric] is None and metric in pair["reason"]
               for pair in value["pairs"])
    assert value["evidence_review"]["available_runs"] == 6


def test_unknown_velocity_prevents_ttc_comparison_but_preserves_observed_clearance(client):
    def missing_motion(frames):
        for frame in frames:
            frame.ego.velocity_mps = None
            frame.ego.acceleration_mps2 = None
    rewrite_observations(client, missing_motion)
    value = review(client, {"minimum_pairs": 3, "thresholds": {"min_ttc_s": 0.0, "min_clearance_m": 0.0}})
    ttc, clearance = (next(item for item in value["assessments"] if item["metric"] == name)
                      for name in ("min_ttc_s", "min_clearance_m"))
    assert ttc["observed_pairs"] == 0 and ttc["decision"] == "inconclusive"
    assert clearance["observed_pairs"] == 3
    assert all(pair["comparable"] and pair["differences"]["min_ttc_s"] is None
               and pair["differences"]["min_clearance_m"] is not None for pair in value["pairs"])


@pytest.mark.parametrize("metric,fields", [("min_clearance_m", ("bounding_box_extent",)),
    ("min_ttc_s", ("velocity_mps",)), ("max_deceleration_mps2", ("velocity_mps", "acceleration_mps2")),
    ("hard_braking_count", ("velocity_mps", "acceleration_mps2"))])
def test_partial_geometry_or_motion_cannot_be_compared_as_a_complete_measurement(client, metric, fields):
    def missing_initial_sample(frames):
        for field in fields:
            setattr(frames[0].ego, field, None)
    rewrite_observations(client, missing_initial_sample, agent_index=1)
    value = review(client, {"minimum_pairs": 3, "thresholds": {metric: 0.0}})
    assert value["state"] == "inconclusive" and value["assessments"][0]["observed_pairs"] == 0
    assert all(not pair["comparable"] and pair["differences"][metric] is None for pair in value["pairs"])
    # Later observations still produce values; they cannot clear missing samples.
    assert all(pair["compared_values"][metric] is not None for pair in value["pairs"])


@pytest.mark.parametrize("metric", ["collision_count", "traffic_violation_count", "max_deceleration_mps2",
    "hard_braking_count", "min_clearance_m", "min_ttc_s", "severity"])
def test_timeline_gaps_cannot_clear_any_selected_measurement(metric):
    from shared.models import SafetyMetrics
    # A gap is disqualifying even when all observed samples have known values.
    metrics = SafetyMetrics.model_validate({name: 1 for name in ("collision_count", "traffic_violation_count",
        "max_deceleration_mps2", "hard_braking_count", "min_clearance_m", "min_ttc_s", "severity")})
    metrics.coverage = metrics.coverage.model_copy(update={"total_frames": 2, "collision_frames": 2,
        "traffic_rule_frames": 2, "braking_frames": 2, "clearance_frames": 2, "ttc_frames": 2,
        "actor_pairs": 2, "clearance_pairs": 2, "ttc_pairs": 2})
    assert regression._metric_observed(metrics, metric)
    metrics.coverage.has_gaps = True
    assert not regression._metric_observed(metrics, metric)


def test_introduced_registered_controller_regression_is_traced_to_frozen_pairs(client):
    api, _, _, versions, settings = client
    value = review(client)
    assert value["state"] == "regression_observed"
    assert [item["version"] for item in value["controllers"]] == ["1.0", "2.0"]
    assert value["controllers"][0]["execution_digest"] != value["controllers"][1]["execution_digest"]
    assert value["evidence_review"]["available_runs"] == 6
    assert value["evidence_review"]["bytes_read"] <= value["evidence_review"]["byte_limit"]
    assert len(value["pairs"]) == 3 and all(item["comparable"] for item in value["pairs"])
    assert all(item["reference_simulation_id"] and item["compared_simulation_id"] for item in value["pairs"])
    severity = next(item for item in value["assessments"] if item["metric"] == "severity")
    assert severity["decision"] == "regression_observed" and severity["mean_change"] > 5
    assert severity["observed_pairs"] == severity["worsening_pairs"] == 3
    assert review(client) == value
    assert api.app.state.runtime._worker is None
    (settings.storage_dir.parent / "regression-report.json").write_text(json.dumps(value, indent=2), encoding="utf-8")
    assert len(api.get("/simulations?limit=100").json()) == 6


def test_lower_ttc_is_worsening_and_keeps_the_raw_negative_difference(client):
    value = review(client, {"minimum_pairs": 3, "thresholds": {"min_ttc_s": 0.0}})
    assessment = value["assessments"][0]
    assert assessment["worsening_direction"] == "lower"
    assert assessment["mean_change"] < 0 and assessment["decision"] == "regression_observed"
    assert all(item["differences"]["min_ttc_s"] < 0 for item in value["pairs"])


def test_no_regression_means_no_selected_threshold_exceeded_in_every_planned_pair(client):
    value = review(client, {"minimum_pairs": 3, "thresholds": {"severity": 100.0}})
    assert value["state"] == "no_regression_observed"
    assert value["assessments"][0]["observed_pairs"] == value["assessments"][0]["planned_pairs"] == 3
    assert value["assessments"][0]["worsening_pairs"] == 0


def test_worsening_must_exceed_the_exact_measured_threshold(client):
    original = review(client)
    difference = next(item for item in original["pairs"])["differences"]["severity"]
    value = review(client, {"minimum_pairs": 3, "thresholds": {"severity": difference}})
    assert value["state"] == "no_regression_observed"
    assert value["assessments"][0]["worsening_pairs"] == 0


def test_insufficient_repeat_count_preserves_the_measured_difference_without_a_verdict(client):
    value = review(client, {"minimum_pairs": 4, "thresholds": {"severity": 5.0}})
    assert value["state"] == "inconclusive"
    assessment = value["assessments"][0]
    assert assessment["mean_change"] > 5 and assessment["observed_pairs"] == 3
    assert assessment["sample_stddev"] == 0


@pytest.mark.parametrize("corrupt", [False, True])
def test_missing_or_corrupt_telemetry_is_unavailable_not_a_good_score(client, corrupt):
    api, _, journal, _, settings = client
    identity = next(row["simulation_id"] for row in journal["evidence"]["trials"] if row["agent_index"] == 1)
    record = api.get(f"/simulations/{identity}").json()
    ref = next(item for item in record["result"]["artifacts"] if item["kind"] == "telemetry")
    path = settings.storage_dir / ref["path"]
    assert path.resolve().is_relative_to(settings.storage_dir.resolve())
    if corrupt:
        path.write_bytes(b"invalid evidence\n")
    else:
        path.unlink()
    value = review(client)
    assert value["state"] == "inconclusive" and value["evidence_review"]["unavailable_runs"] == 1
    assert all(item["observed_pairs"] == 2 for item in value["assessments"])
    missing = next(item for item in value["pairs"] if not item["comparable"])
    assert all(change is None for change in missing["differences"].values())


@pytest.mark.parametrize("difference", ["inputs", "runtime"])
def test_input_or_runtime_differences_prevent_a_version_regression_verdict(client, difference):
    if difference == "inputs":
        changed_child(client, lambda body: body["agent_inputs"]["assistance"].append("fixture_extra_assistance"))
    else:
        changed_child(client, lambda body: body["runtime"].update(client_version="different-build"))
    value = review(client)
    assert value["state"] == "inconclusive"
    assert sum(not item["comparable"] for item in value["pairs"]) == 1
    assert all(item["observed_pairs"] == 2 for item in value["assessments"])


def test_evidence_review_budget_preserves_unknown_denominators(client, monkeypatch):
    monkeypatch.setattr(evidence, "MAX_REVIEW_BYTES", 1)
    monkeypatch.setattr(regression, "MAX_REVIEW_BYTES", 1)
    value = review(client)
    assert value["state"] == "inconclusive" and value["evidence_review"]["limited_runs"] == 6
    assert all(item["observed_pairs"] == 0 and item["mean_change"] is None for item in value["assessments"])


def test_valid_but_changed_trace_cannot_turn_unobserved_pairs_into_a_clear_review(client):
    api, _, journal, _, settings = client
    identity = next(row["simulation_id"] for row in journal["evidence"]["trials"] if row["agent_index"] == 1)
    record = api.get(f"/simulations/{identity}").json()
    reference = next(item for item in record["result"]["artifacts"] if item["kind"] == "telemetry")
    path = settings.storage_dir / reference["path"]
    assert path.resolve().is_relative_to(settings.storage_dir.resolve())
    frames = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for frame in frames:
        frame["collisions"] = None
    path.write_text("".join(json.dumps(frame) + "\n" for frame in frames), encoding="utf-8")
    value = review(client, {"minimum_pairs": 3, "thresholds": {"severity": 100.0}})
    assert value["state"] == "inconclusive"
    assert value["evidence_review"]["unavailable_runs"] == 1
    assert value["assessments"][0]["observed_pairs"] == 2


def test_metadata_labels_alone_do_not_make_a_distinct_controller_version(client, tmp_path):
    api, _, _, versions, _ = client
    original = ComparisonAgent(agent_id=versions[0]["id"], snapshot=versions[0]["snapshot"])
    renamed = register(api, build_package(tmp_path / "renamed", "renamed-label", .1))
    renamed = ComparisonAgent(agent_id=renamed["id"], snapshot=renamed["snapshot"])
    left, right = regression._controller(0, original), regression._controller(1, renamed)
    assert left.snapshot_digest != right.snapshot_digest
    assert left.execution_digest == right.execution_digest
    assert api.app.state.runtime._worker is None


@pytest.mark.parametrize("policy", [{"minimum_pairs": 1}, {"minimum_pairs": True},
    {"thresholds": {"severity": -1.0}}, {"thresholds": {"severity": "5"}}, {"thresholds": {}},
    {"thresholds": {"unknown_metric": 0.0}}])
def test_invalid_policy_cannot_create_work_or_produce_a_verdict(client, policy):
    api, identity, *_ = client
    assert api.post(f"/experimentation/regressions/{identity}", json=policy).status_code == 422
    assert api.app.state.runtime._worker is None
