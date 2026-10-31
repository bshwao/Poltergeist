import test, { after } from "node:test";
import assert from "node:assert/strict";
import { loadComponents } from "../scripts/load-components.mjs";

const source = await loadComponents("hosted/client.ts");
const { createHostedClient, HostedError } = source.module;
const { mergeRuns, terminal, metric } = await source.moduleAt("hosted/workflow.ts");
after(() => source.cleanup());
const owner = "11111111-1111-4111-8111-111111111111", device = "22222222-2222-4222-8222-222222222222";
const request = "33333333-3333-4333-8333-333333333333", simulation = "44444444-4444-4444-8444-444444444444";
const timestamp = "2026-10-25T12:00:00Z";
const session = (id = owner) => ({ account: { account_id: id, username: "alice" }, csrf_token: "a".repeat(43), expires_at: timestamp });
const response = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
const accepted = { request_id: request, operation: "execute", status: "accepted", created_at: timestamp, updated_at: timestamp,
  simulation_id: simulation, reply: { kind: "accepted", request_id: request, simulation_id: simulation, accepted_at: timestamp } };

test("evidence is pulled per page, checked against its hash and bound to the requested run", async () => {
  const frames = [{ frame: 123, time_s: 2.5, speed_mps: null, collisions: null, traffic_violations: 0,
    control: { brake: 0, throttle: 0, steer: 0, hand_brake: false, reverse: false } }];
  const bytes = new TextEncoder().encode(JSON.stringify(frames));
  const digest = Buffer.from(await crypto.subtle.digest("SHA-256", bytes)).toString("hex");
  let corrupt = false, wrongRun = false, calls = 0;
  const client = createHostedClient(async (url, options) => {
    if (url.endsWith("login")) return response(session());
    calls++;
    assert.equal(url, `/v1/devices/${device}/evidence`);
    const body = JSON.parse(options.body);
    return response({ kind: "evidence", request_id: body.request_id, simulation_id: wrongRun ? device : simulation,
      resource: "telemetry", offset: 0, next_offset: 1500, version: "a".repeat(64), state: "available", total_bytes: 2000,
      data_base64: Buffer.from(bytes).toString("base64"), sha256: corrupt ? "0".repeat(64) : digest, frames: [] });
  });
  await client.login("alice", "private password");
  assert.equal(calls, 0);
  const page = await client.evidence(device, { simulation_id: simulation });
  assert.equal(page.frames[0].frame, 123);
  assert.equal(page.frames[0].collisions, null);
  assert.equal(page.frames[0].traffic_violations, 0);
  corrupt = true;
  await assert.rejects(client.evidence(device, { simulation_id: simulation }), error => error.code === "invalid_response");
  corrupt = false; wrongRun = true;
  await assert.rejects(client.evidence(device, { simulation_id: simulation }), error => error.code === "invalid_response");
});

test("hosted requests are fixed same-origin URLs with memory-only CSRF and no redirects", async () => {
  const seen = [];
  const client = createHostedClient(async (url, options) => { seen.push([url, options]); return response(url.endsWith("login") ? session() : accepted); });
  await client.login("alice", "private password");
  await client.send(device, { kind: "execute", request_id: request, scenario: {}, mode: "test", agent_id: null, options: { max_ticks: 10 } });
  assert.equal(seen[0][0], "/v1/auth/login");
  assert.equal(seen[1][0], `/v1/devices/${device}/commands`);
  assert.equal(seen[0][1].headers["X-CSRF-Token"], undefined);
  assert.equal(seen[1][1].headers["X-CSRF-Token"], "a".repeat(43));
  for (const [url, options] of seen) {
    assert.equal(options.mode, "same-origin"); assert.equal(options.credentials, "same-origin");
    assert.equal(options.redirect, "error"); assert.equal(options.cache, "no-store");
    assert.ok(!url.includes("private") && !url.includes("alice"));
  }
  await assert.rejects(client.history("https://untrusted.example"), /Check the submitted/);
  client.clear();
  await assert.rejects(client.send(device, { kind: "status", request_id: request }), error => error.status === 401);
  assert.equal(seen.length, 2);
});

test("an old response stream cannot clear or replace a newer account", async () => {
  let stream, started;
  const ready = new Promise(resolve => { started = resolve; });
  let calls = 0;
  const client = createHostedClient(async () => {
    calls++;
    if (calls !== 2) return response(session(calls === 1 ? owner : device));
    return new Response(new ReadableStream({ start(controller) { stream = controller; started(); } }), { status: 401, headers: { "content-type": "application/json" } });
  });
  await client.login("alice", "first private password");
  const stale = client.devices();
  await ready;
  const current = await client.login("alice", "second private password");
  assert.equal(current.account.account_id, device);
  stream.enqueue(new TextEncoder().encode('{"error":{"code":"session_expired"}}')); stream.close();
  await assert.rejects(stale, error => error.code === "stale_response");
  // A subsequent write reaches fetch, proving the newer CSRF session survived.
  await client.revoke(device);
  assert.equal(calls, 4);
});

test("cancelling a response stream preserves cancellation instead of reporting invalid evidence", async () => {
  let started;
  const ready = new Promise(resolve => { started = resolve; });
  const controller = new AbortController();
  const client = createHostedClient(async (url, options) => new Response(new ReadableStream({
    start(stream) {
      options.signal.addEventListener("abort", () => stream.error(new DOMException("Cancelled", "AbortError")), { once: true });
      started();
    },
  }), { headers: { "content-type": "application/json" } }));
  const pending = client.history(device, controller.signal);
  await ready;
  controller.abort();
  await assert.rejects(pending, error => error.code === "request_cancelled");
});

test("response size, shape, account ownership and command operation are checked", async () => {
  for (const body of ["x".repeat(2 * 1024 * 1024 + 1), '{"items":[],"next_before":"invalid"}']) {
    const client = createHostedClient(async () => new Response(body, { headers: { "content-type": "application/json" } }));
    await assert.rejects(client.history(device), error => error instanceof HostedError && error.code === "invalid_response");
  }
  let value = session();
  const client = createHostedClient(async () => response(value));
  await client.login("alice", "private password");
  value = [{ device: { device_id: device, owner: { account_id: device } }, connection: {} }];
  await assert.rejects(client.devices(), error => error.code === "invalid_response");
  value = { ...accepted, operation: "cancel" };
  await assert.rejects(client.command(device, request), error => error.code === "invalid_response");
  value = session();
  await assert.rejects(client.send(device, { kind: "execute", request_id: request, scenario: { name: "x".repeat(32768) } }), error => error.status === 400);
});

test("accepted and cancellation acknowledgements do not imply completion; receipts merge monotonically", () => {
  let runs = mergeRuns([], [accepted], device, 1);
  assert.equal(runs.length, 1); assert.equal(terminal(runs[0]), false);
  const summary = { simulation_id: simulation, device_id: device, ownership_generation: 1, execute_request_id: request,
    created_at: timestamp, status: "running", receipt_revision: 3, metrics: null };
  const result = { operation: "result", reply: { kind: "result", summary } };
  runs = mergeRuns(runs, [result], device, 1);
  assert.equal(terminal(runs[0]), false);
  const completed = { ...summary, status: "completed", receipt_revision: 4 };
  runs = mergeRuns(runs, [{ ...result, reply: { kind: "result", summary: completed } }, result], device, 1);
  assert.equal(terminal(runs[0]), true); assert.equal(runs[0].summary.receipt_revision, 4);
  assert.deepEqual(mergeRuns([], [result], device, 2), []);
  assert.deepEqual(mergeRuns([], [result], owner, 1), []);
  assert.equal(metric(0), "0"); assert.equal(metric(null), "Not measured"); assert.equal(metric(Infinity), "Not measured");
});
