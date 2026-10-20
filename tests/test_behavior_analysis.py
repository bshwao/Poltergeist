"""Measured behavior, control ownership, timing, unknowns and evidence references."""

from copy import deepcopy
from datetime import UTC, datetime
import json
import math

from pydantic import ValidationError
import pytest

from evaluation import evaluate_safety
from evaluation.behavior_analysis import DecisionEvidence, analyze_behavior
from evaluation.validation import EvaluationError
from scenarios import validate_scenario
from shared.models import (
    ActorState, AgentObservation, AgentStepResult, ControlAudit, ControlInput, ControlIntervention,
    DrivingOutcome, EvaluationSettings, Simulation, SimulationResult, TelemetryFrame, Transform, Vector3,
)
from shared.models.behavior import BehaviorSettings
from shared.models.decision import DecisionRecord


def pose(x=0.0, y=0.0, z=0.0, yaw=0.0):
    return Transform.model_validate({"position": {"x": x, "y": y, "z": z}, "rotation": {"yaw_deg": yaw}})


def frames(count=8, *, native_start=100):
    return [TelemetryFrame(frame=native_start + index, time_s=index * .05,
        ego=ActorState(actor_id="ego", transform=pose(index * .1), velocity_mps=Vector3(x=2),
            acceleration_mps2=Vector3(), bounding_box_extent={"x": 1, "y": 1, "z": 1}),
        actors=[], collisions=[], traffic_violations=[]) for index in range(count)]


def result(samples, *, event_at=.05, task=None, outcome=None, audit=None, custom=False, status="completed", metrics=None):
    recipe = {"name": "behavior measurements", "duration_s": max(.5, (samples[-1].time_s if samples else 0) + .05),
        "actors": [{"id": "ego", "type": "ego_vehicle", "spawn": {}},
                   {"id": "npc", "type": "vehicle", "spawn": {"position": {"x": 80}}}],
        "events": [] if event_at is None else [{"actor_id": "npc", "action": "brake", "trigger_time_s": event_at, "deceleration_mps2": 4}],
        **({} if task is None else {"driving_task": task})}
    now = datetime(2026, 10, 19, tzinfo=UTC)
    simulation = Simulation(scenario=validate_scenario(recipe), agent="custom" if custom else "baseline", mode="test",
        status=status, progress=1 if status == "completed" else 0, created_at=now, started_at=now,
        finished_at=now, error="fixture failed" if status == "failed" else None)
    return SimulationResult(simulation=simulation, metrics=evaluate_safety(samples) if metrics is None else metrics,
        telemetry_frames=len(samples), measured_duration_s=0 if not samples else samples[-1].time_s - samples[0].time_s,
        driving_outcome=outcome, control_audit=audit)


def decisions(samples, proposals=None, *, custom=True, interventions=None, unresolved=False):
    collector = DecisionEvidence()
    for index, before in enumerate(samples[:-1]):
        after = samples[index + 1]
        proposed = after.control if proposals is None else proposals[index]
        observation = AgentObservation(protocol_version="1.3", frame=before.frame, time_s=before.time_s,
            dt_s=.05, ego=before.ego, actors=before.actors)
        collector(DecisionRecord(frame=before.frame, time_s=before.time_s, status="applied",
            observation=observation.model_dump(mode="json"), proposed_control=proposed, applied_control=after.control,
            agent_result=AgentStepResult(protocol_version="1.3", frame=before.frame, control=proposed) if custom else None,
            interventions=[] if interventions is None else interventions.get(before.frame, []),
            resulting_frame=after.frame, resulting_ego=after.ego, full_step_wall_time_s=.001,
            control_mode="scripted" if interventions else "authoritative" if custom else "scripted"))
    if unresolved and samples:
        before = samples[-1]
        observation = AgentObservation(protocol_version="1.3", frame=before.frame, time_s=before.time_s, dt_s=.05,
            ego=before.ego, actors=before.actors)
        collector(DecisionRecord(frame=before.frame, time_s=before.time_s, status="unresolved",
            observation=observation.model_dump(mode="json"), control_mode="authoritative"))
    return collector


def response(analysis, channel, event=0):
    return next(item for item in analysis.events[event].responses if item.channel == channel)


def test_response_uses_preceding_input_clock_and_preserves_native_frame_ids():
    samples = frames()
    samples[4].control = ControlInput(brake=.8)
    analysis = analyze_behavior(result(samples, custom=True), samples, decisions=decisions(samples),
                                settings={"response_window_s": .15})
    for channel in ("proposed_braking", "applied_braking"):
        measured = response(analysis, channel)
        assert measured.status == "observed"
        assert measured.latency_s == pytest.approx(.1)
        assert measured.response_frame == 103  # packet 104 measures that action
        assert measured.response_time_s == pytest.approx(.15)
        assert measured.attribution == "sdk_agent"
        assert measured.window_complete
        assert any(span.source == "decisions" and span.last_frame == 103 for span in measured.evidence)
        assert any(span.source == "telemetry" and span.last_frame == 104 for span in measured.evidence)


def test_zero_latency_and_preexisting_braking_are_distinct():
    samples = frames()
    samples[2].control = ControlInput(brake=.6)
    analysis = analyze_behavior(result(samples), samples, decisions=decisions(samples))
    assert response(analysis, "proposed_braking").latency_s == 0
    assert response(analysis, "proposed_braking").evidence[0].first_frame == 100
    samples[1].control = ControlInput(brake=.6)
    analysis = analyze_behavior(result(samples), samples, decisions=decisions(samples))
    observed = response(analysis, "proposed_braking")
    assert observed.status == "already_active"
    assert observed.latency_s is None
    assert observed.response_frame == 101


def test_initial_braking_has_no_invented_pre_anchor_latency():
    samples = frames()
    samples[1].control = ControlInput(hand_brake=True)
    analysis = analyze_behavior(result(samples, event_at=0), samples, decisions=decisions(samples))
    observed = response(analysis, "applied_braking")
    assert observed.status == "already_active" and observed.latency_s is None
    assert observed.value == 1


def test_steering_change_uses_signed_preceding_control_and_heading_wraps():
    samples = frames()
    samples[1].control = ControlInput(steer=-.3)
    samples[2].control = ControlInput(steer=-.05)
    samples[1].ego.transform = pose(yaw=359)
    samples[2].ego.transform = pose(yaw=1)
    samples[3].ego.transform = pose(yaw=5)
    analysis = analyze_behavior(result(samples, custom=True), samples, decisions=decisions(samples))
    steer = response(analysis, "proposed_steering_change")
    assert steer.status == "observed" and steer.latency_s == 0
    assert steer.reference_value == -.3
    assert steer.value == pytest.approx(.25)
    heading = response(analysis, "measured_heading_change")
    assert heading.value == pytest.approx(6)
    assert heading.latency_s == pytest.approx(.1)
    assert heading.attribution == "measured_motion"


def test_initial_steering_reference_is_observed_control_not_assumed_zero():
    samples = frames()
    for item in samples[1:]:
        item.control = ControlInput(steer=.9)
    analysis = analyze_behavior(result(samples, event_at=0), samples, decisions=decisions(samples))
    observed = response(analysis, "proposed_steering_change")
    assert observed.status == "not_observed"
    assert observed.reference_value == .9
    assert observed.latency_s is None


def test_physical_deceleration_can_use_velocity_intervals_without_claiming_agent_intent():
    samples = frames()
    for sample in samples:
        sample.ego.acceleration_mps2 = None
    samples[3].ego.velocity_mps = Vector3(x=1)
    analysis = analyze_behavior(result(samples), samples)
    physical = response(analysis, "measured_deceleration")
    assert physical.status == "observed" and physical.latency_s == pytest.approx(.1)
    assert physical.value == pytest.approx(20)
    assert physical.attribution == "measured_motion"
    assert response(analysis, "proposed_braking").status == "unavailable"


def test_script_overrides_and_speed_guidance_are_never_credited_as_sdk_responses():
    samples = frames()
    samples[3].control = ControlInput(brake=.7)
    proposals = [sample.control for sample in samples[1:]]
    proposals[2] = ControlInput()
    intervention = ControlIntervention(frame=102, time_s=.1, kind="scripted_ego_braking",
        proposed_control=proposals[2], applied_control=samples[3].control)
    audit = ControlAudit(mode="scripted", controller="sdk_agent", navigation="legacy_assisted", interventions=[intervention])
    evidence = decisions(samples, proposals, interventions={102: ["scripted_ego_braking"]})
    analysis = analyze_behavior(result(samples, custom=True, audit=audit), samples, decisions=evidence)
    applied = response(analysis, "applied_braking")
    assert applied.status == "observed" and applied.attribution == "script_influenced"
    assert analysis.coverage.changed_control_frames == 1
    assert response(analysis, "proposed_braking").status == "not_observed"
    # An unchanged proposal can still be influenced by a scripted speed target.
    proposals[2] = samples[3].control
    intervention.kind = "scripted_ego_speed_target"
    intervention.proposed_control = proposals[2]
    evidence = decisions(samples, proposals, interventions={102: ["scripted_ego_speed_target"]})
    analysis = analyze_behavior(result(samples, custom=True, audit=audit), samples, decisions=evidence)
    assert response(analysis, "proposed_braking").attribution == "script_influenced"


def test_no_response_in_complete_and_truncated_windows_is_explicit():
    samples = frames()
    short = analyze_behavior(result(samples), samples, settings={"response_window_s": .1})
    observed = response(short, "applied_braking")
    assert observed.status == "not_observed" and observed.window_complete
    assert observed.latency_s is None
    long = analyze_behavior(result(samples, status="cancelled"), samples)
    observed = response(long, "applied_braking")
    assert observed.status == "not_observed" and not observed.window_complete
    assert observed.reason == "observation_window_truncated"
    assert observed.observed_until_s == pytest.approx(.3)


@pytest.mark.parametrize("missing", ["frame_gap", "time_gap", "missing_velocity", "missing_heading"])
def test_missing_data_before_response_prevents_false_response_latency(missing):
    samples = frames()
    samples[3].ego.transform = pose(yaw=90)
    samples[3].ego.velocity_mps = Vector3(x=0)
    channel = "measured_heading_change" if missing != "missing_velocity" else "measured_deceleration"
    if missing == "frame_gap":
        samples.pop(2)
    elif missing == "time_gap":
        for sample in samples[2:]:
            sample.time_s += 1
    elif missing == "missing_heading":
        samples[2].ego.transform = None
    else:
        samples[2].ego.velocity_mps = None
    analysis = analyze_behavior(result(samples, status="failed"), samples)
    observed = response(analysis, channel)
    assert observed.status == "unavailable" and observed.latency_s is None
    if "gap" in missing:
        assert analysis.coverage.telemetry_gaps > 0


def test_later_missing_data_does_not_erase_an_established_response():
    samples = frames()
    samples[2].control = ControlInput(brake=.5)
    evidence = decisions(samples)
    # A subsequent missing velocity affects motion coverage, not known control.
    samples[-1].ego.velocity_mps = None
    analysis = analyze_behavior(result(samples), samples, decisions=evidence)
    assert response(analysis, "proposed_braking").latency_s == 0
    assert not response(analysis, "proposed_braking").window_complete


def test_unresolved_sdk_input_is_not_misidentified_as_a_legacy_agent():
    samples = frames(4)
    evidence = decisions(samples[:3], unresolved=True)
    samples[3].control = ControlInput(brake=.5)
    analysis = analyze_behavior(result(samples, custom=True, status="failed"), samples, decisions=evidence)
    assert analysis.coverage.unresolved_inputs == 1
    assert analysis.coverage.decision_coverage == "partial"
    assert response(analysis, "proposed_braking").status == "unavailable"
    assert response(analysis, "applied_braking").attribution == "unknown"


def npc(x, *, velocity=True, geometry=True):
    return ActorState(actor_id="npc", transform=pose(x), velocity_mps=Vector3() if velocity else None,
        bounding_box_extent={"x": 1, "y": 1, "z": 1} if geometry else None)


def test_hazard_crossings_missing_feeds_and_ground_truth_source_are_distinct():
    samples = frames(6)
    for sample in samples:
        sample.ego.transform = pose()
    samples[0].actors = [npc(20)]
    samples[1].actors = [npc(8)]
    samples[2].actors = [npc(7)]
    samples[3].actors = None
    samples[4].actors = [npc(6)]
    samples[5].actors = [npc(5)]
    analysis = analyze_behavior(result(samples, event_at=None), samples)
    assert len(analysis.events) == 2
    first, second = [item.anchor for item in analysis.events]
    assert (first.frame, first.onset, first.source) == (101, "threshold_crossing", "evaluator_ground_truth")
    assert first.ttc_s == pytest.approx(3)
    assert (second.frame, second.onset) == (104, "first_observed")
    assert analysis.coverage.hazard_ttc_pairs == 5


def test_missing_geometry_or_velocity_is_not_a_measured_safe_hazard_reset():
    samples = frames(4)
    for sample in samples:
        sample.ego.transform = pose()
    samples[0].actors = [npc(20, velocity=False)]
    samples[1].actors = [npc(5)]
    samples[2].actors = [npc(20, geometry=False)]
    samples[3].actors = [npc(5)]
    analysis = analyze_behavior(result(samples, event_at=None), samples)
    assert [item.anchor.onset for item in analysis.events] == ["first_observed", "first_observed"]


def test_event_between_ticks_aligns_to_recorded_input_and_unreached_events_stay_unavailable():
    samples = frames()
    aligned = analyze_behavior(result(samples, event_at=.025), samples)
    assert aligned.events[0].anchor.scheduled_time_s == .025
    assert aligned.events[0].anchor.time_s == .05
    assert aligned.events[0].anchor.frame == 101
    unreached = analyze_behavior(result(samples, event_at=.45, status="failed"), samples)
    assert unreached.events[0].anchor.frame is None
    assert all(item.status == "unavailable" for item in unreached.events[0].responses)


def test_event_inside_missing_time_is_not_aligned_to_a_later_unrelated_frame():
    samples = frames()
    samples.pop(1)
    analysis = analyze_behavior(result(samples, event_at=.05, status="failed"), samples)
    assert analysis.events[0].anchor.frame is None


def task_and_outcome(*, owner="framework", route=None, progress=2.0, length=10.0):
    route = [pose(), pose(10)] if route is None else route
    task = {"destination": route[-1], "route_owner": owner, "target_speed_mps": 3}
    outcome = DrivingOutcome(status="timed_out", route_owner=owner, control_mode="authoritative",
        destination=route[-1], resolved_route=route if owner == "framework" else [],
        route_progress_m=progress if owner == "framework" else None, route_length_m=length if owner == "framework" else None,
        termination_reason="duration_elapsed")
    return task, outcome


def test_route_distance_is_to_finite_height_compatible_polyline_and_progress_is_recorded():
    samples = frames(3)
    samples[0].ego.transform = pose()
    samples[1].ego.transform = pose(5, 2)
    samples[2].ego.transform = pose(20, 0)
    task, outcome = task_and_outcome(progress=2.0)
    analysis = analyze_behavior(result(samples, task=task, outcome=outcome), samples)
    assert analysis.route.max_deviation.value == 10
    assert analysis.route.mean_deviation.value == pytest.approx(4)
    assert analysis.route.max_deviation.evidence[0].first_frame == 102
    assert analysis.route.recorded_progress.value == 2  # no nearest-segment shortcut
    assert analysis.route.recorded_completion_fraction.value == .2
    assert analysis.route.reference_source == "recorded_resolved_route"


def test_route_height_mismatch_and_missing_pose_do_not_become_zero_deviation():
    samples = frames(3)
    samples[0].ego.transform = pose(5, 0, 8)
    samples[1].ego.transform = None
    samples[2].ego.transform = pose(5, 3)
    task, outcome = task_and_outcome()
    analysis = analyze_behavior(result(samples, task=task, outcome=outcome), samples)
    assert analysis.route.mean_deviation.value == 3
    assert analysis.route.mean_deviation.observed_samples == 1
    assert analysis.route.mean_deviation.coverage == "partial"
    samples[2].ego.transform = pose(5, 0, 8)
    analysis = analyze_behavior(result(samples, task=task, outcome=outcome), samples)
    assert analysis.route.max_deviation.value is None
    assert analysis.route.max_deviation.reason == "no_height_compatible_route_poses"


def test_agent_route_and_unresolved_scenario_route_are_not_invented_reference_routes():
    samples = frames()
    task, outcome = task_and_outcome(owner="agent")
    analysis = analyze_behavior(result(samples, task=task, outcome=outcome), samples)
    assert analysis.route.max_deviation.value is None
    assert analysis.route.recorded_progress.value is None
    task, _ = task_and_outcome()
    task["route"] = [pose(), pose(10)]
    analysis = analyze_behavior(result(samples, task=task), samples)
    assert analysis.route.reference_source is None
    assert analysis.route.mean_deviation.reason == "resolved_framework_route_not_recorded"


def test_zero_length_route_fraction_is_unavailable_while_zero_progress_is_preserved():
    samples = frames()
    task, outcome = task_and_outcome(route=[pose()], progress=0.0, length=0.0)
    analysis = analyze_behavior(result(samples, task=task, outcome=outcome), samples)
    assert analysis.route.recorded_progress.value == 0
    assert analysis.route.recorded_length.value == 0
    assert analysis.route.recorded_completion_fraction.value is None
    assert analysis.route.recorded_completion_fraction.reason == "zero_length_route"


def test_stopping_episodes_reuse_integrated_braking_measurements_and_script_evidence():
    samples = frames(3)
    for index, speed in enumerate((2.0, 1.0, 0.0)):
        samples[index].ego.velocity_mps = Vector3(x=speed)
        samples[index].control = ControlInput(brake=.5)
    intervention = ControlIntervention(frame=101, time_s=.05, kind="scripted_ego_braking",
        proposed_control=ControlInput(), applied_control=samples[2].control)
    audit = ControlAudit(mode="scripted", controller="baseline", navigation="legacy_assisted", interventions=[intervention])
    analysis = analyze_behavior(result(samples, audit=audit), samples)
    episode = analysis.braking.stopping_episodes[0]
    assert episode.duration_s == .1
    assert episode.distance_m == pytest.approx(.1)
    assert episode.evidence.first_frame == 100 and episode.evidence.last_frame == 102
    assert episode.script_influenced and episode.intervention_frames == 1
    assert analysis.braking.total_stopping_episodes == 1


def test_saved_safety_scores_and_thresholds_are_unchanged_by_custom_analysis_settings():
    samples = frames()
    original = result(samples)
    original.metrics.severity = 42.0
    original.metrics.evaluation_settings.hard_braking_threshold_mps2 = 5.0
    snapshot = original.model_dump(mode="json")
    analysis = analyze_behavior(original, samples, settings={"max_sample_gap_s": .01, "brake_threshold": .9})
    assert analysis.stored_safety_metrics.severity == 42
    assert analysis.stored_safety_metrics == original.metrics
    assert analysis.braking.settings.hard_braking_threshold_mps2 == 5
    assert analysis.braking.settings.max_sample_gap_s == .01
    assert original.model_dump(mode="json") == snapshot


def test_empty_failed_and_legacy_inputs_preserve_unknowns_and_pending_events():
    empty = result([], status="failed")
    empty.metrics.evaluation_settings = None
    analysis = analyze_behavior(empty, [])
    assert analysis.coverage.telemetry_frames == 0
    assert analysis.coverage.decision_coverage == "unavailable"
    assert analysis.braking.max_deceleration.value is None
    assert analysis.braking.hard_braking_count.value is None
    assert analysis.braking.settings_source == "legacy_defaults"
    assert analysis.events[0].anchor.frame is None
    assert analysis.safety_evidence == []


def test_output_is_deterministic_serializable_and_never_retains_observation_payloads():
    samples = frames()
    saved = result(samples, custom=True)
    evidence = decisions(samples)
    assert not hasattr(next(iter(evidence.actions.values())), "observation")
    before = deepcopy(samples)
    first = analyze_behavior(saved, samples, decisions=evidence)
    second = analyze_behavior(saved, samples, decisions=evidence)
    assert first.model_dump_json() == second.model_dump_json()
    assert "NaN" not in first.model_dump_json() and "Infinity" not in first.model_dump_json()
    assert json.loads(first.model_dump_json())["schema_version"] == "1.0"
    assert samples == before


def test_event_and_stop_output_caps_preserve_total_counts_and_deterministic_order():
    samples = frames(6)
    for index, sample in enumerate(samples):
        sample.ego.velocity_mps = Vector3(x=2 if index % 2 == 0 else 0)
        sample.control = ControlInput(brake=.6)
        sample.actors = [npc(1)] if index % 2 == 0 else []
    saved = result(samples, event_at=.2)
    analysis = analyze_behavior(saved, samples, settings={"max_response_events": 1, "max_stopping_episodes": 1})
    assert analysis.coverage.total_response_events == 4
    assert analysis.coverage.response_events_truncated
    assert len(analysis.events) == 1 and analysis.events[0].anchor.frame == 100
    assert analysis.braking.total_stopping_episodes == 3
    assert len(analysis.braking.stopping_episodes) == 1 and analysis.braking.episodes_truncated


@pytest.mark.parametrize("setting,value", [
    ("response_window_s", 0), ("response_window_s", 61), ("brake_threshold", True), ("brake_threshold", 1.1),
    ("steering_change_threshold", 0), ("deceleration_threshold_mps2", -1), ("heading_change_deg", 181),
    ("hazard_ttc_s", float("nan")), ("hazard_clearance_m", float("inf")), ("max_sample_gap_s", 0),
    ("route_height_tolerance_m", 0), ("max_response_events", 2001), ("max_stopping_episodes", True),
    ("method_version", "future"), ("unexpected", 1),
])
def test_settings_reject_invalid_and_nonfinite_values(setting, value):
    with pytest.raises(ValidationError):
        BehaviorSettings.model_validate({setting: value})


def test_analysis_rejects_mismatched_result_counts_and_decision_controls():
    samples = frames()
    saved = result(samples)
    with pytest.raises(EvaluationError):
        analyze_behavior(saved, samples[:-1])
    evidence = decisions(samples)
    samples[2].control = ControlInput(brake=1)
    with pytest.raises(EvaluationError, match="control"):
        analyze_behavior(saved, samples, decisions=evidence)


def test_ego_script_anchor_and_scripted_steering_reference_are_identified():
    samples = frames()
    samples[1].control = ControlInput(steer=.4)
    samples[2].control = ControlInput(steer=0)
    influence = ControlIntervention(frame=100, time_s=0, kind="scripted_ego_speed_target",
        proposed_control=samples[1].control, applied_control=samples[1].control, target_speed_mps=4)
    audit = ControlAudit(mode="scripted", controller="sdk_agent", navigation="legacy_assisted", interventions=[influence])
    evidence = decisions(samples, interventions={100: ["scripted_ego_speed_target"]})
    saved = result(samples, custom=True, audit=audit)
    analysis = analyze_behavior(saved, samples, decisions=evidence)
    measured = response(analysis, "proposed_steering_change")
    assert measured.status == "observed" and measured.attribution == "script_influenced"
    saved.simulation.scenario.events[0].actor_id = "ego"
    analysis = analyze_behavior(saved, samples, decisions=evidence)
    assert analysis.events[0].anchor.ego_event
    assert response(analysis, "applied_steering_change").attribution == "script_influenced"


def test_goal_outcome_and_reached_frame_are_retained_separately_from_execution_status():
    samples = frames(3)
    task, outcome = task_and_outcome(progress=10.0)
    outcome.status = "reached"
    outcome.reached_at_s = .05
    outcome.termination_reason = "goal_reached"
    saved = result(samples, task=task, outcome=outcome)
    analysis = analyze_behavior(saved, samples)
    assert analysis.driving_outcome == outcome
    assert analysis.execution_status == "completed"
    assert any(span.first_frame == span.last_frame == 101 for span in analysis.task_evidence)


def test_recorded_braking_gap_policy_cannot_be_loosened_by_analysis():
    samples = frames()
    saved = result(samples)
    saved.metrics.evaluation_settings.max_sample_gap_s = .01
    analysis = analyze_behavior(saved, samples, settings={"max_sample_gap_s": 1.0})
    assert analysis.braking.settings.max_sample_gap_s == .01
    assert analysis.braking.max_deceleration.coverage == "partial"
