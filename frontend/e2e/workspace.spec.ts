import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

const API = "http://127.0.0.1:8049";

test("registered experiment, behavior review, evidence and printable export work in a real browser", async ({ page, request }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/#overview");
  await expect(page.getByRole("heading", { name: "Start with a scenario." })).toBeVisible();
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Agents", exact: true }).click();
  await page.getByRole("button", { name: /Inspect Starter rule-based agent/ }).click();
  await page.getByRole("button", { name: "Check static readiness" }).click();
  await expect(page.getByText("Static checks passed", { exact: true })).toBeVisible();

  const response = await request.get(`${API}/optimizations?limit=10`);
  expect(response.ok()).toBeTruthy();
  const operations = await response.json();
  const saved = operations.find((o: { request: { kind: string }; status: string }) => o.request.kind === "compare" && o.status === "completed");
  expect(saved).toBeTruthy();
  await page.goto(`/#experiments/${saved.id}`);
  await expect(page.getByRole("heading", { name: "Observed results" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Camera and LiDAR teaching controller/ })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("comparison-desktop.png"), fullPage: true });

  // Submit a short real synthetic run from the dashboard form, using a frozen
  // registered starter and the same valid task as the seeded workspace.
  await page.getByLabel("Scenario recipe JSON").fill(JSON.stringify(saved.request.scenario));
  await page.getByLabel("Matched seeds (comma separated)").fill("19");
  await page.getByRole("checkbox", { name: /Built-in baseline/ }).uncheck();
  await page.getByRole("checkbox", { name: /Starter rule-based agent ·/ }).check();
  await page.getByRole("button", { name: /Start compare · 1 executions · test/ }).click();
  await expect(page).not.toHaveURL(new RegExp(`${saved.id}$`));
  await expect(page.getByRole("heading", { name: "compare · completed" })).toBeVisible();
  await page.getByRole("link", { name: "Review run", exact: true }).click();
  await expect(page.getByRole("heading", { name: /Short installation integration check/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "How the vehicle responded", exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("behavior-desktop.png"), fullPage: true });
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download HTML report", exact: true }).click();
  const download = await downloadEvent;
  const file = await download.path();
  expect(file).toBeTruthy();
  const html = await readFile(file!, "utf-8");
  expect(html).toContain("poltergeist-run-report-v2");
  await page.setContent(html);
  await page.emulateMedia({ media: "print" });
  await expect(page.locator("body")).toContainText("Synthetic");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  await page.pdf({ path: testInfo.outputPath("behavior-report-print.pdf"), format: "A4", printBackground: true });
  await page.screenshot({ path: testInfo.outputPath("behavior-report-print.png"), fullPage: true });
  expect(errors).toEqual([]);
});

test("all workspace pages fit a phone viewport and retain keyboard navigation", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const route of ["overview", "scenarios", "agents", "experiments", "simulations", "failures"]) {
    await page.goto(`/#${route}`);
    await expect(page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: new RegExp(`^${route}\\b`, "i") })).toHaveAttribute("aria-current", "page");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), route).toBeTruthy();
    await page.screenshot({ path: testInfo.outputPath(`${route}-mobile.png`), fullPage: true });
  }
  await page.goto("/#experiments");
  await page.reload();
  await expect(page.getByRole("heading", { name: "Experiments", exact: true })).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("main")).toBeFocused();
});
