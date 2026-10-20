import assert from "node:assert/strict";
import { after, test } from "node:test";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const loaded = await loadComponents("App.tsx");
const App = loaded.module.default;
const { api } = await loaded.moduleAt("services/api.ts");
after(() => loaded.cleanup());
const id = "00000000-0000-4000-8000-000000000001";
const recipe = { name: "Intersection recipe", description: "Template", map: "Town03", seed: 0, duration_s: 1, fixed_delta_seconds: 0.05,
  actors: [{ id: "ego", type: "ego_vehicle", speed_mps: 5 }], events: [], weather: {}, parameters: {}, template: "intersection" };
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
const text = node => typeof node === "string" || typeof node === "number" ? String(node) : (node?.children ?? []).map(text).join("");

async function setup(t, hash = "#scenarios") {
  const oldWindow = globalThis.window, oldFetch = globalThis.fetch, oldDocument = globalThis.document;
  globalThis.document = { title: "" };
  api.listAgents = async () => [];
  const events = new Map();
  globalThis.window = { location: { hash }, setTimeout, clearTimeout, scrollTo() {},
    addEventListener: (name, handler) => events.set(name, handler), removeEventListener: name => events.delete(name) };
  globalThis.fetch = async url => {
    if (url.endsWith("/health")) return Response.json({ status: "ok", service: "poltergeist" });
    if (url.endsWith("/scenarios/templates")) return Response.json([{ template: "intersection", scenario: recipe, parameters: [] }]);
    throw new Error(`Unexpected initial API call ${url}`);
  };
  let view;
  t.after(() => { act(() => view?.unmount()); if (oldWindow === undefined) delete globalThis.window; else globalThis.window = oldWindow; if (oldDocument === undefined) delete globalThis.document; else globalThis.document = oldDocument; globalThis.fetch = oldFetch; });
  await act(async () => { view = TestRenderer.create(React.createElement(App)); });
  return { view, async navigate(next) { await act(async () => { window.location.hash = next; events.get("hashchange")(); }); } };
}

test("dashboard navigation retains unsaved JSON and lazily opens run and failure data", async t => {
  let historyReads = 0, failureReads = 0, analyticsReads = 0, queueReads = 0;
  api.listSimulations = async () => { historyReads++; return []; };
  api.listFailures = async () => { failureReads++; return []; };
  api.getAnalytics = async () => { analyticsReads++; return { groups: [] }; };
  api.getSimulationQueue = async () => { queueReads++; throw new Error("Unexpected queue start"); };
  const app = await setup(t);
  const editor = () => app.view.root.findAllByType("textarea").find(node => node.props.id === "scenario-json");
  assert.ok(editor());
  act(() => editor().props.onChange({ target: { value: '{"name":"Unsaved draft"}' } }));
  assert.equal(historyReads, 0);
  assert.equal(failureReads, 0);
  await app.navigate("#simulations");
  assert.equal(historyReads, 1);
  assert.equal(failureReads, 0);
  await app.navigate("#failures");
  assert.equal(failureReads, 1);
  assert.equal(document.title, "Poltergeist · Failures");
  const navigation = app.view.root.findByProps({ "aria-label": "Main navigation" });
  assert.equal(navigation.findAllByType("a").filter(node => node.props["aria-current"] === "page")[0].props.href, "#failures");
  await app.navigate("#overview");
  assert.equal(analyticsReads, 0);
  assert.equal(queueReads, 0);
  await app.navigate("#scenarios");
  assert.equal(editor().props.value, '{"name":"Unsaved draft"}');
  assert.match(text(app.view.root), /Scenario workspace/);
});

test("opening a run deep link selects that ID and leaving it aborts its pending read", async t => {
  const pending = deferred();
  let selected, signal;
  api.listSimulations = async () => [];
  api.listFailures = async () => [];
  api.getSimulation = async (value, options) => { selected = value; signal = options.signal; return pending.promise; };
  const app = await setup(t, `#simulations/${id}`);
  assert.equal(selected, id);
  assert.equal(signal.aborted, false);
  await app.navigate("#failures");
  assert.equal(signal.aborted, true);
  // The superseded request must not create detail content after navigation.
  await act(async () => pending.resolve({ simulation: { id, scenario: recipe, status: "queued" }, revision: 1, result: null, job: null }));
  assert.equal(app.view.root.findAllByProps({ "aria-label": "Selected simulation" }).length, 0);
});

test('Agents navigation preserves the recipe and refreshes launch selections after register/deactivate', async t => {
  const app = await setup(t);
  const saved = { id, active: true, created_at: '2026-10-19T12:00:00Z', snapshot: {
    registration_id: id, package_path: 'C:/agents/pilot', python_executable: 'C:/agents/python.exe',
    python_sha256: 'a'.repeat(64), python_size_bytes: 100, package_sha256: 'b'.repeat(64),
    observation_mode: 'simulator_state', agent_control_mode: 'authoritative', configuration: {},
    files: [], model_files: [], runtime_files: [], process_options: {},
    manifest: { name: 'Navigation pilot', version: '1.0', protocol_version: '1.3', agent_id: 'navigation-pilot',
      entry_point: 'agent:Driver', navigation_mode: 'provided_route', observation_mode: 'simulator_state', sensors: [], configuration: {} },
  } };
  let registrations = [], selectorReads = 0;
  api.listAgents = async filters => { if (filters.limit === 100) selectorReads++; return registrations.filter(item => filters.active === undefined || filters.active === item.active); };
  api.registerAgent = async () => { registrations = [saved]; return saved; };
  api.getAgent = async () => registrations[0];
  api.getAgentReadiness = async () => ({ agent_id: id, ready: registrations[0].active, issues: [], limitations: [], checked_at: '2026-10-19T12:00:00Z' });
  api.deactivateAgent = async () => { registrations = [{ ...saved, active: false }]; return registrations[0]; };
  const editor = () => app.view.root.findByProps({ id: 'scenario-json' });
  const draft = editor().props.value;
  await app.navigate('#agents');
  assert.equal(document.title, 'Poltergeist · Agents');
  act(() => app.view.root.findByProps({ id: 'agent-package-path' }).props.onChange({ target: { value: 'C:/agents/pilot' } }));
  act(() => app.view.root.findByProps({ id: 'agent-trusted-code' }).props.onChange({ target: { checked: true } }));
  await act(async () => app.view.root.findByProps({ className: 'panel agent-registration' }).findByType('form').props.onSubmit({ preventDefault() {} }));
  assert.match(text(app.view.root), /Registered Navigation pilot/);
  await app.navigate('#scenarios');
  assert.equal(editor().props.value, draft);
  const selector = () => {
    const label = app.view.root.findAllByType('label').find(node => text(node) === 'Ego controller');
    return app.view.root.findByProps({ id: label.props.htmlFor });
  };
  assert.ok(selector().findAllByType('option').some(node => node.props.value === id));
  await act(async () => selector().props.onChange({ target: { value: id } }));
  assert.equal(selector().props.value, id);
  const reads = selectorReads;
  await app.navigate('#agents');
  await act(async () => app.view.root.findAllByType('button').find(node => text(node) === 'Deactivate registration').props.onClick());
  assert.equal(selectorReads, reads, 'Hidden scenario page does not read registry on changes');
  await app.navigate('#scenarios');
  assert.equal(selector().props.value, id, 'Deactivation preserves the explicit prior selection');
  assert.match(text(app.view.root), /missing or deactivated/);
  assert.equal(editor().props.value, draft);
});
