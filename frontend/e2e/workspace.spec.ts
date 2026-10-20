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

test("telemetry hover coordinates match recorded frames on desktop and mobile", async ({ page, request }, testInfo) => {
  const operations = await (await request.get(`${API}/optimizations?limit=10`)).json();
  const comparison = operations.find((o: { request: { kind: string; agents?: unknown[] } }) => o.request.kind === "compare" && (o.request.agents?.length ?? 0) > 1);
  const evidence = await (await request.get(`${API}/optimizations/${comparison.id}`)).json();
  const run = evidence.trials.find((t: { agent_index: number }) => t.agent_index === 0).simulation_id;
  const telemetry = await (await request.get(`${API}/simulations/${run}/telemetry?limit=200`)).json();
  const frames = telemetry.frames;
  const sample = frames[1];
  const velocity = sample.ego.velocity_mps, acceleration = sample.ego.acceleration_mps2;
  const speed = Math.hypot(velocity.x, velocity.y, velocity.z);
  const deceleration = Math.max(0, -(acceleration.x * velocity.x + acceleration.y * velocity.y + acceleration.z * velocity.z) / speed);
  await page.goto(`/#simulations/${run}`);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const [name, expected] of [["Ego speed", `${speed.toFixed(3)} m/s`], ["Longitudinal deceleration", `${deceleration.toFixed(3)} m/s²`]]) {
      const chart = page.getByRole("img", { name: new RegExp(`^${name} over simulation time`) });
      await chart.scrollIntoViewIfNeeded();
      const localX = 52 + (sample.time_s - frames[0].time_s) / (frames[frames.length - 1].time_s - frames[0].time_s) * 570;
      const cursor = await chart.evaluate((element, x) => {
        const svg = element as SVGSVGElement;
        const point = svg.createSVGPoint(); point.x = x; point.y = 100;
        const screen = point.matrixTransform(svg.getScreenCTM()!);
        return { x: screen.x, y: screen.y };
      }, localX);
      await page.mouse.move(cursor.x, cursor.y);
      const tooltip = chart.locator("..").getByRole("tooltip");
      await expect(tooltip).toContainText(`Frame ${sample.frame}`);
      await expect(tooltip).toContainText(`x: ${sample.time_s.toFixed(3)} s`);
      await expect(tooltip).toContainText(`y: ${expected}`);
      const box = (await tooltip.boundingBox())!, plot = (await chart.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(plot.x - 1);
      expect(box.x + box.width).toBeLessThanOrEqual(plot.x + plot.width + 1);
      const marker = (await chart.locator(".chart-inspection circle").boundingBox())!;
      const covers = (x: number, y: number) => x >= box.x && x <= box.x + box.width && y >= box.y && y <= box.y + box.height;
      expect(covers(marker.x + marker.width / 2, marker.y + marker.height / 2)).toBeFalsy();
      expect(covers(cursor.x, cursor.y)).toBeFalsy();
      // At the right edge, the tooltip flips left instead of covering the endpoint.
      const edge = await chart.evaluate(element => {
        const svg = element as SVGSVGElement;
        const point = svg.createSVGPoint(); point.x = 621.99; point.y = 100;
        const screen = point.matrixTransform(svg.getScreenCTM()!);
        return { x: screen.x, y: screen.y };
      });
      await page.mouse.move(edge.x, edge.y);
      await expect(tooltip).toContainText(`Frame ${frames[frames.length - 1].frame}`);
      const flipped = (await tooltip.boundingBox())!;
      expect(flipped.x + flipped.width).toBeLessThan(edge.x);
    }
    await page.getByRole("img", { name: /^Longitudinal deceleration over simulation time/ }).locator("..").screenshot({ path: testInfo.outputPath(`telemetry-hover-${width}.png`) });
  }
  const chart = page.getByRole("img", { name: /^Ego speed over simulation time/ });
  await page.mouse.move(0, 0);
  await chart.focus();
  const tooltip = chart.locator("..").getByRole("tooltip");
  await expect(tooltip).toContainText(`Frame ${frames[0].frame}`);
  await page.keyboard.press("ArrowRight");
  await expect(tooltip).toContainText(`Frame ${sample.frame}`);
  await page.keyboard.press("Escape");
  await expect(tooltip).toHaveCount(0);
});

test("scenario map selection survives generation and validation into the launch recipe", async ({ page }, testInfo) => {
  await page.goto("/#scenarios");
  await page.getByLabel("CARLA map", { exact: true }).selectOption("Town02");
  await page.getByRole("button", { name: /^Generate recipe/ }).click();
  await expect(page.getByLabel("Full recipe JSON")).toHaveValue(/"map": "Town02"/);
  await page.getByRole("button", { name: "Validate JSON", exact: true }).click();
  await expect(page.getByText("The current JSON passed recipe validation.", { exact: true })).toBeVisible();
  await page.getByText("Execution settings", { exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "Allow map reload if the CARLA map differs" })).toBeChecked();
  await page.getByRole("button", { name: /^CARLA/ }).click();
  const quality = page.getByLabel("CARLA graphics quality", { exact: true });
  await expect(quality).toHaveValue("inherit");
  await quality.selectOption("Low");
  await expect(quality).toHaveValue("Low");
  await quality.selectOption("Epic");
  await expect(page.getByText(/close CARLA before launching to apply a new choice/)).toBeVisible();
  await page.getByRole("button", { name: /^Synthetic test/ }).click();
  await expect(quality).toBeDisabled();
  await page.getByRole("button", { name: /^CARLA/ }).click();
  await expect(quality).toBeEnabled();
  await expect(quality).toHaveValue("Epic");
  await expect(page.getByRole("button", { name: /Launch simulation/ })).toBeEnabled();
  await page.getByLabel("CARLA map", { exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("map-selection.png"), fullPage: true });
});
