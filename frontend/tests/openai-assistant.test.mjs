import assert from "node:assert/strict";
import { after, test } from "node:test";
import { readFile } from "node:fs/promises";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const loaded = await loadComponents("components/OpenAIAssistant.tsx");
const { OpenAIAssistant } = loaded.module;
const { api, createApiClient, ApiError } = await loaded.moduleAt("services/api.ts");
const { isAssistantRecord } = await loaded.moduleAt("services/assistant-contract.ts");
const { analysis } = JSON.parse(await readFile(new URL("./fixtures/review.json", import.meta.url), "utf8"));
const { plan } = JSON.parse(await readFile(new URL("./fixtures/experimentation.json", import.meta.url), "utf8"));
after(() => loaded.cleanup());
const status = { enabled: true, configured: true, model: "fixture-model", daily_request_limit: 10, daily_output_token_limit: 8192, scope: "local_api_only" };
const text = node => typeof node === "string" || typeof node === "number" ? String(node) : (node?.children ?? []).map(text).join("");
const button = (view, name) => view.root.findAllByType("button").find(node => text(node) === name);
const check = (view, prefix) => view.root.findAllByType("label").find(node => text(node).startsWith(prefix)).findByType("input");
function record(kind = "plan") {
  const payload = JSON.stringify({ model: status.model, store: false, parallel_tool_calls: false, max_output_tokens: 1024,
    input: JSON.stringify({ question: "Find contacts" }), tool_choice: "required",
    tools: [{ type: "function", name: kind === "plan" ? "draft_discovery" : "report_evidence", strict: true }, { type: "function", name: "decline_request", strict: true }] });
  return { schema_version: "poltergeist-openai-assistance-v1", id: plan.id,
    preview: { id: plan.id, kind, model: status.model, digest: "a".repeat(64), created_at: "2026-11-01T00:00:00Z", expires_at: "2026-11-01T01:00:00Z",
      budget: { max_provider_calls: 1, max_input_bytes: 16384, max_output_tokens: 1024 }, input_bytes: new TextEncoder().encode(payload).length, payload, redactions_applied: false },
    state: "preview", message: "Nothing sent", plan: null, analysis: null, input_tokens: null, output_tokens: null, reserved_output_tokens: 0 };
}
async function render(t, props = {}, methods = {}) {
  const original = Object.fromEntries(Object.keys(methods).map(key => [key, api[key]]));
  Object.assign(api, methods);
  let view; await act(async () => { view = TestRenderer.create(React.createElement(OpenAIAssistant, { active: true, proposal: () => plan.proposal, ...props })); });
  t.after(() => { act(() => view.unmount()); Object.assign(api, original); });
  return view;
}

test("opening assistance reads configuration only; disabled providers cannot create disclosures", async t => {
  let reads = 0, previews = 0;
  const view = await render(t, {}, { getAssistantStatus: async () => { reads++; return { ...status, enabled: false }; }, previewAssistant: async () => { previews++; } });
  assert.equal(reads, 0);
  await act(async () => button(view, "Open language assistance").props.onClick());
  assert.equal(reads, 1); assert.equal(previews, 0);
  assert.ok(text(view.toJSON()).includes("OpenAI assistance is off"));
  assert.equal(view.root.findAllByType("fieldset")[0].props.disabled, true);
});

test("preview and an explicit disclosure approval precede exactly one send; edits revoke approval", async t => {
  const calls = [], draft = record();
  const view = await render(t, {}, { getAssistantStatus: async () => status,
    previewAssistant: async input => { calls.push(["preview", input]); return draft; },
    sendAssistant: async (id, approval) => { calls.push(["send", id, approval]); return { ...draft, state: "declined", message: "unsupported action", reserved_output_tokens: 1024 }; } });
  await act(async () => button(view, "Open language assistance").props.onClick());
  await act(async () => view.root.findByType("textarea").props.onChange({ target: { value: "Find following collisions" } }));
  await act(async () => button(view, "Preview OpenAI disclosure").props.onClick());
  assert.equal(calls.length, 1); assert.equal(calls[0][1].proposal.budget.max_executions, plan.proposal.budget.max_executions);
  assert.equal(view.root.findByProps({ "aria-label": "Exact OpenAI disclosure" }).props.tabIndex, 0);
  assert.equal(button(view, "Send approved OpenAI request").props.disabled, true);
  await act(async () => check(view, "I approve sending").props.onChange({ target: { checked: true } }));
  assert.equal(button(view, "Send approved OpenAI request").props.disabled, false);
  await act(async () => view.root.findByType("textarea").props.onChange({ target: { value: "A different question" } }));
  assert.equal(button(view, "Send approved OpenAI request"), undefined);
  await act(async () => button(view, "Preview OpenAI disclosure").props.onClick());
  assert.equal(check(view, "I approve sending").props.checked, false);
  await act(async () => check(view, "I approve sending").props.onChange({ target: { checked: true } }));
  await act(async () => button(view, "Send approved OpenAI request").props.onClick());
  assert.equal(calls.filter(call => call[0] === "send").length, 1);
  assert.deepEqual(calls.at(-1)[2], { digest: draft.preview.digest, approve: true });
});

test("only chosen observations are requested and verified frame zero routes to the correct evidence viewer", async t => {
  const index = analysis.observations.findIndex(item => item.references.some(ref => ref.frame === 0));
  const chosen = analysis.observations[index], inspected = [], calls = [], draft = record("analysis");
  const view = await render(t, { evidence: analysis, proposal: undefined, inspect: (...args) => inspected.push(args) }, {
    getAssistantStatus: async () => status,
    previewAssistant: async input => { calls.push(input); return draft; },
    sendAssistant: async () => ({ ...draft, state: "completed", reserved_output_tokens: 1024, analysis: { ...analysis,
      observation_ids: [index], observations: [chosen] } }),
  });
  await act(async () => button(view, "Open language assistance").props.onClick());
  await act(async () => view.root.findByType("textarea").props.onChange({ target: { value: "Explain contacts" } }));
  assert.equal(button(view, "Preview OpenAI disclosure").props.disabled, true);
  await act(async () => check(view, chosen.statement).props.onChange({ target: { checked: true } }));
  await act(async () => button(view, "Preview OpenAI disclosure").props.onClick());
  assert.deepEqual(calls[0].observation_ids, [index]); assert.equal(calls[0].simulation_id, analysis.simulation_id);
  await act(async () => check(view, "I approve sending").props.onChange({ target: { checked: true } }));
  await act(async () => button(view, "Send approved OpenAI request").props.onClick());
  const frame = view.root.findAllByType("button").find(node => text(node).startsWith("Inspect frame 0 ·"));
  await act(async () => frame.props.onClick());
  assert.deepEqual(inspected[0], [0, chosen.references.find(ref => ref.frame === 0).source]);
  assert.ok(text(view.toJSON()).includes("Missing evidence stays unknown"));
});

test("browser contracts reject expanded limits, missing coverage, frame corruption and cross-request responses", async () => {
  const value = record();
  assert.equal(isAssistantRecord(value, () => true), true);
  for (const mutate of [v => v.preview.budget.max_provider_calls = 2, v => v.preview.budget.max_output_tokens = 2049,
    v => v.preview.input_bytes++, v => v.preview.expires_at = "yesterday", v => v.preview.id = crypto.randomUUID(),
    v => v.state = "completed", v => v.preview.payload = "not JSON"]) {
    const bad = structuredClone(value); mutate(bad); assert.equal(isAssistantRecord(bad, () => true), false);
  }
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
    await assert.rejects(createApiClient("/api").getAssistantRequest(crypto.randomUUID()), ApiError);
  } finally { globalThis.fetch = original; }
});
