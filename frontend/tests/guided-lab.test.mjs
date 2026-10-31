import assert from "node:assert/strict";
import { after, test } from "node:test";
import { readFile } from "node:fs/promises";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const fixture = JSON.parse(await readFile(new URL("./fixtures/experimentation.json", import.meta.url), "utf8"));
const loaded = await loadComponents("pages/GuidedLab.tsx");
const { GuidedLab, GuidanceFeedbackForm } = loaded.module;
const { Discovery, DiscoveryFindings } = await loaded.moduleAt("pages/Discovery.tsx");
const { SuiteManager } = await loaded.moduleAt("components/SuiteManager.tsx");
const { discoveryDefaults, discoveryProposal, suiteDefaults, suiteInput } = await loaded.moduleAt("components/lab-form.ts");
const { api } = await loaded.moduleAt("services/api.ts");
const { useLabMutation, useLabReport } = await loaded.moduleAt("hooks/useLab.ts");
const { routeFromHash } = await loaded.moduleAt("navigation.ts");
after(() => loaded.cleanup());
const clone = value => structuredClone(value);
const text = n => typeof n === "string" || typeof n === "number" ? String(n) : (n?.children ?? []).map(text).join("");
const button = (view, label) => view.root.findAllByType("button").find(n => text(n) === label);
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const freshPlan = () => ({ ...clone(fixture.plan), expires_at: new Date(Date.now() + 86_400_000).toISOString() });
function client(extra = {}) { return { getGallery: async () => fixture.gallery, listAgents: async () => [], listSuites: async () => [], listDiscoveryPlans: async () => [], getDiscoveryPlan: async () => freshPlan(), getDiscoveryReport: async () => fixture.discovery, getSimulation: async () => fixture.run, getReplayHighlights: async () => fixture.highlights, ...extra }; }
async function render(t, Component, props) {
  const old = globalThis.window;
  const values = new Map();
  globalThis.window = { location: { hash: "#lab" }, localStorage: { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) } };
  let view; await act(async () => { view = TestRenderer.create(React.createElement(Component, props)); });
  t.after(() => { act(() => view.unmount()); if (old === undefined) delete globalThis.window; else globalThis.window = old; });
  return view;
}

test("real synthetic API evidence passes every new contract", () => {
  for (const result of [
    { kind: "gallery", items: fixture.gallery }, { kind: "suite", suite: fixture.suite }, { kind: "plan", plan: fixture.plan },
    { kind: "plan", plan: fixture.approved_plan }, { kind: "discovery", report: fixture.discovery },
    { kind: "suite_report", report: fixture.suite_report }, { kind: "run", record: fixture.run },
    { kind: "highlights", highlights: fixture.highlights }, { kind: "feedback", feedback: fixture.feedback },
    { kind: "plans", items: [fixture.plan], snapshot_at: fixture.plan.created_at, next_offset: null },
  ]) assert.equal(api.validateLabResult(result), true, result.kind);
});
test("suite comparison links retain only a valid operation identity", () => {
  assert.equal(routeFromHash(`#lab/suite-run/${fixture.suite_report.evidence.operation.id}`).suiteRunId, fixture.suite_report.evidence.operation.id);
  assert.equal(routeFromHash(`#lab/suite-run/${fixture.suite.id}/extra`).suiteRunId, undefined);
});
test("contracts reject changed approved recipes, budgets, confirmation claims and page cursors", () => {
  const mutations = [
    r => r.plan.scenarios[0].seed++, r => r.plan.scenarios.pop(), r => r.plan.proposal.budget.max_executions = 2,
    r => r.evidence.operation.request.scenarios[0].duration_s++, r => r.candidates[0].matching_confirmations = 0,
    r => r.candidates[0].confirmation_simulation_ids.reverse(), r => r.executions_used++, r => r.confirmed_failures++,
    r => r.evidence.trials[0].operation_id = fixture.suite.id,
  ];
  for (const mutate of mutations) { const value = clone(fixture.discovery); mutate(value); assert.equal(api.validateLabResult({ kind: "discovery", report: value }), false); }
  assert.equal(api.validateLabResult({ kind: "plans", items: [fixture.plan], snapshot_at: "yesterday", next_offset: null }), false);
  assert.equal(api.validateLabResult({ kind: "plans", items: [fixture.plan], snapshot_at: fixture.plan.created_at, next_offset: -1 }), false);
});
test("typed budgets reject insufficient confirmation reserves, duplicates and nonfinite settings", () => {
  assert.equal(discoveryProposal(discoveryDefaults()).budget.max_executions, 12);
  for (const change of [{ executions: "2" }, { required: "3" }, { simulated: "1" }, { seconds: "0" }, { parameters: '{"value":1e999}' }, { ranges: "[]" }, { duration: "NaN" }, { agent: "missing" }]) assert.throws(() => discoveryProposal({ ...discoveryDefaults(), ...change }));
  const draft = { ...suiteDefaults(), cases: [{ key: "one", name: "Following", recipe: JSON.stringify(fixture.gallery[0].scenario) }] };
  const value = suiteInput({ ...draft, options: '{"max_ticks":500}', evaluation: '{"collision_weight":25}' });
  assert.equal(value.definition.options.max_ticks, 500); assert.equal(value.definition.evaluation_settings.collision_weight, 25);
  for (const change of [{ seeds: "1,1" }, { agents: [] }, { agents: ["baseline", "baseline"] }, { repeats: "1.2" }, { cases: [...draft.cases, ...draft.cases] }]) assert.throws(() => suiteInput({ ...draft, ...change }));
});
test("first visit and scene selection start no executions; launch needs an explicit click", async t => {
  let runs = 0;
  const value = client({ launchSimulation: async input => { runs++; assert.equal(input.mode, "test"); assert.deepEqual(input.scenario, fixture.gallery[0].scenario); return fixture.run; } });
  const view = await render(t, GuidedLab, { active: true, client: value });
  assert.equal(runs, 0);
  const first = view.root.findAllByType("button").find(n => text(n).startsWith("Choose "));
  await act(async () => first.props.onClick()); assert.equal(runs, 0);
  await act(async () => button(view, "Run one synthetic experiment").props.onClick());
  assert.equal(runs, 1); assert.match(text(view.root), /Explain what was recorded/); assert.match(text(view.root), /synthetic fixture/);
  assert.ok(view.root.findAllByType("a").some(n => n.props.href.includes("/frame/")));
  await act(async () => view.update(React.createElement(GuidedLab, { key: "reopened", active: true, client: value })));
  await act(async () => button(view, "Keep scenes for a comparison").props.onClick());
  assert.ok(view.root.findAllByType("textarea").some(n => n.props.value === JSON.stringify(fixture.run.simulation.scenario, null, 2)));
  assert.equal(runs, 1);
});
test("feedback saves only after deliberate answer and consent", async t => {
  let saves = 0;
  const view = await render(t, GuidanceFeedbackForm, { simulationId: fixture.run.simulation.id, active: true, client: { saveGuidanceFeedback: async value => { saves++; assert.equal(value.consent_to_save, true); return fixture.feedback; } } });
  await act(async () => view.root.findByType("form").props.onSubmit({ preventDefault() {} })); assert.equal(saves, 0);
  await act(async () => view.root.findByType("select").props.onChange({ target: { value: "yes" } }));
  await act(async () => view.root.findByType("form").props.onSubmit({ preventDefault() {} })); assert.equal(saves, 0);
  await act(async () => view.root.findByType("input").props.onChange({ target: { checked: true } }));
  await act(async () => view.root.findByType("form").props.onSubmit({ preventDefault() {} })); assert.equal(saves, 1); assert.match(text(view.root), /Feedback saved/);
});
test("discovery review creates no runs and draft edits invalidate approval", async t => {
  let proposals = 0, approvals = 0;
  const value = client({ proposeDiscovery: async () => { proposals++; return freshPlan(); }, approveDiscovery: async () => { approvals++; return fixture.discovery.evidence.operation; } });
  const view = await render(t, Discovery, { active: true, client: value });
  assert.equal(proposals, 0); assert.equal(approvals, 0);
  await act(async () => view.root.findByType("form").props.onSubmit({ preventDefault() {} }));
  assert.equal(proposals, 1); assert.equal(approvals, 0); assert.equal(button(view, "Approve and start discovery").props.disabled, true);
  await act(async () => view.root.findAllByType("input").find(n => n.props.type === "checkbox").props.onChange({ target: { checked: true } }));
  assert.equal(button(view, "Approve and start discovery").props.disabled, false);
  await act(async () => view.root.findAllByType("textarea")[0].props.onChange({ target: { value: "A different driving question" } }));
  assert.equal(button(view, "Approve and start discovery"), undefined); assert.equal(approvals, 0);
});
test("exact displayed plan is approved once while a response is pending", async t => {
  const pending = deferred(); let calls = 0;
  const value = client({ approveDiscovery: (plan, options) => { calls++; assert.equal(plan.digest, fixture.plan.digest); assert.equal(options.signal.aborted, false); return pending.promise; } });
  const view = await render(t, Discovery, { active: true, client: value, planId: fixture.plan.id });
  await act(async () => view.root.findAllByType("input").find(n => n.props.type === "checkbox").props.onChange({ target: { checked: true } }));
  const approve = button(view, "Approve and start discovery");
  await act(async () => { approve.props.onClick(); approve.props.onClick(); }); assert.equal(calls, 1);
  await act(async () => pending.reject(new Error("lost acknowledgement")));
  assert.match(text(view.root), /request may have arrived/); assert.equal(calls, 1);
});
test("confirmed findings save exact recipe and settings as a rerunnable suite", async t => {
  let saved;
  const view = await render(t, DiscoveryFindings, { report: fixture.discovery, client: client({ saveSuite: async value => { saved = value; return fixture.suite; } }) });
  await act(async () => view.root.findAllByType("button").find(n => text(n) === "Save confirmed scenario as a suite").props.onClick());
  assert.deepEqual(saved.definition.cases[0].scenario, fixture.discovery.candidates[0].scenario);
  assert.deepEqual(saved.definition.options, fixture.discovery.evidence.operation.execution.options);
  assert.deepEqual(saved.definition.seeds, [fixture.discovery.candidates[0].scenario.seed]);
  assert.match(text(view.root), /Confirmed scenario saved/);
});
test("suite editing protects drafts and deletion requires a separate deliberate click", async t => {
  let deletes = 0;
  const value = client({ listSuites: async () => [fixture.suite], deleteSuite: async (id, revision) => { deletes++; assert.equal(revision, 1); } });
  const view = await render(t, SuiteManager, { active: true, incoming: null, client: value });
  const name = () => view.root.findAllByType("input").find(n => n.props.maxLength === 128);
  await act(async () => name().props.onChange({ target: { value: "Unsaved idea" } }));
  await act(async () => button(view, `Edit ${fixture.suite.definition.name}`).props.onClick()); assert.equal(name().props.value, "Unsaved idea");
  await act(async () => button(view, "Replace unsaved draft").props.onClick()); assert.equal(name().props.value, fixture.suite.definition.name);
  await act(async () => button(view, `Delete ${fixture.suite.definition.name}`).props.onClick()); assert.equal(deletes, 0);
  await act(async () => button(view, "Confirm suite deletion").props.onClick()); assert.equal(deletes, 1);
});
test("late import results cannot replace a hidden suite draft", async t => {
  const pending = deferred(), value = client();
  const view = await render(t, SuiteManager, { active: true, incoming: null, client: value });
  await act(async () => view.root.findAllByType("input").find(n => n.props.type === "file").props.onChange({ target: { files: [{ size: 20, text: () => pending.promise }], value: "file.json" } }));
  await act(async () => view.update(React.createElement(SuiteManager, { active: false, incoming: null, client: value })));
  await act(async () => pending.resolve(JSON.stringify(fixture.suite)));
  assert.ok(view.root.findAllByType("input").some(n => n.props.value === "My experiment suite"));
});
test("saved suites expose durable past comparisons and direct links reopen their evidence", async t => {
  const operation = fixture.suite_report.evidence.operation;
  const value = client({ listSuites: async () => [fixture.suite], listSuiteRuns: async id => { assert.equal(id, fixture.suite.id); return [operation]; }, getSuiteReport: async id => { assert.equal(id, operation.id); return fixture.suite_report; } });
  const view = await render(t, SuiteManager, { active: true, incoming: null, client: value });
  await act(async () => button(view, `Past results for ${fixture.suite.definition.name}`).props.onClick());
  assert.ok(view.root.findAllByType("a").some(n => n.props.href === `#lab/suite-run/${operation.id}`));
  await act(async () => view.update(React.createElement(SuiteManager, { active: true, incoming: null, client: value, initialRunId: operation.id })));
  assert.match(text(view.root), /Suite run · completed/); assert.match(text(view.root), /Braking approach/);
});
test("mutation timeout releases a hung adapter and ignores its eventual acknowledgement", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] }); const pending = deferred(); let hook, accepted = 0;
  function Probe({ active }) { hook = useLabMutation(active); return null; }
  await render(t, Probe, { active: true });
  await act(async () => { void hook.run(() => pending.promise, () => accepted++); }); assert.equal(hook.busy, true);
  await act(async () => t.mock.timers.tick(15_001)); assert.equal(hook.busy, false); assert.match(hook.error, /never resubmitted automatically/);
  await act(async () => pending.resolve({})); assert.equal(accepted, 0);
});
test("poll timeout stops loading without losing evidence and late reads stay fenced", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] }); const pending = deferred(); let hook;
  function Probe() { hook = useLabReport("saved-plan", true, () => pending.promise, () => true); return null; }
  await render(t, Probe, {}); assert.equal(hook.loading, true);
  await act(async () => t.mock.timers.tick(12_001)); assert.equal(hook.loading, false); assert.match(hook.error, /timed out/);
  await act(async () => pending.resolve(fixture.plan)); assert.equal(hook.value, null);
});
test("hiding a mutation aborts its scope even when the adapter ignores cancellation", async t => {
  let hook, signal, accepted = 0; const pending = deferred();
  function Probe({ active }) { hook = useLabMutation(active); return null; }
  const view = await render(t, Probe, { active: true });
  await act(async () => { void hook.run(s => { signal = s; return pending.promise; }, () => accepted++); });
  await act(async () => view.update(React.createElement(Probe, { active: false }))); assert.equal(signal.aborted, true); assert.equal(hook.busy, false);
  await act(async () => pending.resolve({})); assert.equal(accepted, 0);
});
