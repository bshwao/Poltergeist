import { test, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";

// Opt-in only: the normal fixture and CI never require or launch a simulator.
test("website launches a measured baseline on the already running CARLA server", async ({ page, request }, testInfo) => {
  test.skip(process.env.POLTERGEIST_HOSTED_LIVE_CARLA !== "1", "Requires explicitly selected live CARLA acceptance");
  async function control(action: string) {
    const result = await request.post(`http://127.0.0.1:8059/${action}`, {
      headers: { Authorization: `Bearer ${process.env.POLTERGEIST_HOSTED_FIXTURE_TOKEN}` }, data: { name: "alice" },
    });
    expect(result.status()).toBe(200);
    return result.json();
  }
  const summaries: Record<string, unknown>[] = [], errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("response", async response => {
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
  await page.getByRole("button", { name: "Run experiment", exact: true }).click();
  const detail = page.getByRole("region", { name: "Website CARLA baseline" });
  await expect(detail.getByText("completed", { exact: true })).toBeVisible({ timeout: 60_000 });
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
  expect((await control("state")).runs).toBe(1);
  await writeFile(testInfo.outputPath("carla-summary.json"), JSON.stringify(completed, null, 2));
  await page.screenshot({ path: testInfo.outputPath("carla-completed.png"), fullPage: true });
  await page.reload();
  await expect(detail.getByText("completed", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});
