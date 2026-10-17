import assert from "node:assert/strict";
import { after, test } from "node:test";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const loaded = await loadComponents("components/LaunchPanel.tsx");
const { LaunchPanel } = loaded.module;
const { api, ApiError } = await loaded.moduleAt("services/api.ts");
const originalAnimationFrame = globalThis.requestAnimationFrame;
globalThis.requestAnimationFrame = callback => { callback(0); return 0; };
after(async () => {
  if (originalAnimationFrame === undefined) delete globalThis.requestAnimationFrame;
  else globalThis.requestAnimationFrame = originalAnimationFrame;
  await loaded.cleanup();
});

const recipe = {
  name: "Intersection test", description: "A recipe", map: "Town03", seed: 4,
  duration_s: 1, fixed_delta_seconds: 0.05, template: "intersection",
  actors: [{ id: "ego", type: "ego_vehicle", speed_mps: 5 }], events: [], parameters: {}, weather: {},
};
const accepted = (status = "queued", position = null) => ({
  simulation: { id: "894d8ac8-43aa-4111-9bf3-deec7f039f6f", status, mode: "test", scenario: structuredClone(recipe) },
  job: { queue_position: position }, result: null,
});
function text(node) {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (node === null || node === undefined) return "";
  return (node.children ?? []).map(text).join("");
}
function button(root, startsWith) {
  return root.findAllByType("button").find(node => text(node).startsWith(startsWith));
}
function field(root, labelText) {
  const label = root.findAllByType("label").find(node => text(node) === labelText);
  assert.ok(label, `Missing field: ${labelText}`);
  return root.findByProps({ id: label.props.htmlFor });
}
function checkbox(root, labelText) {
  const label = root.findAllByType("label").find(node => text(node).includes(labelText));
  assert.ok(label, `Missing checkbox: ${labelText}`);
  return label.findByType("input");
}
function setup(t, props = {}) {
  api.getSimulationQueue = async () => { throw new Error("Unexpected queue request"); };
  api.launchSimulation = async () => { throw new Error("Unexpected launch request"); };
  let view;
  act(() => { view = TestRenderer.create(React.createElement(LaunchPanel, { scenario: structuredClone(recipe), disabledReason: null, ...props })); });
  t.after(() => act(() => view.unmount()));
  return view;
}
async function submit(view) {
  await act(async () => { await view.root.findByType("form").props.onSubmit({ preventDefault() {} }); });
}
function choose(view, mode) {
  act(() => button(view.root, mode === "test" ? "Synthetic test" : "CARLA").props.onClick());
}
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test("queue inspection is manual and never starts on mount", async t => {
  const view = setup(t);
  let checks = 0;
  api.getSimulationQueue = async () => {
    checks += 1;
    return { accepting: true, active_id: null, error: null, pending: 0, started: true, worker_alive: true };
  };
  assert.equal(checks, 0);
  assert.match(text(view.root), /Queue state has not been checked/);
  await act(async () => { await button(view.root, "Check queue").props.onClick(); });
  assert.equal(checks, 1);
  assert.match(text(view.root), /API accepting submissions · 0 waiting · No active run/);
  assert.match(text(view.root), /does not confirm that CARLA is connected/);
});

test("launch requires an explicit mode choice and does not POST until it is selected", async t => {
  const view = setup(t);
  let launches = 0;
  api.launchSimulation = async () => { launches += 1; return accepted(); };
  assert.equal(button(view.root, "CARLA").props["aria-pressed"], false);
  assert.equal(button(view.root, "Synthetic test").props["aria-pressed"], false);
  await submit(view);
  assert.equal(launches, 0);
  assert.match(text(view.root), /Choose CARLA or synthetic test mode explicitly/);
  choose(view, "test");
  await submit(view);
  assert.equal(launches, 1);
});

test("choosing test mode clears CARLA captures and disables their controls", async t => {
  const view = setup(t);
  choose(view, "carla");
  act(() => {
    checkbox(view.root, "Capture camera").props.onChange({ target: { checked: true } });
    checkbox(view.root, "Capture LiDAR").props.onChange({ target: { checked: true } });
  });
  assert.equal(checkbox(view.root, "Capture camera").props.checked, true);
  assert.equal(checkbox(view.root, "Capture LiDAR").props.checked, true);
  choose(view, "test");
  for (const label of ["Capture camera", "Capture LiDAR"]) {
    assert.equal(checkbox(view.root, label).props.checked, false);
    assert.equal(checkbox(view.root, label).props.disabled, true);
  }
  assert.match(text(view.root), /capture were turned off when you selected synthetic test mode/);
  let sent;
  api.launchSimulation = async input => { sent = input; return accepted(); };
  await submit(view);
  assert.equal(sent.options.camera, false);
  assert.equal(sent.options.lidar, false);
});

test("dirty invalid numeric and cross-field settings show inline and prevent POST", async t => {
  const view = setup(t);
  choose(view, "test");
  let launches = 0;
  api.launchSimulation = async () => { launches += 1; return accepted(); };
  act(() => field(view.root, "Maximum ticks").props.onChange({ target: { value: "" } }));
  assert.equal(field(view.root, "Maximum ticks").props["aria-invalid"], true);
  assert.match(text(view.root), /Enter a value/);
  await submit(view);
  assert.equal(launches, 0);
  assert.equal(view.root.findAllByType("details")[0].props.open, true);
  act(() => {
    field(view.root, "Maximum ticks").props.onChange({ target: { value: "200" } });
    field(view.root, "Time-to-collision warning (s)").props.onChange({ target: { value: "12" } });
    field(view.root, "Braking weight (%)").props.onChange({ target: { value: "16" } });
  });
  assert.equal(field(view.root, "Time-to-collision horizon (s)").props["aria-invalid"], true);
  assert.match(text(view.root), /horizon must cover the warning/);
  assert.match(text(view.root), /weights must sum to 100%/);
  await submit(view);
  assert.equal(launches, 0);
  assert.equal(view.root.findAllByType("details")[1].props.open, true);
});

test("an accepted queued run shows its ID and acceptance status without asserting completion", async t => {
  const view = setup(t);
  choose(view, "test");
  api.launchSimulation = async () => accepted("queued", null);
  await submit(view);
  const receipt = view.root.findByProps({ className: "launch-receipt notice" });
  assert.match(text(receipt), /Run accepted/);
  assert.match(text(receipt), /894d8ac8-43aa-4111-9bf3-deec7f039f6f/);
  assert.match(text(receipt), /Status at acceptancequeued/);
  assert.match(text(receipt), /Queue position at acceptanceNot available/);
  assert.match(text(receipt), /does not track its subsequent progress or outcome/);
  assert.doesNotMatch(text(receipt), /completed|successful/i);
});

test("a late acceptance remains visible after the recipe changes and retains the submitted snapshot", async t => {
  const view = setup(t);
  choose(view, "test");
  const pending = deferred();
  let input;
  let launchPromise;
  api.launchSimulation = async request => { input = request; return pending.promise; };
  act(() => { launchPromise = view.root.findByType("form").props.onSubmit({ preventDefault() {} }); });
  assert.equal(button(view.root, "Submitting run").props.disabled, true);
  const nextRecipe = { ...structuredClone(recipe), name: "Changed recipe", seed: 100 };
  act(() => view.update(React.createElement(LaunchPanel, { scenario: nextRecipe, disabledReason: null })));
  await act(async () => { pending.resolve(accepted()); await launchPromise; });
  assert.equal(input.scenario.name, recipe.name);
  assert.equal(input.scenario.seed, 4);
  const receipt = view.root.findByProps({ className: "launch-receipt notice" });
  assert.match(text(receipt), /Run accepted for a prior recipe/);
  assert.match(text(receipt), /Intersection test/);
  assert.match(text(receipt), /894d8ac8/);
});

test("queue full and unavailable responses are clear rejections while lost responses warn before retry", async t => {
  const view = setup(t);
  choose(view, "test");
  for (const [status, message] of [[429, "The simulation queue is full."], [503, "The service is temporarily unavailable."]]) {
    api.launchSimulation = async () => { throw new ApiError(message, status); };
    await submit(view);
    assert.match(text(view.root), /Run was not accepted/);
    assert.match(text(view.root), new RegExp(message.replace(/[.]/g, "\\.")));
    assert.doesNotMatch(text(view.root), /Check run history before retrying/);
  }
  for (const status of [0, 202]) {
    api.launchSimulation = async () => { throw new ApiError("The response could not be read.", status); };
    await submit(view);
    assert.match(text(view.root), /Run acceptance is uncertain/);
    assert.match(text(view.root), /Check run history before retrying/);
    assert.match(text(view.root), /may have accepted this run/);
  }
});

test("synchronous duplicate submissions share one POST and do not automatically retry", async t => {
  const view = setup(t);
  choose(view, "test");
  const pending = deferred();
  let launches = 0;
  let first;
  let second;
  api.launchSimulation = async () => { launches += 1; return pending.promise; };
  const submitHandler = view.root.findByType("form").props.onSubmit;
  act(() => {
    first = submitHandler({ preventDefault() {} });
    second = submitHandler({ preventDefault() {} });
  });
  assert.equal(launches, 1);
  await act(async () => { pending.resolve(accepted("queued", 1)); await Promise.all([first, second]); });
  assert.equal(launches, 1);
  assert.match(text(view.root), /Status at acceptancequeued/);
  assert.match(text(view.root), /Queue position at acceptance1/);
});

test("a stale recipe disables launching and an unmount aborts outstanding requests", async t => {
  const view = setup(t, { scenario: null, disabledReason: "Validate your JSON edits before launching." });
  assert.equal(button(view.root, "Launch simulation").props.disabled, true);
  assert.match(text(view.root), /Validate your JSON edits before launching/);
  const queuePending = deferred();
  let queueSignal;
  let request;
  api.getSimulationQueue = async ({ signal }) => { queueSignal = signal; return queuePending.promise; };
  act(() => { request = button(view.root, "Check queue").props.onClick(); });
  act(() => view.unmount());
  assert.equal(queueSignal.aborted, true);
  await act(async () => { queuePending.resolve({ accepting: false, active_id: null, error: null, pending: 0 }); await request; });
});

test("unmount aborts a pending launch without resubmitting it", async t => {
  const view = setup(t);
  choose(view, "test");
  const pending = deferred();
  let signal;
  let launches = 0;
  let request;
  api.launchSimulation = async (_input, options) => { signal = options.signal; launches += 1; return pending.promise; };
  act(() => { request = view.root.findByType("form").props.onSubmit({ preventDefault() {} }); });
  act(() => view.unmount());
  assert.equal(signal.aborted, true);
  await act(async () => { pending.resolve(accepted()); await request; });
  assert.equal(launches, 1);
});
