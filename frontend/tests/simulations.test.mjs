import assert from "node:assert/strict";
import { after, test } from "node:test";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const loaded = await loadComponents("pages/Simulations.tsx");
const { Simulations, RunDetail } = loaded.module;
const { useSimulation } = await loaded.moduleAt("hooks/useSimulation.ts");
const { api, ApiError } = await loaded.moduleAt("services/api.ts");
after(() => loaded.cleanup());

const id = number => `00000000-0000-4000-8000-${String(number).padStart(12, "0")}`;
const timestamp = "2026-10-16T12:00:00Z";
const recipe = { name: "Selected recipe", description: "Repeatable scene", map: "Town03", seed: 7, duration_s: 1, fixed_delta_seconds: 0.05, actors: [], events: [], parameters: {}, weather: {} };
const coverage = { total_frames: 0, collision_frames: 0, traffic_rule_frames: 0, braking_frames: 0, clearance_frames: 0, ttc_frames: 0, actor_pairs: 0, clearance_pairs: 0, ttc_pairs: 0, has_gaps: false };
function record(number = 1, status = "running", revision = 1) {
  const simulation = { id: id(number), status, mode: "test", agent: "baseline", scenario: { ...recipe, name: `Recipe ${number}` }, progress: status === "completed" ? 1 : status === "queued" ? 0 : 0.25,
    created_at: timestamp, started_at: status === "queued" ? null : timestamp, finished_at: ["completed", "failed", "cancelled"].includes(status) ? timestamp : null, error: status === "failed" ? "Execution stopped" : null };
  return { simulation, revision, created_at: timestamp, updated_at: timestamp, scenario_id: null, job: { options: {}, evaluation_settings: {}, cancel_requested: false, queue_position: status === "queued" ? 1 : null }, result: null };
}
function complete(number = 1, revision = 3) {
  const value = record(number, "completed", revision);
  value.result = { simulation: value.simulation, measured_duration_s: 0, telemetry_frames: 0, runtime: null, artifacts: [], metrics: {
    severity: null, collision_count: null, min_ttc_s: null, min_clearance_m: null, max_deceleration_mps2: null, hard_braking_count: null, traffic_violation_count: null, max_stopping_time_s: null, max_stopping_distance_m: null, coverage, evaluation_settings: null,
  } };
  return value;
}
function progress(value, fields = {}) {
  return { simulation_id: value.simulation.id, status: value.simulation.status, progress: value.simulation.progress, revision: value.revision,
    started_at: value.simulation.started_at, finished_at: value.simulation.finished_at, cancel_requested: value.job?.cancel_requested ?? false, queue_position: value.job?.queue_position ?? null, error: value.simulation.error, ...fields };
}
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function text(node) {
  if (typeof node === "string" || typeof node === "number") return String(node);
  return (node?.children ?? []).map(text).join("");
}
const button = (root, label) => root.findAllByType("button").find(node => text(node) === label);
function field(root, labelText) {
  const label = root.findAllByType("label").find(node => text(node) === labelText);
  assert.ok(label, `Missing field ${labelText}`);
  return root.findByProps({ id: label.props.htmlFor });
}
function defaults() {
  api.getSimulation = async () => complete();
  api.getSimulationProgress = async () => { throw new Error("Unexpected progress poll"); };
  api.getSimulationResult = async () => { throw new Error("Unexpected result request"); };
  api.cancelSimulation = async () => { throw new Error("Unexpected cancellation"); };
  api.listSimulations = async () => [];
  api.getTelemetry = async () => ({ frames: [], total: 0, offset: 0, limit: 200 });
}
async function hook(t, resourceId = id(1), active = true) {
  let state;
  function Harness(props) { state = useSimulation(props.id, props.active); return null; }
  let view;
  await act(async () => { view = TestRenderer.create(React.createElement(Harness, { id: resourceId, active })); });
  t.after(() => act(() => view.unmount()));
  return { get state() { return state; }, async update(nextId, nextActive = true) { await act(async () => view.update(React.createElement(Harness, { id: nextId, active: nextActive }))); } };
}
async function mount(t, Component, props) {
  let view;
  await act(async () => { view = TestRenderer.create(React.createElement(Component, props)); });
  t.after(() => act(() => view.unmount()));
  return view;
}

test("a terminal run with persisted output does not poll progress or request duplicate results", async t => {
  defaults();
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let reads = 0;
  api.getSimulation = async () => { reads++; return complete(); };
  const mounted = await hook(t);
  assert.equal(mounted.state.record.simulation.status, "completed");
  assert.equal(mounted.state.record.result.metrics.severity, null);
  assert.equal(mounted.state.loading, false);
  await act(async () => t.mock.timers.tick(30_000));
  assert.equal(reads, 1);
});

test("running progress polls serially and fetches terminal output once when the run finishes", async t => {
  defaults();
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let reads = 0, polls = 0, resultReads = 0;
  const final = complete();
  api.getSimulation = async () => ++reads === 1 ? record() : { ...final, result: null };
  api.getSimulationProgress = async () => ++polls === 1 ? progress(record(1, "running", 2), { progress: 0.5 }) : progress(final);
  api.getSimulationResult = async () => { resultReads++; return final.result; };
  const mounted = await hook(t);
  assert.equal(mounted.state.progress.progress, 0.5);
  assert.equal(polls, 1);
  await act(async () => t.mock.timers.tick(1_200));
  assert.equal(mounted.state.record.simulation.status, "completed");
  assert.ok(mounted.state.record.result);
  assert.equal(reads, 2);
  assert.equal(resultReads, 1);
  await act(async () => t.mock.timers.tick(30_000));
  assert.equal(polls, 2);
});

test("switching the selected ID aborts the old request and ignores a late response", async t => {
  defaults();
  const old = deferred();
  let oldSignal;
  api.getSimulation = async (resourceId, options) => resourceId === id(1) ? (oldSignal = options.signal, old.promise) : complete(2);
  const mounted = await hook(t);
  await mounted.update(id(2));
  assert.equal(oldSignal.aborted, true);
  assert.equal(mounted.state.record.simulation.id, id(2));
  await act(async () => old.resolve(complete(1)));
  assert.equal(mounted.state.record.simulation.id, id(2));
});

test("hidden detail aborts outstanding reads and makes no background requests", async t => {
  defaults();
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const pending = deferred();
  let signal, reads = 0;
  api.getSimulation = async (_id, options) => { reads++; signal = options.signal; return pending.promise; };
  const mounted = await hook(t);
  await mounted.update(id(1), false);
  assert.equal(signal.aborted, true);
  await act(async () => { pending.resolve(complete()); t.mock.timers.tick(30_000); });
  assert.equal(reads, 1);
  assert.equal(mounted.state.record, null);
  assert.equal(mounted.state.loading, false);
});

test("cancellation acknowledges a request and an older in-flight poll cannot clear that observation", async t => {
  defaults();
  const older = deferred();
  const accepted = record(1, "running", 4);
  accepted.job.cancel_requested = true;
  let reads = 0, polls = 0, oldSignal, cancellations = 0;
  api.getSimulation = async () => ++reads === 1 ? record() : accepted;
  api.getSimulationProgress = async (_id, options) => ++polls === 1 ? (oldSignal = options.signal, older.promise) : progress(accepted);
  api.cancelSimulation = async () => { cancellations++; return accepted; };
  const mounted = await hook(t);
  await act(async () => mounted.state.cancel());
  assert.equal(cancellations, 1);
  assert.equal(oldSignal.aborted, true);
  assert.equal(mounted.state.record.simulation.status, "running");
  assert.equal(mounted.state.record.job.cancel_requested, true);
  await act(async () => older.resolve(progress(record(1, "running", 2))));
  assert.equal(mounted.state.record.revision, 4);
  assert.equal(mounted.state.progress.cancel_requested, true);
});

test("a newer progress observation remains visible while a manual refresh is pending", async t => {
  defaults();
  const next = deferred();
  let reads = 0;
  api.getSimulation = async () => ++reads === 1 ? record() : next.promise;
  api.getSimulationProgress = async () => progress(record(1, "running", 3), { progress: 0.75 });
  const mounted = await hook(t);
  assert.equal(mounted.state.progress.revision, 3);
  await act(async () => mounted.state.refresh());
  assert.equal(mounted.state.loading, true);
  assert.equal(mounted.state.progress?.revision, 3);
  assert.equal(mounted.state.progress?.progress, 0.75);
  await act(async () => next.resolve(complete(1, 4)));
});

test("stale terminal progress cannot stop polling a newer running record", async t => {
  defaults();
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let polls = 0, reads = 0;
  api.getSimulation = async () => ++reads === 1 ? record(1, "running", 5) : complete(1, 4);
  api.getSimulationProgress = async () => ++polls === 1 ? progress(complete(1, 4)) : progress(record(1, "running", 6), { progress: 0.6 });
  const mounted = await hook(t);
  assert.equal(mounted.state.record.revision, 5);
  await act(async () => t.mock.timers.tick(1_200));
  assert.equal(polls, 2);
  assert.equal(mounted.state.progress?.revision, 6);
  assert.equal(mounted.state.record.simulation.status, "running");
});

test("a missing run waits for an explicit retry", async t => {
  defaults();
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let reads = 0;
  api.getSimulation = async () => { if (++reads === 1) throw new ApiError("Run not found", 404); return complete(); };
  const mounted = await hook(t);
  assert.equal(mounted.state.error, "Run not found");
  await act(async () => t.mock.timers.tick(30_000));
  assert.equal(reads, 1);
  await act(async () => mounted.state.refresh());
  assert.equal(reads, 2);
  assert.equal(mounted.state.record.simulation.status, "completed");
  assert.equal(mounted.state.error, null);
});

test("transient progress failures retain the last measurement and retry with backoff", async t => {
  defaults();
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let polls = 0;
  api.getSimulation = async () => record();
  api.getSimulationProgress = async () => {
    polls++;
    if (polls === 2) throw new ApiError("Worker temporarily unavailable", 503);
    return progress(record(1, "running", polls + 1), { progress: polls === 1 ? 0.5 : 0.75 });
  };
  const mounted = await hook(t);
  await act(async () => t.mock.timers.tick(1_200));
  assert.equal(mounted.state.error, "Worker temporarily unavailable");
  assert.equal(mounted.state.progress.progress, 0.5);
  await act(async () => t.mock.timers.tick(2_499));
  assert.equal(polls, 2);
  await act(async () => t.mock.timers.tick(1));
  assert.equal(polls, 3);
  assert.equal(mounted.state.progress.progress, 0.75);
  assert.equal(mounted.state.error, null);
});

test("pending cancellation suppresses duplicates, aborts on hiding, and ignores a late acknowledgement", async t => {
  defaults();
  const pending = deferred();
  let signal, cancellations = 0;
  api.getSimulation = async () => record();
  api.getSimulationProgress = async () => progress(record());
  api.cancelSimulation = async (_id, options) => { cancellations++; signal = options.signal; return pending.promise; };
  const mounted = await hook(t);
  let first;
  await act(async () => { first = mounted.state.cancel(); void mounted.state.cancel(); });
  assert.equal(cancellations, 1);
  assert.equal(mounted.state.cancelling, true);
  await mounted.update(id(1), false);
  assert.equal(signal.aborted, true);
  const accepted = record(1, "cancelled", 2);
  accepted.job.cancel_requested = true;
  await act(async () => { pending.resolve(accepted); await first; });
  assert.equal(mounted.state.record.simulation.status, "running");
  assert.equal(mounted.state.cancelling, false);
});

test("lost cancellation acknowledgement warns about uncertainty and never resubmits automatically", async t => {
  defaults();
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let cancellations = 0;
  api.getSimulation = async () => record();
  api.getSimulationProgress = async () => progress(record());
  api.cancelSimulation = async () => { cancellations++; throw new ApiError("Lost connection", 0); };
  const mounted = await hook(t);
  await act(async () => mounted.state.cancel());
  assert.match(mounted.state.cancelError, /could not be confirmed.*may have reached the worker/);
  assert.equal(mounted.state.record.simulation.status, "running");
  assert.equal(mounted.state.cancelling, false);
  await act(async () => t.mock.timers.tick(1_200));
  assert.equal(cancellations, 1);
});

test("run history pages use lookahead and reset paging when mode or status filters change", async t => {
  defaults();
  const calls = [];
  api.listSimulations = async (filters, options) => { calls.push({ filters, signal: options.signal }); return Array.from({ length: 11 }, (_, index) => record(index + 1)); };
  const view = await mount(t, Simulations, { active: true, selectedId: null });
  assert.deepEqual(calls[0].filters, { limit: 11, offset: 0 });
  assert.equal(view.root.findAllByType("tbody")[0].findAllByType("tr").length, 10);
  assert.equal(button(view.root, "Previous runs").props.disabled, true);
  assert.equal(button(view.root, "Next runs").props.disabled, false);
  await act(async () => button(view.root, "Next runs").props.onClick());
  assert.deepEqual(calls.at(-1).filters, { limit: 11, offset: 10 });
  await act(async () => field(view.root, "Execution mode").props.onChange({ target: { value: "test" } }));
  assert.deepEqual(calls.at(-1).filters, { limit: 11, offset: 0, mode: "test" });
  await act(async () => field(view.root, "Status").props.onChange({ target: { value: "completed" } }));
  assert.deepEqual(calls.at(-1).filters, { limit: 11, offset: 0, mode: "test", status: "completed" });
  assert.match(text(view.root), /Unscored/);
  assert.match(text(view.root), /Synthetic test/);
  assert.equal(calls[0].signal.aborted, true);
});

test("history filter changes abort old reads and ignore stale response pages", async t => {
  defaults();
  const old = deferred();
  let signal;
  api.listSimulations = async (filters, options) => filters.mode ? [record(2)] : (signal = options.signal, old.promise);
  const view = await mount(t, Simulations, { active: true, selectedId: null });
  await act(async () => field(view.root, "Execution mode").props.onChange({ target: { value: "carla" } }));
  assert.equal(signal.aborted, true);
  assert.match(text(view.root), /Recipe 2/);
  await act(async () => old.resolve([record(1)]));
  assert.doesNotMatch(text(view.root), /Recipe 1/);
  assert.match(text(view.root), /Recipe 2/);
});

test("history errors expose retry and selection links open the requested run detail", async t => {
  defaults();
  let lists = 0;
  api.listSimulations = async () => { if (++lists === 1) throw new ApiError("History unavailable", 503); return [complete(2)]; };
  api.getSimulation = async resourceId => { assert.equal(resourceId, id(2)); return complete(2); };
  const view = await mount(t, Simulations, { active: true, selectedId: id(2) });
  assert.match(text(view.root), /History unavailable/);
  await act(async () => button(view.root, "Retry history").props.onClick());
  assert.ok(view.root.findAllByType("a").some(node => node.props.href.includes(id(2))));
  assert.equal(view.root.findAllByType("tr").filter(node => node.props.className === "selected-row").length, 1);
  assert.match(text(view.root), /Completion alone does not establish safety/);
  assert.equal(view.root.findAllByProps({ "aria-label": "Selected simulation" }).length, 1);
});

test("run detail keeps cooperative cancellation visible as a request while execution is running", async t => {
  defaults();
  const accepted = record(1, "running", 2);
  accepted.job.cancel_requested = true;
  let cancelCount = 0;
  api.getSimulation = async () => cancelCount ? accepted : record();
  api.getSimulationProgress = async () => progress(cancelCount ? accepted : record());
  api.cancelSimulation = async () => { cancelCount++; return accepted; };
  const view = await mount(t, RunDetail, { id: id(1), active: true });
  await act(async () => button(view.root, "Cancel run").props.onClick());
  assert.match(text(view.root), /API recorded a cancellation request. The run is still running/);
  assert.equal(button(view.root, "Cancellation requested").props.disabled, true);
  assert.doesNotMatch(text(view.root), /Execution has ended/);
});

test("manual records without worker jobs cannot issue cancellation requests", async t => {
  defaults();
  const manual = { ...record(), job: null };
  api.getSimulation = async () => manual;
  api.getSimulationProgress = async () => progress(manual);
  const view = await mount(t, RunDetail, { id: id(1), active: true });
  assert.match(text(view.root), /manually stored record has no API job to cancel/);
  assert.equal(button(view.root, "Cancel run"), undefined);
});

const agentSnapshot = {
  registration_id: id(901), package_path: 'C:/agents/original-pilot', python_executable: 'C:/agents/env/python.exe',
  python_sha256: 'a'.repeat(64), python_size_bytes: 100, package_sha256: 'b'.repeat(64),
  observation_mode: 'simulator_state', agent_control_mode: 'authoritative',
  manifest: { name: 'Recorded driving pilot', version: '2.1', protocol_version: '1.3', agent_id: 'pilot',
    entry_point: 'agent:Driver', observation_mode: 'simulator_state', navigation_mode: 'provided_route',
    control_mode: 'vehicle_control', sensors: [], assets: [], configuration: { target: 4 } },
  configuration: { target: 4 }, files: [{ path: 'agent.py', sha256: 'c'.repeat(64), size_bytes: 100 }],
  model_files: [], runtime_files: [], process_options: { load_timeout_s: 30, step_timeout_s: 2 },
};

test('history and run detail preserve the recorded agent identity without consulting its current registration', async t => {
  defaults();
  const saved = complete();
  saved.simulation.agent = 'custom'; saved.simulation.agent_snapshot = agentSnapshot;
  saved.result.agent_inputs = { source: 'synthetic_state', assistance: [] };
  api.listSimulations = async () => [saved];
  api.getSimulation = async () => saved;
  let registryReads = 0;
  api.getAgent = async () => { registryReads++; throw new ApiError('Registration has been deactivated', 409); };
  const view = await mount(t, Simulations, { active: true, selectedId: id(1) });
  assert.ok(view.root.findAllByType('thead').some(node => /Agent/.test(text(node))));
  assert.match(text(view.root), /Recorded driving pilot/);
  assert.match(text(view.root), /v2.1/);
  assert.match(text(view.root), /Registration 00000000/);
  const details = view.root.findByProps({ 'aria-label': 'Recorded agent' });
  assert.match(text(details), /C:\/agents\/original-pilot/);
  assert.match(text(details), /Recorded input source: Synthetic state/);
  assert.match(text(details), /Agent controls the ego vehicle/);
  assert.match(text(details), /v2.1/);
  assert.equal(registryReads, 0);
});

test('older custom runs display unavailable identity instead of inferring a current agent', async t => {
  defaults();
  const saved = complete(); saved.simulation.agent = 'custom';
  api.getSimulation = async () => saved;
  const view = await mount(t, RunDetail, { id: id(1), active: true });
  const details = view.root.findByProps({ 'aria-label': 'Recorded agent' });
  assert.match(text(details), /Version and input mode not recorded/);
  assert.match(text(details), /cannot be inferred/);
});
