import assert from "node:assert/strict";
import { after, afterEach, test } from "node:test";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { createHash } from "node:crypto";
import { loadComponents } from "../scripts/load-components.mjs";

const loaded = await loadComponents("components/DecisionTimeline.tsx");
const { DecisionTimeline } = loaded.module;
const { api, createApiClient, ApiError } = await loaded.moduleAt("services/api.ts");
const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });
after(() => loaded.cleanup());
const ID = "00000000-0000-4000-8000-000000000001";
const OTHER = "00000000-0000-4000-8000-000000000002";
const control = { throttle: .3, brake: 0, steer: -.1, reverse: false, hand_brake: false };
const transform = { position: { x: 0, y: 0, z: 0 }, rotation: { pitch_deg: 0, roll_deg: 0, yaw_deg: 0 } };
const ego = { actor_id: "ego", transform, velocity_mps: { x: 2, y: 0, z: 0 }, acceleration_mps2: null,
  bounding_box_extent: null, bounding_box_transform: null, speed_limit_mps: null };
const frame = index => ({ frame: index, time_s: index * .05, ego, actors: [], control, collisions: [], traffic_violations: [], rule_checks: [] });
function record(index) {
  return { frame: index, time_s: index * .05, status: "applied", control_mode: "authoritative", sensors: [],
    observation: { protocol_version: "1.3", observation_mode: "simulator_state", frame: index, time_s: index * .05, dt_s: .05,
      ego, actors: [], sensors: [], target_speed_mps: 4, route_heading_deg: null, traffic_light_red: null, scene: null, driving_task: null,
      ego_state_source: "simulator_state", sensor_source: null },
    agent_result: { protocol_version: "1.3", frame: index, control, diagnostics: { maneuver: "yield", reason_codes: ["obstacle"], hazard_actor_ids: [],
      target_speed_mps: null, assistance: [], planned_trajectory: [], metadata: { confidence: .8 } } }, proposed_control: control, applied_control: control,
    interventions: [], worker_step_wall_time_s: .01, round_trip_wall_time_s: .012, full_step_wall_time_s: .02,
    resulting_frame: index + 1, resulting_ego: ego };
}
function page(offset = 0, limit = 10, total = 12) {
  const count = Math.min(limit, Math.max(0, total - offset));
  return { simulation_id: ID, available: true, coverage: "complete", total, applied: total, transitions: total, offset, limit, reset: null,
    recording_artifact_index: null, records: Array.from({ length: count }, (_, i) => record(offset + i)),
    telemetry: count ? Array.from({ length: count + 1 }, (_, i) => frame(offset + i)) : [] };
}
function unavailable() { return { ...page(0, 10, 0), available: false, coverage: "unavailable" }; }
function unresolved() {
  const data = page(0, 10, 1);
  data.applied = data.transitions = 0;
  data.coverage = "partial";
  data.telemetry.pop();
  Object.assign(data.records[0], { status: "unresolved", proposed_control: null, applied_control: null, agent_result: null, resulting_frame: null,
    resulting_ego: null, full_step_wall_time_s: null, worker_step_wall_time_s: null, round_trip_wall_time_s: null });
  return data;
}
const text = node => typeof node === "string" || typeof node === "number" ? String(node) : (node?.children ?? []).map(text).join("");
function button(view, label) { const found = view.root.findAllByType("button").find(node => text(node) === label); assert.ok(found, `Missing ${label}`); return found; }
async function click(view, label) { await act(async () => { await button(view, label).props.onClick(); }); }
async function mount(t, props = {}) { let view; await act(async () => { view = TestRenderer.create(React.createElement(DecisionTimeline, { simulationId: ID, active: true, ...props })); }); t.after(() => act(() => view.unmount())); return view; }
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { resolve, reject, promise }; };
const response = body => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });

test("decision API preserves exact frame associations, pages, identity and abort signal", async () => {
  const client = createApiClient("/api"); const controller = new AbortController();
  const payload = page(10, 2);
  globalThis.fetch = async (url, options) => { assert.equal(url, `/api/simulations/${ID}/decisions?limit=2&offset=10`); assert.equal(options.signal, controller.signal); return response(payload); };
  assert.deepEqual(await client.getDecisions(ID, { offset: 10, limit: 2 }, { signal: controller.signal }), payload);
});

test("decision API rejects mismatched clocks, output, diagnostics, counts and sensor leaks", async () => {
  const client = createApiClient("/api");
  for (const mutate of [
    data => { data.simulation_id = OTHER; }, data => { data.offset = 2; }, data => { data.records[0].resulting_frame = 8; },
    data => { data.records[0].observation.frame = 3; }, data => { data.records[0].agent_result.frame = 7; },
    data => { data.records[0].applied_control = { ...control, brake: 1 }; }, data => { data.records[0].agent_result.diagnostics.reason_codes = "unsafe shape"; },
    data => { data.telemetry = []; }, data => { data.applied = 13; }, data => { data.coverage = "empty"; },
    data => { data.records[0].worker_step_wall_time_s = -1; }, data => { data.records[0].observation.sensors = [{ data_base64: "unsafe" }]; },
  ]) {
    const data = page(); mutate(data); globalThis.fetch = async () => response(data);
    await assert.rejects(client.getDecisions(ID, { limit: 10 }), ApiError);
  }
  globalThis.fetch = async () => { throw new Error("must reject before fetch"); };
  for (const query of [{ limit: 101 }, { limit: 0 }, { offset: -1 }, { limit: 1.5 }]) await assert.rejects(client.getDecisions(ID, query), ApiError);
});

test("unavailable and unresolved timelines are accepted without inventing actions", async () => {
  const client = createApiClient("/api");
  for (const data of [unavailable(), unresolved(), { ...page(0, 10, 0), coverage: "empty" }]) {
    globalThis.fetch = async () => response(data);
    assert.deepEqual(await client.getDecisions(ID, { limit: 10 }), data);
  }
});

test("selected frame synchronizes input, controls, diagnostics and measured telemetry across pages", async t => {
  const requests = [];
  api.getDecisions = async (id, query) => { requests.push({ id, ...query }); return page(query.offset); };
  const view = await mount(t);
  assert.match(text(view.root), /Input 0 → result 1/);
  assert.match(text(view.root), /Agent-reported diagnostics/);
  assert.match(text(view.root), /do not establish its internal reasoning/);
  await click(view, "Frame 3");
  assert.match(text(view.root), /Input 3 → result 4/);
  assert.match(text(view.root), /Input time 0.150 s/);
  assert.equal(requests.length, 1);
  await click(view, "Next decisions");
  assert.match(text(view.root), /Input 10 → result 11/);
  assert.equal(button(view, "Next decisions").props.disabled, true);
  await click(view, "Previous decisions");
  assert.match(text(view.root), /Input 0 → result 1/);
  assert.deepEqual(requests.map(item => item.offset), [0, 10, 0]);
});

test("older runs and partial failed steps explicitly describe evidence gaps", async t => {
  api.getDecisions = async () => unavailable();
  const view = await mount(t);
  assert.match(text(view.root), /cannot be reconstructed retroactively/);
  api.getDecisions = async () => unresolved();
  await click(view, "Refresh timeline");
  assert.match(text(view.root), /Evidence coverage: partial/);
  assert.match(text(view.root), /Input 0 → unresolved action/);
  assert.match(text(view.root), /No action or reasoning is inferred/);
  assert.match(text(view.root), /No recorded control/);
});

test("late requests are aborted and cannot replace another selected run", async t => {
  const pending = deferred(); let signal;
  api.getDecisions = async (id, query, options) => { if (id === ID) { signal = options.signal; return pending.promise; } return { ...unavailable(), simulation_id: OTHER }; };
  const view = await mount(t);
  await act(async () => view.update(React.createElement(DecisionTimeline, { simulationId: OTHER, active: true })));
  assert.equal(signal.aborted, true);
  await act(async () => pending.resolve(page()));
  assert.match(text(view.root), /unavailable for this run/);
  assert.doesNotMatch(text(view.root), /Input 0 →/);
});

test("inactive timeline does not read; active request failure can be retried", async t => {
  let reads = 0;
  api.getDecisions = async () => { reads++; throw new ApiError("Temporary API error"); };
  const view = await mount(t, { active: false });
  assert.equal(reads, 0);
  await act(async () => view.update(React.createElement(DecisionTimeline, { simulationId: ID, active: true })));
  assert.match(text(view.root), /Temporary API error/);
  api.getDecisions = async () => page();
  await click(view, "Refresh timeline");
  assert.match(text(view.root), /Input 0 → result 1/);
});

test("sensor preview fetches only on demand and verifies the selected input bytes", async t => {
  const bytes = Uint8Array.from([10, 20, 30, 255]).buffer;
  const data = page(0, 10, 1);
  data.recording_artifact_index = 0;
  data.records[0].observation = { ...data.records[0].observation, observation_mode: "sensors", actors: null,
    sensor_source: "deterministic_fixture", ego_state_source: "simulated_ego_kinematics" };
  data.records[0].sensors = [{ sample_index: 2, byte_length: 4, header: { sensor_id: "front", type: "sensor.camera.rgb", frame: 0,
    timestamp_s: 0, encoding: "bgra8", width: 1, height: 1, fov_deg: 80, channels: null, sha256: createHash("sha256").update(new Uint8Array(bytes)).digest("hex") } }];
  api.getDecisions = async () => data;
  let reads = 0;
  api.getCaptureSample = async (id, artifact, index) => { assert.deepEqual([id, artifact, index], [ID, 0, 2]); reads++; return bytes; };
  const view = await mount(t);
  assert.equal(reads, 0);
  assert.match(text(view.root), /Deterministic fixture inputs/);
  await click(view, "Inspect recorded input");
  assert.equal(reads, 1);
  for (let i = 0; i < 50 && !text(view.root).includes("Input SHA-256 and byte length verified"); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)); });
  assert.match(text(view.root), /Input SHA-256 and byte length verified/);
  assert.ok(view.root.findByType("canvas"));
  api.getCaptureSample = async () => Uint8Array.from([0, 0, 0, 0]).buffer;
  await click(view, "Inspect recorded input");
  for (let i = 0; i < 50 && !text(view.root).includes("Could not verify or decode"); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)); });
  assert.match(text(view.root), /Could not verify or decode/);
  assert.equal(view.root.findAllByType("canvas").length, 0);
});
