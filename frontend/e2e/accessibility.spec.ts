import { test, expect } from "@playwright/test";
import { auditAccessibility } from "../test-support/accessibility";

for (const width of [1440, 390]) {
  for (const route of ["overview", "scenarios", "agents", "experiments", "simulations", "failures"]) {
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
  await page.route("**/health", route => route.abort("failed"));
  await page.goto("/#overview");
  const status = page.getByRole("status", { name: "API connection" });
  await expect(status).toContainText("The local API is unavailable");
  await page.unroute("**/health");
  await status.getByRole("button", { name: "Retry connection" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".connection-pill")).toHaveText("API connected");
  await expect(status).not.toBeVisible();
});
