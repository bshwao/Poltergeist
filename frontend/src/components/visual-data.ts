import type { TelemetryFrame } from "../types/generated";

export function measurement(value: number | null | undefined, decimals = 2): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(decimals) : "Unknown";
}

export interface TelemetryPoint {
  time: number;
  frame: number;
  speed: number | null;
  deceleration: number | null;
}

/** Direct observations only: no page-boundary velocity differences or inferred geometry. */
export function telemetryPoints(frames: TelemetryFrame[]): TelemetryPoint[] {
  return frames.map(frame => {
    const velocity = frame.ego.velocity_mps;
    const acceleration = frame.ego.acceleration_mps2;
    const magnitude = velocity ? Math.hypot(velocity.x, velocity.y, velocity.z) : null;
    const speed = magnitude !== null && Number.isFinite(magnitude) ? magnitude : null;
    // At rest the vector projection has no known direction of travel. Aggregate
    // evaluation instead follows its own recorded stopped-speed settings.
    const projected = speed !== null && speed > 1e-9 && velocity && acceleration
      ? Math.max(0, -(acceleration.x * velocity.x / speed + acceleration.y * velocity.y / speed + acceleration.z * velocity.z / speed)) : null;
    return { time: frame.time_s, frame: frame.frame, speed, deceleration: projected !== null && Number.isFinite(projected) ? projected : null };
  });
}

export function seriesSegments(points: TelemetryPoint[], metric: "speed" | "deceleration", maxGapS = Infinity) {
  const maxGap = Number.isFinite(maxGapS) && maxGapS > 0 ? maxGapS : Infinity;
  const segments: { time: number; value: number }[][] = [];
  let current: { time: number; value: number }[] = [];
  let previous: TelemetryPoint | undefined;
  for (const point of points) {
    const value = point[metric];
    const gap = previous && (point.frame !== previous.frame + 1 || point.time <= previous.time || point.time - previous.time > maxGap);
    if (value === null || gap) {
      if (current.length) segments.push(current);
      current = [];
    }
    if (value !== null) current.push({ time: point.time, value });
    previous = point;
  }
  if (current.length) segments.push(current);
  return segments;
}

export async function verifySample(buffer: ArrayBuffer, byteLength: number, expectedHash: string): Promise<void> {
  if (buffer.byteLength !== byteLength) throw new Error("The raw sample length does not match its manifest.");
  if (!globalThis.crypto?.subtle) throw new Error("Sample integrity checks require a secure browser context (HTTPS or localhost).");
  const digest = await globalThis.crypto.subtle.digest("SHA-256", buffer);
  const hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
  if (hash !== expectedHash.toLowerCase()) throw new Error("The raw sample checksum does not match its manifest.");
}

export function decodeBgra(buffer: ArrayBuffer, width: number, height: number): Uint8ClampedArray {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 || width > 4096 || height > 4096 || buffer.byteLength !== width * height * 4) {
    throw new Error("The camera sample has invalid BGRA8 dimensions or length.");
  }
  const source = new Uint8Array(buffer);
  const rgba = new Uint8ClampedArray(source.length);
  for (let index = 0; index < source.length; index += 4) {
    rgba[index] = source[index + 2]; rgba[index + 1] = source[index + 1]; rgba[index + 2] = source[index]; rgba[index + 3] = source[index + 3];
  }
  return rgba;
}

export function inspectLidar(buffer: ArrayBuffer) {
  if (buffer.byteLength % 16) throw new Error("The LiDAR sample does not contain complete float32 XYZI points.");
  const data = new DataView(buffer);
  const minimum = [Infinity, Infinity, Infinity];
  const maximum = [-Infinity, -Infinity, -Infinity];
  let finitePoints = 0;
  for (let offset = 0; offset < data.byteLength; offset += 16) {
    const point = [0, 4, 8, 12].map(index => data.getFloat32(offset + index, true));
    if (!point.every(Number.isFinite)) continue;
    finitePoints += 1;
    for (let axis = 0; axis < 3; axis += 1) {
      minimum[axis] = Math.min(minimum[axis], point[axis]); maximum[axis] = Math.max(maximum[axis], point[axis]);
    }
  }
  return { points: data.byteLength / 16, finitePoints, bounds: finitePoints ? minimum.map((min, index) => ({ min, max: maximum[index] })) : null };
}
