import { useEffect, useId, useState } from "react";
import { api, ApiError } from "../services/api";
import type { BehaviorAnalysis, BehaviorMeasure, BehaviorResponse, BehaviorSettings, EvidenceSpan } from "../types/generated";
import { AgentIdentity } from "./AgentIdentity";

export type InspectEvidence = (source: "decisions" | "telemetry", frame: number) => void;
const readable = (text: string) => text.replace(/_/g, " ");
const fixed = (value: number) => value.toFixed(3);
const channelNames: Record<BehaviorResponse["channel"], string> = {
  proposed_braking: "Proposed braking", applied_braking: "Applied braking",
  proposed_steering_change: "Proposed steering change", applied_steering_change: "Applied steering change",
  measured_deceleration: "Measured deceleration", measured_heading_change: "Measured heading change",
};
const PAGE_SIZE = 5;
const settingLabels: Record<keyof BehaviorSettings, string> = {
  method_version: "Analysis method version", response_window_s: "Response window (s)", brake_threshold: "Brake threshold (normalized control)",
  steering_change_threshold: "Steering change (normalized control)", deceleration_threshold_mps2: "Deceleration threshold (m/s²)",
  heading_change_deg: "Heading change (degrees)", hazard_ttc_s: "Hazard TTC horizon (s)", hazard_clearance_m: "Hazard clearance (m)",
  max_sample_gap_s: "Maximum sample gap (s)", route_height_tolerance_m: "Route height tolerance (m)",
  max_response_events: "Maximum retained event anchors", max_stopping_episodes: "Maximum retained stopping episodes",
};
const attributionLabels: Record<BehaviorResponse["attribution"], string> = {
  sdk_agent: "Autonomous agent", baseline_controller: "Baseline controller", legacy_custom: "Custom controller (legacy)",
  script_influenced: "Script influenced", unknown: "Unknown", measured_motion: "Measured vehicle motion",
};

function Evidence({ spans, inspect, decisions }: { spans: EvidenceSpan[]; inspect: InspectEvidence; decisions: boolean }) {
  if (!spans.length) return <p className="field-hint">No supporting frames available.</p>;
  return <details className="behavior-evidence"><summary>Supporting recorded frames</summary>{spans.map((span, i) => <div key={i}>
    <p>{readable(span.source)} · frames {span.first_frame}–{span.last_frame} · {fixed(span.first_time_s)}–{fixed(span.last_time_s)} s</p>
    <div className="behavior-evidence-actions">{[...new Set([span.first_frame, span.last_frame])].map(frame => <span key={frame}>
      <button type="button" className="button secondary small" onClick={() => inspect(span.source === "decisions" ? "decisions" : "telemetry", frame)}>
        Inspect {span.source === "decisions" ? "decision" : "telemetry"} frame {frame}</button>
      {span.source !== "decisions" && decisions && <button type="button" className="button secondary small" onClick={() => inspect("decisions", frame)}>Review input at frame {frame}</button>}
    </span>)}</div>
  </div>)}</details>;
}

function Measure({ label, measure, inspect, decisions }: { label: string; measure: BehaviorMeasure; inspect: InspectEvidence; decisions: boolean }) {
  const value = measure.value === null ? "Unavailable" : measure.unit === "fraction" ? `${(measure.value * 100).toFixed(1)}%`
    : `${measure.value.toFixed(measure.unit === "episodes" ? 0 : 2)} ${measure.unit}`;
  return <article className="behavior-measure"><h3>{label}</h3><strong>{value}</strong><p className="field-hint">{measure.coverage} · {measure.observed_samples} / {measure.eligible_samples} eligible samples</p>
    {measure.reason && <p>{readable(measure.reason)}</p>}<Evidence spans={measure.evidence} inspect={inspect} decisions={decisions} /></article>;
}

function Response({ response: r, inspect, decisions }: { response: BehaviorResponse; inspect: InspectEvidence; decisions: boolean }) {
  const status = r.status === "observed" ? `${fixed(r.latency_s!)} s after anchor` : r.status === "already_active" ? "Already active; onset unknown"
    : r.status === "not_observed" ? "No response observed in measured window" : "Unavailable";
  return <tr><th scope="row">{channelNames[r.channel]}</th><td><strong>{status}</strong>
    <p className="field-hint">{r.window_complete ? "Complete" : "Incomplete"} observation window · observed through {r.observed_until_s === null ? "unknown" : `${fixed(r.observed_until_s)} s`} / ends {fixed(r.window_end_s)} s</p>
    {r.reason && <p>{readable(r.reason)}</p>}</td><td>{attributionLabels[r.attribution]}{r.attribution === "script_influenced" && <p className="field-hint">Includes scenario assistance; do not credit this as an independent agent response.</p>}</td>
    <td><p>Threshold {fixed(r.threshold)} {readable(r.unit)}</p>{r.value !== null && <p>Observed {fixed(r.value)} · frame {r.response_frame} at {fixed(r.response_time_s!)} s</p>}
      {r.reference_frame !== null && <p>Reference frame {r.reference_frame} · value {r.reference_value === null ? "unknown" : fixed(r.reference_value)}</p>}
      <Evidence spans={r.evidence} inspect={inspect} decisions={decisions} /></td></tr>;
}

function Findings({ analysis: a, inspect }: { analysis: BehaviorAnalysis; inspect: InspectEvidence }) {
  const prefix = useId();
  const [eventIndex, setEventIndex] = useState(0);
  const [stopPage, setStopPage] = useState(0);
  const decisions = a.coverage.applied_decisions + a.coverage.unresolved_inputs > 0;
  const event = a.events[eventIndex];
  const eventStart = Math.floor(eventIndex / PAGE_SIZE) * PAGE_SIZE;
  const endTime = Math.max(a.coverage.measured_duration_s, ...a.events.map(e => e.anchor.time_s), .001);
  const evidenceProps = { inspect, decisions };
  const measures: [string, BehaviorMeasure][] = [["Maximum deceleration", a.braking.max_deceleration], ["Hard-braking episodes", a.braking.hard_braking_count],
    ["Maximum route deviation", a.route.max_deviation], ["Mean route deviation", a.route.mean_deviation],
    ["Recorded route progress", a.route.recorded_progress], ["Recorded route length", a.route.recorded_length], ["Recorded route completion", a.route.recorded_completion_fraction]];
  return <>
    {a.mode === "test" && <p className="notice">Synthetic test evidence. These findings do not establish real-world or CARLA driving performance.</p>}
    {(a.execution_status !== "completed" || a.coverage.telemetry_gaps > 0 || a.coverage.decision_coverage !== "complete") && <p className="notice">Evidence may be incomplete: execution {a.execution_status}, decision coverage {a.coverage.decision_coverage}, {a.coverage.telemetry_gaps} telemetry gaps. Available measurements describe only their recorded samples.</p>}
    <AgentIdentity simulation={a} inputs={a.agent_inputs} />
    <p className="field-hint">Input source: {a.agent_inputs ? readable(a.agent_inputs.source) : "Not recorded"} · control authority: {a.control_mode ? readable(a.control_mode) : "Not recorded"} · execution: {a.execution_status}</p>
    <h3>Measured behavior</h3><div className="behavior-measures">{measures.map(([label, measure]) => <Measure key={label} label={label} measure={measure} {...evidenceProps} />)}</div>
    <p className="field-hint">Route deviation is distance from the saved route, not lane compliance. Route owner: {a.route.route_owner ?? "unavailable"}. Reference: {a.route.reference_source ? readable(a.route.reference_source) : "unavailable"}.</p>
    <div className="behavior-task"><h3>Recorded driving task</h3><p>{a.driving_outcome ? `${readable(a.driving_outcome.status)} · ${readable(a.driving_outcome.termination_reason)}` : "No driving outcome was recorded."}</p>
      <p className="field-hint">Task completion and safety are separate outcomes. Original safety scores remain in the safety panel above.</p><Evidence spans={a.task_evidence} {...evidenceProps} /></div>

    <section className="behavior-section" aria-labelledby={`${prefix}-events`}><h3 id={`${prefix}-events`}>Events &amp; response intervals</h3>
      <p>Intervals start at a defined scenario command or evaluator hazard. They do not establish when the agent perceived a hazard or why it acted.</p>
      <p className="field-hint">Showing {a.events.length} of {a.coverage.total_response_events} anchors{a.coverage.response_events_truncated ? " · output capped by analysis settings" : ""}. Zero anchors do not establish a hazard-free run.</p>
      {a.events.length > 0 ? <>
        <svg className="behavior-event-chart" viewBox="0 0 680 72" role="img" aria-labelledby={`${prefix}-chart-title ${prefix}-chart-desc`}>
          <title id={`${prefix}-chart-title`}>Recorded event anchors</title><desc id={`${prefix}-chart-desc`}>Markers show anchor times in episode seconds. Purple is the selected anchor. Select an event using the controls below.</desc>
          <line x1="24" x2="656" y1="25" y2="25" stroke="#bcb6d2" />{a.events.map((e, index) => <circle key={e.anchor.id} cx={24 + e.anchor.time_s / endTime * 632} cy="25" r={eventIndex === index ? 7 : 4} fill={eventIndex === index ? "#7053dc" : "#9791ac"}><title>{e.anchor.actor_id}: {fixed(e.anchor.time_s)} s</title></circle>)}
          <text x="24" y="58" fontSize="12" fill="#5d5670">0 s</text><text x="656" y="58" textAnchor="end" fontSize="12" fill="#5d5670">{fixed(endTime)} s</text>
        </svg>
        <div className="behavior-event-buttons" aria-label="Select event">{a.events.slice(eventStart, eventStart + PAGE_SIZE).map((e, i) => <button type="button" key={e.anchor.id} className="button secondary small" aria-pressed={eventIndex === eventStart + i} onClick={() => setEventIndex(eventStart + i)}>{eventStart + i + 1}. {e.anchor.actor_id} · {fixed(e.anchor.time_s)} s</button>)}</div>
        <div className="pagination"><button type="button" className="button secondary small" disabled={eventIndex === 0} onClick={() => setEventIndex(i => i - 1)}>Previous event</button><span>Event {eventIndex + 1} of {a.events.length}</span><button type="button" className="button secondary small" disabled={eventIndex + 1 >= a.events.length} onClick={() => setEventIndex(i => i + 1)}>Next event</button></div>
        {event && <div className="behavior-event-detail"><h4>{readable(event.anchor.kind)} · {event.anchor.actor_id}</h4>
          <p>{readable(event.anchor.source)} · {readable(event.anchor.onset)} · anchor {fixed(event.anchor.time_s)} s · frame {event.anchor.frame ?? "unavailable"}</p>
          {event.anchor.scheduled_time_s !== null && <p>Scheduled command: {fixed(event.anchor.scheduled_time_s)} s · {readable(event.anchor.event_action ?? "unknown")} {event.anchor.ego_event ? "(ego script)" : ""}</p>}
          {event.anchor.kind === "measured_hazard" && <p>Evaluator ground truth; it may include information the agent never received. TTC: {event.anchor.ttc_s === null ? "unknown" : `${fixed(event.anchor.ttc_s)} s`}; clearance: {event.anchor.clearance_m === null ? "unknown" : `${fixed(event.anchor.clearance_m)} m`}.</p>}
          {event.anchor.reason && <p className="notice">{readable(event.anchor.reason)}</p>}
          {event.anchor.frame !== null && <button type="button" className="button secondary small" onClick={() => inspect("telemetry", event.anchor.frame!)}>Inspect anchor frame {event.anchor.frame}</button>}
          <div className="table-scroll" tabIndex={0} role="region" aria-label={`Response evidence for event ${eventIndex + 1}; scroll horizontally for more columns`}><table className="data-table behavior-response-table"><caption>Measured responses to event {eventIndex + 1}</caption><thead><tr><th scope="col">Signal</th><th scope="col">Observed interval</th><th scope="col">Attribution</th><th scope="col">Threshold &amp; evidence</th></tr></thead><tbody>{event.responses.map(r => <Response key={r.channel} response={r} {...evidenceProps} />)}</tbody></table></div>
        </div>}
      </> : <p>No response anchors are available in the recorded evidence.</p>}
    </section>

    <section className="behavior-section" aria-labelledby={`${prefix}-stops`}><h3 id={`${prefix}-stops`}>Completed stopping episodes</h3><p className="field-hint">{a.braking.stopping_episodes.length} of {a.braking.total_stopping_episodes} episodes retained{a.braking.episodes_truncated ? " · output capped" : ""}. Distance is integrated from measured speed. An unfinished stop is not a completed episode.</p>
      {a.braking.stopping_episodes.length === 0 ? <p>No completed stops were measurable in the saved evidence.</p> : <>
        <div className="table-scroll" tabIndex={0} role="region" aria-label="Stopping evidence; scroll horizontally for more columns"><table className="data-table"><caption className="sr-only">Completed stops, page {stopPage + 1}</caption><thead><tr><th scope="col">Stop</th><th scope="col">Duration</th><th scope="col">Distance</th><th scope="col">Script influence</th><th scope="col">Evidence</th></tr></thead><tbody>{a.braking.stopping_episodes.slice(stopPage * PAGE_SIZE, (stopPage + 1) * PAGE_SIZE).map((stop, i) => <tr key={stop.evidence.last_frame}><th scope="row">{stopPage * PAGE_SIZE + i + 1}</th><td>{fixed(stop.duration_s)} s</td><td>{fixed(stop.distance_m)} m</td><td>{stop.script_influenced === null ? "Unknown" : stop.script_influenced ? `Yes · ${stop.intervention_frames} intervention frames` : "None recorded"}</td><td><Evidence spans={[stop.evidence]} {...evidenceProps} /></td></tr>)}</tbody></table></div>
        <div className="pagination"><button type="button" className="button secondary small" disabled={stopPage === 0} onClick={() => setStopPage(p => p - 1)}>Previous stops</button><span>Page {stopPage + 1}</span><button type="button" className="button secondary small" disabled={(stopPage + 1) * PAGE_SIZE >= a.braking.stopping_episodes.length} onClick={() => setStopPage(p => p + 1)}>Next stops</button></div>
      </>}
    </section>

    <details className="behavior-section"><summary>Coverage &amp; analysis settings</summary><dl className="detail-facts">
      <div><dt>Telemetry</dt><dd>{a.coverage.telemetry_frames} frames · {fixed(a.coverage.measured_duration_s)} s · {a.coverage.telemetry_gaps} gaps</dd></div>
      <div><dt>Pose / velocity samples</dt><dd>{a.coverage.pose_frames} / {a.coverage.velocity_frames}</dd></div>
      <div><dt>Decision evidence</dt><dd>{a.coverage.decision_coverage} · {a.coverage.applied_decisions} applied · {a.coverage.unresolved_inputs} unresolved</dd></div>
      <div><dt>Script influence</dt><dd>{a.coverage.script_intervention_frames} intervention frames · {a.coverage.changed_control_frames} changed controls</dd></div>
      <div><dt>Hazard observations</dt><dd>{a.coverage.hazard_clearance_pairs} clearance pairs · {a.coverage.hazard_ttc_pairs} TTC pairs</dd></div>
      <div><dt>Braking settings source</dt><dd>{readable(a.braking.settings_source)}</dd></div>
    </dl><p>Response controls use the preceding input clock; the following telemetry frame measures the resulting motion. Unknown values are never treated as zero.</p>
      <h4>Effective analysis settings</h4><dl className="behavior-settings">{Object.entries(a.settings).map(([key, value]) => <div key={key}><dt>{settingLabels[key as keyof BehaviorSettings]}</dt><dd>{String(value)}</dd></div>)}</dl>
      <details><summary>Recorded braking evaluation settings</summary><pre>{JSON.stringify(a.braking.settings, null, 2)}</pre></details>
    </details>
    <section className="behavior-section"><h3>Evidence limits &amp; interpretation</h3><p>Agent-reported diagnostics appear separately in the decision timeline. They are the agent’s own statements, not verified internal reasoning.</p><ul>{a.interpretation_notes.map((note, i) => <li key={i}>{note}</li>)}</ul></section>
  </>;
}

export function BehaviorReview({ simulationId, active, inspect }: { simulationId: string; active: boolean; inspect: InspectEvidence }) {
  const prefix = useId();
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ key: string; loading: boolean; data?: BehaviorAnalysis; error?: string } | null>(null);
  const key = `${simulationId}/${attempt}`;
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController(); let current = true;
    const deadline = setTimeout(() => controller.abort(), 30_000);
    setState({ key, loading: true });
    void api.getBehaviorAnalysis(simulationId, { signal: controller.signal }).then(data => {
      if (current && !controller.signal.aborted) setState({ key, loading: false, data });
    }, error => {
      if (current) setState({ key, loading: false, error: controller.signal.aborted ? "Behavior analysis timed out. Retry when the API is available."
        : error instanceof ApiError && error.status === 404 ? "Analysis is unavailable. Check that the run exists and the backend includes Phase 8.1; restart it after updating."
        : error instanceof ApiError ? error.message : "Could not load behavior analysis." });
    }).finally(() => clearTimeout(deadline));
    return () => { current = false; controller.abort(); clearTimeout(deadline); };
  }, [simulationId, active, attempt, key]);
  const visible = state?.key === key ? state : null;
  const loading = active && (!visible || visible.loading);
  return <section className="panel behavior-review" aria-labelledby={`${prefix}-title`} aria-busy={loading}>
    <div className="panel-header"><div><p className="eyebrow">RECORDED BEHAVIOR</p><h2 id={`${prefix}-title`}>How the vehicle responded</h2><p>Measured responses with traceable evidence and explicit limits.</p></div><button type="button" className="button secondary small" disabled={!active || loading} onClick={() => setAttempt(a => a + 1)}>Refresh analysis</button></div>
    <div className="form-section">{loading && <p role="status">Analyzing saved run evidence…</p>}{!active && !visible && <p>Open this workspace to load behavior analysis.</p>}
      {visible?.error && <div className="notice error-notice" role="alert"><p>{visible.error}</p><button type="button" className="button secondary small" disabled={!active} onClick={() => setAttempt(a => a + 1)}>Retry analysis</button></div>}
      {visible?.data && <Findings key={key} analysis={visible.data} inspect={inspect} />}
    </div>
  </section>;
}
