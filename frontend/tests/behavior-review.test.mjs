import assert from "node:assert/strict";
import { after, afterEach, test } from "node:test";
import { readFile } from "node:fs/promises";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const loaded = await loadComponents("pages/Simulations.tsx");
const { RunDetail } = loaded.module;
const { BehaviorReview } = await loaded.moduleAt("components/BehaviorReview.tsx");
const { DecisionTimeline } = await loaded.moduleAt("components/DecisionTimeline.tsx");
const { MetricsChart } = await loaded.moduleAt("components/MetricsChart.tsx");
const { locateFrame } = await loaded.moduleAt("components/evidence-navigation.ts");
const { api, createApiClient, ApiError } = await loaded.moduleAt("services/api.ts");
// Exported from the real backend analysis engine; all frames are synthetic.
const fixture = JSON.parse(await readFile(new URL("fixtures/behavior-review.json", import.meta.url), "utf8"));
const ID = fixture.analysis.simulation_id, OTHER = "00000000-0000-4000-8000-000000000002";
const originalFetch = globalThis.fetch;
const browserGlobals = Object.fromEntries(["document", "window", "HTMLElement"].map(key => [key, globalThis[key]]));
afterEach(() => {
  globalThis.fetch = originalFetch;
  for (const [key, value] of Object.entries(browserGlobals)) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; }
});
after(() => loaded.cleanup());
const clone = () => structuredClone(fixture.analysis);
const text = node => typeof node === "string" || typeof node === "number" ? String(node) : (node?.children ?? []).map(text).join("");
const button = (view, label) => { const node = view.root.findAllByType("button").find(n => text(n) === label); assert.ok(node, `Missing ${label}`); return node; };
const click = async (view, label) => act(async () => button(view, label).props.onClick());
const response = body => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { resolve, reject, promise }; };
const telemetry = ({ offset = 0, limit = 200 } = {}) => ({ offset, limit, total: fixture.telemetry.length, frames: fixture.telemetry.slice(offset, offset + limit) });
const decisions = ({ offset = 0, limit = 10 } = {}) => ({ simulation_id: ID, available: true, coverage: "complete", offset, limit,
  total: fixture.records.length, applied: fixture.records.length, transitions: fixture.records.length, reset: null, recording_artifact_index: null,
  records: fixture.records.slice(offset, offset + limit), telemetry: fixture.telemetry.slice(offset, Math.min(offset + limit, fixture.records.length) + 1) });
async function mount(t, Component = BehaviorReview, props = {}) {
  let view;
  await act(async () => { view = TestRenderer.create(React.createElement(Component, { simulationId: ID, active: true, inspect: () => {}, ...props })); });
  t.after(() => act(() => view.unmount())); return view;
}

test("analysis client accepts backend fixture, retains identity and forwards cancellation", async () => {
  const controller = new AbortController();
  globalThis.fetch = async (url, options) => {
    assert.equal(url, `/api/simulations/${ID}/analysis`); assert.equal(options.method, "GET"); assert.equal(options.signal, controller.signal);
    return response(fixture.analysis);
  };
  assert.deepEqual(await createApiClient("/api").getBehaviorAnalysis(ID, { signal: controller.signal }), fixture.analysis);
});

test("analysis client rejects malformed nested evidence, clocks, channels, status and identity", async () => {
  const client = createApiClient("/api");
  for (const change of [
    a => a.simulation_id = OTHER, a => a.execution_status = "running", a => a.schema_version = "2",
    a => a.route.max_deviation.value = null, a => a.route.mean_deviation.value = -1,
    a => a.coverage.telemetry_frames = -1, a => a.coverage.response_events_truncated = true,
    a => a.braking.settings = {}, a => a.settings.brake_threshold = 2,
    a => a.events[0].responses[0].latency_s = null, a => a.events[0].responses.pop(),
    a => a.events[0].responses[0].evidence[0].last_frame = 0,
    a => a.events[0].responses[0].response_time_s = "0.1", a => a.events[0].anchor.frame = -1,
    a => a.interpretation_notes = "bad", a => a.agent_inputs = {}, a => a.stored_safety_metrics = {},
  ]) {
    const data = clone(); change(data); globalThis.fetch = async () => response(data);
    await assert.rejects(client.getBehaviorAnalysis(ID), ApiError);
  }
  globalThis.fetch = async () => { throw new Error("Must not fetch"); };
  await assert.rejects(client.getBehaviorAnalysis("../private"), ApiError);
});

test("review shows real measurements, task and provenance with evidence limits", async t => {
  api.getBehaviorAnalysis = async () => clone(); const view = await mount(t);
  const content = text(view.root);
  for (const pattern of [/How the vehicle responded/, /Synthetic test evidence/, /Maximum route deviation/, /Recorded route completion/,
    /timed out/, /Complete observation window/, /Agent-reported diagnostics/, /not verified internal reasoning/, /Effective analysis settings/,
    /Completed stopping episodes/, /23 applied/, /Maximum deceleration/]) assert.match(content, pattern);
  assert.equal(view.root.findAllByType("svg").length, 1);
  assert.equal(view.root.findAllByType("svg")[0].props.role, "img");
});

test("observed zero latency, already-active, missing response and unknown stay distinct", async t => {
  const data = clone(); const responses = data.events[0].responses;
  responses[0].latency_s = 0;
  responses[1].status = "already_active"; responses[1].latency_s = null;
  Object.assign(responses[2], { status: "not_observed", latency_s: null, response_frame: null, response_time_s: null, value: null, window_complete: false });
  Object.assign(responses[3], { status: "unavailable", latency_s: null, response_frame: null, response_time_s: null, value: null, reason: "missing_decision_input" });
  data.route.max_deviation = { value: 0, unit: "m", coverage: "partial", observed_samples: 1, eligible_samples: 2, reason: "missing_pose", evidence: data.route.max_deviation.evidence };
  data.route.mean_deviation = { ...data.route.max_deviation, value: null, coverage: "unavailable", observed_samples: 0, evidence: [] };
  api.getBehaviorAnalysis = async () => data; const view = await mount(t); const content = text(view.root);
  for (const pattern of [/0.000 s after anchor/, /Already active; onset unknown/, /No response observed in measured window/, /Incomplete observation window/,
    /missing decision input/, /0.00 m/, /Unavailable/, /partial · 1 \/ 2/]) assert.match(content, pattern);
});

test("script influence and evaluator hazards are explicit, without crediting independent response", async t => {
  const data = clone(); data.events[0].responses[0].attribution = "script_influenced";
  Object.assign(data.events[0].anchor, { kind: "measured_hazard", source: "evaluator_ground_truth", onset: "first_observed", event_action: null, scheduled_time_s: null });
  api.getBehaviorAnalysis = async () => data; const view = await mount(t); const content = text(view.root);
  assert.match(content, /Includes scenario assistance/); assert.match(content, /do not credit this as an independent agent response/);
  assert.match(content, /may include information the agent never received/); assert.match(content, /first observed/);
});

test("legacy empty failed run stays useful without fabricated decisions, routes or safety", async t => {
  const data = clone(); data.execution_status = "failed"; data.events = []; data.coverage.total_response_events = 0;
  data.coverage.decision_coverage = "unavailable"; data.coverage.applied_decisions = 0; data.coverage.unresolved_inputs = 0;
  data.driving_outcome = null; data.braking.stopping_episodes = []; data.braking.total_stopping_episodes = 0;
  api.getBehaviorAnalysis = async () => data; const view = await mount(t); const content = text(view.root);
  assert.match(content, /execution: failed/); assert.match(content, /No driving outcome was recorded/);
  assert.match(content, /No response anchors/); assert.match(content, /Zero anchors do not establish a hazard-free run/);
  assert.match(content, /No completed stops/); assert.doesNotMatch(content, /Review input at frame/);
});

test("event and stopping pagination preserve output-cap notices and exact evidence targets", async t => {
  const data = clone(); const event = data.events[0], stop = data.braking.stopping_episodes[0];
  data.events = Array.from({ length: 7 }, (_, i) => ({ ...event, anchor: { ...event.anchor, id: `e${i}`, actor_id: `actor${i}`, time_s: i } }));
  data.coverage.total_response_events = 10; data.coverage.response_events_truncated = true;
  data.braking.stopping_episodes = Array.from({ length: 7 }, (_, i) => ({ ...stop, evidence: { ...stop.evidence, last_frame: stop.evidence.last_frame + i } }));
  data.braking.total_stopping_episodes = 8; data.braking.episodes_truncated = true;
  const targets = []; api.getBehaviorAnalysis = async () => data;
  const view = await mount(t, BehaviorReview, { inspect: (...args) => targets.push(args) });
  assert.match(text(view.root), /Showing 7 of 10 anchors · output capped/);
  for (let i = 0; i < 5; i++) await click(view, "Next event");
  assert.match(text(view.root), /Event 6 of 7/); assert.match(text(view.root), /scenario event · actor5/);
  await click(view, "Next stops"); assert.match(text(view.root), /Completed stops, page 2/);
  await click(view, `Inspect anchor frame ${event.anchor.frame}`); assert.deepEqual(targets.at(-1), ["telemetry", event.anchor.frame]);
  const decisionSpan = event.responses.flatMap(r => r.evidence).find(s => s.source === "decisions");
  await click(view, `Inspect decision frame ${decisionSpan.last_frame}`); assert.deepEqual(targets.at(-1), ["decisions", decisionSpan.last_frame]);
});

test("analysis error can be retried, and refresh removes stale findings while loading", async t => {
  api.getBehaviorAnalysis = async () => { throw new ApiError("Temporary analysis error", 503); };
  const view = await mount(t); assert.match(text(view.root), /Temporary analysis error/);
  api.getBehaviorAnalysis = async () => clone(); await click(view, "Retry analysis"); assert.match(text(view.root), /Measured behavior/);
  const pending = deferred(); api.getBehaviorAnalysis = () => pending.promise;
  await click(view, "Refresh analysis"); assert.match(text(view.root), /Analyzing saved/); assert.doesNotMatch(text(view.root), /Measured behavior/);
  await act(async () => pending.resolve(clone())); assert.match(text(view.root), /Measured behavior/);
});

test("old backend and missing run explain analysis availability", async t => {
  api.getBehaviorAnalysis = async () => { throw new ApiError("Not found", 404); };
  const view = await mount(t); assert.match(text(view.root), /backend includes Phase 8.1; restart it after updating/);
});

test("inactive review performs no reads and stale run responses cannot replace current analysis", async t => {
  const pending = deferred(); let signal, reads = 0;
  api.getBehaviorAnalysis = async (id, options) => { reads++; signal = options.signal; if (id === ID) return pending.promise; return { ...clone(), simulation_id: OTHER, events: [] }; };
  const view = await mount(t, BehaviorReview, { active: false }); assert.equal(reads, 0);
  await act(async () => view.update(React.createElement(BehaviorReview, { simulationId: ID, active: true, inspect: () => {} })));
  const oldSignal = signal;
  await act(async () => view.update(React.createElement(BehaviorReview, { simulationId: OTHER, active: true, inspect: () => {} })));
  assert.equal(oldSignal.aborted, true);
  await act(async () => pending.resolve(clone())); assert.match(text(view.root), /No response anchors/);
});

test("analysis timeout offers a retry without retaining old findings", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  api.getBehaviorAnalysis = (id, { signal }) => new Promise((resolve, reject) => signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError"))));
  const view = await mount(t); await act(async () => t.mock.timers.tick(30_000));
  assert.match(text(view.root), /Behavior analysis timed out/); assert.equal(button(view, "Retry analysis").props.disabled, false);
});

test("native frame lookup is logarithmic, exact and supports sparse high IDs", async () => {
  const controller = new AbortController(); let reads = 0;
  const read = async offset => { reads++; return { total: 10000, items: [{ frame: 5000 + offset * 2 }] }; };
  assert.equal(await locateFrame(18000, read, controller.signal), 6500); assert.ok(reads < 17);
  assert.equal(await locateFrame(18001, read, controller.signal), null);
  controller.abort(); assert.equal(await locateFrame(5000, read, controller.signal), null);
});

test("finding opens a native decision beyond the first page and manual paging still works", async t => {
  const calls = []; api.getDecisions = async (id, query) => { calls.push(query); return decisions(query); };
  const view = await mount(t, DecisionTimeline, { focus: { frame: 112, token: 1 } });
  assert.match(text(view.root), /Input 112 → result 113/); assert.match(text(view.root), /Opened supporting decision input at native frame 112/);
  assert.ok(calls.some(q => q.offset === 12 && q.limit === 10));
  await click(view, "Previous decisions"); assert.match(text(view.root), /Input 102 → result 103/);
  await click(view, "Next decisions"); assert.match(text(view.root), /Input 112 → result 113/);
});

test("missing decision frame does not substitute a nearby action", async t => {
  api.getDecisions = async (id, query) => decisions(query);
  const view = await mount(t, DecisionTimeline, { focus: { frame: 123, token: 1 } });
  assert.match(text(view.root), /No recorded decision input exists at frame 123/); assert.doesNotMatch(text(view.root), /Input 122 →/);
});

test("telemetry finding opens final measured packet even when it has no decision", async t => {
  const calls = []; api.getTelemetry = async (id, query) => { calls.push(query); return telemetry(query); };
  const view = await mount(t, MetricsChart, { focus: { frame: 123, token: 1 } });
  assert.match(text(view.root), /Opened supporting telemetry at native frame 123/); assert.match(text(view.root), /123 \/ 1.150 s/);
  assert.ok(calls.some(q => q.offset === 23 && q.limit === 200));
  await click(view, "Previous frames"); assert.match(text(view.root), /100 \/ 0.000 s/);
});

test("missing telemetry target has an explicit unavailable state", async t => {
  api.getTelemetry = async (id, query) => telemetry(query);
  const view = await mount(t, MetricsChart, { focus: { frame: 300, token: 1 } });
  assert.match(text(view.root), /No measured telemetry exists at frame 300/); assert.doesNotMatch(text(view.root), /100 \/ 0.000 s/);
});

test("a stale decision lookup cannot replace a newer evidence selection", async t => {
  const pending = deferred(); let first = true, oldSignal;
  api.getDecisions = async (id, query, options) => {
    if (first) { first = false; oldSignal = options.signal; return pending.promise; }
    return decisions(query);
  };
  const view = await mount(t, DecisionTimeline, { focus: { frame: 112, token: 1 } });
  await act(async () => view.update(React.createElement(DecisionTimeline, { simulationId: ID, active: true, focus: { frame: 118, token: 2 } })));
  assert.equal(oldSignal.aborted, true);
  await act(async () => pending.resolve(decisions({ offset: 0, limit: 1 })));
  assert.match(text(view.root), /Input 118 → result 119/); assert.doesNotMatch(text(view.root), /Input 112 →/);
});

test("a stale telemetry lookup cannot replace a newer evidence selection", async t => {
  const pending = deferred(); let first = true, oldSignal;
  api.getTelemetry = async (id, query, options) => {
    if (first) { first = false; oldSignal = options.signal; return pending.promise; }
    return telemetry(query);
  };
  const view = await mount(t, MetricsChart, { focus: { frame: 112, token: 1 } });
  await act(async () => view.update(React.createElement(MetricsChart, { simulationId: ID, active: true, focus: { frame: 123, token: 2 } })));
  assert.equal(oldSignal.aborted, true);
  await act(async () => pending.resolve(telemetry({ offset: 0, limit: 1 })));
  assert.match(text(view.root), /123 \/ 1.150 s/); assert.doesNotMatch(text(view.root), /112 \/ 0.600 s/);
});

test("run detail wires analysis links to both real evidence components", async t => {
  const simulation = { id: ID, status: "completed", mode: "test", agent: "custom", progress: 1, started_at: null, finished_at: null,
    scenario: { name: "Synthetic behavior review", seed: 0, map: "Town03", duration_s: 1.2, fixed_delta_seconds: .05 } };
  api.getSimulation = async () => ({ simulation, revision: 1, job: null, result: { simulation, metrics: fixture.analysis.stored_safety_metrics,
    measured_duration_s: 1.15, telemetry_frames: 24, artifacts: [], runtime: null, driving_outcome: fixture.analysis.driving_outcome } });
  api.getBehaviorAnalysis = async () => clone(); api.getDecisions = async (id, query) => decisions(query); api.getTelemetry = async (id, query) => telemetry(query);
  const view = await mount(t, RunDetail, { id: ID });
  await click(view, "Inspect telemetry frame 123"); assert.match(text(view.root), /Opened supporting telemetry at native frame 123/);
  await click(view, "Inspect decision frame 103"); assert.match(text(view.root), /Opened supporting decision input at native frame 103/);
});

test("keyboard evidence return restores the finding and respects reduced motion", async t => {
  class Element {
    isConnected = true;
    scrollIntoView(options) { this.scroll = options; }
    focus(options) { this.focused = options; }
  }
  const source = new Element(), destination = new Element(), review = new Element();
  globalThis.HTMLElement = Element;
  globalThis.document = { activeElement: source, getElementById(id) { return id.startsWith("behavior-") ? review : destination; } };
  globalThis.window = { matchMedia: () => ({ matches: true }) };
  const simulation = { id: ID, status: "completed", mode: "test", agent: "custom", progress: 1,
    scenario: { name: "Synthetic review", seed: 0, map: "Town03", duration_s: 1.2, fixed_delta_seconds: .05 } };
  api.getSimulation = async () => ({ simulation, revision: 1, job: null, result: { simulation, metrics: fixture.analysis.stored_safety_metrics,
    measured_duration_s: 1.15, telemetry_frames: 24, artifacts: [], runtime: null } });
  api.getBehaviorAnalysis = async () => clone(); api.getDecisions = async (id, query) => decisions(query); api.getTelemetry = async (id, query) => telemetry(query);
  const view = await mount(t, RunDetail, { id: ID });
  await click(view, "Inspect telemetry frame 123");
  assert.equal(destination.scroll.behavior, "auto"); assert.deepEqual(destination.focused, { preventScroll: true });
  await click(view, "Return to behavior finding"); assert.deepEqual(source.focused, { preventScroll: true });
  source.isConnected = false; await click(view, "Return to behavior finding"); assert.deepEqual(review.focused, { preventScroll: true });
});
