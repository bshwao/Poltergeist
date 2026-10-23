import { test, expect } from "@playwright/test";

test("stress catalogue loads a real recipe and protects a newer draft", async ({ page }, testInfo) => {
  let launches = 0;
  page.on("request", request => {
    if (request.method() === "POST" && /\/simulations$/.test(request.url())) launches++;
  });
  await page.goto("/#scenarios");
  const panel = page.getByRole("region", { name: "Collision stress presets" });
  await expect(panel).toContainText("12 presets");
  await panel.getByLabel("Stress preset", { exact: true }).selectOption("intersection-medium");
  await panel.getByRole("button", { name: "Load stress preset", exact: true }).click();
  await expect(page.getByLabel("Recipe name", { exact: true })).toHaveValue("intersection-medium");
  await expect(page.getByLabel("Experiment duration", { exact: false }).first()).toHaveValue("8");
  const recipe = JSON.parse(await page.getByLabel("Full recipe JSON").inputValue());
  expect(recipe.parameters.crossing_distance_m).toBe(6);
  expect(recipe.map).toBe("Town10HD_Opt");
  expect(recipe.seed).toBe(7);
  await page.getByLabel("Recipe name", { exact: true }).fill("Preserve this draft");
  await panel.getByLabel("Stress preset", { exact: true }).selectOption("sudden_braking-high");
  await panel.getByRole("button", { name: "Load stress preset", exact: true }).click();
  const confirmation = page.getByRole("region", { name: "Replace workspace draft" });
  await expect(confirmation).toBeVisible();
  await confirmation.getByRole("button", { name: "Keep current draft" }).click();
  await expect(page.getByLabel("Recipe name", { exact: true })).toHaveValue("Preserve this draft");
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await panel.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
    await panel.screenshot({ path: testInfo.outputPath(`stress-presets-${width}.png`) });
  }
  expect(launches).toBe(0);
});
