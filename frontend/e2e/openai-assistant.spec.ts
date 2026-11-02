import { test, expect } from "@playwright/test";
import { auditAccessibility } from "../test-support/accessibility";

const API = "http://127.0.0.1:8049";

test("optional assistant previews disclosure, requires separate approval and links a checked draft", async ({ page, request }, testInfo) => {
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  const baseline = (await (await request.get(`${API}/simulations?limit=100`)).json()).length;
  let sends = 0, previews = 0;
  const provider = await (await request.get(`${API}/assistant/status`)).json();
  // The normal test matrix keeps the provider disabled. The explicit fixture
  // run exercises the real routes/journal with only the external call replaced.
  test.skip(provider.model !== "browser-fixture", "Set POLTERGEIST_ASSISTANT_FIXTURE=1 for the local provider fixture");
  page.on("request", outgoing => {
    if (outgoing.url().endsWith("/assistant/previews")) previews++;
    if (/\/assistant\/requests\/.+\/send$/.test(outgoing.url())) sends++;
  });
  await page.goto("/#discovery");
  const panel = page.getByRole("region", { name: "Optional OpenAI assistance" });
  expect(previews).toBe(0); expect(sends).toBe(0);
  await panel.getByRole("button", { name: "Open language assistance" }).click();
  await panel.getByLabel("Question for OpenAI").fill("Find repeatable following contacts");
  await panel.getByRole("button", { name: "Preview OpenAI disclosure" }).click();
  await expect(panel.getByRole("heading", { name: "Review disclosure and usage" })).toBeVisible();
  const send = panel.getByRole("button", { name: "Send approved OpenAI request" });
  await expect(send).toBeDisabled(); expect(sends).toBe(0);
  await panel.getByText("Exact provider request (no API key)").click();
  await expect(panel.getByText(/Find repeatable following contacts/, { exact: false }).last()).toBeVisible();
  await panel.getByLabel("I approve sending this exact disclosure to OpenAI within these usage limits").check();
  await send.click();
  await expect(panel.getByRole("link", { name: "Review the drafted experiment plan" })).toBeVisible();
  expect(sends).toBe(1);
  expect((await (await request.get(`${API}/simulations?limit=100`)).json()).length).toBe(baseline);
  await auditAccessibility(page, testInfo, "s18-draft-desktop");
  await page.setViewportSize({ width: 390, height: 900 });
  await auditAccessibility(page, testInfo, "s18-draft-mobile");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("s18-draft-mobile.png"), fullPage: true });
  await panel.getByRole("link", { name: "Review the drafted experiment plan" }).click();
  await expect(page.getByRole("heading", { name: "Review the experiment plan" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve and start discovery" })).toBeDisabled();
  expect((await (await request.get(`${API}/simulations?limit=100`)).json()).length).toBe(baseline);
  expect(errors).toEqual([]);
});

test("disabled configuration is clear and causes no provider disclosure", async ({ page }) => {
  await page.route("**/assistant/status", route => route.fulfill({ json: { enabled: false, configured: false, model: "",
    daily_request_limit: 10, daily_output_token_limit: 8192, scope: "local_api_only" } }));
  await page.goto("/#discovery");
  const panel = page.getByRole("region", { name: "Optional OpenAI assistance" });
  await panel.getByRole("button", { name: "Open language assistance" }).click();
  await expect(panel.getByText(/OpenAI assistance is off/)).toBeVisible();
  await expect(panel.getByLabel("Question for OpenAI")).toBeDisabled();
});

test("analysis sends only selected observations and returns the verified coverage snapshot", async ({ page, request }, testInfo) => {
  const provider = await (await request.get(`${API}/assistant/status`)).json();
  test.skip(provider.model !== "browser-fixture", "Requires the explicit local provider fixture");
  const runs = await (await request.get(`${API}/simulations?limit=100`)).json();
  const run = runs.find((item: any) => item.simulation.status === "completed");
  expect(run).toBeTruthy();
  const identity = run.simulation.id;
  const analysis = await (await request.get(`${API}/experimentation/analysis/${identity}`)).json();
  expect(["partial", "complete"]).toContain(analysis.state);
  const index = analysis.observations.findIndex((item: any) => item.references.some((ref: any) => ref.frame !== null));
  const selected = index >= 0 ? index : 0;
  await page.goto(`/#simulations/${identity}`);
  await page.getByRole("button", { name: "Analyze recorded evidence", exact: true }).click();
  const panel = page.getByRole("region", { name: "Optional OpenAI assistance" });
  await panel.getByRole("button", { name: "Open language assistance" }).click();
  await panel.getByLabel("Question for OpenAI").fill("Which recorded observations address this outcome?");
  await expect(panel.getByRole("button", { name: "Preview OpenAI disclosure" })).toBeDisabled();
  await panel.getByRole("checkbox").nth(selected).check();
  await panel.getByRole("button", { name: "Preview OpenAI disclosure" }).click();
  await panel.getByLabel("I approve sending this exact disclosure to OpenAI within these usage limits").check();
  const result = page.waitForResponse(response => /\/assistant\/requests\/.+\/send$/.test(response.url()));
  await panel.getByRole("button", { name: "Send approved OpenAI request" }).click();
  const record = await (await result).json();
  expect(record.state).toBe("completed");
  expect(record.analysis.observations).toEqual([analysis.observations[selected]]);
  expect(record.analysis.coverage).toEqual(analysis.coverage);
  expect(record.analysis.source_digest).toBe(analysis.source_digest);
  const disclosed = JSON.parse(JSON.parse(record.preview.payload).input);
  expect(disclosed.observations).toHaveLength(1); expect(JSON.stringify(disclosed)).not.toContain(identity);
  await expect(panel.getByText(/Missing evidence stays unknown/)).toBeVisible();
  await auditAccessibility(page, testInfo, "s18-analysis-desktop");
});
