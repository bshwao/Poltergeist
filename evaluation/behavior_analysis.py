"""Read-only behavior measurements from finalized episode evidence.

No clock, simulator, model, network or random state is consulted. Intervals are
relative to declared/observed anchors, never assertions of perception or intent.
"""

from bisect import bisect_left
from dataclasses import dataclass
from heapq import heappush, heapreplace
import math

from shared.models import ControlInput, EvaluationSettings, SimulationResult
from shared.models.behavior import (
    BehaviorAnalysis, BehaviorAnchor, BehaviorCoverage, BehaviorMeasure, BehaviorResponse,
    BehaviorSettings, BrakingBehavior, EventBehavior, EvidenceSpan, RouteBehavior, StoppingEpisode,
)
from .braking_metrics import summarize_braking
from .distance_metrics import bounding_box_clearance
from .route_metrics import PolylineReference
from .ttc_calculator import can_compute_ttc, time_to_collision
from .validation import EvaluationError, validate_frames


@dataclass(frozen=True, slots=True)
class _Action:
    frame: int
    time_s: float
    status: str
    proposed: ControlInput | None
    applied: ControlInput | None
    sdk_response: bool
    mode: str
    interventions: tuple[str, ...]


class DecisionEvidence:
    """Streaming consumer retaining controls only, never scene/camera payloads."""

    def __init__(self, *, available=True):
        self.available = available
        self.actions = {}

    def __call__(self, record):
        if record.frame in self.actions:
            raise EvaluationError("Duplicate decision input frame")
        self.actions[record.frame] = _Action(
            record.frame, record.time_s, record.status,
            None if record.proposed_control is None else record.proposed_control.model_copy(deep=True),
            None if record.applied_control is None else record.applied_control.model_copy(deep=True),
            record.agent_result is not None, record.control_mode, tuple(record.interventions),
        )


def _finite(value):
    if not math.isfinite(value):
        raise EvaluationError("Behavior arithmetic exceeded the supported finite range")
    return value


def _continuous(first, second, settings):
    return second.frame == first.frame + 1 and second.time_s - first.time_s <= settings.max_sample_gap_s + 1e-9


def _span(frames, first=0, last=None, source="telemetry"):
    if not frames:
        return []
    last = len(frames) - 1 if last is None else last
    a, b = frames[first], frames[last]
    return [EvidenceSpan(source=source, first_frame=a.frame, last_frame=b.frame,
        first_time_s=a.time_s, last_time_s=b.time_s)]


def _measure(value, unit, observed, eligible, evidence, *, gaps=False, reason=None):
    coverage = "unavailable" if value is None else "partial" if observed < eligible or gaps else "complete"
    return BehaviorMeasure(value=value, unit=unit, observed_samples=observed, eligible_samples=eligible,
        coverage=coverage, reason=reason, evidence=evidence)


def _speed(frame):
    v = frame.ego.velocity_mps
    return None if v is None else _finite(math.hypot(v.x, v.y, v.z))


def _deceleration(frames, index, settings, stopped_speed):
    frame = frames[index]
    speed = _speed(frame)
    if speed is None:
        return None
    acceleration = frame.ego.acceleration_mps2
    if acceleration is not None:
        if speed <= stopped_speed:
            return 0.0
        velocity = frame.ego.velocity_mps
        return _finite(max(0.0, -math.fsum(getattr(acceleration, axis) * (getattr(velocity, axis) / speed)
                                        for axis in ("x", "y", "z"))))
    if index and _continuous(frames[index - 1], frame, settings):
        previous = _speed(frames[index - 1])
        if previous is not None:
            return _finite(max(0.0, (previous - speed) / (frame.time_s - frames[index - 1].time_s)))
    return None


def _braking(result, frames, actions, settings, interventions, gaps):
    recorded = result.metrics.evaluation_settings
    base = recorded or EvaluationSettings()
    # Analysis can tighten continuity, never bridge gaps rejected by saved scoring.
    effective = EvaluationSettings.model_validate(base.model_dump() | {
        "max_sample_gap_s": min(base.max_sample_gap_s, settings.max_sample_gap_s),
    })
    summary = summarize_braking(frames, hard_braking_threshold_mps2=effective.hard_braking_threshold_mps2,
        stopped_speed_mps=effective.stopped_speed_mps, max_sample_gap_s=effective.max_sample_gap_s)
    gaps = gaps or any(not _continuous(first, second, effective) for first, second in zip(frames, frames[1:]))
    episodes = []
    times = [frame.time_s for frame in frames]
    for episode in summary.stopping_episodes[:settings.max_stopping_episodes]:
        start = bisect_left(times, episode.start_time_s)
        end = bisect_left(times, episode.end_time_s)
        # Control at a measured frame was applied from the preceding input.
        ids = {frame.frame for frame in frames[max(0, start - 1):end]}
        influenced = len(ids & interventions)
        known_audit = result.control_audit is not None
        decisions_known = all(frame_id in actions and actions[frame_id].status == "applied" for frame_id in ids)
        episodes.append(StoppingEpisode(duration_s=episode.duration_s, distance_m=episode.distance_m,
            evidence=_span(frames, start, end)[0], intervention_frames=influenced,
            script_influenced=True if influenced else False if known_audit or decisions_known else None))
    observed = summary.observed_frames
    reason = "no_observed_deceleration" if not observed else None
    evidence = _span(frames)
    return BrakingBehavior(settings=effective,
        settings_source="recorded_evaluation" if recorded is not None else "legacy_defaults",
        max_deceleration=_measure(summary.max_deceleration_mps2, "m/s²", observed, len(frames), evidence,
                                  gaps=gaps, reason=reason),
        hard_braking_count=_measure(summary.hard_braking_count, "episodes", observed, len(frames), evidence,
                                   gaps=gaps, reason=reason),
        stopping_episodes=episodes, total_stopping_episodes=len(summary.stopping_episodes),
        episodes_truncated=len(episodes) < len(summary.stopping_episodes))


def _route(result, frames, settings, gaps):
    outcome = result.driving_outcome
    task = result.simulation.scenario.driving_task
    owner = outcome.route_owner if outcome is not None else None if task is None else task.route_owner
    route = [] if outcome is None or owner != "framework" else outcome.resolved_route
    reason = ("no_driving_task" if task is None else "agent_owned_route_not_recorded" if owner == "agent"
              else "resolved_framework_route_not_recorded" if not route else None)
    distances = []
    if route:
        reference = PolylineReference(route)
        for index, frame in enumerate(frames):
            if frame.ego.transform is not None:
                value = reference.distance(frame.ego.transform, height_tolerance_m=settings.route_height_tolerance_m)
                if value is not None:
                    distances.append((index, value))
    observed = len(distances)
    evidence = _span(frames)
    maximum = max(distances, key=lambda pair: pair[1]) if distances else None
    mean = _finite(math.fsum(value / observed for _, value in distances)) if distances else None
    unavailable = reason or ("no_height_compatible_route_poses" if not distances else None)
    progress = None if outcome is None or owner != "framework" else outcome.route_progress_m
    length = None if outcome is None or owner != "framework" else outcome.route_length_m
    if not frames:
        progress = length = None
    progress_reason = reason or ("task_monitor_progress_unavailable" if progress is None else None)
    outcome_evidence = _span(frames, source="driving_outcome")
    fraction = None if progress is None or length is None or length <= 1e-9 else min(1.0, progress / length)
    return RouteBehavior(route_owner=owner, reference_source="recorded_resolved_route" if route else None,
        max_deviation=_measure(None if maximum is None else maximum[1], "m", observed, len(frames),
            [] if maximum is None else _span(frames, maximum[0], maximum[0]), gaps=gaps, reason=unavailable),
        mean_deviation=_measure(mean, "m", observed, len(frames), evidence, gaps=gaps, reason=unavailable),
        recorded_progress=_measure(progress, "m", int(progress is not None), 1, outcome_evidence, reason=progress_reason),
        recorded_length=_measure(length, "m", int(length is not None), 1, outcome_evidence, reason=progress_reason),
        recorded_completion_fraction=_measure(fraction, "fraction", int(fraction is not None), 1, outcome_evidence,
            reason=progress_reason or ("zero_length_route" if fraction is None else None)))


def _anchors(result, frames, settings):
    """Keep earliest findings in a bounded heap while counting every anchor."""
    kept, total = [], 0
    clearance_pairs = ttc_pairs = 0

    def offer(anchor):
        nonlocal total
        item = (-anchor.time_s, -total, anchor)
        total += 1
        if len(kept) < settings.max_response_events:
            heappush(kept, item)
        elif item[:2] > kept[0][:2]:
            heapreplace(kept, item)

    times = [frame.time_s for frame in frames]
    ego_id = next(actor.id for actor in result.simulation.scenario.actors if actor.type == "ego_vehicle")
    for index, event in enumerate(result.simulation.scenario.events):
        at = bisect_left(times, event.trigger_time_s - 1e-9)
        observed = (at < len(frames) - 1 and frames[at].time_s - event.trigger_time_s < result.simulation.scenario.fixed_delta_seconds - 1e-9
                    and _continuous(frames[at], frames[at + 1], settings))
        offer(BehaviorAnchor(id=f"event:{index}", kind="scenario_event", source="scenario_script",
            actor_id=event.actor_id, time_s=frames[at].time_s if observed else event.trigger_time_s,
            frame=frames[at].frame if observed else None, event_index=index, event_action=event.action,
            scheduled_time_s=event.trigger_time_s, ego_event=event.actor_id == ego_id, onset="scheduled_command",
            reason=None if observed else "scheduled_event_not_observed"))

    previous_states = {}
    for index, frame in enumerate(frames):
        continuous = index > 0 and _continuous(frames[index - 1], frame, settings)
        prior = previous_states if continuous else {}
        states = {}
        for actor in sorted(frame.actors or [], key=lambda item: item.actor_id):
            clearance = bounding_box_clearance(frame.ego, actor)
            computable = can_compute_ttc(frame.ego, actor)
            ttc = time_to_collision(frame.ego, actor, horizon_s=settings.hazard_ttc_s) if computable else None
            clearance_pairs += clearance is not None
            ttc_pairs += computable
            hazard = ((clearance is not None and clearance <= settings.hazard_clearance_m + 1e-9)
                      or (ttc is not None and ttc <= settings.hazard_ttc_s + 1e-9))
            known = hazard or clearance is not None and computable
            state = hazard if known else None
            states[actor.actor_id] = state
            previous = prior.get(actor.actor_id)
            if hazard and previous is not True:
                offer(BehaviorAnchor(id=f"hazard:{frame.frame}:{actor.actor_id}", kind="measured_hazard",
                    source="evaluator_ground_truth", actor_id=actor.actor_id, time_s=frame.time_s,
                    frame=frame.frame, onset="threshold_crossing" if previous is False else "first_observed",
                    ttc_s=ttc, clearance_m=clearance))
        previous_states = states
    anchors = [item[2] for item in kept]
    anchors.sort(key=lambda item: (item.time_s, 0 if item.kind == "scenario_event" else 1,
                                   item.event_index if item.event_index is not None else item.actor_id))
    return anchors, total, clearance_pairs, ttc_pairs


_CHANNELS = ("proposed_braking", "applied_braking", "proposed_steering_change", "applied_steering_change",
             "measured_deceleration", "measured_heading_change")


def _signal(channel, index, frames, actions, settings, stopped_speed):
    if index < 0 or index >= len(frames):
        return None
    frame = frames[index]
    if channel == "measured_deceleration":
        return _deceleration(frames, index, settings, stopped_speed)
    if channel == "measured_heading_change":
        return None if frame.ego.transform is None else frame.ego.transform.rotation.yaw_deg % 360.0
    action = actions.get(frame.frame)
    if channel.startswith("proposed"):
        control = None if action is None or action.status != "applied" else action.proposed
    else:
        control = frames[index + 1].control if index + 1 < len(frames) and _continuous(frame, frames[index + 1], settings) else None
    if control is None:
        return None
    return (1.0 if control.hand_brake else control.brake) if channel.endswith("braking") else control.steer


def _response(channel, anchor, start, result, frames, actions, settings, interventions, stopped_speed):
    steering = "steering" in channel
    heading = channel == "measured_heading_change"
    motion = channel.startswith("measured")
    threshold = (settings.steering_change_threshold if steering else settings.heading_change_deg if heading
                 else settings.deceleration_threshold_mps2 if motion else settings.brake_threshold)
    unit = "degrees" if heading else "m/s²" if motion else "normalized_control"
    end_time = _finite(anchor.time_s + settings.response_window_s)
    common = dict(channel=channel, threshold=threshold, unit=unit, window_end_s=end_time)
    if start is None:
        return BehaviorResponse(**common, status="unavailable", latency_s=None, response_frame=None,
            response_time_s=None, value=None, attribution="measured_motion" if motion else "unknown",
            observed_until_s=None, window_complete=False, reason="anchor_unobserved")

    def signal(index):
        return _signal(channel, index, frames, actions, settings, stopped_speed)

    prior_known = start > 0 and _continuous(frames[start - 1], frames[start], settings)
    prior = signal(start - 1) if prior_known else None
    first = signal(start)
    reference = first if heading or prior is None else prior
    reference_index = start if heading or prior is None else start - 1
    hit = None
    last = None
    missing = None
    window_complete = False
    for index in range(start, len(frames)):
        frame = frames[index]
        if frame.time_s > end_time + 1e-9:
            break
        if not motion and index == len(frames) - 1 and index > start and frames[index].frame not in actions:
            # The terminal packet has no following applied action. It bounds
            # the observed prefix, rather than inventing a missing extra step.
            break
        if index > start and not _continuous(frames[index - 1], frame, settings):
            missing = "telemetry_gap"
            break
        raw = signal(index)
        if raw is None or reference is None:
            missing = "decision_input_unavailable" if channel.startswith("proposed") else "signal_unavailable"
            break
        value = abs((raw - reference + 180.0) % 360.0 - 180.0) if heading else abs(raw - reference) if steering else raw
        last = index
        window_complete = frame.time_s >= end_time - 1e-9
        if hit is None and value + 1e-9 >= threshold:
            active = index == start and not (heading or steering) and (prior is None or prior + 1e-9 >= threshold)
            hit = index, value, active
    target = hit[0] if hit is not None else last
    affected = anchor.ego_event or target is not None and any(
        frame.frame in interventions for frame in frames[reference_index if steering else start:target + 1])
    attribution = "measured_motion" if motion else "script_influenced" if affected else "unknown"
    if not motion and not affected and target is not None:
        action = actions.get(frames[target].frame)
        if action is not None and action.status == "applied" and action.sdk_response:
            attribution = "sdk_agent"
        elif action is not None and action.status == "applied":
            attribution = "baseline_controller" if result.simulation.agent == "baseline" else "legacy_custom"
        elif result.control_audit is not None and result.control_audit.controller == "baseline":
            attribution = "baseline_controller"
    evidence = []
    if target is not None:
        # References include the baseline sample and resulting measured packet
        # for controls; native frame IDs never double as pagination offsets.
        low = reference_index
        high = target if motion else min(target + 1, len(frames) - 1)
        evidence = _span(frames, low, high)
        if not motion and all(frames[index].frame in actions and actions[frames[index].frame].status == "applied"
                              for index in range(low, target + 1)):
            evidence += _span(frames, low, target, source="decisions")
    observed_until = None if last is None else frames[last].time_s
    shared = dict(**common, attribution=attribution, observed_until_s=observed_until, window_complete=window_complete,
        reference_frame=None if reference is None else frames[reference_index].frame,
        reference_value=reference, evidence=evidence)
    if hit is not None:
        frame = frames[hit[0]]
        return BehaviorResponse(**shared, status="already_active" if hit[2] else "observed",
            latency_s=None if hit[2] else max(0.0, frame.time_s - anchor.time_s),
            response_frame=frame.frame, response_time_s=frame.time_s, value=hit[1],
            reason="active_at_anchor_or_first_observation" if hit[2] else None)
    return BehaviorResponse(**shared, status="unavailable" if missing else "not_observed",
        latency_s=None, response_frame=None, response_time_s=None, value=None,
        reason=missing or (None if window_complete else "observation_window_truncated"))


def analyze_behavior(result, telemetry, *, decisions=None, settings=None):
    """Analyze one terminal result; identical inputs/settings yield identical JSON."""
    result = SimulationResult.model_validate(result.model_dump(mode="json"))
    settings = BehaviorSettings() if settings is None else BehaviorSettings.model_validate(
        settings.model_dump(mode="json") if isinstance(settings, BehaviorSettings) else settings)
    frames = validate_frames(telemetry)
    if len(frames) != result.telemetry_frames or (frames and not math.isclose(
            frames[-1].time_s - frames[0].time_s, result.measured_duration_s, abs_tol=1e-6)):
        raise EvaluationError("Behavior inputs do not match finalized result telemetry")
    actions = {} if decisions is None else decisions.actions
    available = decisions is not None and decisions.available
    if actions and not available:
        raise EvaluationError("Unavailable decision evidence cannot contain actions")
    indexed = {frame.frame: index for index, frame in enumerate(frames)}
    for position, action in enumerate(actions.values()):
        if action.frame not in indexed or indexed[action.frame] != position or not math.isclose(
                action.time_s, frames[position].time_s, abs_tol=1e-6):
            raise EvaluationError("Decision evidence must match the preceding telemetry prefix")
        if action.status == "applied" and (position + 1 >= len(frames) or action.applied != frames[position + 1].control):
            raise EvaluationError("Applied decision differs from measured control")
        if action.status != "applied" and position != len(actions) - 1:
            raise EvaluationError("Only the last input may be unresolved")
    gaps = sum(not _continuous(first, second, settings) for first, second in zip(frames, frames[1:]))
    applied = sum(action.status == "applied" for action in actions.values())
    unresolved = len(actions) - applied
    transitions = max(0, len(frames) - 1)
    decision_coverage = ("unavailable" if not available else "empty" if not actions and not transitions else
                         "complete" if applied == transitions and not unresolved else "partial")
    interventions = set()
    if result.control_audit is not None:
        interventions.update(item.frame for item in result.control_audit.interventions)
    interventions.update(action.frame for action in actions.values() if action.interventions)
    braking = _braking(result, frames, actions, settings, interventions, gaps)
    anchors, total, clearance_pairs, ttc_pairs = _anchors(result, frames, settings)
    events = [EventBehavior(anchor=anchor, responses=[_response(channel, anchor, indexed.get(anchor.frame), result,
        frames, actions, settings, interventions, braking.settings.stopped_speed_mps) for channel in _CHANNELS]) for anchor in anchors]
    task_evidence = []
    if result.driving_outcome is not None and frames:
        task_evidence = _span(frames, len(frames) - 1, source="driving_outcome")
        reached = result.driving_outcome.reached_at_s
        if reached is not None:
            matches = [index for index, frame in enumerate(frames) if math.isclose(frame.time_s, reached, abs_tol=1e-6)]
            if matches:
                task_evidence += _span(frames, matches[0], matches[0], source="driving_outcome")
    return BehaviorAnalysis(simulation_id=result.simulation.id, mode=result.simulation.mode,
        execution_status=result.simulation.status, agent=result.simulation.agent,
        agent_snapshot=result.simulation.agent_snapshot, agent_inputs=result.agent_inputs,
        control_mode=None if result.control_audit is None else result.control_audit.mode,
        settings=settings, stored_safety_metrics=result.metrics,
        safety_evidence=_span(frames, source="stored_safety_metrics"), driving_outcome=result.driving_outcome,
        task_evidence=task_evidence, braking=braking, route=_route(result, frames, settings, gaps), events=events,
        coverage=BehaviorCoverage(telemetry_frames=len(frames), telemetry_gaps=gaps,
            pose_frames=sum(frame.ego.transform is not None for frame in frames),
            velocity_frames=sum(frame.ego.velocity_mps is not None for frame in frames),
            measured_duration_s=result.measured_duration_s, decision_coverage=decision_coverage,
            applied_decisions=applied, unresolved_inputs=unresolved, script_intervention_frames=len(interventions),
            changed_control_frames=sum(action.status == "applied" and action.proposed != action.applied for action in actions.values()),
            hazard_clearance_pairs=clearance_pairs, hazard_ttc_pairs=ttc_pairs,
            total_response_events=total, response_events_truncated=total > len(events)),
        interpretation_notes=[
            "Synthetic fixture measurements are not CARLA or real-driving evidence." if result.simulation.mode == "test" else
                "Measurements describe the recorded CARLA episode, not real-driving safety.",
            "Response intervals start at a scheduled command's aligned input frame or a measured hazard frame, not known agent perception.",
            "Already-active signals have no onset latency. Missing signals and unobserved windows are not zero response time.",
            "Hazard anchors use evaluator ground truth and do not establish that an agent received or perceived that information.",
            "Control proposals, applied controls and measured motion are distinct; motion alone does not establish agent intent or causality.",
            "Steering changes use a preceding control when available, otherwise the anchor control; heading changes use the anchor heading.",
            "Route deviation is planar distance to the recorded finite route with height gating, not lane compliance; progress is the saved task monitor value.",
            "Stored safety metrics/settings are unchanged. Agent-reported diagnostics remain separate in the decision timeline.",
        ])
