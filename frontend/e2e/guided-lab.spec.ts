import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { auditAccessibility } from "../test-support/accessibility";

const API = "http://127.0.0.1:8049";

test("an unavailable saved guided run can be cleared after a confirmed missing response", async ({ page }) => {
  const key = `poltergeist-guided-v1/${API}`;
  const id = "00000000-0000-4000-8000-000000000123";
  let offline = true;
  let launches = 0;
  page.on("request", request => {
    if (request.method() === "POST" && request.url() === `${API}/simulations`
      || request.method() === "PUT" && request.url().startsWith(`${API}/simulations/requests/`)) launches += 1;
  });
  await page.route(`${API}/simulations/${id}`, route => offline
    ? route.fulfill({ status: 503, json: { detail: "The worker is temporarily unavailable." }, headers: { "Access-Control-Allow-Origin": "*" } })
    : route.continue());
  await page.goto("/");
  await page.evaluate(({ key, id }) => localStorage.setItem(key, id), { key, id });
  await page.goto("/#lab");

  await expect(page.getByRole("alert").filter({ hasText: "The worker is temporarily unavailable." })).toBeVisible();
  const chooseAnother = page.getByRole("button", { name: "Choose another experiment", exact: true });
  await expect(chooseAnother).toHaveCount(0);
  expect(await page.evaluate(key => localStorage.getItem(key), key)).toBe(id);

  offline = false;
  await page.getByRole("button", { name: "Refresh guided run" }).click();
  await expect(chooseAnother).toBeVisible({ timeout: 4_000 });
  await expect(page.getByRole("link", { name: "Open full run evidence" })).toHaveCount(0);
  await chooseAnother.click();
  await expect(page.getByRole("heading", { name: "1. Choose a driving question" })).toBeFocused();
  expect(await page.evaluate(key => localStorage.getItem(key), key)).toBeNull();
  await page.reload();
  await expect(page.getByRole("heading", { name: "1. Choose a driving question" })).toBeVisible();
  expect(launches).toBe(0);
});

test("the scenario gallery announces loading and retries from its own tab", async ({ page }) => {
  let releaseFirst!: () => void;
  const firstResponse = new Promise<void>(resolve => { releaseFirst = resolve; });
  let offline = true;
  await page.route(`${API}/experimentation/gallery`, async route => {
    if (!offline) return route.continue();
    await firstResponse;
    await route.fulfill({ status: 503, json: { detail: "The gallery is temporarily unavailable." }, headers: { "Access-Control-Allow-Origin": "*" } });
  });
  await page.goto("/#lab");
  await page.getByRole("tab", { name: "Scenario gallery" }).click();
  const gallery = page.getByRole("tabpanel", { name: "Scenario gallery" });
  try {
    await expect(gallery.getByRole("status")).toHaveText("Loading illustrated scenarios…", { timeout: 4_000 });
  } finally { releaseFirst(); }
  await expect(gallery.getByRole("alert")).toContainText("The gallery is temporarily unavailable.");
  offline = false;
  const retry = gallery.getByRole("button", { name: "Retry gallery" });
  await retry.focus();
  await page.keyboard.press("Enter");
  await expect(gallery.getByRole("heading", { name: "Choose a situation to investigate" })).toBeFocused();
  await expect(gallery.getByRole("button", { name: "Choose Sudden Braking", exact: true })).toBeVisible();
});

test("a lost guided launch acknowledgement recovers the same run after reloading", async ({ page, request }, testInfo) => {
  const attempts: { url: string; body: string | null }[] = [];
  let originalId: string | undefined;
  let releaseRetry!: () => void;
  const retryResponse = new Promise<void>(resolve => { releaseRetry = resolve; });
  const before = (await (await request.get(`${API}/simulations?limit=100`)).json()).length;
  await page.route(`${API}/simulations/requests/*`, async route => {
    attempts.push({ url: route.request().url(), body: route.request().postData() });
    const accepted = await route.fetch();
    expect(accepted.status()).toBe(202);
    if (attempts.length === 1) {
      originalId = (await accepted.json()).simulation.id;
      await route.abort("failed");
    } else {
      await retryResponse;
      await route.fulfill({ response: accepted });
    }
  });
  await page.setViewportSize({ width: 320, height: 800 });
  await page.emulateMedia({ forcedColors: "active" });
  await page.goto("/#lab");
  await page.getByRole("button", { name: "Choose Sudden Braking", exact: true }).click();
  await page.getByRole("button", { name: "Run one synthetic experiment" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "The request may have arrived" })).toBeVisible();
  await page.reload();
  const retry = page.getByRole("button", { name: "Retry saved submission" });
  await expect(retry).toBeVisible();
  expect(attempts).toHaveLength(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(321);
  await auditAccessibility(page, testInfo, "saved-guided-submission-forced-colors");
  try {
    await retry.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByText("Submitting your experiment…", { exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "2. Run one experiment" })).toBeFocused({ timeout: 2_000 });
  } finally { releaseRetry(); }
  await expect(page.getByRole("heading", { name: "3. Explain what was recorded" })).toBeVisible();
  expect(attempts).toHaveLength(2);
  expect(attempts[1]).toEqual(attempts[0]);
  await expect(page.getByRole("link", { name: "Open full run evidence" })).toHaveAttribute("href", `#simulations/${originalId}`);
  expect((await (await request.get(`${API}/simulations?limit=100`)).json()).length).toBe(before + 1);
});

test("keyboard focus follows the first experiment and gallery shortcuts", async ({ page }) => {
  await page.goto("/#lab");
  const browse = page.getByRole("button", { name: "Browse all scenarios" });
  await browse.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("tab", { name: "Scenario gallery" })).toBeFocused();
  const gallery = page.getByRole("tabpanel", { name: "Scenario gallery" });
  await gallery.getByLabel("Gallery level").selectOption("starter");
  await expect(gallery.getByRole("status")).toHaveText("Showing 4 of 16 scenarios.");
  await expect(gallery.getByRole("button", { name: "Add to suite (Sudden Braking)", exact: true })).toBeVisible();
  const choose = page.getByRole("button", { name: "Choose Sudden Braking", exact: true });
  await choose.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "2. Run one experiment" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("First experiment mode")).toBeFocused();
});

test("a first experiment leads to frame evidence, deliberate feedback and a reusable suite", async ({ page, request }, testInfo) => {
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  await page.goto("/#lab");
  await expect(page.getByRole("heading", { name: "Your experiment lab" })).toBeVisible();
  const currentStep = page.getByRole("list", { name: "First experiment progress" }).locator('[aria-current="step"]');
  await expect(currentStep).toContainText("Choose a scene");
  const count = (await (await request.get(`${API}/simulations?limit=100`)).json()).length;
  await page.getByRole("button", { name: "Choose Sudden Braking", exact: true }).click();
  await expect(currentStep).toContainText("Run one experiment");
  expect((await (await request.get(`${API}/simulations?limit=100`)).json()).length).toBe(count);
  await page.getByRole("button", { name: "Run one synthetic experiment", exact: true }).click();
  await expect(page.getByRole("heading", { name: "3. Explain what was recorded" })).toBeVisible();
  await expect(currentStep).toContainText("Explain the evidence");
  await expect(page.getByRole("heading", { name: "2. Run one experiment" })).toBeFocused();
  await expect(page.getByRole("region", { name: "Replay highlights" }).getByRole("link", { name: /^Inspect frame / }).first()).toBeVisible();
  await page.getByLabel("Can you explain the recorded outcome?").selectOption("yes");
  await page.getByLabel("What would bring you back for another experiment?").fill("Compare the next controller version on the same scene.");
  await expect(page.getByRole("button", { name: "Save first-run feedback" })).toBeDisabled();
  await page.getByLabel("Save this feedback with the experiment").check();
  await page.getByRole("button", { name: "Save first-run feedback" }).click();
  await expect(page.getByText("Feedback saved. Thank you for helping improve this workflow.")).toBeVisible();
  await auditAccessibility(page, testInfo, "guided-outcome");
  await page.screenshot({ path: testInfo.outputPath("guided-outcome-desktop.png"), fullPage: true });
  const frameLink = page.getByRole("region", { name: "Replay highlights" }).getByRole("link", { name: /^Inspect frame / }).first();
  const href = await frameLink.getAttribute("href"); expect(href).toMatch(/#simulations\/.+\/frame\/\d+$/);
  await frameLink.click(); await expect(page.getByRole("heading", { name: "How the vehicle responded", exact: true })).toBeVisible();
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Guided lab", exact: true }).click();
  await page.reload(); await expect(page.getByRole("heading", { name: "3. Explain what was recorded" })).toBeVisible();
  await page.getByRole("button", { name: "Keep scenes for a comparison" }).click();
  await expect(page.getByRole("tab", { name: "Saved suites" })).toBeFocused();
  await page.getByLabel("Suite name", { exact: true }).fill("My first repeatable suite");
  await page.getByLabel("Matched suite seeds").fill("7,11");
  await page.getByRole("button", { name: "Save experiment suite", exact: true }).click();
  await expect(page.getByRole("heading", { name: "My first repeatable suite", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Run My first repeatable suite · 2 executions", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Suite run · completed", exact: true })).toBeVisible();
  const comparison = page.getByRole("region", { name: "Comparison for Sudden Braking" });
  await expect(comparison).toContainText("2 / 2");
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download suite comparison evidence" }).click();
  const file = await (await downloadEvent).path(); const report = JSON.parse(await readFile(file!, "utf8"));
  expect(report.schema_version).toBe("poltergeist-suite-report-v1"); expect(report.evidence.trials.length).toBe(2);
  await expect(page).toHaveURL(new RegExp(`#lab/suite-run/${report.evidence.operation.id}$`));
  await page.reload(); await expect(page.getByRole("heading", { name: "Suite run · completed", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Past results for My first repeatable suite", exact: true }).click();
  await expect(page.getByRole("link", { name: `Review suite comparison ${report.evidence.operation.id.slice(0, 8)}` })).toBeVisible();
  await page.setViewportSize({ width: 320, height: 900 });
  await comparison.focus();
  await page.keyboard.press("ArrowRight");
  await expect.poll(() => comparison.evaluate(table => table.scrollLeft)).toBeGreaterThan(0);
  await auditAccessibility(page, testInfo, "saved-suite-mobile");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("saved-suite-mobile.png"), fullPage: true });
  expect(errors).toEqual([]);
});

test("a typed discovery plan requires explicit approval and exports its complete negative search", async ({ page, request }, testInfo) => {
  await page.goto("/#discovery");
  await page.getByLabel("Candidate duration (simulated seconds)").fill("1");
  await page.getByLabel("Maximum total executions").fill("3");
  await page.getByLabel("Maximum search candidates").fill("1");
  await page.getByLabel("Maximum candidates to confirm").fill("1");
  await page.getByLabel("Simulated-time budget (seconds)").fill("3");
  const before = (await (await request.get(`${API}/simulations?limit=100`)).json()).length;
  await page.getByRole("button", { name: "Build a plan for review" }).click();
  await expect(page.getByRole("heading", { name: "Review the experiment plan" })).toBeVisible();
  expect((await (await request.get(`${API}/simulations?limit=100`)).json()).length).toBe(before);
  const approve = page.getByRole("button", { name: "Approve and start discovery" }); await expect(approve).toBeDisabled();
  await page.getByLabel("I approve this plan and its execution and time budgets").check();
  await approve.click();
  await expect(page.getByRole("heading", { name: "No failure confirmed", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Discovery trial evidence" })).toContainText("Negative observation");
  await expect(page.getByText("No failure was confirmed within this approved plan.", { exact: false })).toBeVisible();
  const downloadEvent = page.waitForEvent("download"); await page.getByRole("button", { name: "Download complete discovery report" }).click();
  const report = JSON.parse(await readFile((await (await downloadEvent).path())!, "utf8"));
  expect(report.confirmed_failures).toBe(0); expect(report.executions_used).toBe(1); expect(report.negative_trials).toBe(1);
  expect(report.plan.scenarios).toHaveLength(1); expect(report.evidence.trials[0].scenario).toEqual(report.plan.scenarios[0]);
  await auditAccessibility(page, testInfo, "discovery-report-desktop");
  await page.screenshot({ path: testInfo.outputPath("discovery-report-desktop.png"), fullPage: true });
  await page.reload(); await expect(page.getByRole("heading", { name: "No failure confirmed", exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 900 });
  await auditAccessibility(page, testInfo, "discovery-report-mobile");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
