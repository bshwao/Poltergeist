import { test, expect } from "@playwright/test";
import { auditAccessibility } from "../test-support/accessibility";

for (const width of [1440, 390]) {
  for (const route of ["overview", "lab", "discovery", "scenarios", "agents", "experiments", "simulations", "failures"]) {
    test(`accessible ${route} layout at ${width}px`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`/#${route}`);
      await expect(page.locator(".connection-pill")).toHaveText("API connected");
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await auditAccessibility(page, testInfo, `${route}-${width}`);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), route).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`${route}-${width}.png`), fullPage: true });
    });
  }
}

test("a lost local API has a keyboard reachable recovery action", async ({ page }) => {
  let release!: () => void;
  const templates = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/scenarios/templates", async route => { await templates; await route.continue(); });
  await page.route("**/health", route => route.abort("failed"));
  await page.goto("/#overview");
  const status = page.getByRole("status", { name: "API connection" });
  const retry = status.getByRole("button", { name: "Retry connection" });
  try {
    await expect(status).toContainText("The local API is unavailable");
    await expect(retry).toBeDisabled();
  } finally { release(); }
  await page.unroute("**/health");
  await expect(retry).toBeEnabled();
  await retry.focus();
  await expect(retry).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator(".connection-pill")).toHaveText("API connected");
  await expect(status).not.toBeVisible();
});
