import { test, expect, type Page, type APIRequestContext } from "@playwright/test";
import { auditAccessibility } from "../test-support/accessibility";

async function control(request: APIRequestContext, action: string, name = "alice") {
  const result = await request.post(`http://127.0.0.1:8059/${action}`, {
    headers: { Authorization: `Bearer ${process.env.POLTERGEIST_HOSTED_FIXTURE_TOKEN}` }, data: { name },
  });
  expect(result.status()).toBe(200);
  return result.json();
}
async function register(page: Page, username: string) {
  await page.goto("/app/");
  await page.getByRole("button", { name: "New here? Create an account" }).click();
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill("private browser fixture passphrase");
  await page.getByRole("button", { name: "Create account", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Your lab, connected." })).toBeVisible();
}

test("computer loading and network failure remain distinct from an empty workspace", async ({ page }, testInfo) => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/v1/browser/devices", async route => { await gate; await route.continue(); });
  try {
    await register(page, "designreview");
    await expect(page.getByText("Loading your computers…", { exact: true })).toBeVisible();
    await expect(page.getByText("No computers connected yet", { exact: true })).not.toBeVisible();
    await expect(page.locator(".hosted-stats > div").first()).toContainText("Loading…");
  } finally { release(); }
  await expect(page.getByText("No computers connected yet", { exact: true })).toBeVisible();
  await page.unroute("**/v1/browser/devices");
  await page.route("**/v1/browser/devices", route => route.abort("failed"));
  await page.getByRole("button", { name: "Refresh computers", exact: true }).click();
  await expect(page.getByText("Computer availability could not be refreshed", { exact: true })).toBeVisible();
  await expect(page.locator(".hosted-stats > div").first()).toContainText("Unknown");
  await expect(page.getByText("No computers connected yet", { exact: true })).not.toBeVisible();
  await auditAccessibility(page, testInfo, "availability-unknown");
  await page.unroute("**/v1/browser/devices");
  await page.getByRole("button", { name: "Refresh computers", exact: true }).click();
  await expect(page.getByText("No computers connected yet", { exact: true })).toBeVisible();
});

test("two isolated accounts pair, execute once across a lost response, cancel, reload, pause and revoke", async ({ page, browser, request }, testInfo) => {
  const errors: string[] = [], destinations: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", request => destinations.push(new URL(request.url()).origin));
  await page.addInitScript(() => {
    (window as unknown as { cspViolations: string[] }).cspViolations = [];
    document.addEventListener("securitypolicyviolation", event => {
      (window as unknown as { cspViolations: string[] }).cspViolations.push(event.violatedDirective);
    });
  });
  const document = await page.goto("/app/");
  expect(document?.headers()["content-security-policy"]).toContain("connect-src 'self'");
  expect(document?.headers()["content-security-policy"]).not.toContain("unsafe-inline");
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("main")).toBeFocused();
  await auditAccessibility(page, testInfo, "sign-in");
  await page.screenshot({ path: testInfo.outputPath("sign-in-desktop.png"), fullPage: true });
  await register(page, "alice");
  await page.getByRole("link", { name: "Skip to content" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("main")).toBeFocused();
  await expect(page.getByText("No computers connected yet", { exact: true })).toBeVisible();
  await auditAccessibility(page, testInfo, "empty-workspace");
  await expect(page.getByText("Your next experiment starts here")).not.toBeVisible();
  const first = await control(request, "new");
  await page.getByLabel("Pairing code", { exact: true }).fill(first.code);
  await page.getByRole("button", { name: "Connect computer", exact: true }).click();
  await expect(page.getByText("Waiting for local approval", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Pairing code", { exact: true })).toHaveValue("");
  const alice = await control(request, "approve");
  const computer = page.getByRole("region", { name: "Choose your companion" });
  await expect(computer.getByText("connected", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Execution computer")).toContainText("Alice computer");
  await expect(page.getByRole("button", { name: "Run experiment", exact: true })).toBeEnabled();
  expect(await page.evaluate(() => Object.keys(localStorage).length + Object.keys(sessionStorage).length)).toBe(0);

  const bobContext = await browser.newContext({ baseURL: "https://localhost:8058", ignoreHTTPSErrors: true });
  try {
    const bob = await bobContext.newPage();
    await register(bob, "bob");
    const second = await control(request, "new", "bob");
    await bob.getByLabel("Pairing code", { exact: true }).fill(second.code);
    await bob.getByRole("button", { name: "Connect computer", exact: true }).click();
    await expect(bob.getByText("Waiting for local approval", { exact: true })).toBeVisible();
    await control(request, "approve", "bob");
    await expect(bob.getByLabel("Execution computer")).toContainText("Bob computer");
    await expect(bob.getByLabel("Execution computer")).not.toContainText("Alice computer");
    const denied = await bobContext.request.get(`/v1/devices/${alice.device_id}/commands`, { headers: { "X-Poltergeist-Client": "browser" } });
    expect(denied.status()).toBe(404);

    // Drop the HTTP response after the real coordinator has admitted the write.
    // Keep readback hidden until the explicit retry so this proves identity reuse.
    const executeIds: string[] = []; let dropped = false, retried = false;
    await page.route("**/v1/devices/*/commands?limit=20", async route => {
      if (dropped && !retried) await route.fulfill({ json: { items: [], next_before: null } });
      else await route.continue();
    });
    await page.route("**/v1/devices/*/commands", async route => {
      const body = route.request().postDataJSON();
      if (body?.kind !== "execute") { await route.continue(); return; }
      executeIds.push(body.request_id);
      if (!dropped) { await route.fetch(); dropped = true; await route.abort("failed"); }
      else { retried = true; await route.continue(); }
    });
    await page.getByLabel("Experiment name", { exact: true }).fill("First browser experiment");
    await page.getByRole("button", { name: "Run experiment", exact: true }).click();
    await expect(page.getByText("Delivery is unconfirmed", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Retry original request", exact: true }).click();
    await expect(page.getByRole("region", { name: "First browser experiment" }).getByText("completed", { exact: true })).toBeVisible();
    expect(executeIds.length).toBeGreaterThanOrEqual(2);
    expect(new Set(executeIds).size).toBe(1);
    expect((await control(request, "state")).runs).toBe(1);
    expect((await control(request, "state", "bob")).runs).toBe(0);
    await page.reload();
    const result = page.getByRole("region", { name: "First browser experiment" });
    await expect(result.getByText("completed", { exact: true })).toBeVisible();
    await expect(result.getByText("Evidence stays on your computer", { exact: true })).toBeVisible();
    await expect(result.getByText("Not measured", { exact: true })).toBeVisible();
    await expect(result.locator(".hosted-metrics > div").first()).toHaveText("Collisions0");
    const evidence = page.getByRole("region", { name: "Telemetry evidence", exact: true });
    await expect(evidence.getByRole("button", { name: "Read telemetry", exact: true })).toBeEnabled();
    await evidence.getByRole("button", { name: "Read telemetry", exact: true }).click();
    await expect(evidence.getByRole("table")).toBeVisible();
    expect(await evidence.locator("tbody tr").count()).toBeGreaterThan(0);
    expect(await evidence.locator("tbody tr").count()).toBeLessThanOrEqual(100);

    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
      if (width === 390) {
        const table = page.getByRole("region", { name: "Recorded telemetry table" });
        await table.focus();
        await page.keyboard.press("ArrowRight");
        await expect.poll(() => table.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
      }
      await auditAccessibility(page, testInfo, `result-${width}`);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: testInfo.outputPath(`workspace-${width}.png`), fullPage: true });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByLabel("Experiment name", { exact: true }).fill("Cancel browser experiment");
    await page.getByRole("button", { name: "Run experiment", exact: true }).click();
    await expect.poll(async () => (await control(request, "state")).entered).toBe(true);
    await expect(page.getByRole("button", { name: "Request cancellation", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "Request cancellation", exact: true }).click();
    await expect(page.getByText("Cancellation requested. The final experiment result confirms its outcome.", { exact: true })).toBeVisible();
    await control(request, "release");
    await expect(page.getByRole("region", { name: "Cancel browser experiment" }).getByText("cancelled", { exact: true })).toBeVisible();
    expect((await control(request, "state")).runs).toBe(2);

    await control(request, "pause");
    await expect(computer.getByText("paused", { exact: true })).toBeVisible();
    await auditAccessibility(page, testInfo, "paused-computer");
    await expect(page.getByRole("button", { name: "Run experiment", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Refresh result", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Read telemetry", exact: true })).toBeDisabled();
    await control(request, "disconnect");
    await expect(computer.getByText("offline", { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("region", { name: "Cancel browser experiment" }).getByText("cancelled", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Revoke computer access", exact: true }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await auditAccessibility(page, testInfo, "revoke-dialog");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).not.toBeVisible();
    await page.getByRole("button", { name: "Revoke computer access", exact: true }).click();
    await page.getByRole("button", { name: "Revoke access", exact: true }).click();
    await expect(computer.getByText("revoked", { exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Recent experiments", exact: true })).not.toBeVisible();
    await expect(page.getByRole("region", { name: "Cancel browser experiment" })).not.toBeVisible();
    expect((await control(request, "state")).runs).toBe(2);
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  } finally { await bobContext.close(); }
  expect(errors).toEqual([]);
  expect(await page.evaluate(() => (window as unknown as { cspViolations: string[] }).cspViolations)).toEqual([]);
  expect(new Set(destinations)).toEqual(new Set(["https://localhost:8058"]));
});
