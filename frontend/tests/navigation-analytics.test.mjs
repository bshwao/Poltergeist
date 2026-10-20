import assert from "node:assert/strict";
import { after, test } from "node:test";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const loaded = await loadComponents("components/AnalyticsPanel.tsx");
const { AnalyticsPanel } = loaded.module;
const { api, ApiError } = await loaded.moduleAt("services/api.ts");
const { routeFromHash, simulationLink } = await loaded.moduleAt("navigation.ts");
after(() => loaded.cleanup());
const id = "00000000-0000-4000-8000-000000000001";
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const text = node => typeof node === "string" || typeof node === "number" ? String(node) : (node?.children ?? []).map(text).join("");
const button = view => view.root.findByType("button");
async function mount(t, active = true) {
  let view;
  await act(async () => { view = TestRenderer.create(React.createElement(AnalyticsPanel, { active })); });
  t.after(() => act(() => view.unmount()));
  return view;
}
const group = mode => ({ mode, total_runs: 5, status_counts: { completed: 3, failed: 2 }, result_count: 5, scored_runs: 2, unscored_results: 3,
  mean_severity: null, collision_runs: 0, collision_observed_runs: 2, traffic_violation_runs: 0, traffic_observed_runs: 0, failure_reports: 0, failure_kinds: {} });

test("hash routes accept only known pages and complete UUID run IDs", () => {
  assert.deepEqual(routeFromHash("#overview"), { page: "overview", simulationId: null });
  assert.deepEqual(routeFromHash("#failures"), { page: "failures", simulationId: null });
  assert.deepEqual(routeFromHash("#agents"), { page: "agents", simulationId: null });
  assert.deepEqual(routeFromHash(simulationLink(id)), { page: "simulations", simulationId: id });
  for (const hash of ["#simulations/../../private", "#simulations/javascript:alert(1)", `#simulations/${id}/extra`, "#simulations"]) assert.deepEqual(routeFromHash(hash), { page: "simulations", simulationId: null });
  assert.deepEqual(routeFromHash("#unknown"), { page: "scenarios", simulationId: null });
  assert.equal(simulationLink("invalid"), "#simulations");
});

test("analytics reads history only on explicit request and preserves separate modes and unknown means", async t => {
  let calls = 0;
  api.getAnalytics = async (mode, options) => { calls++; assert.equal(mode, undefined); assert.ok(options.signal); return { groups: [group("carla"), group("test")] }; };
  const view = await mount(t);
  assert.equal(calls, 0);
  await act(async () => { await button(view).props.onClick(); });
  assert.equal(calls, 1);
  const cards = view.root.findAllByType("article");
  assert.equal(cards.length, 2);
  assert.match(text(cards[0]), /CARLA/);
  assert.match(text(cards[1]), /Synthetic test/);
  for (const card of cards) {
    assert.match(text(card), /Mean severityUnknown/);
    assert.match(text(card), /Runs with collisions0 \/ 2 observed/);
    assert.match(text(card), /Runs with traffic violations0 \/ 0 observed/);
  }
  assert.match(text(view.root), /Partial positive observations can be scored/);
});

test("analytics hides and aborts an in-flight read and cannot adopt its late response", async t => {
  const pending = deferred();
  let signal;
  api.getAnalytics = async (_, options) => { signal = options.signal; return pending.promise; };
  const view = await mount(t);
  await act(async () => { button(view).props.onClick(); });
  assert.equal(button(view).props.disabled, true);
  await act(async () => view.update(React.createElement(AnalyticsPanel, { active: false })));
  assert.equal(signal.aborted, true);
  await act(async () => pending.resolve({ groups: [group("test")] }));
  assert.equal(view.root.findAllByType("article").length, 0);
  await act(async () => view.update(React.createElement(AnalyticsPanel, { active: true })));
  assert.equal(button(view).props.disabled, false);
});

test("analytics refresh failures retain a labeled prior snapshot and allow explicit retry", async t => {
  api.getAnalytics = async () => ({ groups: [{ ...group("test"), mean_severity: 0 }] });
  const view = await mount(t);
  await act(async () => { await button(view).props.onClick(); });
  api.getAnalytics = async () => { throw new ApiError("API temporarily unavailable.", 503); };
  await act(async () => { await button(view).props.onClick(); });
  assert.match(text(view.root), /API temporarily unavailable. Previous snapshot shown below/);
  assert.match(text(view.root), /Mean severity0.0 \/ 100/);
  api.getAnalytics = async () => ({ groups: [] });
  await act(async () => { await button(view).props.onClick(); });
  assert.match(text(view.root), /No recorded history is available/);
  assert.equal(view.root.findAllByProps({ role: "alert" }).length, 0);
});
