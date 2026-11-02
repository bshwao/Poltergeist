import assert from "node:assert/strict";
import { after, test } from "node:test";
import { readFile } from "node:fs/promises";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const fixture = JSON.parse(await readFile(new URL("./fixtures/github-reporting.json", import.meta.url), "utf8"));
const loaded = await loadComponents("components/GitHubPublisher.tsx");
const { GitHubPublisher } = loaded.module;
const { api, createApiClient, ApiError } = await loaded.moduleAt("services/api.ts");
const { isGitHubPublication, isGitHubInputs, isGitHubStatus } = await loaded.moduleAt("services/github-contract.ts");
after(() => loaded.cleanup());
const clone = v => structuredClone(v);
const text = node => typeof node === "string" || typeof node === "number" ? String(node) : (node?.children ?? []).map(text).join("");
const button = (view, label) => view.root.findAllByType("button").find(item => text(item) === label);
const field = (view, label, type = "input") => view.root.findAllByType("label").find(item => text(item).startsWith(label)).findByType(type);
async function render(t, methods = {}, props = {}) {
  const defaults = { getGitHubStatus: async () => fixture.status, getGitHubInputs: async () => fixture.inputs };
  const original = Object.fromEntries(Object.keys({ ...defaults, ...methods }).map(key => [key, api[key]]));
  Object.assign(api, defaults, methods);
  let view; await act(async () => { view = TestRenderer.create(React.createElement(GitHubPublisher, { report: fixture.report, active: true, ...props })); });
  t.after(() => { act(() => view.unmount()); Object.assign(api, original); });
  return view;
}
async function fill(view) {
  await act(async () => button(view, "Open GitHub reporting").props.onClick());
  for (const [label, value] of [["Reference full GitHub commit SHA", fixture.request.reference.commit], ["Candidate full GitHub commit SHA", fixture.request.candidate.commit],
    ["Reference package directory", "pkg"], ["Candidate package directory", "pkg"]]) {
    await act(async () => field(view, label).props.onChange({ target: { value } }));
  }
}

test("real typed server fixtures pass browser provenance and publication contracts", () => {
  assert.equal(isGitHubStatus(fixture.status), true); assert.equal(isGitHubInputs(fixture.inputs), true);
  assert.equal(isGitHubPublication(fixture.preview), true); assert.equal(isGitHubPublication(fixture.published), true);
});

test("mount and disabled configuration send no verification or publication requests", async t => {
  const calls = [];
  const view = await render(t, { getGitHubStatus: async () => { calls.push("status"); return { ...fixture.status, enabled: false }; },
    getGitHubInputs: async () => calls.push("inputs"), previewGitHubCheck: async () => calls.push("preview"), publishGitHubCheck: async () => calls.push("publish") });
  assert.deepEqual(calls, []);
  await act(async () => button(view, "Open GitHub reporting").props.onClick());
  assert.deepEqual(calls, ["status"]); assert.ok(text(view.toJSON()).includes("GitHub reporting is off"));
});

test("commit verification and explicit disclosure approval precede one publish; edits revoke approval", async t => {
  const calls = [];
  const view = await render(t, { previewGitHubCheck: async input => { calls.push(["preview", input]); return fixture.preview; },
    publishGitHubCheck: async (...args) => { calls.push(["publish", ...args]); return fixture.published; } });
  await fill(view);
  await act(async () => button(view, "Verify commits and preview GitHub check").props.onClick());
  assert.deepEqual(calls[0][1].disclose, []); assert.equal(calls.length, 1);
  assert.equal(button(view, "Publish approved GitHub check").props.disabled, true);
  assert.equal(view.root.findByProps({ "aria-label": "Exact GitHub disclosure" }).props.tabIndex, 0);
  await act(async () => field(view, "I approve publishing").props.onChange({ target: { checked: true } }));
  await act(async () => field(view, "Candidate full GitHub commit SHA").props.onChange({ target: { value: "3".repeat(40) } }));
  assert.equal(button(view, "Publish approved GitHub check"), undefined);
  await act(async () => field(view, "Candidate full GitHub commit SHA").props.onChange({ target: { value: fixture.request.candidate.commit } }));
  await act(async () => button(view, "Verify commits and preview GitHub check").props.onClick());
  assert.equal(field(view, "I approve publishing").props.checked, false);
  await act(async () => field(view, "I approve publishing").props.onChange({ target: { checked: true } }));
  await act(async () => button(view, "Publish approved GitHub check").props.onClick());
  assert.equal(calls.filter(item => item[0] === "publish").length, 1);
  assert.deepEqual(calls.at(-1)[2], { digest: fixture.preview.preview.digest, approve: true });
});

test("refreshing a changed disclosure cannot carry forward its prior approval", async t => {
  const view = await render(t, { previewGitHubCheck: async () => fixture.preview,
    getGitHubPublication: async () => ({ ...clone(fixture.preview), preview: { ...clone(fixture.preview.preview), digest: "b".repeat(64) } }) });
  await fill(view);
  await act(async () => button(view, "Verify commits and preview GitHub check").props.onClick());
  await act(async () => field(view, "I approve publishing").props.onChange({ target: { checked: true } }));
  await act(async () => button(view, "Refresh saved GitHub publication").props.onClick());
  assert.equal(field(view, "I approve publishing").props.checked, false);
  assert.equal(button(view, "Publish approved GitHub check").props.disabled, true);
});

test("lost replies stay uncertain and reconciliation never submits another publication", async t => {
  let writes = 0, reads = 0;
  const view = await render(t, { previewGitHubCheck: async () => fixture.preview,
    publishGitHubCheck: async () => { writes++; return { ...fixture.preview, state: "uncertain", message: "Outcome unconfirmed" }; },
    reconcileGitHubCheck: async () => { reads++; return fixture.published; } });
  await fill(view);
  await act(async () => button(view, "Verify commits and preview GitHub check").props.onClick());
  await act(async () => field(view, "I approve publishing").props.onChange({ target: { checked: true } }));
  await act(async () => button(view, "Publish approved GitHub check").props.onClick());
  assert.equal(button(view, "Publish approved GitHub check"), undefined);
  await act(async () => button(view, "Reconcile GitHub check (read only)").props.onClick());
  assert.equal(writes, 1); assert.equal(reads, 1);
});

test("changed evidence is fenced and invalid short commits never leave the form", async t => {
  let previews = 0;
  const view = await render(t, { previewGitHubCheck: async () => { previews++; return { ...fixture.preview, preview: { ...fixture.preview.preview, source_digest: "b".repeat(64) } }; } });
  await fill(view);
  await act(async () => field(view, "Candidate full GitHub commit SHA").props.onChange({ target: { value: "main" } }));
  await act(async () => button(view, "Verify commits and preview GitHub check").props.onClick());
  assert.equal(previews, 0);
  await act(async () => field(view, "Candidate full GitHub commit SHA").props.onChange({ target: { value: fixture.request.candidate.commit } }));
  await act(async () => button(view, "Verify commits and preview GitHub check").props.onClick());
  assert.ok(text(view.toJSON()).includes("Regression evidence changed"));
  assert.equal(button(view, "Publish approved GitHub check"), undefined);
});

test("browser contracts reject wrong commits, false success, malformed proofs and unsafe result links", async () => {
  for (const change of [v => { const body = JSON.parse(v.preview.payload); body.head_sha = "a".repeat(40); v.preview.payload = JSON.stringify(body); },
    v => v.preview.candidate.package_files++, v => v.preview.app_id = null,
    v => { v.state = "published"; v.check_url = "https://github.com/other/private/runs/1"; v.check_run_id = 1; },
    v => { v.state = "published"; v.check_url = "javascript:alert(1)"; v.check_run_id = 1; },
    v => { v.preview.execution_status = "failed"; v.preview.conclusion = "success"; const body = JSON.parse(v.preview.payload); body.conclusion = "success"; v.preview.payload = JSON.stringify(body); }]) {
    const value = clone(fixture.preview); change(value); assert.equal(isGitHubPublication(value), false);
  }
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(JSON.stringify(fixture.preview), { status: 200, headers: { "Content-Type": "application/json" } });
    await assert.rejects(createApiClient("/api").getGitHubPublication(crypto.randomUUID()), ApiError);
  } finally { globalThis.fetch = original; }
});
