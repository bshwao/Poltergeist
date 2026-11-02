import { test, expect, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { auditAccessibility } from "../test-support/accessibility";

const API = "http://127.0.0.1:8049";
const fixture = JSON.parse(await readFile(new URL("../tests/fixtures/github-reporting.json", import.meta.url), "utf8"));
let operation: string;

test.beforeAll(async ({ request }) => {
  // One real saved baseline suite supplies the surrounding UI. GitHub/report
  // responses below are serialized service fixtures, never a live publication
  // or a claim that this baseline ran the fixture's registered model versions.
  const saved = await request.post(`${API}/experimentation/suites`, { data: { definition: {
    kind: "suite", name: "GitHub UI fixture", mode: "test", agents: [null], seeds: [7],
    cases: [{ name: "Synthetic baseline", scenario: { name: "GitHub UI baseline", duration_s: .15,
      actors: [{ id: "ego", type: "ego_vehicle", speed_mps: 2 }] } }],
  } } });
  expect(saved.status(), await saved.text()).toBe(201);
  const suite = await saved.json(); operation = randomUUID();
  const launched = await request.post(`${API}/experimentation/suites/${suite.id}/runs`, {
    data: { expected_revision: suite.revision, request_id: operation } });
  expect(launched.status(), await launched.text()).toBe(202);
  await expect.poll(async () => (await (await request.get(`${API}/experimentation/suite-runs/${operation}`)).json()).evidence.operation.status,
    { timeout: 30_000 }).toBe("completed");
});

async function open(page: Page, options: { disabled?: boolean; uncertain?: boolean } = {}) {
  const calls: { previews: any[]; approvals: any[]; reconciles: number } = { previews: [], approvals: [], reconciles: 0 };
  let record = structuredClone(fixture.preview);
  await page.route(`**/experimentation/regressions/${operation}*`, route => route.fulfill({ json: { ...fixture.report, operation_id: operation } }));
  await page.route("**/github/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/status")) return route.fulfill({ json: { ...fixture.status, enabled: !options.disabled } });
    if (path.includes("/inputs/")) return route.fulfill({ json: { ...fixture.inputs, operation_id: operation } });
    if (path.endsWith("/previews")) {
      const input = route.request().postDataJSON(); calls.previews.push(input);
      record = structuredClone(fixture.preview); record.id = input.request_id;
      record.preview.id = input.request_id; record.preview.operation_id = operation;
      return route.fulfill({ status: 201, json: record });
    }
    if (path.endsWith("/publish")) {
      calls.approvals.push(route.request().postDataJSON());
      record = { ...record, state: options.uncertain ? "uncertain" : "published",
        message: options.uncertain ? "Outcome unconfirmed; reconcile before taking further action." : fixture.published.message,
        check_run_id: options.uncertain ? null : fixture.published.check_run_id,
        check_url: options.uncertain ? null : fixture.published.check_url };
      return route.fulfill({ json: record });
    }
    if (path.endsWith("/reconcile")) {
      calls.reconciles++;
      record = { ...record, state: "published", message: fixture.published.message,
        check_run_id: fixture.published.check_run_id, check_url: fixture.published.check_url };
      return route.fulfill({ json: record });
    }
    return route.fulfill({ json: path.endsWith("/publications") ? [record] : record });
  });
  await page.goto(`/#lab/suite-run/${operation}`);
  await page.getByRole("button", { name: "Review version regression", exact: true }).click();
  const panel = page.getByRole("region", { name: "Optional GitHub reporting", exact: true });
  await panel.getByRole("button", { name: "Open GitHub reporting" }).click();
  return { panel, calls };
}

async function preview(page: Page) {
  const { panel, calls } = await open(page);
  await panel.getByLabel("Reference full GitHub commit SHA").fill(fixture.request.reference.commit);
  await panel.getByLabel("Candidate full GitHub commit SHA").fill(fixture.request.candidate.commit);
  await panel.getByLabel("Reference package directory in the repository").fill("pkg");
  await panel.getByLabel("Candidate package directory in the repository").fill("pkg");
  await panel.getByRole("button", { name: "Verify commits and preview GitHub check" }).click();
  await expect(panel.getByRole("heading", { name: "Review the exact GitHub publication" })).toBeVisible();
  return { panel, calls };
}

test("exact commit disclosure requires approval and remains accessible on desktop and mobile", async ({ page, request }, testInfo) => {
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  const before = (await (await request.get(`${API}/simulations?limit=100`)).json()).length;
  const { panel, calls } = await preview(page);
  expect(calls.previews).toHaveLength(1); expect(calls.previews[0].disclose).toEqual([]); expect(calls.approvals).toHaveLength(0);
  const publish = panel.getByRole("button", { name: "Publish approved GitHub check" }); await expect(publish).toBeDisabled();
  await panel.getByText("Exact outgoing GitHub check JSON (no token)", { exact: true }).click();
  const disclosure = panel.getByRole("region", { name: "Exact GitHub disclosure", exact: true });
  await expect(disclosure).toContainText(fixture.request.candidate.commit);
  expect(JSON.parse((await disclosure.textContent())!).head_sha).toBe(fixture.request.candidate.commit);
  await auditAccessibility(page, testInfo, "s19-preview-desktop");
  await page.setViewportSize({ width: 390, height: 900 });
  await auditAccessibility(page, testInfo, "s19-preview-mobile");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("s19-preview-mobile.png"), fullPage: true });
  await panel.getByLabel("I approve publishing this exact check and selected evidence to this repository and commit").check();
  await publish.click();
  await expect(panel.getByRole("link", { name: "Open verified GitHub check" })).toHaveAttribute("href", fixture.published.check_url);
  expect(calls.approvals).toEqual([{ approve: true, digest: fixture.preview.preview.digest }]);
  expect((await (await request.get(`${API}/simulations?limit=100`)).json()).length).toBe(before);
  expect(errors).toEqual([]);
});

test("uncertain replies reconcile by reading and never submit another check", async ({ page }) => {
  const { panel, calls } = await open(page, { uncertain: true });
  for (const label of ["Reference", "Candidate"]) {
    await panel.getByLabel(`${label} full GitHub commit SHA`).fill(label === "Reference" ? fixture.request.reference.commit : fixture.request.candidate.commit);
    await panel.getByLabel(`${label} package directory in the repository`).fill("pkg");
  }
  await panel.getByRole("button", { name: "Verify commits and preview GitHub check" }).click();
  await panel.getByLabel("I approve publishing this exact check and selected evidence to this repository and commit").check();
  await panel.getByRole("button", { name: "Publish approved GitHub check" }).click();
  await expect(panel.getByText(/The remote outcome is unconfirmed/)).toBeVisible();
  await expect(panel.getByRole("button", { name: "Publish approved GitHub check" })).toHaveCount(0);
  await panel.getByRole("button", { name: "Reconcile GitHub check (read only)" }).click();
  await expect(panel.getByRole("link", { name: "Open verified GitHub check" })).toBeVisible();
  expect(calls.approvals).toHaveLength(1); expect(calls.reconciles).toBe(1);
});

test("off configuration prevents verification and publishing", async ({ page }) => {
  const { panel, calls } = await open(page, { disabled: true });
  await expect(panel.getByText(/GitHub reporting is off/)).toBeVisible();
  await expect(panel.getByLabel("Candidate full GitHub commit SHA")).toBeDisabled();
  await expect(panel.getByRole("button", { name: "Verify commits and preview GitHub check" })).toBeDisabled();
  expect(calls.previews).toEqual([]); expect(calls.approvals).toEqual([]);
});
