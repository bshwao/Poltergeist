import { test, expect } from "@playwright/test";

test("Overview shows real static readiness and preserves it on a failed refresh", async ({ page }, testInfo) => {
  let explicitProbes = 0;
  page.on("request", request => { if (request.url().endsWith("/readiness/carla-check")) explicitProbes++; });
  await page.goto("/#overview");
  const panel = page.getByRole("region", { name: "Setup readiness" });
  await expect(panel.getByRole("heading", { name: "Synthetic test", exact: true })).toBeVisible();
  await expect(panel.getByRole("heading", { name: "CARLA live simulation" })).toBeVisible();
  await expect(panel).toContainText("Ready to try synthetic mode");
  await expect(panel).toContainText("Static checks only; CARLA connection is not checked.");
  expect(explicitProbes).toBe(0);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await panel.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
    await panel.screenshot({ path: testInfo.outputPath(`readiness-${width}.png`) });
  }
  await page.route("**/readiness", route => route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"Setup service unavailable."}' }));
  await panel.getByRole("button", { name: "Refresh setup" }).click();
  await expect(panel.getByRole("alert")).toContainText("Previous snapshot shown below; it may be out of date.");
  await expect(panel.getByRole("heading", { name: "Synthetic test", exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Retry setup check" })).toBeEnabled();
  expect(explicitProbes).toBe(0);
});
