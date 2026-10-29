import { test, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";

// Opt-in only: the normal fixture and CI never require or launch a simulator.
test("website launches a measured baseline on the already running CARLA server", async ({ page, request }, testInfo) => {
  test.setTimeout(600_000);
  test.skip(process.env.POLTERGEIST_HOSTED_LIVE_CARLA !== "1", "Requires explicitly selected live CARLA acceptance");
  async function control(action: string) {
    const result = await request.post(`http://127.0.0.1:8059/${action}`, {
      headers: { Authorization: `Bearer ${process.env.POLTERGEIST_HOSTED_FIXTURE_TOKEN}` }, data: { name: "alice" },
    });
    expect(result.status()).toBe(200);
    return result.json();
  }
  const summaries: Record<string, unknown>[] = [], errors: string[] = [];
  const evidence: Record<string, unknown>[] = [];
  let evidenceRequestsDuringRun = 0, completedRun = false;
  page.on("request", current => { if (current.url().endsWith("/evidence") && !completedRun) evidenceRequestsDuringRun++; });
  page.on("pageerror", error => errors.push(error.message));
  // Chromium can evict a completed response before CDP's getResponseBody call.
  // Observe a clone in the browser while preserving the native HTTPS request and
  // the original response consumed by the application. Never retain raw chunks.
  await page.exposeBinding("observeRecordingEvidence", (_source, value: Record<string, unknown>) => {
    evidence.push(value);
  });
  await page.addInitScript(() => {
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (...args: Parameters<typeof fetch>) => {
      const response = await originalFetch(...args);
      if (response.url.endsWith("/evidence") && response.ok) {
        const raw = await response.clone().text(), value = JSON.parse(raw);
        await (window as unknown as { observeRecordingEvidence(value: Record<string, unknown>): Promise<void> })
          .observeRecordingEvidence({ resource: value.resource, state: value.state, offset: value.offset,
            next_offset: value.next_offset, artifact_index: value.artifact_index, version: value.version,
            total_bytes: value.total_bytes, frame: value.frame, timestamp: value.timestamp, sample: value.sample,
            recordings: value.recordings, samples: value.samples, sha256: value.sha256,
            sample_sha256: value.sample_sha256, encoded_chunk_characters: value.data_base64?.length ?? 0,
            response_bytes: new TextEncoder().encode(raw).byteLength });
      }
      return response;
    };
  });
  page.on("requestfinished", async current => {
    const response = await current.response();
    if (!response) return;
    if (!/\/v1\/devices\/[^/]+\/commands/.test(response.url()) || !response.ok()) return;
    try {
      const value = await response.json();
      for (const row of value.items ?? [value]) if (row.reply?.kind === "result") summaries.push(row.reply.summary);
    } catch { /* Teardown can abort a poll; completion below requires a real receipt. */ }
  });
  await page.goto("/app/");
  await page.getByRole("button", { name: "New here? Create an account" }).click();
  await page.getByLabel("Username", { exact: true }).fill("alice");
  await page.getByLabel("Password", { exact: true }).fill("private live browser fixture passphrase");
  await page.getByRole("button", { name: "Create account", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Your lab, connected." })).toBeVisible();
  const created = await control("new");
  await page.getByLabel("Pairing code", { exact: true }).fill(created.code);
  await page.getByRole("button", { name: "Connect computer", exact: true }).click();
  await expect(page.getByText("Waiting for local approval", { exact: true })).toBeVisible();
  const approved = await control("approve");
  await expect(page.getByRole("region", { name: "Choose your companion" }).getByText("connected", { exact: true })).toBeVisible();
  await page.getByLabel("Experiment name", { exact: true }).fill("Website CARLA baseline");
  await page.getByLabel("Map already loaded", { exact: true }).fill(process.env.POLTERGEIST_HOSTED_CARLA_MAP || "Town10HD_Opt");
  await page.getByRole("radio", { name: "CARLA experiment", exact: true }).check();
  const duration = Number(process.env.POLTERGEIST_HOSTED_CAPTURE_DURATION || "30");
  await page.getByLabel("Duration (simulated seconds)", { exact: true }).fill(String(duration));
  await page.getByRole("checkbox", { name: "Save camera recording", exact: true }).check();
  await page.getByRole("checkbox", { name: "Save LiDAR recording", exact: true }).check();
  await page.getByRole("button", { name: "Run experiment", exact: true }).click();
  const detail = page.getByRole("region", { name: "Website CARLA baseline" });
  await expect.poll(async () => detail.locator(".hosted-status").first().getAttribute("data-state"),
    { timeout: 480_000 }).toMatch(/^(completed|failed|cancelled)$/);
  await expect(detail.getByText("completed", { exact: true })).toBeVisible();
  completedRun = true;
  expect(evidenceRequestsDuringRun).toBe(0);
  await expect.poll(() => summaries.some(row => row.status === "completed")).toBe(true);
  const completed = summaries.find(row => row.status === "completed")!;
  expect(completed.mode).toBe("carla");
  expect(completed.agent).toBe("baseline");
  expect(completed.device_id).toBe(approved.device_id);
  expect(completed.result_available).toBe(true);
  const runtime = completed.runtime as Record<string, unknown>, metrics = completed.metrics as Record<string, unknown>;
  expect(runtime.adapter).toBe("carla");
  expect(runtime.client_version).toBe("0.9.16");
  expect(runtime.server_version).toBe("0.9.16");
  expect((metrics.coverage as Record<string, number>).total_frames).toBeGreaterThan(0);
  expect(completed.measured_duration_s).toBeGreaterThan(0);
  expect(completed.measured_duration_s).toBeCloseTo(duration, 4);
  expect((await control("state")).runs).toBe(1);
  await writeFile(testInfo.outputPath("carla-summary.json"), JSON.stringify(completed, null, 2));
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("HeapProfiler.collectGarbage");
  const heapBefore = await cdp.send("Runtime.getHeapUsage");
  const recordings = page.getByRole("region", { name: "Recording evidence", exact: true });
  await recordings.getByRole("button", { name: "Read recordings", exact: true }).click();
  await expect(recordings.getByRole("combobox", { name: "Recording", exact: true })).toBeVisible();
  await recordings.getByRole("button", { name: "Read sample list", exact: true }).click();
  await expect(recordings.getByRole("combobox", { name: "Saved sample", exact: true })).toBeVisible();
  // Native sensor callbacks can arrive in either order; choose the saved kind.
  await recordings.getByRole("combobox", { name: "Saved sample", exact: true }).selectOption(await recordings.getByRole("combobox", { name: "Saved sample", exact: true })
    .locator("option").filter({ hasText: "Camera" }).last().getAttribute("value") as string);
  await recordings.getByRole("button", { name: "Inspect saved sample", exact: true }).click();
  const camera = recordings.getByRole("img", { name: /Recorded camera pixels at frame/ });
  await expect(camera).toBeVisible();
  expect(await camera.evaluate((canvas: HTMLCanvasElement) => canvas.width * canvas.height)).toBe(320 * 180);
  expect(await camera.evaluate((canvas: HTMLCanvasElement) => {
    const values = canvas.getContext("2d")!.getImageData(0, 0, canvas.width, canvas.height).data;
    const colors = new Set();
    for (let i = 0; i < values.length; i += 128) colors.add(`${values[i]},${values[i + 1]},${values[i + 2]}`);
    return colors.size;
  })).toBeGreaterThan(2);
  await recordings.getByRole("combobox", { name: "Saved sample", exact: true }).selectOption(await recordings.getByRole("combobox", { name: "Saved sample", exact: true })
    .locator("option").filter({ hasText: "LiDAR" }).first().getAttribute("value") as string);
  await recordings.getByRole("button", { name: "Inspect saved sample", exact: true }).click();
  await expect(recordings.getByRole("img", { name: /Recorded LiDAR points at frame/ })).toBeVisible();
  await expect(recordings.getByText(/finite points/)).toBeVisible();
  const next = recordings.getByRole("button", { name: "Next saved samples", exact: true });
  let pages = 1;
  while (await next.count()) {
    expect(pages).toBeLessThan(200);
    await next.click();
    await expect.poll(() => evidence.filter(value => value.resource === "samples").length).toBe(++pages);
    await expect(recordings).toHaveAttribute("aria-busy", "false");
  }
  const samplePages = evidence.filter(value => value.resource === "samples");
  const first = (samplePages[0].samples as Record<string, number>[])[0];
  const lastPage = samplePages[samplePages.length - 1].samples as Record<string, number>[];
  const last = lastPage[lastPage.length - 1];
  expect(last.frame - first.frame).toBeGreaterThan(duration * 20 * .9);
  await recordings.getByRole("combobox", { name: "Saved sample", exact: true }).selectOption(String(last.sample_index));
  await recordings.getByRole("button", { name: "Inspect saved sample", exact: true }).click();
  await expect(recordings.getByText(/SHA-256 verified/)).toBeVisible();
  await expect(recordings.getByRole("img", { name: new RegExp(`at frame ${last.frame}$`) })).toBeVisible();
  const capture = (evidence.find(value => value.resource === "recordings")?.recordings as Record<string, unknown>[])[0];
  expect(capture.state).toBe("available");
  expect(capture.sample_count).toBeGreaterThan(duration * 20);
  expect(capture.total_bytes).toBeGreaterThan(duration * 20 * 320 * 180 * 4);
  expect(samplePages.reduce((count, value) => count + (value.samples as unknown[]).length, 0)).toBe(capture.sample_count);
  expect(evidence.filter(value => value.resource === "sample").every(value => Number(value.encoded_chunk_characters) <= 87384)).toBe(true);
  const transferred = evidence.reduce((count, value) => count + Number(value.response_bytes), 0);
  expect(transferred).toBeLessThan(Number(capture.total_bytes) / 10);
  await cdp.send("HeapProfiler.collectGarbage");
  const heapAfter = await cdp.send("Runtime.getHeapUsage");
  expect(heapAfter.usedSize).toBeLessThan(64 * 1024**2);
  expect(heapAfter.usedSize - heapBefore.usedSize).toBeLessThan(32 * 1024**2);
  await writeFile(testInfo.outputPath("carla-recording-evidence.json"), JSON.stringify({ duration_s: duration,
    evidence_requests_during_run: evidenceRequestsDuringRun, preview_bytes_limit: 4 * 1024**2,
    response_bytes: transferred, browser_heap_after_gc: { before: heapBefore, after: heapAfter }, responses: evidence }, null, 2));
  await page.screenshot({ path: testInfo.outputPath("carla-completed.png"), fullPage: true });
  await page.reload();
  await expect(detail.getByText("completed", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});
