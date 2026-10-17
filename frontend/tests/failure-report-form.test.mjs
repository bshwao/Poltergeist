import assert from "node:assert/strict";
import { after, test } from "node:test";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const loaded = await loadComponents("components/FailureReportForm.tsx");
const { FailureReportForm, eligibleFailureKinds } = loaded.module;
const { api, ApiError } = await loaded.moduleAt("services/api.ts");
const { DEFAULT_EVALUATION } = await loaded.moduleAt("components/launch-form.ts");
after(() => loaded.cleanup());
const id = number => `00000000-0000-4000-8000-${String(number).padStart(12, "0")}`;
function result(number = 1) {
  return {
    simulation: { id: id(number), status: "completed", mode: "test", scenario: { name: `Recipe ${number}`, seed: number, map: "Town03", actors: [{ id: "ego", speed_mps: 5 }], events: [], parameters: {} } },
    metrics: { severity: 45, collision_count: 0, min_ttc_s: 1.2, min_clearance_m: 0.5, hard_braking_count: 0, traffic_violation_count: 0, evaluation_settings: { ...DEFAULT_EVALUATION } },
  };
}
function text(node) { return typeof node === "string" || typeof node === "number" ? String(node) : (node?.children ?? []).map(text).join(""); }
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function setup(t, props = {}) {
  api.createFailure = async () => { throw new Error("Unexpected report POST"); };
  let view;
  act(() => { view = TestRenderer.create(React.createElement(FailureReportForm, { result: result(), ...props })); });
  t.after(() => act(() => view.unmount()));
  return view;
}
function describe(view, value = "A measured near miss.") { act(() => view.root.findByType("textarea").props.onChange({ target: { value } })); }
async function submit(view) { await act(async () => view.root.findByType("form").props.onSubmit({ preventDefault() {} })); }

test("eligible kinds use positive evidence and exact recorded near-miss thresholds", () => {
  const source = result();
  assert.deepEqual(eligibleFailureKinds(source), ["near_miss"]);
  source.metrics.evaluation_settings.ttc_warning_s = 1;
  source.metrics.evaluation_settings.clearance_warning_m = 0.5;
  assert.deepEqual(eligibleFailureKinds(source), []);
  source.metrics.collision_count = 1; source.metrics.hard_braking_count = 2; source.metrics.traffic_violation_count = 3;
  assert.deepEqual(eligibleFailureKinds(source), ["collision", "hard_braking", "traffic_violation"]);
  source.metrics.evaluation_settings = null;
  assert.deepEqual(eligibleFailureKinds(source), ["collision", "near_miss", "hard_braking", "traffic_violation"]);
});

test("unscored, nonterminal, and absent observations never create eligible evidence", () => {
  for (const status of ["queued", "running"]) { const source = result(); source.simulation.status = status; assert.deepEqual(eligibleFailureKinds(source), []); }
  const unknown = result(); unknown.metrics.severity = null; unknown.metrics.collision_count = 1;
  assert.deepEqual(eligibleFailureKinds(unknown), []);
  const noEvidence = result();
  for (const key of ["collision_count", "hard_braking_count", "traffic_violation_count", "min_ttc_s", "min_clearance_m"]) noEvidence.metrics[key] = null;
  assert.deepEqual(eligibleFailureKinds(noEvidence), []);
  for (const status of ["failed", "cancelled"]) { const source = result(); source.simulation.status = status; source.metrics.collision_count = 1; source.metrics.severity = 100; assert.ok(eligibleFailureKinds(source).includes("collision")); }
});

test("mounting or editing never posts; empty summaries require an explicit description", async t => {
  const view = setup(t);
  let calls = 0; api.createFailure = async report => { calls += 1; return report; };
  assert.equal(calls, 0);
  await submit(view);
  assert.equal(calls, 0); assert.match(text(view.root), /Describe the observed failure/);
  describe(view); assert.equal(calls, 0);
});

test("creating a report preserves exact recipe and severity with unknown reproduction", async t => {
  const source = result();
  const view = setup(t, { result: source });
  let sent;
  api.createFailure = async report => { sent = report; return report; };
  describe(view, "  Vehicle passed too close.  ");
  await submit(view);
  assert.match(sent.id, /^[0-9a-f-]{36}$/);
  assert.equal(sent.simulation_id, source.simulation.id); assert.deepEqual(sent.scenario, source.simulation.scenario);
  assert.notEqual(sent.scenario, source.simulation.scenario); assert.equal(sent.severity, 45); assert.equal(sent.reproduced, null);
  assert.equal(sent.summary, "Vehicle passed too close."); assert.equal(sent.kind, "near_miss"); assert.ok(Number.isFinite(Date.parse(sent.created_at)));
  assert.match(text(view.root), /Failure report saved/); assert.match(text(view.root), /Reproduction Unknown/);
  assert.equal(view.root.findByType("a").props.href, "#failures"); assert.equal(view.root.findAllByType("form").length, 0);
});

test("scored zero remains zero and only eligible kinds are selectable", async t => {
  const source = result(); source.metrics.severity = 0; source.metrics.collision_count = 1;
  const view = setup(t, { result: source });
  assert.deepEqual(view.root.findAllByType("option").map(node => node.props.value), ["collision", "near_miss"]);
  let sent; api.createFailure = async report => { sent = report; return report; };
  describe(view); await submit(view); assert.equal(sent.severity, 0);
});

test("unknown scores and absent positive evidence render without a submission form", t => {
  const source = result(); source.metrics.severity = null;
  const view = setup(t, { result: source });
  assert.equal(view.root.findAllByType("form").length, 0); assert.match(text(view.root), /no scored positive failure evidence/);
});

test("duplicate submits use one immutable report and one POST", async t => {
  const source = result(); const view = setup(t, { result: source });
  const pending = deferred(); let calls = 0; let sent;
  api.createFailure = async report => { calls += 1; sent = report; return pending.promise; };
  describe(view); const form = view.root.findByType("form");
  act(() => { form.props.onSubmit({ preventDefault() {} }); form.props.onSubmit({ preventDefault() {} }); });
  assert.equal(calls, 1);
  source.simulation.scenario.actors[0].speed_mps = 100;
  assert.equal(sent.scenario.actors[0].speed_mps, 5);
  await act(async () => pending.resolve(sent)); assert.equal(calls, 1);
});

test("changed run IDs discard late report receipts and preserve the new form", async t => {
  const view = setup(t); const pending = deferred(); let signal; let sent;
  api.createFailure = async (report, options) => { signal = options.signal; sent = report; return pending.promise; };
  describe(view); act(() => view.root.findByType("form").props.onSubmit({ preventDefault() {} }));
  act(() => view.update(React.createElement(FailureReportForm, { result: result(2) })));
  assert.equal(signal.aborted, true);
  describe(view, "New run description");
  await act(async () => pending.resolve(sent));
  assert.equal(view.root.findByType("textarea").props.value, "New run description"); assert.doesNotMatch(text(view.root), /Failure report saved/);
});

test("hidden pages abort writes, retain uncertainty advice, and never retry automatically", async t => {
  const source = result(); const view = setup(t, { result: source }); const pending = deferred(); let signal; let calls = 0;
  api.createFailure = async (_report, options) => { signal = options.signal; calls += 1; return pending.promise; };
  describe(view); act(() => view.root.findByType("form").props.onSubmit({ preventDefault() {} }));
  act(() => view.update(React.createElement(FailureReportForm, { result: source, active: false })));
  assert.equal(signal.aborted, true); assert.match(text(view.root), /Check Failure reports before creating another report/);
  await act(async () => pending.resolve({}));
  act(() => view.update(React.createElement(FailureReportForm, { result: source, active: true })));
  assert.equal(calls, 1); assert.doesNotMatch(text(view.root), /Failure report saved/);
});

test("uncertain responses direct users to history while validation rejection stays explicit", async t => {
  const view = setup(t); describe(view);
  api.createFailure = async () => { throw new ApiError("Response lost", 0); };
  await submit(view); assert.match(text(view.root), /Check Failure reports before retrying; the API may have saved this report/);
  api.createFailure = async () => { throw new ApiError("No evidence", 422); };
  await submit(view); assert.match(text(view.root), /No evidence No report was accepted/);
});

test("unmount aborts outstanding creation and ignores its late response", async t => {
  const view = setup(t); const pending = deferred(); let signal;
  api.createFailure = async (_report, options) => { signal = options.signal; return pending.promise; };
  describe(view); act(() => view.root.findByType("form").props.onSubmit({ preventDefault() {} }));
  act(() => view.unmount()); assert.equal(signal.aborted, true);
  await act(async () => pending.resolve({}));
});
