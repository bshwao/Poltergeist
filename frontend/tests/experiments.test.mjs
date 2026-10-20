import assert from "node:assert/strict";
import { after, test } from "node:test";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const loaded = await loadComponents("pages/Experiments.tsx");
const { Experiments } = loaded.module;
const { api } = await loaded.moduleAt("services/api.ts");
const { experimentInput, emptyExperiment } = await loaded.moduleAt("components/experiment-form.ts");
const { routeFromHash } = await loaded.moduleAt("navigation.ts");
const { experimentContracts } = await loaded.moduleAt("services/experiment-contract.ts");
after(() => loaded.cleanup());
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const text = n => typeof n === "string" || typeof n === "number" ? String(n) : (n?.children ?? []).map(text).join("");
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const draft = () => ({ ...emptyExperiment(), recipe: '{"name":"Fixture"}' });
function record(overrides = {}) { return { id: id(1), request: { kind: "reproduce", failure_id: id(2), repeats: 3, required_successes: null }, execution: { mode: "test", options: {}, evaluation_settings: {} }, agent_snapshot: null, comparison_agents: [], source_failure: { id: id(2) }, revision: 4, status: "interrupted", cancel_requested: false, created_at: "2026-10-20T12:00:00Z", started_at: "2026-10-20T12:00:01Z", finished_at: "2026-10-20T12:00:02Z", error: null, engine_version: "test-v1", outcome: null, ...overrides }; }
function button(view, label) { return view.root.findAllByType("button").find(n => text(n) === label); }
async function setup(t, props = {}) {
  api.listAgents = async () => [];
  api.listOptimizations = async () => [];
  api.getOptimization = async () => ({ operation: record(), trials: [] });
  const oldWindow = globalThis.window;
  globalThis.window = { location: { hash: "#experiments" } };
  let view;
  await act(async () => { view = TestRenderer.create(React.createElement(Experiments, { selectedId: null, templates: [], ...props })); });
  t.after(() => { act(() => view.unmount()); if (oldWindow === undefined) delete globalThis.window; else globalThis.window = oldWindow; });
  return view;
}

test("comparison plan matches seed/repetition cells and preserves selected agent order", () => {
  const input = experimentInput({ ...draft(), agents: [id(3), "baseline"], repeats: "2", seeds: "7, 9" });
  assert.deepEqual(input.agents, [id(3), null]); assert.deepEqual(input.seeds, [7, 9]); assert.equal(input.repeats, 2); assert.equal(input.mode, "test");
});
test("plans reject duplicate seeds, excessive budgets, nonfinite values and ambiguous searches", () => {
  for (const change of [{ seeds: "0,0" }, { seeds: "" }, { seeds: "-1" }, { repeats: "1.2" }, { agents: [] }, { agents: ["bad"] }, { options: '{"x":[1e999]}' }, { recipe: "[]" }, { seeds: Array.from({ length: 20 }, (_, i) => i).join(","), repeats: "10", agents: ["baseline", id(1)] }, { kind: "search", agents: ["baseline", id(2)] }]) assert.throws(() => experimentInput({ ...draft(), ...change }));
});
test("source operations inherit execution and agent identity instead of accepting form overrides", () => {
  assert.deepEqual(experimentInput({ ...draft(), kind: "reproduce", failureId: id(1), repeats: "3", required: "2", mode: "carla", agents: [id(7)] }), { kind: "reproduce", failure_id: id(1), repeats: 3, required_successes: 2 });
  assert.throws(() => experimentInput({ ...draft(), kind: "minimize", failureId: id(1), repeats: "3", trials: "2" }));
});
test("experiment routes preserve owned IDs and reject extra path segments", () => {
  assert.equal(routeFromHash(`#experiments/${id(1)}`).operationId, id(1));
  assert.equal(routeFromHash(`#experiments/minimize/${id(2)}`).failureId, id(2));
  assert.equal(routeFromHash(`#experiments/reproduce/${id(2)}`).experimentKind, "reproduce");
  assert.equal(routeFromHash(`#experiments/${id(1)}/extra`).operationId, null);
});
test("contract rejects another operation's trial, nonfinite scores and invalid lifecycle", () => {
  const validate = experimentContracts({ scenario: () => true, options: () => true, evaluation: () => true, snapshot: () => true, failure: () => true });
  const operation = record();
  assert.equal(validate.view({ operation, trials: [] }, id(1)), true);
  assert.equal(validate.view({ operation, trials: [] }, id(9)), false);
  assert.equal(validate.record({ ...operation, finished_at: null }), false);
  const trial = { operation_id: id(1), number: 0, simulation_id: id(3), candidate_index: 0, repeat_index: 0, agent_index: 0, role: "reproduce", scenario: {}, assessed: false, matches: null, score: null, failure_id: null, reason: null };
  assert.equal(validate.view({ operation, trials: [trial] }, id(1)), true);
  assert.equal(validate.view({ operation, trials: [{ ...trial, operation_id: id(9) }] }, id(1)), false);
  assert.equal(validate.view({ operation, trials: [{ ...trial, score: Infinity }] }, id(1)), false);
});
test("hidden page starts no requests and invalid form never submits", async t => {
  const view = await setup(t, { active: false });
  let submissions = 0; api.createOptimization = async () => { submissions++; };
  await act(async () => view.root.findByType("form").props.onSubmit({ preventDefault() {} }));
  assert.equal(submissions, 0); assert.match(text(view.root), /Scenario recipe must be valid JSON/);
});
test("failure deep link fills the source and resume uses the observed revision exactly once", async t => {
  const view = await setup(t, { selectedId: id(1), sourceFailureId: id(2), initialKind: "reproduce" });
  const pending = deferred(); let count = 0;
  api.resumeOptimization = (owner, revision) => { count++; assert.equal(owner, id(1)); assert.equal(revision, 4); return pending.promise; };
  assert.ok(view.root.findAllByType("input").some(n => n.props.value === id(2)));
  await act(async () => { button(view, "Resume remaining trials").props.onClick(); });
  assert.equal(count, 1); assert.equal(button(view, "Resume remaining trials").props.disabled, true);
  await act(async () => pending.reject(new Error("Connection lost")));
  assert.match(text(view.root), /may have been accepted/); assert.match(text(view.root), /Refresh experiment history before retrying/); assert.equal(count, 1);
});
test("switching selected experiments aborts and ignores stale reads", async t => {
  const view = await setup(t);
  const pending = deferred(); let signal;
  api.getOptimization = (owner, options) => { if (owner === id(1)) { signal = options.signal; return pending.promise; } return Promise.resolve({ operation: record({ id: id(3) }), trials: [] }); };
  await act(async () => view.update(React.createElement(Experiments, { active: true, selectedId: id(1), templates: [] })));
  await act(async () => view.update(React.createElement(Experiments, { active: true, selectedId: id(3), templates: [] })));
  assert.equal(signal.aborted, true);
  await act(async () => pending.resolve({ operation: record(), trials: [] }));
  const codes = view.root.findAllByType("code").map(text);
  assert.ok(codes.includes(id(3))); assert.ok(!codes.includes(id(1)));
});

test("comparison review preserves unavailable observations and links each trial's evidence", async t => {
  const view = await setup(t);
  const operation = record({ status: "completed", request: { kind: "compare" }, source_failure: null });
  const trial = { number: 0, role: "compare", agent_index: 0, scenario: { seed: 7 }, repeat_index: 0, assessed: true, matches: null, reason: "Collision observation unavailable", simulation_id: id(3), failure_id: null };
  const evidence = { operation, trials: [trial] };
  api.getOptimization = async () => evidence;
  api.getComparison = async () => ({ evidence, groups: [{ agent_index: 0, name: "Fixture agent", version: "1", completed_runs: 1, planned_runs: 2, matching_runs: 0, negative_runs: 0, unknown_runs: 1, unassessed_runs: 0, partial_coverage_results: 1, status_counts: { completed: 1 }, input_sources: { synthetic_state: 1 }, task_outcomes: { not_recorded: 1 }, metrics: { collision_count: { unit: "episodes", observed_results: 0, eligible_results: 1, mean: null, minimum: null, maximum: null, sample_stddev: null } } }], paired_comparisons: [], notes: ["Partial coverage is explicit."] });
  await act(async () => view.update(React.createElement(Experiments, { selectedId: id(1), templates: [] })));
  assert.match(text(view.root), /1 unknown/);
  assert.match(text(view.root), /0 \/ 1UnavailableUnavailableUnavailableUnavailable/);
  assert.ok(view.root.findAllByType("a").some(a => a.props.href === `#simulations/${id(3)}`));
  assert.ok(view.root.findAllByType("a").some(a => a.props.href.endsWith(`/optimizations/${id(1)}/comparison?download=true`)));
});
