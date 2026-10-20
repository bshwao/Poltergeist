import assert from "node:assert/strict";
import { after, test } from "node:test";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const loaded = await loadComponents("pages/Agents.tsx");
const { Agents } = loaded.module;
const { api, ApiError } = await loaded.moduleAt("services/api.ts");
const { emptyRegistrationDraft, registrationInput } = await loaded.moduleAt("components/agent-registration-form.ts");
after(() => loaded.cleanup());
const id = number => `00000000-0000-4000-8000-${String(number).padStart(12, "0")}`;
const text = node => typeof node === "string" || typeof node === "number" ? String(node) : (node?.children ?? []).map(text).join("");
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function agent(number = 1, fields = {}) {
  return { id: id(number), active: true, created_at: "2026-10-19T12:00:00Z", snapshot: {
    registration_id: id(number), package_path: "C:/agents/pilot", package_sha256: "a".repeat(64),
    python_executable: "C:/agents/venv/python.exe", python_sha256: "b".repeat(64), python_size_bytes: 100,
    observation_mode: "simulator_state", agent_control_mode: "authoritative", configuration: { sensitivity: 0.5 },
    process_options: { load_timeout_s: 30, reset_timeout_s: 10, step_timeout_s: 2, close_timeout_s: 2, terminate_timeout_s: 1, max_message_bytes: 4194304 },
    files: [{ path: "agent.py", sha256: "c".repeat(64), size_bytes: 40 }], model_files: [], runtime_files: [],
    manifest: { name: `Pilot ${number}`, version: "2.0", agent_id: `pilot-${number}`, protocol_version: "1.3", entry_point: "agent:Driver", navigation_mode: "provided_route", observation_mode: "simulator_state", configuration: { sensitivity: 0.5 }, sensors: [] },
  }, ...fields };
}
function find(view, itemId) { return view.root.findByProps({ id: itemId }); }
function button(view, label) { return view.root.findAllByType("button").find(node => text(node) === label); }
function change(view, itemId, value) { act(() => find(view, itemId).props.onChange({ target: { value, checked: value } })); }
async function click(view, label) { const node = button(view, label); assert.ok(node, label); await act(async () => node.props.onClick()); }
async function setup(t, props = {}) {
  api.listAgents = async () => [];
  api.getAgent = async selected => agent(Number(selected.slice(-2)));
  api.registerAgent = async () => { throw new Error("Unexpected registration"); };
  api.getAgentReadiness = async () => { throw new Error("Unexpected static readiness"); };
  api.deactivateAgent = async () => { throw new Error("Unexpected deactivation"); };
  let view;
  await act(async () => { view = TestRenderer.create(React.createElement(Agents, props)); });
  t.after(() => act(() => view.unmount()));
  return view;
}
function validDraft(view) { change(view, "agent-package-path", " C:/agents/pilot "); change(view, "agent-trusted-code", true); }
async function submit(view) { await act(async () => view.root.findByType("form").props.onSubmit({ preventDefault() {} })); }
async function showAgent(view, item = agent()) {
  api.listAgents = async () => [item]; api.getAgent = async () => item;
  await click(view, "Refresh registrations"); await click(view, `Inspect ${item.snapshot.manifest.name}`);
}

test("registration draft validates absolute local paths, JSON objects, model lists and explicit trust", () => {
  const draft = emptyRegistrationDraft();
  assert.deepEqual(Object.keys(registrationInput(draft).errors), ["packagePath", "trusted"]);
  draft.packagePath = " C:\\agents\\pilot "; draft.trusted = true;
  draft.pythonExecutable = "/opt/venv/bin/python";
  draft.modelFiles = " C:/models/weights.bin \n/opt/models/second.bin\n";
  draft.configuration = '{"nested":{"threshold":0.3},"enabled":true}';
  const { input, errors } = registrationInput(draft);
  assert.deepEqual(errors, {}); assert.equal(input.package_path, "C:\\agents\\pilot");
  assert.equal(input.python_executable, "/opt/venv/bin/python");
  assert.deepEqual(input.model_files, ["C:/models/weights.bin", "/opt/models/second.bin"]);
  assert.equal(input.agent_control_mode, "authoritative"); assert.equal(input.trusted_local_code, true);
});
test("nonfinite JSON, array config, network paths and invalid execution budgets fail locally", () => {
  const draft = { ...emptyRegistrationDraft(), packagePath: "/agents/pilot", trusted: true };
  for (const configuration of ["[]", "null", '"string"', '{"overflow":1e999}', '{"nested":[1e999]}', "{broken}"]) {
    assert.ok(registrationInput({ ...draft, configuration }).errors.configuration);
  }
  for (const packagePath of ["pilot", "C:pilot", "//network/pilot", "\\\\server\\pilot", ""]) assert.ok(registrationInput({ ...draft, packagePath }).errors.packagePath);
  for (const modelFiles of ["weights.pt", "/a\n/a", Array.from({ length: 257 }, (_, n) => `/model-${n}`).join("\n")]) assert.ok(registrationInput({ ...draft, modelFiles }).errors.modelFiles);
  for (const value of ["", "0", "-1", "Infinity"]) assert.ok(registrationInput({ ...draft, processOptions: { ...draft.processOptions, step_timeout_s: value } }).errors.step_timeout_s);
  for (const value of ["1.5", "67108865"]) assert.ok(registrationInput({ ...draft, processOptions: { ...draft.processOptions, max_message_bytes: value } }).errors.max_message_bytes);
});
test("page does not mutate or execute models on mount; invalid draft stays editable", async t => {
  const view = await setup(t); let calls = 0;
  api.registerAgent = async () => { calls++; return agent(); };
  await submit(view); assert.equal(calls, 0);
  assert.match(text(view.root), /absolute local package directory/); assert.match(text(view.root), /Acknowledge that you trust/);
  assert.match(text(view.root), /Model loading and inference happen only when you explicitly execute/);
  change(view, "agent-package-path", "/agents/pilot"); assert.equal(calls, 0);
});
test("registration sends exact validated config and advanced settings and opens the saved identity", async t => {
  let changed = 0; const view = await setup(t, { onRegistryChange: () => changed++ });
  validDraft(view); change(view, "agent-python", "C:/agents/env/python.exe"); change(view, "agent-configuration", '{"brake":0.4}');
  change(view, "agent-model-files", "C:/models/weights.pt"); change(view, "agent-step_timeout_s", "0.25");
  let input; api.registerAgent = async value => { input = value; return agent(); };
  await submit(view);
  assert.equal(input.package_path, "C:/agents/pilot"); assert.equal(input.python_executable, "C:/agents/env/python.exe");
  assert.deepEqual(input.configuration, { brake: .4 }); assert.deepEqual(input.model_files, ["C:/models/weights.pt"]);
  assert.equal(input.process_options.step_timeout_s, .25); assert.equal(input.trusted_local_code, true);
  assert.equal(changed, 1); assert.match(text(view.root), /Registered Pilot 1 2.0/);
  assert.match(text(view.root), /Package SHA-256/); assert.match(text(view.root), /Structured state includes simulator ground truth/);
  assert.equal(find(view, "agent-configuration").props.value, '{"brake":0.4}'); assert.equal(find(view, "agent-trusted-code").props.checked, false);
});
test("duplicate registration submits send one immutable request and cannot edit pending form", async t => {
  const view = await setup(t); validDraft(view);
  const pending = deferred(); let calls = 0, sent;
  api.registerAgent = async value => { calls++; sent = value; return pending.promise; };
  const form = view.root.findByType("form");
  act(() => { form.props.onSubmit({ preventDefault() {} }); form.props.onSubmit({ preventDefault() {} }); });
  assert.equal(calls, 1); assert.equal(view.root.findByType("fieldset").props.disabled, true);
  assert.equal(sent.package_path, "C:/agents/pilot");
  await act(async () => pending.resolve(agent())); assert.equal(calls, 1);
});
test("registration errors distinguish rejection from uncertainty and preserve draft", async t => {
  let changes = 0; const view = await setup(t, { onRegistryChange: () => changes++ }); validDraft(view);
  api.registerAgent = async () => { throw new ApiError("Invalid manifest", 422, [{ path: "configuration", message: "Invalid setting" }]); };
  await submit(view); assert.match(text(view.root), /Invalid manifest No registration was accepted.*configuration: Invalid setting/); assert.equal(changes, 0);
  api.registerAgent = async () => { throw new ApiError("Response lost", 0); };
  await submit(view); assert.match(text(view.root), /Refresh the registration list before submitting again/); assert.equal(changes, 1);
  assert.equal(find(view, "agent-package-path").props.value, " C:/agents/pilot ");
});
test("hidden registration aborts, reports uncertainty once, and ignores late receipts without retry", async t => {
  let changes = 0; const props = { onRegistryChange: () => changes++ }; const view = await setup(t, props); validDraft(view);
  const pending = deferred(); let signal, calls = 0;
  api.registerAgent = async (_input, options) => { signal = options.signal; calls++; return pending.promise; };
  act(() => view.root.findByType("form").props.onSubmit({ preventDefault() {} }));
  await act(async () => view.update(React.createElement(Agents, { ...props, active: false })));
  assert.equal(signal.aborted, true); assert.equal(changes, 1); assert.match(text(view.root), /Registration may have reached the API/);
  await act(async () => pending.resolve(agent()));
  await act(async () => view.update(React.createElement(Agents, props)));
  assert.equal(calls, 1); assert.doesNotMatch(text(view.root), /Registered Pilot/);
  assert.equal(find(view, "agent-package-path").props.value, " C:/agents/pilot ");
});
test("hidden pages do not read registry; filter and page requests use server pagination", async t => {
  const view = await setup(t, { active: false }); const requests = [];
  api.listAgents = async (filters, options) => { requests.push({ filters, signal: options.signal }); return Array.from({ length: 9 }, (_, index) => agent(index + 1)); };
  assert.equal(requests.length, 0);
  await act(async () => view.update(React.createElement(Agents)));
  assert.deepEqual(requests[0].filters, { limit: 9, offset: 0, active: true });
  await click(view, "Next agent page"); assert.deepEqual(requests.at(-1).filters, { limit: 9, offset: 8, active: true });
  await act(async () => change(view, "agent-active-filter", "inactive")); assert.deepEqual(requests.at(-1).filters, { limit: 9, offset: 0, active: false });
  await act(async () => change(view, "agent-active-filter", "all")); assert.deepEqual(requests.at(-1).filters, { limit: 9, offset: 0 });
});
test("superseded registry lists are aborted and never replace the current page", async t => {
  const view = await setup(t); const old = deferred(); let signal;
  api.listAgents = async (_filters, options) => { signal = options.signal; return old.promise; };
  act(() => button(view, "Refresh registrations").props.onClick());
  api.listAgents = async () => [agent(2)];
  await act(async () => view.update(React.createElement(Agents, { active: false })));
  assert.equal(signal.aborted, true);
  await act(async () => view.update(React.createElement(Agents)));
  await act(async () => old.resolve([agent(1)]));
  assert.ok(button(view, "Inspect Pilot 2")); assert.equal(button(view, "Inspect Pilot 1"), undefined);
});
test("static readiness is explicit, accurately scoped and displays issues and limitations", async t => {
  const view = await setup(t); await showAgent(view);
  let calls = 0; api.getAgentReadiness = async selected => { calls++; return { agent_id: selected, ready: false, checked_at: "2026-10-19T12:01:00Z", issues: ["Package file changed"], limitations: ["Agent code was not executed"] }; };
  assert.equal(calls, 0); await click(view, "Check static readiness");
  assert.equal(calls, 1); assert.match(text(view.root), /Static checks found issues/); assert.match(text(view.root), /Package file changed/); assert.match(text(view.root), /Agent code was not executed/);
  api.getAgentReadiness = async selected => ({ agent_id: selected, ready: true, checked_at: "2026-10-19T12:01:00Z", issues: [], limitations: [] });
  await click(view, "Check static readiness"); assert.match(text(view.root), /Static checks passed/); assert.match(text(view.root), /Dependencies, inference, and driving behavior are unverified/);
});
test("switching registration aborts a pending readiness result and discards its late response", async t => {
  const view = await setup(t); api.listAgents = async () => [agent(1), agent(2)];
  await click(view, "Refresh registrations"); await click(view, "Inspect Pilot 1");
  const pending = deferred(); let signal;
  api.getAgentReadiness = async (_id, options) => { signal = options.signal; return pending.promise; };
  act(() => button(view, "Check static readiness").props.onClick());
  await click(view, "Inspect Pilot 2"); assert.equal(signal.aborted, true);
  await act(async () => pending.resolve({ agent_id: id(1), ready: false, checked_at: "2026-10-19T12:01:00Z", issues: ["Stale pilot 1"], limitations: [] }));
  assert.doesNotMatch(text(view.root), /Stale pilot 1/); assert.match(text(view.root), /Pilot 2 · 2.0/);
});
test("deactivation preserves history, refreshes selectors and excludes duplicate writes", async t => {
  let changes = 0; const view = await setup(t, { onRegistryChange: () => changes++ }); await showAgent(view);
  const pending = deferred(); let calls = 0;
  api.deactivateAgent = async selected => { calls++; assert.equal(selected, id(1)); return pending.promise; };
  const action = button(view, "Deactivate registration"); act(() => { action.props.onClick(); action.props.onClick(); });
  assert.equal(calls, 1); api.getAgent = async () => agent(1, { active: false });
  await act(async () => pending.resolve(agent(1, { active: false })));
  assert.equal(changes, 1); assert.match(text(view.root), /Saved and queued runs retain their original agent snapshot/);
  assert.match(text(view.root), /This registration is inactive/); assert.equal(button(view, "Deactivate registration"), undefined);
});
test("sensor detail labels ideal localization assistance and displays declared rig without executing it", async t => {
  const view = await setup(t); const item = agent(); item.snapshot.observation_mode = "sensors"; item.snapshot.manifest.observation_mode = "sensors";
  item.snapshot.manifest.sensors = [{ id: "front-camera", type: "sensor.camera.rgb", attributes: { image_size_x: 320 }, mount: { position: { x: 1.5, y: 0, z: 2 } } }];
  await showAgent(view, item);
  assert.match(text(view.root), /Ideal ego localization is still provided/); assert.match(text(view.root), /front-camera/); assert.match(text(view.root), /sensor.camera.rgb/);
});
test("unmount aborts pending registration and ignores its eventual receipt", async t => {
  const view = await setup(t); validDraft(view); const pending = deferred(); let signal;
  api.registerAgent = async (_value, options) => { signal = options.signal; return pending.promise; };
  act(() => view.root.findByType("form").props.onSubmit({ preventDefault() {} }));
  act(() => view.unmount()); assert.equal(signal.aborted, true);
  await act(async () => pending.resolve(agent()));
});
