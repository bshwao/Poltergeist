import assert from "node:assert/strict";
import { after, test } from "node:test";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";
import { readinessFixture } from "./readiness-fixture.mjs";

const loaded = await loadComponents("components/ReadinessPanel.tsx");
const { ReadinessPanel } = loaded.module;
const { api, ApiError } = await loaded.moduleAt("services/api.ts");
after(() => loaded.cleanup());
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
const text = node => typeof node === "string" || typeof node === "number" ? String(node) : (node?.children ?? []).map(text).join("");
const button = (view, label) => view.root.findAllByType("button").find(node => text(node) === label);
async function mount(t, active = true) {
  let view;
  await act(async () => { view = TestRenderer.create(React.createElement(ReadinessPanel, { active })); });
  t.after(() => act(() => view.unmount()));
  return view;
}
async function click(view, label) { await act(async () => button(view, label).props.onClick()); }

test("Overview automatically reads static prerequisites, keeping execution and CARLA checks separate", async t => {
  let reads = 0, probes = 0;
  api.getReadiness = async options => { reads++; assert.ok(options.signal); return readinessFixture(); };
  api.checkCarlaConnection = async () => { probes++; throw new Error("Unexpected probe"); };
  const view = await mount(t);
  assert.equal(reads, 1); assert.equal(probes, 0);
  const cards = view.root.findAllByType("article");
  assert.equal(cards.length, 2);
  assert.match(text(cards[0]), /Synthetic testReady/);
  assert.match(text(cards[1]), /CARLA live simulationMissing/);
  assert.match(text(cards[1]), /Simulator installationReady/);
  assert.match(text(cards[1]), /CARLA connectionNot checked/);
  assert.match(text(view.root), /Static checks only/);
  assert.match(text(view.root), /Synthetic results do not establish real driving/);
});

test("CARLA RPC is requested only by its explicit button; refresh returns to a static snapshot", async t => {
  api.getReadiness = async () => readinessFixture();
  let probes = 0;
  api.checkCarlaConnection = async options => {
    probes++; assert.ok(options.signal);
    return { ...readinessFixture(), carla_connection_requested: true };
  };
  const view = await mount(t);
  await click(view, "Check CARLA connection");
  assert.equal(probes, 1); assert.match(text(view.root), /Includes an explicit connection check/);
  await click(view, "Refresh setup");
  assert.equal(probes, 1); assert.match(text(view.root), /Static checks only/);
});

test("a failed initial read has unknown readiness and an actionable retry", async t => {
  api.getReadiness = async () => { throw new ApiError("Backend unavailable.", 503); };
  const view = await mount(t);
  assert.match(text(view.root.findByProps({ role: "alert" })), /Backend unavailable.*Readiness is unknown/);
  assert.equal(view.root.findAllByType("article").length, 0);
  api.getReadiness = async () => readinessFixture();
  await click(view, "Retry setup check");
  assert.equal(view.root.findAllByProps({ role: "alert" }).length, 0);
  assert.equal(view.root.findAllByType("article").length, 2);
});

test("refresh failure keeps the previous snapshot visibly labeled as possibly out of date", async t => {
  api.getReadiness = async () => readinessFixture();
  const view = await mount(t);
  api.getReadiness = async () => { throw new ApiError("Unavailable.", 503); };
  await click(view, "Refresh setup");
  assert.match(text(view.root), /Previous snapshot shown below; it may be out of date/);
  assert.equal(view.root.findAllByType("article").length, 2);
});

test("inactive Overview does not inspect setup, and navigation aborts and rejects late snapshots", async t => {
  let reads = 0, signal;
  const old = deferred();
  api.getReadiness = async options => { reads++; signal = options.signal; return old.promise; };
  const view = await mount(t, false);
  assert.equal(reads, 0);
  await act(async () => view.update(React.createElement(ReadinessPanel, { active: true })));
  assert.equal(reads, 1);
  assert.match(text(view.root), /Checking local setup/);
  assert.ok(view.root.findAllByType("button").every(b => b.props.disabled));
  await act(async () => view.update(React.createElement(ReadinessPanel, { active: false })));
  assert.equal(signal.aborted, true);
  api.getReadiness = async () => ({ ...readinessFixture(), checked_at: "2026-10-20T13:00:00Z" });
  await act(async () => view.update(React.createElement(ReadinessPanel, { active: true })));
  await act(async () => old.resolve(readinessFixture()));
  assert.equal(view.root.findByType("time").props.dateTime, "2026-10-20T13:00:00Z");
});

test("a hung read times out, permits retry and cannot overwrite a newer report", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const old = deferred(); let signal;
  api.getReadiness = async options => { signal = options.signal; return old.promise; };
  const view = await mount(t);
  await act(async () => t.mock.timers.tick(16_000));
  assert.equal(signal.aborted, true);
  assert.match(text(view.root), /Setup check timed out/);
  api.getReadiness = async () => ({ ...readinessFixture(), checked_at: "2026-10-20T13:00:00Z" });
  await click(view, "Retry setup check");
  await act(async () => old.resolve(readinessFixture()));
  assert.equal(view.root.findByType("time").props.dateTime, "2026-10-20T13:00:00Z");
});
