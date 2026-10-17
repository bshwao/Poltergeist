import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { loadTypeScript } from "../scripts/load-typescript.mjs";

const { measurement, telemetryPoints, seriesSegments, verifySample, decodeBgra, inspectLidar } = await loadTypeScript(new URL("../src/components/visual-data.ts", import.meta.url));
const frame = (number, time, velocity, acceleration = null) => ({ frame: number, time_s: time, ego: { velocity_mps: velocity, acceleration_mps2: acceleration } });
const vector = (x, y = 0, z = 0) => ({ x, y, z });

test("measurement distinguishes real zero from unavailable values", () => {
  assert.equal(measurement(0), "0.00");
  for (const value of [null, undefined, NaN, Infinity, -Infinity]) assert.equal(measurement(value), "Unknown");
});
test("speed uses the full measured velocity magnitude", () => {
  assert.equal(telemetryPoints([frame(0, 0, vector(2, 3, 6))])[0].speed, 7);
});
test("deceleration projects against velocity, including reversing", () => {
  const points = telemetryPoints([frame(0, 0, vector(3, 4), vector(-6, -8)), frame(1, 1, vector(-5), vector(2))]);
  assert.equal(points[0].deceleration, 10);
  assert.equal(points[1].deceleration, 2);
});
test("cornering and positive acceleration do not create braking", () => {
  const points = telemetryPoints([frame(0, 0, vector(5), vector(0, 8)), frame(1, 1, vector(5), vector(2))]);
  assert.deepEqual(points.map(point => point.deceleration), [0, 0]);
});
test("missing vectors and rest retain unknown direction instead of guessing zero", () => {
  const points = telemetryPoints([frame(0, 0, null, vector(-3)), frame(1, 1, vector(3)), frame(2, 2, vector(0), vector(-3))]);
  assert.deepEqual(points.map(point => point.deceleration), [null, null, null]);
  assert.equal(points[0].speed, null); assert.equal(points[2].speed, 0);
});
test("chart never infers acceleration from adjacent speed samples", () => {
  assert.deepEqual(telemetryPoints([frame(0, 0, vector(5)), frame(1, 1, vector(1))]).map(point => point.deceleration), [null, null]);
});
test("series breaks at missing observations and skipped native frame numbers", () => {
  const points = telemetryPoints([frame(1, 0, vector(3)), frame(2, 1, null), frame(3, 2, vector(2)), frame(5, 3, vector(1)), frame(6, 4, vector(0))]);
  assert.deepEqual(seriesSegments(points, "speed"), [[{ time: 0, value: 3 }], [{ time: 2, value: 2 }], [{ time: 3, value: 1 }, { time: 4, value: 0 }]]);
});
test("series also breaks if timestamps stop increasing", () => {
  const points = telemetryPoints([frame(1, 0, vector(3)), frame(2, 0, vector(2)), frame(3, 1, vector(1))]);
  assert.equal(seriesSegments(points, "speed").length, 2);
});
test("contiguous frame IDs still break at a time gap above the recorded policy", () => {
  const points = telemetryPoints([frame(1, 0, vector(3)), frame(2, 0.5, vector(2)), frame(3, 1.25, vector(1))]);
  assert.deepEqual(seriesSegments(points, "speed", 0.5), [[{ time: 0, value: 3 }, { time: 0.5, value: 2 }], [{ time: 1.25, value: 1 }]]);
  // No run context means no invented timing threshold.
  assert.equal(seriesSegments(points, "speed").length, 1);
});
test("unknown values split series even when no timing threshold is supplied", () => {
  const points = telemetryPoints([frame(1, 0, vector(3)), frame(2, 0.05, null), frame(3, 5, vector(1))]);
  assert.deepEqual(seriesSegments(points, "speed", Infinity), [[{ time: 0, value: 3 }], [{ time: 5, value: 1 }]]);
  assert.deepEqual(seriesSegments(points, "deceleration", 0.5), []);
});
test("BGRA decoding preserves actual colors and alpha", () => {
  assert.deepEqual([...decodeBgra(new Uint8Array([10, 20, 30, 255, 40, 50, 60, 120]).buffer, 2, 1)], [30, 20, 10, 255, 60, 50, 40, 120]);
});
test("BGRA rejects mismatched dimensions, fractional sizes, and excessive allocation", () => {
  for (const [width, height] of [[0, 1], [1.2, 1], [4097, 1], [2, 1]]) assert.throws(() => decodeBgra(new ArrayBuffer(4), width, height), /dimensions or length/);
});
test("sample verification checks both byte count and SHA256", async () => {
  const bytes = new Uint8Array([1, 2, 3, 4]);
  const hash = createHash("sha256").update(bytes).digest("hex");
  await verifySample(bytes.buffer, 4, hash.toUpperCase());
  await assert.rejects(verifySample(bytes.buffer, 3, hash), /length/);
  await assert.rejects(verifySample(bytes.buffer, 4, "0".repeat(64)), /checksum/);
});
test("little-endian LiDAR inspection counts points and finite XYZ bounds", () => {
  const points = new Float32Array([2, -1, 3, 0.8, -2, 4, 1, 0.5, NaN, 0, 0, 1]);
  assert.deepEqual(inspectLidar(points.buffer), { points: 3, finitePoints: 2, bounds: [{ min: -2, max: 2 }, { min: -1, max: 4 }, { min: 1, max: 3 }] });
});
test("LiDAR excludes nonfinite intensity as well as invalid positions", () => {
  assert.deepEqual(inspectLidar(new Float32Array([1, 2, 3, Infinity]).buffer), { points: 1, finitePoints: 0, bounds: null });
});
test("empty LiDAR has no fabricated zero bounds; incomplete points fail", () => {
  assert.deepEqual(inspectLidar(new ArrayBuffer(0)), { points: 0, finitePoints: 0, bounds: null });
  assert.throws(() => inspectLidar(new ArrayBuffer(15)), /complete/);
});
