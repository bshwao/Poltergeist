import assert from "node:assert/strict";
import { after, afterEach, test } from "node:test";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const loaded = await loadComponents("components/ReportDownloads.tsx");
const { ReportDownloads } = loaded.module;
const { api, createApiClient, ApiError } = await loaded.moduleAt("services/api.ts");
const originalFetch = globalThis.fetch, originalDocument = globalThis.document;
afterEach(() => { globalThis.fetch = originalFetch; if (originalDocument === undefined) delete globalThis.document; else globalThis.document = originalDocument; });
after(() => loaded.cleanup());
test("run exports request full telemetry only after the user selects it for JSON", t => {
  const id = "00000000-0000-4000-8000-000000000001";
  let view;
  act(() => { view = TestRenderer.create(React.createElement(ReportDownloads, { simulationId: id })); });
  t.after(() => act(() => view.unmount()));
  const links = () => view.root.findAllByType("a");
  assert.equal(links().length, 3);
  assert.ok(links().every(link => link.props.download === true && link.props.href.includes(`/reports/simulations/${id}?format=`)));
  assert.ok(links().every(link => !link.props.href.includes("include_telemetry")));
  assert.ok(links().every(link => link.props.href.endsWith("&include_behavior=true")));
  act(() => view.root.findAllByType("input")[0].props.onChange({ target: { checked: true } }));
  assert.match(links()[0].props.href, /format=json&include_telemetry=true&include_behavior=true$/);
  assert.match(links()[1].props.href, /format=csv&include_behavior=true$/);
  assert.match(links()[2].props.href, /format=html&include_behavior=true$/);
  act(() => view.root.findAllByType("input")[1].props.onChange({ target: { checked: false } }));
  assert.match(links()[1].props.href, /format=csv$/);
});

const ID = "00000000-0000-4000-8000-000000000001", OTHER = "00000000-0000-4000-8000-000000000002";
const text = node => typeof node === "string" || typeof node === "number" ? String(node) : (node?.children ?? []).map(text).join("");
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { resolve, reject, promise }; };
const click = (view, format = "json", change = {}) => act(async () => view.root.findAllByType("a").find(link => link.props.href.includes(`format=${format}`)).props.onClick({ button: 0, preventDefault() {}, ...change }));
async function mount(t, props = {}) { let view; await act(async () => { view = TestRenderer.create(React.createElement(ReportDownloads, { simulationId: ID, ...props })); }); t.after(() => act(() => view.unmount())); return view; }
function mockSave() {
  const files = [];
  globalThis.document = { body: { appendChild() {} }, createElement() { return { click() { files.push(this.download); }, remove() {} }; } };
  return files;
}
function reply(format = "json", options = {}) { return new Response(options.body ?? "report", { status: options.status ?? 200, headers: {
  "Content-Type": { json: "application/json", csv: "text/csv", html: "text/html" }[format], "X-Poltergeist-Report-Schema": "poltergeist-run-report-v2", ...options.headers } }); }

test("all report file types retain the enhanced flag, MIME type and AbortSignal", async () => {
  const client = createApiClient("/api"), controller = new AbortController();
  for (const format of ["json", "csv", "html"]) {
    globalThis.fetch = async (url, options) => { assert.equal(url, `/api/reports/simulations/${ID}?format=${format}&include_behavior=true`); assert.equal(options.signal, controller.signal); return reply(format); };
    assert.equal(await (await client.getReport(ID, format, false, true, { signal: controller.signal })).text(), "report");
  }
});

test("report response rejects old schemas, wrong types, empty or incomplete transfers", async () => {
  const client = createApiClient("/api");
  for (const options of [ { headers: { "X-Poltergeist-Report-Schema": "poltergeist-run-report-v1" } },
    { status: 202 }, { body: "" }, { headers: { "Content-Type": "text/html" } },
    { headers: { "Content-Length": "999" } }, { headers: { "Content-Length": "invalid" } } ]) {
    globalThis.fetch = async () => reply("json", options); await assert.rejects(client.getReport(ID, "json"), ApiError);
  }
  globalThis.fetch = async () => reply("json", { headers: { "X-Poltergeist-Report-Schema": "poltergeist-run-report-v1" } });
  assert.equal((await client.getReport(ID, "json", false, false)).size, 6);
});

test("proxy compression does not compare decoded bytes against encoded Content-Length", async () => {
  globalThis.fetch = async () => reply("json", { headers: { "Content-Length": "2", "Content-Encoding": "gzip" } });
  assert.equal((await createApiClient("/api").getReport(ID, "json")).size, 6);
});

test("report failures stay actionable without exposing server HTML, and abort retains identity", async () => {
  const client = createApiClient("/api");
  globalThis.fetch = async () => new Response("<h1>SECRET_TRACEBACK</h1>", { status: 503 });
  await assert.rejects(client.getReport(ID, "json"), error => error instanceof ApiError && !error.message.includes("SECRET"));
  const abort = new DOMException("Superseded", "AbortError"); globalThis.fetch = async () => { throw abort; };
  await assert.rejects(client.getReport(ID, "json"), error => error === abort);
  globalThis.fetch = async () => { throw new Error("private network details"); };
  await assert.rejects(client.getReport(ID, "json"), error => error instanceof ApiError && /backend is running/.test(error.message));
});

test("completed report downloads use owned filenames and show completion", async t => {
  const files = mockSave(), calls = [];
  api.getReport = async (...args) => { calls.push(args); return new Blob(["report"]); };
  const view = await mount(t);
  for (const format of ["json", "csv", "html"]) { await click(view, format); assert.match(text(view.root), /sent to your browser’s downloads/); }
  assert.deepEqual(files, ["json", "csv", "html"].map(format => `poltergeist-${ID}.${format}`));
  assert.ok(calls.every(args => args[3] === true));
});

test("cancelled, stale and inactive requests never save a file or replace a new run", async t => {
  const files = mockSave(), pending = deferred(); let signal;
  api.getReport = async (...args) => { signal = args[4].signal; return pending.promise; };
  const view = await mount(t); await click(view);
  assert.ok(view.root.findAllByType("a").every(a => a.props["aria-disabled"]));
  await act(async () => view.root.findByProps({ children: "Cancel download" }).props.onClick());
  assert.equal(signal.aborted, true); assert.match(text(view.root), /download cancelled/);
  await act(async () => pending.resolve(new Blob(["old"]))); assert.deepEqual(files, []);
  const next = deferred(); api.getReport = async (...args) => { signal = args[4].signal; return next.promise; };
  await click(view);
  await act(async () => view.update(React.createElement(ReportDownloads, { simulationId: OTHER, active: false })));
  assert.equal(signal.aborted, true);
  await act(async () => next.resolve(new Blob(["stale"]))); assert.deepEqual(files, []);
  assert.ok(view.root.findAllByType("a").every(a => a.props["aria-disabled"]));
});

test("timeout unlocks retry and late data cannot save an obsolete file", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const files = mockSave(), pending = deferred();
  api.getReport = () => pending.promise;
  const view = await mount(t); await click(view);
  await act(async () => t.mock.timers.tick(60_000));
  assert.match(text(view.root), /Report download timed out/); assert.ok(view.root.findAllByType("a").every(a => !a.props["aria-disabled"]));
  api.getReport = async () => new Blob(["retry"]); await click(view, "csv");
  await act(async () => pending.resolve(new Blob(["old"])));
  assert.deepEqual(files, [`poltergeist-${ID}.csv`]);
});

test("failed export permits retry, and modified clicks retain normal download links", async t => {
  let reads = 0; api.getReport = async () => { reads++; throw new ApiError("Stored artifact unavailable", 503); };
  const view = await mount(t);
  await click(view, "html", { ctrlKey: true }); assert.equal(reads, 0);
  await click(view, "html"); assert.match(text(view.root), /Stored artifact unavailable/);
  const files = mockSave(); api.getReport = async () => new Blob(["retry"]);
  await click(view, "html"); assert.deepEqual(files, [`poltergeist-${ID}.html`]);
});
