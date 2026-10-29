import test, { after } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { loadComponents } from "../scripts/load-components.mjs";

const source = await loadComponents("hosted/client.ts");
const { createHostedClient, MAX_SAMPLE_VIEW_BYTES } = source.module;
after(() => source.cleanup());
const device = "22222222-2222-4222-8222-222222222222", run = "44444444-4444-4444-8444-444444444444";
const hash = data => createHash("sha256").update(data).digest("hex");
const response = value => new Response(JSON.stringify(value), { headers: { "content-type": "application/json" } });
const bytes = Uint8Array.from({ length: 200000 }, (_, index) => index % 251);
const selected = { sample_index: 50, index_offset: 1234, kind: "camera", frame: 732, timestamp: 91.5,
  byte_length: bytes.length, sha256: hash(bytes), metadata: { encoding: "bgra8", width: 250, height: 200 } };

async function fixture(change = value => value, sample = selected) {
  const requests = [];
  const client = createHostedClient(async (url, options) => {
    if (url.endsWith("login")) return response({ account: { account_id: device, username: "alice" },
      csrf_token: "a".repeat(43), expires_at: "2026-10-30T12:00:00Z" });
    const request = JSON.parse(options.body); requests.push(request);
    assert.equal(request.simulation_id, run);
    const chunk = bytes.subarray(request.offset, request.offset + 65536);
    const end = request.offset + chunk.length;
    return response(change({ kind: "evidence", request_id: request.request_id, simulation_id: run,
      artifact_index: 2, resource: "sample", state: "available", offset: request.offset,
      next_offset: end < bytes.length ? end : null, version: "a".repeat(64), total_bytes: bytes.length,
      data_base64: Buffer.from(chunk).toString("base64"), sha256: hash(chunk), sample_sha256: sample.sha256,
      frame: sample.frame, timestamp: sample.timestamp,
      // JSON key order differs between catalogue and chunk; identity is semantic.
      sample: Object.fromEntries(Object.entries(sample).reverse()) }));
  });
  await client.login("alice", "fixture passphrase");
  return { client, requests };
}

test("selected recordings use serial bounded chunks and verify the complete recorded identity", async () => {
  const { client, requests } = await fixture();
  const progress = [];
  const data = await client.sample(device, run, 2, selected, undefined, (received, total) => progress.push([received, total]));
  assert.deepEqual(new Uint8Array(data), bytes);
  assert.deepEqual(requests.map(value => value.offset), [0, 65536, 131072, 196608]);
  assert.ok(requests.every(value => value.sample_index === 50 && value.sample_cursor === 1234 && value.artifact_index === 2));
  assert.equal(requests[0].version, null);
  assert.ok(requests.slice(1).every(value => value.version === "a".repeat(64)));
  assert.deepEqual(progress.at(-1), [bytes.length, bytes.length]);
});

test("chunk corruption, cursor changes and wrong recorded frames are rejected before more bytes", async () => {
  for (const change of [value => ({ ...value, sha256: "0".repeat(64) }),
    value => ({ ...value, next_offset: 1 }), value => ({ ...value, artifact_index: 1 }),
    value => ({ ...value, frame: 733, sample: { ...value.sample, frame: 733 } })]) {
    const { client, requests } = await fixture(change);
    await assert.rejects(client.sample(device, run, 2, selected), error => error.code === "invalid_response");
    assert.equal(requests.length, 1);
  }
});

test("whole-sample SHA-256 catches corruption even if every transferred chunk hashes correctly", async () => {
  const wrong = { ...selected, sha256: "f".repeat(64) };
  const { client, requests } = await fixture(value => value, wrong);
  await assert.rejects(client.sample(device, run, 2, wrong), /checksum does not match/);
  assert.equal(requests.length, 4);
});

test("cancelling a sample stops after its in-flight chunk and returns no preview", async () => {
  const { client, requests } = await fixture();
  const controller = new AbortController();
  await assert.rejects(client.sample(device, run, 2, selected, controller.signal, () => controller.abort()),
    error => error.code === "request_cancelled");
  assert.equal(requests.length, 1);
});

test("preview storage is bounded before any allocation or network request", async () => {
  const { client, requests } = await fixture();
  assert.equal(MAX_SAMPLE_VIEW_BYTES, 4 * 1024**2);
  const tooLarge = { ...selected, byte_length: 1025 * 1025 * 4, metadata: { encoding: "bgra8", width: 1025, height: 1025 } };
  await assert.rejects(client.sample(device, run, 2, tooLarge), /preview limit/);
  assert.equal(requests.length, 0);
});

test("lost and changed sample states remain unavailable and never produce a fabricated empty preview", async () => {
  for (const state of ["missing", "deleted", "changed", "limit_exceeded"]) {
    const { client, requests } = await fixture(value => ({ ...value, state }));
    await assert.rejects(client.sample(device, run, 2, selected), /unavailable|removed|changed/);
    assert.equal(requests.length, 1);
  }
});
