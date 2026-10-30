import test, { after } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { loadComponents } from "../scripts/load-components.mjs";

const fixture = JSON.parse(await readFile(new URL("./fixtures/experimentation.json", import.meta.url), "utf8"));
const loaded = await loadComponents("hosted/lab-client.ts");
const { createHostedLabClient } = loaded.module;
const { createHostedClient, HostedError } = await loaded.moduleAt("hosted/client.ts");
const { hostedLabRoute } = await loaded.moduleAt("hosted/LabWorkspace.tsx");
after(() => loaded.cleanup());
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const target = { device: { device_id: id(1), policy: { max_ticks: 5000 } } };
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const reply = (command, result) => ({ kind: "lab", request_id: command.request_id, result });

test("hosted guided routes retain exact run/frame/plan identities", () => {
  assert.deepEqual(hostedLabRoute(`#lab/suite-run/${id(4)}`), { section: "guide", suiteRunId: id(4) });
  assert.deepEqual(hostedLabRoute(`#discovery/${id(2)}`), { section: "discovery", planId: id(2) });
  assert.deepEqual(hostedLabRoute(`#lab-run=${id(3)}&frame=42`), { section: "run", runId: id(3), frame: 42 });
  for (const value of [`#lab-run=${id(3)}&frame=-1`, `#discovery/${id(2)}/extra`, "#lab-run=arbitrary"]) assert.equal(hostedLabRoute(value).section, "legacy");
});
test("suite history pages bind every operation to its requested saved definition", async () => {
  let wrong = false;
  const adapter = createHostedLabClient({ lab: async (_, command) => reply(command, { kind: "suite_runs", items: [wrong ? { ...fixture.suite_report.evidence.operation, request: { ...fixture.suite_report.evidence.operation.request, suite_id: id(99) } } : fixture.suite_report.evidence.operation], snapshot_at: fixture.plan.created_at, next_offset: null }) }, target, () => true);
  try {
    assert.equal((await adapter.listSuiteRuns(fixture.suite.id))[0].id, fixture.suite_report.evidence.operation.id);
    wrong = true; await assert.rejects(adapter.listSuiteRuns(fixture.suite.id), /inconsistent/);
  } finally { adapter.dispose(); }
});
test("ambiguous write retries keep their original identity; accepted writes get a new identity", async () => {
  const commands = []; let fail = true;
  const adapter = createHostedLabClient({ lab: async (_, command) => { commands.push(command); if (fail) throw new HostedError("network_error"); return reply(command, { kind: "plan", plan: fixture.plan }); } }, target, () => true);
  try {
    await assert.rejects(adapter.proposeDiscovery(fixture.plan.proposal));
    fail = false; await adapter.proposeDiscovery(fixture.plan.proposal);
    assert.equal(commands[0].request_id, commands[1].request_id);
    await adapter.proposeDiscovery(fixture.plan.proposal); assert.notEqual(commands[1].request_id, commands[2].request_id);
    assert.equal(commands[0].action.proposal.options.max_ticks, 5000);
  } finally { adapter.dispose(); }
});
test("mutations are serial and disposed contexts cannot dispatch queued actions", async () => {
  const pending = deferred(), seen = []; let signal;
  const adapter = createHostedLabClient({ lab: async (_, command, current) => { seen.push(command); signal = current; return pending.promise; } }, target, () => true);
  const first = adapter.getGallery(), second = adapter.listDiscoveryPlans();
  await Promise.resolve(); assert.equal(seen.length, 1);
  adapter.dispose(); assert.equal(signal.aborted, true);
  pending.resolve(reply(seen[0], { kind: "gallery", items: fixture.gallery }));
  await assert.rejects(first, error => error.name === "AbortError");
  await assert.rejects(second, error => error.name === "AbortError"); assert.equal(seen.length, 1);
});
test("paged native replies are combined with a fixed snapshot and checked cursors", async () => {
  const commands = [], when = fixture.plan.created_at;
  const adapter = createHostedLabClient({ lab: async (_, command) => {
    commands.push(command); const offset = command.action.offset;
    return reply(command, { kind: "plans", items: [{ ...fixture.plan, id: id(offset + 10) }], snapshot_at: when, next_offset: offset < 2 ? offset + 1 : null });
  } }, target, () => true);
  try {
    const values = await adapter.listDiscoveryPlans({ limit: 3 }); assert.equal(values.length, 3);
    assert.deepEqual(commands.map(c => c.action.offset), [0, 1, 2]); assert.equal(commands[0].action.before, null);
    assert.equal(commands[1].action.before, when); assert.equal(commands[2].action.limit, 1);
  } finally { adapter.dispose(); }
});
test("duplicate native rows, changed snapshots and nonadvancing pages fail closed", async () => {
  for (const failure of ["duplicate", "snapshot", "cursor"]) {
    let calls = 0;
    const adapter = createHostedLabClient({ lab: async (_, command) => { calls++; return reply(command, { kind: "plans", items: [{ ...fixture.plan, id: failure === "duplicate" ? id(2) : id(calls + 2) }], snapshot_at: failure === "snapshot" && calls > 1 ? new Date().toISOString() : fixture.plan.created_at, next_offset: failure === "cursor" ? 0 : command.action.offset + 1 }); } }, target, () => true);
    try { await assert.rejects(adapter.listDiscoveryPlans({ limit: 2 }), /inconsistent/); } finally { adapter.dispose(); }
  }
});
test("suite launches retain a single inner/outer identity and custom agents stay local", async () => {
  const requests = [], operation = { ...fixture.suite_report.evidence.operation, id: id(9) };
  const adapter = createHostedLabClient({ lab: async (_, command) => { requests.push(command); return reply(command, command.action.kind === "suite_get" ? { kind: "suite", suite: fixture.suite } : { kind: "operation", operation }); } }, target, () => true);
  try {
    await adapter.runSuite(fixture.suite.id, 1, id(9));
    assert.equal(requests[1].request_id, id(9)); assert.equal(requests[1].action.launch.request_id, id(9));
    assert.equal(requests[1].mode, "test"); assert.deepEqual(await adapter.listAgents(), []);
  } finally { adapter.dispose(); }
});
test("unavailable approval scope dispatches nothing and 401 clears the owning UI", async () => {
  let count = 0, failures = 0;
  const adapter = createHostedLabClient({ lab: async () => { count++; throw new HostedError("authentication_required", 401); } }, target, () => false);
  try { await assert.rejects(adapter.getGallery()); assert.equal(count, 0); } finally { adapter.dispose(); }
  const expired = createHostedLabClient({ lab: async () => { throw new HostedError("authentication_required", 401); } }, target, () => true, () => failures++);
  try { await assert.rejects(expired.getGallery()); assert.equal(failures, 1); } finally { expired.dispose(); }
});
test("experiment RPCs use same-origin CSRF and reject a reply for another request", async () => {
  let corrupt = false, seen;
  const client = createHostedClient(async (url, options) => {
    if (url.endsWith("login")) return new Response(JSON.stringify({ account: { account_id: id(1), username: "alice" }, csrf_token: "a".repeat(43), expires_at: "2099-01-01T00:00:00Z" }), { status: 200, headers: { "content-type": "application/json" } });
    seen = [url, options]; const command = JSON.parse(options.body);
    return new Response(JSON.stringify({ kind: "lab", request_id: corrupt ? id(99) : command.request_id, result: { kind: "gallery", items: fixture.gallery } }), { status: 200, headers: { "content-type": "application/json" } });
  });
  await client.login("alice", "private password");
  const command = { kind: "lab", request_id: id(2), action: { kind: "gallery" }, mode: null };
  await client.lab(id(1), command); assert.equal(seen[0], `/v1/devices/${id(1)}/experimentation`);
  assert.equal(seen[1].headers["X-CSRF-Token"], "a".repeat(43)); assert.equal(seen[1].mode, "same-origin"); assert.equal(seen[1].redirect, "error");
  corrupt = true; await assert.rejects(client.lab(id(1), command), error => error.code === "invalid_response");
});
