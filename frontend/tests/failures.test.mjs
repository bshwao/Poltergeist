import assert from "node:assert/strict";
import { after, test } from "node:test";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const loaded = await loadComponents("pages/Failures.tsx");
const { Failures } = loaded.module;
const { api } = await loaded.moduleAt("services/api.ts");
const { comparisonNote, fullCoverage, observation, reproductionLabel, settingsDifferences } = await loaded.moduleAt("services/failure-view.ts");
after(() => loaded.cleanup());

const id = number => `00000000-0000-4000-8000-${String(number).padStart(12, "0")}`;
function failure(number, reproduced = null) {
  return { id: id(number), simulation_id: id(number + 100), kind: "near_miss", severity: 45, reproduced, summary: "A measured near miss.", created_at: "2026-10-16T12:00:00Z", scenario: { name: `Recipe ${number}`, map: "Town03", seed: number, actors: [], events: [], parameters: {} } };
}
function run(report, mode = "test") {
  return {
    simulation: { id: report.simulation_id, status: "completed", mode, agent: "baseline", scenario: report.scenario },
    job: { options: { max_ticks: 200 }, evaluation_settings: { method_version: "1.0", ttc_warning_s: 3 } },
    result: { measured_duration_s: 2, telemetry_frames: 5, runtime: null, metrics: {
      severity: 45, collision_count: 0, min_ttc_s: 1.2, min_clearance_m: 0.5, hard_braking_count: 0, max_deceleration_mps2: 0, traffic_violation_count: 0, max_stopping_time_s: null, max_stopping_distance_m: null,
      evaluation_settings: { method_version: "1.0", ttc_warning_s: 3 },
      coverage: { total_frames: 5, has_gaps: false, collision_frames: 5, braking_frames: 5, clearance_frames: 5, ttc_frames: 5, traffic_rule_frames: 5, actor_pairs: 10, clearance_pairs: 10, ttc_pairs: 10 },
    } },
  };
}
function text(node) {
  if (typeof node === "string" || typeof node === "number") return String(node);
  return (node?.children ?? []).map(text).join("");
}
const button = (root, label) => root.findAllByType("button").find(node => text(node) === label);
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
async function mount(t, { records = [failure(1), failure(2, false), failure(3, true)], active = true, list, getFailure, getSimulation } = {}) {
  api.listFailures = list ?? (async () => records);
  api.getFailure = getFailure ?? (async resourceId => records.find(item => item.id === resourceId));
  api.getSimulation = getSimulation ?? (async resourceId => run(records.find(item => item.simulation_id === resourceId)));
  api.updateReproduction = async () => { throw new Error("Unexpected evidence mutation"); };
  let view;
  await act(async () => { view = TestRenderer.create(React.createElement(Failures, { active })); });
  t.after(() => act(() => view.unmount()));
  return view;
}
async function open(view, number) {
  await act(async () => { view.root.findByProps({ "aria-label": `Open failure ${id(number)}` }).props.onClick(); });
}
async function compare(view, number) {
  await act(async () => { view.root.findByProps({ "aria-label": `Compare failure ${id(number)}` }).props.onChange(); });
}

test("reproduction evidence preserves true, false, and unknown independently", () => {
  assert.equal(reproductionLabel(true), "Confirmed");
  assert.equal(reproductionLabel(false), "Not reproduced");
  assert.equal(reproductionLabel(null), "Unknown");
  assert.equal(observation(0), "0");
  assert.equal(observation(null), "Unknown");
  assert.equal(observation(undefined), "Unknown");
});

test("complete coverage requires frame channels, pair channels, and no gaps", () => {
  const metrics = run(failure(1)).result.metrics;
  assert.equal(fullCoverage(metrics), true);
  for (const field of ["collision_frames", "braking_frames", "clearance_frames", "ttc_frames", "traffic_rule_frames", "clearance_pairs", "ttc_pairs"]) {
    const partial = structuredClone(metrics);
    partial.coverage[field] -= 1;
    assert.equal(fullCoverage(partial), false, field);
  }
  assert.equal(fullCoverage({ ...metrics, coverage: { ...metrics.coverage, has_gaps: true } }), false);
  assert.equal(fullCoverage({ ...metrics, coverage: { ...metrics.coverage, total_frames: 0 } }), false);
});

test("comparison rejects modes, settings, partial traces, unscored and unfinished runs", () => {
  const left = run(failure(1));
  assert.equal(comparisonNote(left, run(failure(2))).comparable, true);
  assert.match(comparisonNote(left, run(failure(2), "carla")).message, /Different execution modes/);
  for (const change of [
    value => { value.simulation.agent = "custom"; },
    value => { value.job.options.max_ticks = 400; },
    value => { value.job = null; },
    value => { value.result.metrics.evaluation_settings.ttc_warning_s = 5; },
    value => { value.result.metrics.evaluation_settings.method_version = "2.0"; },
    value => { value.result.metrics.evaluation_settings = null; },
    value => { value.result.metrics.coverage.has_gaps = true; },
    value => { value.result.metrics.coverage.ttc_pairs = 0; },
    value => { value.result.metrics.severity = null; },
    value => { value.simulation.status = "failed"; value.result.metrics.severity = 100; },
    value => { value.result = null; },
  ]) {
    const right = run(failure(2)); change(right);
    assert.equal(comparisonNote(left, right).comparable, false);
  }
});

test("settings differences report actual fields and tolerate object ordering", () => {
  assert.deepEqual(settingsDifferences({ method_version: "1.0", ttc_warning_s: 3 }, { ttc_warning_s: 3, method_version: "1.0" }), []);
  assert.deepEqual(settingsDifferences({ method_version: "1.0", ttc_warning_s: 3 }, { method_version: "1.0", ttc_warning_s: 4 }), ["ttc warning s: 3 → 4"]);
  assert.match(settingsDifferences(null, null)[0], /unavailable/);
});

test("failure list fetches bounded lookahead pages and shows all three evidence states", async t => {
  const calls = [];
  const view = await mount(t, { list: async filter => { calls.push(filter); return Array.from({ length: 9 }, (_, index) => failure(index + 1, index === 0 ? true : index === 1 ? false : null)); } });
  assert.deepEqual(calls, [{ limit: 9, offset: 0, reproduced: "all" }]);
  assert.equal(view.root.findAllByType("tbody")[0].findAllByType("tr").length, 8);
  assert.match(text(view.root), /Confirmed/); assert.match(text(view.root), /Not reproduced/); assert.match(text(view.root), /Unknown/);
  assert.equal(button(view.root, "Previous failure page").props.disabled, true);
  await act(async () => button(view.root, "Next failure page").props.onClick());
  assert.equal(calls.at(-1).offset, 8);
  await act(async () => view.root.findByProps({ id: "failure-reproduction" }).props.onChange({ target: { value: "unknown" } }));
  assert.deepEqual(calls.at(-1), { limit: 9, offset: 0, reproduced: "unknown" });
});

test("run filter validates UUIDs before requesting and applies to the whole query", async t => {
  const calls = [];
  const view = await mount(t, { list: async filter => { calls.push(filter); return []; } });
  act(() => view.root.findByProps({ id: "failure-simulation" }).props.onChange({ target: { value: "not-an-id" } }));
  await act(async () => view.root.findByType("form").props.onSubmit({ preventDefault() {} }));
  assert.equal(calls.length, 1); assert.match(text(view.root), /Enter a valid simulation ID/);
  act(() => view.root.findByProps({ id: "failure-simulation" }).props.onChange({ target: { value: ` ${id(101)} ` } }));
  await act(async () => view.root.findByType("form").props.onSubmit({ preventDefault() {} }));
  assert.equal(calls.at(-1).simulation_id, id(101));
  assert.match(text(view.root), /No failure reports on this page/);
});

test("hidden pages fetch nothing; pending list requests abort and stale responses are discarded", async t => {
  const pending = deferred(); let requests = 0; let signal;
  const view = await mount(t, { active: false, list: async (_filter, options) => { requests += 1; signal = options.signal; return requests === 1 ? pending.promise : [failure(2)]; } });
  assert.equal(requests, 0);
  await act(async () => view.update(React.createElement(Failures, { active: true })));
  assert.equal(requests, 1);
  await act(async () => view.update(React.createElement(Failures, { active: false })));
  assert.equal(signal.aborted, true);
  await act(async () => { pending.resolve([failure(1)]); });
  assert.equal(view.root.findAllByProps({ "aria-label": `Open failure ${id(1)}` }).length, 0);
  await act(async () => view.update(React.createElement(Failures, { active: true })));
  assert.equal(requests, 2); assert.equal(view.root.findAllByProps({ "aria-label": `Open failure ${id(2)}` }).length, 1);
});

test("filter changes discard old list responses even if the transport ignores abort", async t => {
  const pending = deferred(); let calls = 0; let oldSignal;
  const view = await mount(t, { list: async (_filter, { signal }) => { calls += 1; if (calls === 1) { oldSignal = signal; return pending.promise; } return [failure(2, false)]; } });
  await act(async () => view.root.findByProps({ id: "failure-reproduction" }).props.onChange({ target: { value: "false" } }));
  assert.equal(oldSignal.aborted, true);
  await act(async () => pending.resolve([failure(1)]));
  assert.equal(view.root.findAllByProps({ "aria-label": `Open failure ${id(1)}` }).length, 0);
  assert.equal(view.root.findAllByProps({ "aria-label": `Open failure ${id(2)}` }).length, 1);
});

test("detail fetches fresh report plus actual parent and renders zero, unknown, coverage, recipe and settings", async t => {
  const report = failure(1);
  const calls = [];
  const view = await mount(t, { records: [report], getFailure: async resourceId => { calls.push(resourceId); return { ...report, summary: "Fresh report" }; }, getSimulation: async resourceId => { calls.push(resourceId); const result = run(report); result.result.metrics.min_ttc_s = null; result.result.metrics.coverage.collision_frames = 3; return result; } });
  await open(view, 1);
  assert.deepEqual(calls, [report.id, report.simulation_id]);
  const detail = view.root.findByProps({ className: "panel failure-detail" });
  assert.match(text(detail), /Fresh report/); assert.match(text(detail), /Synthetic test/); assert.match(text(detail), /Zero observed events does not establish absence/);
  assert.match(text(detail), /Observed collisions0/); assert.match(text(detail), /Minimum TTC \(s\)Unknown/); assert.match(text(detail), /Collision frames3 \/ 5/);
  assert.match(text(detail), /"seed": 1/); assert.match(text(detail), /"max_ticks": 200/); assert.match(text(detail), /Runtime provenance is unavailable/);
  const links = detail.findAllByType("a").map(a => a.props.href);
  assert.ok(links.includes(`#simulations/${report.simulation_id}`));
  assert.ok(links.includes(`#experiments/reproduce/${report.id}`));
  assert.ok(links.includes(`#experiments/minimize/${report.id}`));
});

test("switching reports aborts the prior detail and cannot start its parent request after hide", async t => {
  const old = deferred(); let signal; let parentCalls = 0;
  const view = await mount(t, { getFailure: async (resourceId, options) => { if (resourceId === id(1)) { signal = options.signal; return old.promise; } return failure(2); }, getSimulation: async resourceId => { parentCalls += 1; return run(failure(2)); } });
  await open(view, 1); await open(view, 2);
  assert.equal(signal.aborted, true);
  await act(async () => old.resolve(failure(1)));
  assert.equal(parentCalls, 1);
  assert.match(text(view.root.findByProps({ className: "panel failure-detail" })), /"seed": 2/);
});

test("detail failure has a retry and does not invent parent metrics", async t => {
  let attempts = 0;
  const view = await mount(t, { getFailure: async () => { attempts += 1; if (attempts === 1) throw new Error("Unavailable report"); return failure(1); } });
  await open(view, 1); assert.match(text(view.root), /Unavailable report/);
  assert.equal(view.root.findByProps({ className: "panel failure-detail" }).findAllByType("tbody").length, 0);
  await act(async () => button(view.root, "Retry report detail").props.onClick());
  assert.match(text(view.root.findByProps({ className: "panel failure-detail" })), /Observed collisions0/);
});

test("comparison fetches parent metrics, distinguishes modes, and limits selection to two", async t => {
  const view = await mount(t, { getSimulation: async resourceId => run(failure(resourceId === id(101) ? 1 : 2), resourceId === id(101) ? "test" : "carla") });
  await compare(view, 1); await compare(view, 2);
  assert.equal(view.root.findByProps({ "aria-label": `Compare failure ${id(3)}` }).props.disabled, true);
  assert.match(text(view.root), /Different execution modes — compare observations separately/);
  assert.equal(view.root.findAllByProps({ className: "data-table comparison-table" }).length, 1);
  assert.match(text(view.root), /No risk reduction is inferred/);
  await act(async () => button(view.root, "Clear comparison").props.onClick());
  assert.equal(view.root.findByProps({ "aria-label": `Compare failure ${id(3)}` }).props.disabled, false);
  assert.equal(view.root.findAllByProps({ className: "data-table comparison-table" }).length, 0);
});

test("comparison cannot display a late result after selection is removed", async t => {
  const pending = deferred(); let signal;
  const view = await mount(t, { getFailure: async (resourceId, options) => { signal = options.signal; return resourceId === id(1) ? pending.promise : failure(2); } });
  await compare(view, 1); await compare(view, 2);
  await act(async () => button(view.root, "Clear comparison").props.onClick());
  assert.equal(signal.aborted, true);
  await act(async () => pending.resolve(failure(1)));
  assert.equal(view.root.findAllByProps({ className: "data-table comparison-table" }).length, 0);
  assert.match(text(view.root), /0 of 2 reports selected/);
});

test("manual reproduction saves only the chosen tri-state flag without starting a run", async t => {
  const view = await mount(t);
  await open(view, 1);
  const calls = [];
  api.updateReproduction = async (resourceId, reproduced) => { calls.push([resourceId, reproduced]); return failure(1, reproduced); };
  api.launchSimulation = async () => { throw new Error("No reruns are authorized by an evidence update"); };
  const select = () => view.root.findByProps({ id: `reproduction-observation-${id(1)}` });
  assert.equal(calls.length, 0);
  for (const [value, expected] of [["false", false], ["true", true], ["unknown", null]]) {
    act(() => select().props.onChange({ target: { value } }));
    await act(async () => { view.root.findByProps({ className: "reproduction-observation" }).props.onSubmit({ preventDefault() {} }); });
    assert.deepEqual(calls.at(-1), [id(1), expected]);
    assert.match(text(view.root), /No simulation was run/);
  }
});

test("late manual update cannot overwrite a newly selected report and aborts on unmount", async t => {
  const view = await mount(t);
  await open(view, 1);
  const pending = deferred(); let signal; let calls = 0;
  api.updateReproduction = async (_id, _value, options) => { calls += 1; signal = options.signal; return pending.promise; };
  const form = view.root.findByProps({ className: "reproduction-observation" });
  act(() => { form.props.onSubmit({ preventDefault() {} }); form.props.onSubmit({ preventDefault() {} }); });
  assert.equal(calls, 1);
  await open(view, 2);
  assert.equal(signal.aborted, true);
  await act(async () => pending.resolve(failure(1, true)));
  const detail = view.root.findByProps({ className: "panel failure-detail" });
  assert.match(text(detail), /"seed": 2/); assert.match(text(detail), /ReproductionNot reproduced/);
  assert.doesNotMatch(text(detail), /Saved manual observation/);
});

test("manual evidence updates refresh filtered pages without changing the open report", async t => {
  let report = failure(1, false);
  const requests = [];
  const view = await mount(t, {
    list: async filter => { requests.push(filter); return filter.reproduced === "false" && report.reproduced !== false ? [] : [report]; },
    getFailure: async () => report,
    getSimulation: async () => run(report),
  });
  await act(async () => view.root.findByProps({ id: "failure-reproduction" }).props.onChange({ target: { value: "false" } }));
  await open(view, 1);
  api.updateReproduction = async () => { report = { ...report, reproduced: true }; return report; };
  act(() => view.root.findByProps({ id: `reproduction-observation-${id(1)}` }).props.onChange({ target: { value: "true" } }));
  await act(async () => view.root.findByProps({ className: "reproduction-observation" }).props.onSubmit({ preventDefault() {} }));
  assert.deepEqual(requests.at(-1), { limit: 9, offset: 0, reproduced: "false" });
  assert.equal(requests.length, 3);
  assert.equal(view.root.findAllByProps({ "aria-label": `Open failure ${id(1)}` }).length, 0);
  assert.match(text(view.root.findByProps({ className: "panel failure-detail" })), /ReproductionConfirmed/);
  assert.match(text(view.root), /Saved manual observation: Confirmed/);
});

test("hiding the page aborts a pending evidence PATCH without sending another mutation", async t => {
  const view = await mount(t);
  await open(view, 1);
  const pending = deferred(); let signal; let writes = 0;
  api.updateReproduction = async (_id, _value, options) => { signal = options.signal; writes += 1; return pending.promise; };
  act(() => view.root.findByProps({ className: "reproduction-observation" }).props.onSubmit({ preventDefault() {} }));
  await act(async () => view.update(React.createElement(Failures, { active: false })));
  assert.equal(signal.aborted, true);
  await act(async () => pending.resolve(failure(1, true)));
  assert.equal(writes, 1);
  assert.doesNotMatch(text(view.root), /Saved manual observation/);
  await act(async () => view.update(React.createElement(Failures, { active: true })));
  assert.equal(writes, 1);
});

test("list errors retry explicitly and unmount aborts requests", async t => {
  let attempts = 0;
  const view = await mount(t, { list: async () => { attempts += 1; if (attempts === 1) throw new Error("Offline"); return [failure(1)]; } });
  assert.match(text(view.root), /Offline/);
  await act(async () => button(view.root, "Retry reports").props.onClick());
  assert.equal(attempts, 2);
  const pending = deferred(); let signal;
  api.listFailures = async (_filter, options) => { signal = options.signal; return pending.promise; };
  await act(async () => button(view.root, "Refresh reports").props.onClick());
  act(() => view.unmount());
  assert.equal(signal.aborted, true);
  await act(async () => pending.resolve([]));
});

test('different registered agents or unknown custom identities cannot be labelled matched', () => {
  const left = run(failure(1)); const right = run(failure(2));
  left.simulation.agent = right.simulation.agent = 'custom';
  assert.equal(comparisonNote(left, right).comparable, false);
  left.simulation.agent_snapshot = { registration_id: id(10), configuration: { brake: .5 }, observation_mode: 'simulator_state' };
  right.simulation.agent_snapshot = { registration_id: id(11), configuration: { brake: .5 }, observation_mode: 'simulator_state' };
  assert.match(comparisonNote(left, right).message, /Different registered agents/);
  right.simulation.agent_snapshot = structuredClone(left.simulation.agent_snapshot);
  assert.equal(comparisonNote(left, right).comparable, true);
  right.simulation.agent_snapshot.configuration.brake = .8;
  assert.equal(comparisonNote(left, right).comparable, false);
});
