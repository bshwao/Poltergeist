import type { BehaviorAnalysis } from "../types/generated";

const object = (v: unknown): v is Record<string, any> => v !== null && typeof v === "object" && !Array.isArray(v);
const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const nonnegative = (v: unknown) => num(v) && v >= 0;
const count = (v: unknown) => nonnegative(v) && Number.isSafeInteger(v);
const nullable = (check: (v: any) => boolean) => (v: unknown) => v === null || check(v);
const one = (v: unknown, choices: unknown[]) => choices.includes(v);
const list = (v: unknown, check: (item: any) => boolean) => Array.isArray(v) && v.every(check);
const text = (v: unknown) => typeof v === "string" && v.trim().length > 0;
const span = (v: unknown) => object(v) && one(v.source, ["telemetry", "decisions", "driving_outcome", "stored_safety_metrics"])
  && count(v.first_frame) && count(v.last_frame) && v.last_frame >= v.first_frame
  && nonnegative(v.first_time_s) && nonnegative(v.last_time_s) && v.last_time_s >= v.first_time_s;
const measure = (v: unknown) => object(v) && nullable(nonnegative)(v.value)
  && one(v.unit, ["m", "m/s²", "episodes", "fraction"]) && one(v.coverage, ["complete", "partial", "unavailable"])
  && (v.value === null) === (v.coverage === "unavailable") && count(v.observed_samples) && count(v.eligible_samples)
  && v.observed_samples <= v.eligible_samples && nullable(text)(v.reason) && list(v.evidence, span)
  && (v.value === null || v.observed_samples > 0 && v.evidence.length > 0);
export const responseChannels = ["proposed_braking", "applied_braking", "proposed_steering_change", "applied_steering_change", "measured_deceleration", "measured_heading_change"] as const;

/** Validate the nested data used by the review; reject invalid values before rendering. */
export function isBehaviorAnalysis(v: unknown, id: string): v is BehaviorAnalysis {
  if (!object(v) || typeof v.simulation_id !== "string" || v.simulation_id.toLowerCase() !== id.toLowerCase()
      || v.schema_version !== "1.0" || !one(v.mode, ["test", "carla"]) || !one(v.agent, ["baseline", "custom"])
      || !one(v.execution_status, ["completed", "failed", "cancelled"]) || !one(v.control_mode, [null, "scripted", "authoritative"])
      || !object(v.settings) || !object(v.coverage) || !object(v.braking) || !object(v.route)) return false;
  const s = v.settings, c = v.coverage, b = v.braking, r = v.route;
  if (s.method_version !== "1.0" || !["response_window_s", "brake_threshold", "steering_change_threshold", "deceleration_threshold_mps2",
    "heading_change_deg", "hazard_ttc_s", "max_sample_gap_s", "route_height_tolerance_m"].every(k => num(s[k]) && s[k] > 0)
    || s.response_window_s > 60 || s.brake_threshold > 1 || s.steering_change_threshold > 2 || s.heading_change_deg > 180 || s.hazard_ttc_s > 60
    || !nonnegative(s.hazard_clearance_m) || !["max_response_events", "max_stopping_episodes"].every(k => count(s[k]) && s[k] >= 1 && s[k] <= 2000)) return false;
  if (!one(c.decision_coverage, ["unavailable", "empty", "partial", "complete"]) || !nonnegative(c.measured_duration_s)
    || !["telemetry_frames", "telemetry_gaps", "pose_frames", "velocity_frames", "applied_decisions", "unresolved_inputs", "script_intervention_frames",
      "changed_control_frames", "hazard_clearance_pairs", "hazard_ttc_pairs", "total_response_events"].every(k => count(c[k]))
    || typeof c.response_events_truncated !== "boolean") return false;
  if (!measure(b.max_deceleration) || !measure(b.hard_braking_count) || !count(b.total_stopping_episodes) || typeof b.episodes_truncated !== "boolean"
    || !one(b.settings_source, ["recorded_evaluation", "legacy_defaults"]) || !object(b.settings)
    || !list(b.stopping_episodes, e => object(e) && nonnegative(e.distance_m) && nonnegative(e.duration_s) && span(e.evidence)
      && one(e.script_influenced, [null, true, false]) && count(e.intervention_frames))
    || b.stopping_episodes.length > b.total_stopping_episodes || b.episodes_truncated !== (b.stopping_episodes.length < b.total_stopping_episodes)) return false;
  if (!one(r.route_owner, [null, "framework", "agent"]) || !one(r.reference_source, [null, "recorded_resolved_route"])
    || !["max_deviation", "mean_deviation", "recorded_progress", "recorded_length", "recorded_completion_fraction"].every(k => measure(r[k]))) return false;
  if (!list(v.events, e => {
    if (!object(e) || !object(e.anchor)) return false;
    const a = e.anchor;
    return text(a.id) && text(a.actor_id) && one(a.kind, ["scenario_event", "measured_hazard"])
      && one(a.source, ["scenario_script", "evaluator_ground_truth"]) && nonnegative(a.time_s) && nullable(count)(a.frame)
      && nullable(count)(a.event_index) && one(a.event_action, [null, "brake", "set_speed"]) && typeof a.ego_event === "boolean"
      && one(a.onset, ["scheduled_command", "threshold_crossing", "first_observed"])
      && ["scheduled_time_s", "ttc_s", "clearance_m"].every(k => nullable(nonnegative)(a[k])) && nullable(text)(a.reason)
      && list(e.responses, p => object(p) && one(p.channel, [...responseChannels]) && one(p.status, ["observed", "already_active", "not_observed", "unavailable"])
        && nullable(nonnegative)(p.latency_s) && (p.status === "observed") === (p.latency_s !== null)
        && nullable(count)(p.response_frame) && nullable(count)(p.reference_frame) && nullable(num)(p.reference_value)
        && ["response_time_s", "value", "observed_until_s"].every(k => nullable(nonnegative)(p[k]))
        && num(p.threshold) && p.threshold > 0 && one(p.unit, ["normalized_control", "m/s²", "degrees"])
        && one(p.attribution, ["sdk_agent", "baseline_controller", "legacy_custom", "script_influenced", "unknown", "measured_motion"])
        && nonnegative(p.window_end_s) && typeof p.window_complete === "boolean" && nullable(text)(p.reason) && list(p.evidence, span)
        && (!one(p.status, ["observed", "already_active"]) || p.response_frame !== null && p.response_time_s !== null && p.value !== null && p.evidence.length > 0))
      && e.responses.length === 6 && new Set(e.responses.map((p: any) => p.channel)).size === 6;
  }) || v.events.length > c.total_response_events || c.response_events_truncated !== (v.events.length < c.total_response_events)
    || new Set(v.events.map((e: any) => e.anchor.id)).size !== v.events.length) return false;
  return list(v.safety_evidence, span) && list(v.task_evidence, span) && list(v.interpretation_notes, text);
}
