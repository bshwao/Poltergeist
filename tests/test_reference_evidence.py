"""Evidence preserves frame association, unknowns and performance boundaries."""

from copy import deepcopy
from datetime import UTC, datetime
import json
import math
from types import SimpleNamespace

import pytest

from scenarios import validate_scenario
from shared.models import ActorState, AgentObservation, AgentStepResult, ControlInput, Simulation, SimulationResult, SimulationStatus, TelemetryFrame
from simulator.agents.package import inspect_agent_package
from simulator.reference_evidence import DecisionTrace, compare_runs, latency_summary, motion_summary, package_fingerprint, route_progress, summarize_run


def frame(index, x, *, speed=2.0, time=None):
    return TelemetryFrame(frame=index, time_s=index * 0.05 if time is None else time,
                          ego=ActorState(actor_id="ego", transform={"position": {"x": x}}, velocity_mps=None if speed is None else {"x": speed}),
                          actors=[], collisions=[], traffic_violations=[])


def decision(index=0):
    observation = AgentObservation(frame=index, time_s=index*0.05, dt_s=0.05, ego=frame(index, index*0.1).ego,
                                   actors=[], target_speed_mps=2.0)
    result = AgentStepResult(frame=index, control=ControlInput(throttle=0.5), diagnostics={"metadata": {"checkpoint_sha256": "abc"}})
    return {"frame": index, "time_s": observation.time_s, "observation": observation.model_dump(mode="json"),
            "agent_result": result.model_dump(mode="json"), "proposed_control": result.control.model_dump(mode="json"),
            "applied_control": result.control.model_dump(mode="json"), "interventions": [],
            "worker_step_wall_time_s": 0.001, "round_trip_wall_time_s": 0.002, "full_step_wall_time_s": 0.01,
            "resulting_frame": index+1, "resulting_ego": frame(index+1, (index+1)*0.1).ego.model_dump(mode="json")}


def output(frames=None, *, mode="test", status="completed", execution=0.05):
    frames = [frame(0, 0.0), frame(1, 0.1)] if frames is None else frames
    scenario = validate_scenario({"name": "evidence", "map": "Town03", "duration_s": 0.05,
                                  "actors": [{"id": "ego", "type": "ego_vehicle"}]})
    now = datetime.now(UTC)
    sim = Simulation(scenario=scenario, mode=mode, status=status, progress=1 if status == "completed" else 0,
                     created_at=now, started_at=now, finished_at=now, error="test failed" if status == "failed" else None)
    result = SimulationResult(simulation=sim, metrics={}, telemetry_frames=len(frames),
                              measured_duration_s=0.0 if not frames else frames[-1].time_s)
    return SimpleNamespace(result=result, telemetry=tuple(frames), setup_wall_time_s=0.2, execution_wall_time_s=execution)


def test_percentiles_budget_misses_and_unknown_empty_samples():
    metrics = latency_summary([0.01, 0.02, 0.03, 0.1], 0.05)
    assert metrics["p50_s"] == pytest.approx(0.025)
    assert metrics["p95_s"] == pytest.approx(0.0895)
    assert metrics["p99_s"] == pytest.approx(0.0979)
    assert metrics["budget_misses"] == 1
    assert metrics["budget_miss_fraction"] == 0.25
    empty = latency_summary([], 0.05)
    assert empty["samples"] == 0
    assert empty["p95_s"] is empty["budget_miss_fraction"] is None


@pytest.mark.parametrize("samples,budget", [([math.nan], 0.05), ([math.inf], 0.05), ([-1], 0.05), ([True], 0.05), ([0.1], 0), ([0.1], math.inf), ([0.1], True)])
def test_latency_rejects_non_measurements(samples, budget):
    with pytest.raises(ValueError):
        latency_summary(samples, budget)


def test_trace_detaches_validated_payload_and_keeps_raw_evidence(tmp_path):
    source = decision()
    with DecisionTrace(tmp_path / "trace.jsonl") as trace:
        trace(source)
        source["proposed_control"]["throttle"] = 0
        assert trace.records[0]["proposed_control"]["throttle"] == 0.5
        trace(decision(1))
    saved = [json.loads(line) for line in trace.path.read_text().splitlines()]
    assert saved == trace.records
    with pytest.raises(FileExistsError):
        DecisionTrace(trace.path)


@pytest.mark.parametrize("change", [
    lambda value: value.update(frame=1),
    lambda value: value.update(time_s=1),
    lambda value: value["agent_result"].update(frame=5),
    lambda value: value["agent_result"].update(protocol_version="1.1"),
    lambda value: value["proposed_control"].update(throttle=0.1),
    lambda value: value["applied_control"].update(throttle=2),
    lambda value: value.update(resulting_frame=5),
    lambda value: value.update(resulting_frame=True),
    lambda value: value["resulting_ego"].update(actor_id="wrong-ego"),
    lambda value: value.update(interventions="hidden"),
    lambda value: value.update(worker_step_wall_time_s=-0.1),
    lambda value: value.update(round_trip_wall_time_s=True),
    lambda value: value.update(full_step_wall_time_s=math.nan),
])
def test_trace_rejects_mismatched_actions_and_invalid_measurements(tmp_path, change):
    item = decision()
    change(item)
    with DecisionTrace(tmp_path / "trace.jsonl") as trace:
        with pytest.raises((ValueError, TypeError)):
            trace(item)
        assert trace.records == []
        assert trace.path.read_text() == ""


def test_trace_rejects_skipped_native_frames_but_accepts_arbitrary_first_frame(tmp_path):
    with DecisionTrace(tmp_path / "trace.jsonl") as trace:
        trace(decision(100))
        with pytest.raises(ValueError, match="consecutive"):
            trace(decision(102))


def test_fingerprint_hashes_actual_assets_and_preprocessing_without_import(tmp_path):
    package = tmp_path / "agent"
    package.mkdir()
    (package / "agent.json").write_text(json.dumps({"agent_id": "fingerprint", "name": "Fingerprint", "version": "1", "entry_point": "agent:Agent", "assets": ["weights.bin"], "requirements_file": "requirements.txt"}))
    (package / "agent.py").write_text("raise RuntimeError('must never import')\n")
    (package / "preprocessing.py").write_text("gain = 1\n")
    (package / "weights.bin").write_bytes(b"real checkpoint fixture")
    (package / "requirements.txt").write_text("example==1\n")
    inspected = inspect_agent_package(package)
    first = package_fingerprint(inspected)
    assert set(first["files"]) == {"agent.json", "agent.py", "preprocessing.py", "weights.bin", "requirements.txt"}
    (package / "weights.bin").write_bytes(b"different checkpoint fixture")
    second = package_fingerprint(inspected)
    assert first["files"]["weights.bin"]["sha256"] != second["files"]["weights.bin"]["sha256"]
    assert first["files"]["preprocessing.py"] == second["files"]["preprocessing.py"]


def test_motion_reports_stop_without_claiming_it_is_a_driving_failure():
    frames = [frame(index, 0.0, speed=0.0, time=index*1.0) for index in range(5)]
    motion = motion_summary(frames)
    assert motion["distance_travelled_m"] == 0
    assert motion["longest_observed_stop_s"] == 4
    assert motion["possible_stuck"] is True
    assert "appropriate hazard response" in motion["stuck_interpretation"]
    frames[-1].ego.velocity_mps = None
    assert motion_summary(frames)["possible_stuck"] is None


def test_route_projection_is_only_available_for_a_supplied_route():
    frames = [frame(0, 1.0), frame(1, 4.0)]
    assert route_progress(frames, [decision()]) is None
    record = decision()
    record["observation"]["scene"] = {"route": [{"position": {"x": 0, "y": 0}}, {"position": {"x": 10, "y": 0}}]}
    progress = route_progress(frames, [record])
    assert progress["progress_m"] == 3
    assert progress["route_length_m"] == 10
    assert progress["final_cross_track_m"] == 0


def test_synthetic_success_does_not_claim_live_carla_or_missing_lane_measurements():
    summary = summarize_run(output(), [decision()], total_wall_time_s=0.4, variant="reference_ai")
    assert summary["control_evidence"]["complete_trace"]
    assert summary["control_evidence"]["proposed_controls_reached_adapter_unchanged"]
    assert not summary["control_evidence"]["live_carla_execution"]
    assert summary["lane_departures"] is None
    assert summary["route_progress"] is None
    assert summary["timing"]["real_time_factor"] == 1
    assert summary["timing"]["setup_wall_time_s"] == 0.2
    assert summary["control_evidence"]["reported_checkpoint_sha256"] == ["abc"]
    assert summary["timing"]["inference"]["samples"] == 0
    assert summary["timing"]["agent_step"]["p95_s"] == 0.001


def test_neural_forward_pass_and_complete_agent_step_are_distinct_measurements():
    item = decision()
    item["agent_result"]["diagnostics"]["metadata"]["inference_wall_time_s"] = 0.0005
    summary = summarize_run(output(), [item], total_wall_time_s=0.4, variant="reference_ai")
    assert summary["timing"]["inference"]["p95_s"] == 0.0005
    assert summary["timing"]["agent_step"]["p95_s"] == 0.001
    assert summary["timing"]["round_trip"]["p95_s"] == 0.002


def test_missing_complete_step_measurement_cannot_pass_a_throughput_gate():
    item = decision()
    item["full_step_wall_time_s"] = None
    summary = summarize_run(output(), [item], total_wall_time_s=0.4, variant="reference_ai")
    assert not summary["timing"]["single_trial_throughput_gate"]


@pytest.mark.parametrize("mode,status,execution,full_step,expected", [
    ("carla", "completed", 0.05, 0.01, True),
    ("carla", "completed", 0.1, 0.01, False),
    ("carla", "completed", 0.05, 0.06, False),
    ("carla", "failed", 0.05, 0.01, False),
    ("test", "completed", None, 0.01, False),
])
def test_throughput_requires_rtf_step_budgets_and_complete_execution(mode, status, execution, full_step, expected):
    item = decision()
    item["full_step_wall_time_s"] = full_step
    summary = summarize_run(output(mode=mode, status=status, execution=execution), [item], total_wall_time_s=0.4, variant="reference_ai")
    assert summary["timing"]["single_trial_throughput_gate"] is expected
    assert summary["control_evidence"]["live_carla_execution"] is (mode == "carla" and status == "completed")


def test_incomplete_trace_never_passes_performance_gate():
    summary = summarize_run(output(), [], total_wall_time_s=0.4, variant="reference_ai")
    assert not summary["control_evidence"]["complete_trace"]
    assert summary["control_evidence"]["proposed_controls_reached_adapter_unchanged"] is None
    assert not summary["timing"]["single_trial_throughput_gate"]


def test_control_ablation_comparison_records_motion_and_action_change():
    pilot = output()
    ablated = output()
    ablated.telemetry[1].control = ControlInput(brake=1)
    ablated.telemetry[1].ego.transform.position.x = 0.01
    comparison = compare_runs(pilot, ablated)
    assert comparison["same_recipe"]
    assert comparison["changed_applied_control_frames"] == 1
    assert comparison["motion_changed_over_1cm"]
