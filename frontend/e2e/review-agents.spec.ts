import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { auditAccessibility } from "../test-support/accessibility";

const API = "http://127.0.0.1:8049";

test("frozen controller versions produce a reviewable regression and frame-linked analyst export", async ({ page, request }, testInfo) => {
  test.setTimeout(180_000);
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  const versions: string[] = [];
  // These trusted packages are created only in this test's owned output folder.
  // The normal synthetic adapter detects contacts from the resulting motion.
  for (const [name, version, control] of [["reference", "1.0", "{'brake':1.0}"], ["candidate", "2.0", "{'throttle':1.0}"]]) {
    const directory = testInfo.outputPath("controllers", name);
    await mkdir(directory, { recursive: true });
    await writeFile(join(directory, "agent.json"), JSON.stringify({ protocol_version: "1.3", agent_id: "browser-regression-fixture",
      name: "Browser regression fixture", version, entry_point: "agent:Driver", observation_mode: "simulator_state" }));
    await writeFile(join(directory, "agent.py"), `from simulator.agents import AutonomousAgent
class Driver(AutonomousAgent):
 def load(self, configuration): pass
 def reset(self, context): pass
 def step(self, observation): return {'protocol_version':'1.3','frame':observation.frame,'control':${control}}
 def close(self): pass
`);
    const response = await request.post(`${API}/agents`, { data: { package_path: directory, trusted_local_code: true,
      agent_control_mode: "authoritative", process_options: { close_timeout_s: 10 } } });
    expect(response.status(), await response.text()).toBe(201); versions.push((await response.json()).id);
  }
  const before = (await (await request.get(`${API}/simulations?limit=100`)).json()).length;
  const saved = await request.post(`${API}/experimentation/suites`, { data: { definition: {
    kind: "suite", name: "Frozen version contact review", mode: "test", agents: versions, seeds: [7, 11, 13],
    cases: [{ name: "Stationary lead vehicle", scenario: { name: "Recorded synthetic contact", duration_s: 2,
      actors: [{ id: "ego", type: "ego_vehicle", speed_mps: 4, spawn: { position: { x: 0 } } },
        { id: "lead", type: "lead_vehicle", speed_mps: 0, spawn: { position: { x: 8 } } }] } }],
  } } });
  expect(saved.status(), await saved.text()).toBe(201);
  const suite = await saved.json(), identity = randomUUID();
  const launched = await request.post(`${API}/experimentation/suites/${suite.id}/runs`, {
    data: { expected_revision: suite.revision, request_id: identity } });
  expect(launched.status(), await launched.text()).toBe(202);
  await expect.poll(async () => (await (await request.get(`${API}/experimentation/suite-runs/${identity}`)).json()).evidence.operation.status,
    { timeout: 120_000, intervals: [500, 1000] }).toBe("completed");
  expect((await (await request.get(`${API}/simulations?limit=100`)).json()).length).toBe(before + 6);
  await page.goto(`/#lab/suite-run/${identity}`);
  const regression = page.getByRole("region", { name: "Regression Agent", exact: true });
  await expect(regression.getByRole("button", { name: "Review version regression", exact: true })).toBeVisible();
  await expect(regression.getByRole("heading", { name: "Worsening pattern observed", exact: true })).toHaveCount(0);
  await regression.getByRole("button", { name: "Review version regression", exact: true }).click();
  await expect(regression.getByRole("heading", { name: "Worsening pattern observed", exact: true })).toBeVisible();
  await expect(regression).toContainText("Version 1.0"); await expect(regression).toContainText("Version 2.0");
  await expect(regression.getByRole("region", { name: "Regression assessments" })).toContainText("3 / 3");
  const regressionDownload = page.waitForEvent("download");
  await regression.getByRole("button", { name: "Download regression review" }).click();
  const report = JSON.parse(await readFile((await (await regressionDownload).path())!, "utf8"));
  expect(report.operation_id).toBe(identity); expect(report.state).toBe("regression_observed");
  expect(report.pairs).toHaveLength(3); expect(report.evidence_review.available_runs).toBe(6);
  expect(report.controllers[0].execution_digest).not.toBe(report.controllers[1].execution_digest);
  expect(report.policy).toEqual({ minimum_pairs: 3, thresholds: { severity: 5, collision_count: 0 } });
  await auditAccessibility(page, testInfo, "regression-desktop");
  await page.screenshot({ path: testInfo.outputPath("regression-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 900 });
  await auditAccessibility(page, testInfo, "regression-mobile");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("regression-mobile.png"), fullPage: true });
  const candidate = report.pairs[0].compared_simulation_id;
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`/#simulations/${candidate}`);
  const analyst = page.getByRole("region", { name: "Evidence Analyst", exact: true });
  await analyst.getByRole("button", { name: "Analyze recorded evidence", exact: true }).click();
  await expect(analyst).toContainText("recorded frames contain contact events");
  const analysisDownload = page.waitForEvent("download");
  await analyst.getByRole("button", { name: "Download evidence analysis" }).click();
  const analysis = JSON.parse(await readFile((await (await analysisDownload).path())!, "utf8"));
  expect(analysis.simulation_id).toBe(candidate); expect(analysis.schema_version).toBe("poltergeist-evidence-analysis-v1");
  expect(analysis.telemetry_digest).toMatch(/^[a-f0-9]{64}$/);
  const contact = analysis.observations.find((item: { kind: string }) => item.kind === "contact");
  expect(contact.value).toBeGreaterThan(0); const frame = contact.references[0].frame;
  await auditAccessibility(page, testInfo, "analyst-desktop");
  await page.setViewportSize({ width: 390, height: 900 });
  await auditAccessibility(page, testInfo, "analyst-mobile");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("analyst-mobile.png"), fullPage: true });
  await analyst.getByRole("button", { name: `Inspect frame ${frame}`, exact: true }).first().click();
  const telemetry = page.getByRole("region", { name: "Telemetry charts", exact: true });
  await expect(telemetry).toContainText(`Opened supporting telemetry at native frame ${frame}.`);
  await expect(telemetry.locator(".frame-values")).toContainText(`${frame} /`);
  expect((await (await request.get(`${API}/simulations?limit=100`)).json()).length).toBe(before + 6);
  expect(errors).toEqual([]);
});
