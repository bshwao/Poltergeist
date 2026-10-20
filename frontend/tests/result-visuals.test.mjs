import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { after, test } from "node:test";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const loaded = await loadComponents("components/MetricsChart.tsx");
const { MetricsChart } = loaded.module;
const { SafetyMetrics } = await loaded.moduleAt("components/SafetyMetrics.tsx");
const { SimulationViewer } = await loaded.moduleAt("components/SimulationViewer.tsx");
const { api, ApiError } = await loaded.moduleAt("services/api.ts");
after(() => loaded.cleanup());
const ID = "894d8ac8-43aa-4111-9bf3-deec7f039f6f";
const OTHER = "b94d8ac8-43aa-4111-9bf3-deec7f039f6f";
function text(node) { return typeof node === "string" || typeof node === "number" ? String(node) : (node?.children ?? []).map(text).join(""); }
function button(view, label) { const found = view.root.findAllByType("button").find(node => text(node) === label); assert.ok(found, `Missing button ${label}`); return found; }
async function click(view, label) { await act(async () => { await button(view, label).props.onClick(); }); }
function deferred() { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { resolve, reject, promise }; }
function setup(t, Component, props, options) {
  let view; act(() => { view = TestRenderer.create(React.createElement(Component, props), options); });
  t.after(() => act(() => view.unmount())); return view;
}
function result(mode = "test", artifacts = []) { return {
  simulation: { id: ID, mode, status: "completed" }, artifacts, measured_duration_s: 1, telemetry_frames: 3, runtime: null,
  metrics: { severity: null, collision_count: 0, min_ttc_s: null, min_clearance_m: 0, max_deceleration_mps2: null, hard_braking_count: 0, traffic_violation_count: null, max_stopping_time_s: null, max_stopping_distance_m: null, evaluation_settings: null,
    coverage: { actor_pairs: 2, total_frames: 3, collision_frames: 3, ttc_frames: 0, ttc_pairs: 0, clearance_frames: 2, clearance_pairs: 2, braking_frames: 1, traffic_rule_frames: 0, has_gaps: false } },
}; }
function frame(index, time, speed) { return { frame: index, time_s: time, ego: { velocity_mps: speed === null ? null : { x: speed, y: 0, z: 0 }, acceleration_mps2: null }, control: { throttle: 0.5, brake: 0, steer: 0 }, collisions: null, traffic_violations: [] }; }
function page(offset = 0, total = 201) { return { offset, total, limit: 200, frames: Array.from({ length: Math.min(200, total - offset) }, (_, index) => frame(offset + index + 1, (offset + index) / 20, index === 1 ? null : 4)) }; }

test("safety cards preserve zero and unknown, mode, coverage, and provenance", t => {
  const view = setup(t, SafetyMetrics, { result: result() });
  const cards = view.root.findAll(node => node.props.className === "safety-card");
  assert.match(text(cards[0]), /SeverityUnknown/); assert.match(text(cards[1]), /Collisions0events/);
  assert.match(text(cards[3]), /Minimum clearance0.00m/);
  assert.match(text(view.root), /Synthetic test · completed/); assert.match(text(view.root), /3 \/ 3/);
  assert.match(text(view.root), /Scoring method: Unknown/); assert.match(text(view.root), /Evaluation settings were not recorded/);
});
test("cancelled and gapped results explicitly label partial observations", t => {
  const data = result("carla"); data.simulation.status = "cancelled"; data.metrics.coverage.has_gaps = true;
  const view = setup(t, SafetyMetrics, { result: data });
  assert.match(text(view.root), /CARLA · cancelled/); assert.match(text(view.root), /Partial run or observation gaps/);
});
test("telemetry fetch is bounded and next page uses offset200 without fetching whole trace", async t => {
  const calls = []; api.getTelemetry = async (id, pagination) => { calls.push([id, pagination]); return page(pagination.offset); };
  const view = setup(t, MetricsChart, { simulationId: ID }); await act(async () => {});
  assert.deepEqual(calls, [[ID, { limit: 200, offset: 0 }]]);
  assert.match(text(view.root), /Frames 1–200 of 201/); assert.match(text(view.root), /No directly measured longitudinal deceleration/);
  assert.equal(view.root.findAllByType("svg").length, 1);
  await click(view, "Next frames"); assert.deepEqual(calls[1], [ID, { limit: 200, offset: 200 }]);
  assert.match(text(view.root), /Frames 201–201 of 201/);
});
test("frame scrubber reports unknown measurements instead of fabricated zeros", async t => {
  api.getTelemetry = async () => page(); const view = setup(t, MetricsChart, { simulationId: ID }); await act(async () => {});
  act(() => view.root.findByProps({ type: "range" }).props.onChange({ target: { value: "1" } }));
  const inspector = view.root.findByProps({ className: "telemetry-inspector" });
  assert.match(text(inspector), /SpeedUnknown/); assert.match(text(inspector), /Collision \/ rule eventsUnknown \/ 0/);
});
test("a recorded time-gap limit splits otherwise contiguous plotted observations", async t => {
  api.getTelemetry = async () => ({ offset: 0, limit: 200, total: 3, frames: [frame(1, 0, 4), frame(2, 0.1, 3), frame(3, 2, 2)] });
  const view = setup(t, MetricsChart, { simulationId: ID, maxSampleGapS: 0.5 }); await act(async () => {});
  assert.equal(view.root.findAllByType("polyline").length, 1); assert.equal(view.root.findAllByType("circle").length, 1);
  assert.match(text(view.root), /Time gaps over 0.500 s also break chart lines/);
  await act(async () => view.update(React.createElement(MetricsChart, { simulationId: ID })));
  assert.equal(view.root.findAllByType("polyline").length, 1); assert.equal(view.root.findAllByType("circle").length, 0);
  assert.doesNotMatch(text(view.root), /Time gaps over/);
});
test("empty trace and failed telemetry page have useful retry states", async t => {
  let attempts = 0; api.getTelemetry = async () => { if (++attempts === 1) throw new ApiError("Trace is unavailable.", 404); return { frames: [], total: 0, offset: 0, limit: 200 }; };
  const view = setup(t, MetricsChart, { simulationId: ID }); await act(async () => {});
  assert.match(text(view.root), /Trace is unavailable/); await click(view, "Retry telemetry");
  assert.match(text(view.root), /No telemetry was recorded/); assert.equal(button(view, "Next frames").props.disabled, true);
});
test("telemetry aborts when hidden and ignores a late response from the prior run", async t => {
  const first = deferred(); let signal; api.getTelemetry = (id, pagination, options) => { if (id === ID) { signal = options.signal; return first.promise; } return Promise.resolve({ ...page(), frames: [frame(9, 9, 7)] }); };
  const view = setup(t, MetricsChart, { simulationId: ID });
  await act(async () => { view.update(React.createElement(MetricsChart, { simulationId: OTHER })); });
  assert.equal(signal.aborted, true);
  await act(async () => { first.resolve(page()); }); assert.match(text(view.root), /9 \/ 9.000 s/);
  let hiddenSignal; api.getTelemetry = (id, pagination, options) => { hiddenSignal = options.signal; return new Promise(() => {}); };
  act(() => view.update(React.createElement(MetricsChart, { simulationId: ID })));
  act(() => view.update(React.createElement(MetricsChart, { simulationId: ID, active: false })));
  assert.equal(hiddenSignal.aborted, true);
});
test("switching runs resets telemetry page before starting a request", async t => {
  const calls = []; api.getTelemetry = async (id, pagination) => { calls.push([id, pagination.offset]); return page(pagination.offset, 1000); };
  const view = setup(t, MetricsChart, { simulationId: ID }); await act(async () => {}); await click(view, "Next frames");
  await act(async () => view.update(React.createElement(MetricsChart, { simulationId: OTHER })));
  assert.deepEqual(calls, [[ID, 0], [ID, 200], [OTHER, 0]]);
});
test("synthetic artifacts explain missing captures and use only owned indexed URLs", t => {
  const view = setup(t, SimulationViewer, { result: result("test", [{ kind: "telemetry", media_type: "application/x-ndjson", path: "untrusted/path" }]) });
  assert.match(text(view.root), /Synthetic test runs do not produce CARLA/);
  const link = view.root.findByType("a"); assert.equal(link.props.href, api.artifactUrl(ID, 0)); assert.doesNotMatch(link.props.href, /untrusted/);
});
const pixels = new Uint8Array([3, 2, 1, 255]);
const recording = [{ kind: "recording", media_type: "application/json", path: "recordings/manifest.json" }];
function manifest() { return { format: "raw-sensor-v1", simulation_id: ID, artifact_id: OTHER, sample_count: 2, total_bytes: 8, samples: [0, 1].map(index => ({ kind: "camera", frame: index, timestamp: index / 20, file: `0000000${index}-camera.bin`, byte_length: 4, sha256: createHash("sha256").update(pixels).digest("hex"), metadata: { encoding: "bgra8", width: 1, height: 1 } })) }; }
test("capture metadata and bytes are lazy, and camera canvas receives actual verified pixels", async t => {
  let manifests = 0, samples = 0, drawn;
  api.getCaptureManifest = async () => { manifests += 1; return manifest(); }; api.getCaptureSample = async () => { samples += 1; return pixels.slice().buffer; };
  const view = setup(t, SimulationViewer, { result: result("carla", recording) }, { createNodeMock: node => node.type === "canvas" ? { getContext: () => ({ createImageData: () => ({ data: new Uint8ClampedArray(4) }), putImageData: image => { drawn = [...image.data]; } }) } : null });
  assert.equal(manifests, 0); assert.equal(samples, 0); await click(view, "Load capture manifest"); assert.equal(manifests, 1); assert.equal(samples, 0);
  await click(view, "Inspect sample"); assert.equal(samples, 1); assert.deepEqual(drawn, [1, 2, 3, 255]); assert.match(text(view.root), /SHA-256 and byte length verified/);
  await click(view, "Next sample"); assert.equal(view.root.findAllByType("canvas").length, 0); assert.match(text(view.root), /1 \/ 0.050 s/);
});
test("a corrupt sample is never displayed", async t => {
  api.getCaptureManifest = async () => manifest(); api.getCaptureSample = async () => new Uint8Array([1, 2, 3, 4]).buffer;
  const view = setup(t, SimulationViewer, { result: result("carla", recording) }); await click(view, "Load capture manifest"); await click(view, "Inspect sample");
  assert.match(text(view.root), /checksum does not match/); assert.equal(view.root.findAllByType("canvas").length, 0);
});
test("capture navigation aborts old sample reads and late pixels cannot replace selected metadata", async t => {
  const old = deferred(); let signal; api.getCaptureManifest = async () => manifest(); api.getCaptureSample = (id, artifact, index, options) => { signal = options.signal; return old.promise; };
  const view = setup(t, SimulationViewer, { result: result("carla", recording) }); await click(view, "Load capture manifest");
  let promise; act(() => { promise = button(view, "Inspect sample").props.onClick(); });
  await click(view, "Next sample"); assert.equal(signal.aborted, true);
  await act(async () => { old.resolve(pixels.slice().buffer); await promise; });
  assert.match(text(view.root), /1 \/ 0.050 s/); assert.equal(view.root.findAllByType("canvas").length, 0);
});
test("capture requests abort on hiding, and a different run starts with no old manifest", async t => {
  const pending = deferred(); let signal; api.getCaptureManifest = (id, index, options) => { signal = options.signal; return pending.promise; };
  const data = result("carla", recording); const view = setup(t, SimulationViewer, { result: data });
  let promise; act(() => { promise = button(view, "Load capture manifest").props.onClick(); });
  act(() => view.update(React.createElement(SimulationViewer, { result: data, active: false }))); assert.equal(signal.aborted, true);
  await act(async () => { pending.resolve(manifest()); await promise; }); assert.doesNotMatch(text(view.root), /2 raw samples/);
  const next = result("carla", recording); next.simulation.id = OTHER;
  act(() => view.update(React.createElement(SimulationViewer, { result: next }))); assert.equal(button(view, "Load capture manifest").props.disabled, false);
});
test("LiDAR renders actual verified point counts and explicitly labels byte order", async t => {
  const points = new Float32Array([1, 2, 3, 0.5, -2, 4, 5, 0.8]);
  const capture = manifest(); capture.samples = [{ kind: "lidar", frame: 7, timestamp: 2.5, file: "00000000-lidar.bin", byte_length: points.byteLength, sha256: createHash("sha256").update(new Uint8Array(points.buffer)).digest("hex"), metadata: { encoding: "float32_xyzi", channels: 16 } }]; capture.sample_count = 1; capture.total_bytes = points.byteLength;
  api.getCaptureManifest = async () => capture; api.getCaptureSample = async () => points.buffer;
  const view = setup(t, SimulationViewer, { result: result("carla", recording) }); await click(view, "Load capture manifest"); await click(view, "Inspect sample");
  assert.match(text(view.root), /2 XYZI points · 2 finite points/); assert.match(text(view.root), /decoded as little endian/);
  assert.match(text(view.root), /X-2.0001.000/); assert.equal(view.root.findAllByType("canvas").length, 0);
});
test("a rejected manifest can be retried without exposing an unexpected diagnostic", async t => {
  let attempts = 0; api.getCaptureManifest = async () => { if (++attempts === 1) throw new Error("private server traceback"); return manifest(); };
  const view = setup(t, SimulationViewer, { result: result("carla", recording) }); await click(view, "Load capture manifest");
  assert.match(text(view.root), /Could not load the raw capture manifest/); assert.doesNotMatch(text(view.root), /private server/);
  await click(view, "Load capture manifest"); assert.match(text(view.root), /2 raw samples/);
});
test("unmount aborts an outstanding telemetry read and an outstanding raw sample read", async t => {
  let telemetrySignal, captureSignal;
  api.getTelemetry = (id, options, request) => { telemetrySignal = request.signal; return new Promise(() => {}); };
  let chart; act(() => { chart = TestRenderer.create(React.createElement(MetricsChart, { simulationId: ID })); });
  act(() => chart.unmount()); assert.equal(telemetrySignal.aborted, true);
  api.getCaptureManifest = async () => manifest(); api.getCaptureSample = (id, artifact, index, request) => { captureSignal = request.signal; return new Promise(() => {}); };
  const viewer = setup(t, SimulationViewer, { result: result("carla", recording) }); await click(viewer, "Load capture manifest");
  act(() => { button(viewer, "Inspect sample").props.onClick(); }); act(() => viewer.unmount()); assert.equal(captureSignal.aborted, true);
});

test('synthetic sensor recordings disclose fixtures and identify the named stream', async t => {
  const capture = manifest(); capture.samples[0].metadata.agent_sensor_id = 'front-camera';
  api.getCaptureManifest = async () => capture;
  const data = result('test', recording); data.agent_inputs = { source: 'deterministic_fixture' };
  const view = setup(t, SimulationViewer, { result: data });
  assert.match(text(view.root), /deterministic fixtures.*not rendered CARLA frames/);
  await click(view, 'Load capture manifest');
  assert.match(text(view.root), /Camera · front-camera/);
});
