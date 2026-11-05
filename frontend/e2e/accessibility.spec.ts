import { test, expect } from "@playwright/test";
import { auditAccessibility } from "../test-support/accessibility";

const workspacePages = ["overview", "lab", "discovery", "scenarios", "agents", "experiments", "simulations", "failures"];

for (const width of [1440, 768, 390, 320]) {
  for (const route of workspacePages) {
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

for (const route of workspacePages) {
  test(`accessible ${route} layout in dark appearance`, async ({ page }, testInfo) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.setViewportSize({ width: 390, height: 900 });
    await page.goto(`/#${route}`);
    await expect(page.locator("html")).toHaveCSS("color-scheme", "dark");
    await expect(page.locator(".connection-pill")).toHaveText("API connected");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await auditAccessibility(page, testInfo, `${route}-dark`);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), route).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`${route}-dark.png`), fullPage: true });
  });
}

test("navigation stays readable and reachable in a short desktop viewport", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 600 });
  await page.goto("/#overview");
  await expect(page.locator(".connection-pill")).toHaveText("API connected");
  const sidebar = page.locator(".sidebar");
  const footer = sidebar.getByText("Local development", { exact: false });
  await footer.scrollIntoViewIfNeeded();
  await expect(footer).toBeInViewport();
  await auditAccessibility(page, testInfo, "short-desktop-sidebar-end");
  const overview = sidebar.getByRole("link", { name: "Overview", exact: true });
  await overview.focus();
  await expect(overview).toBeFocused();
  await expect(overview).toBeInViewport();
  await auditAccessibility(page, testInfo, "short-desktop-sidebar-navigation");
});

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

test("keyboard focus remains visible on the recipe editor in forced colors", async ({ page }) => {
  await page.emulateMedia({ forcedColors: "active" });
  await page.goto("/#scenarios");
  const editor = page.getByLabel("Full recipe JSON");
  await expect(editor).toBeVisible();
  await page.keyboard.press("Tab");
  await editor.focus();
  const focus = await editor.evaluate(element => ({ visible: element.matches(":focus-visible"),
    style: getComputedStyle(element).outlineStyle, width: getComputedStyle(element).outlineWidth }));
  expect(focus.visible).toBe(true); expect(focus.style).toBe("solid"); expect(focus.width).toBe("3px");
});

test("active navigation and lab tabs remain visually identifiable in forced colors", async ({ page }) => {
  await page.emulateMedia({ forcedColors: "active", colorScheme: "dark" });
  await page.goto("/#lab");
  const nav = page.getByRole("navigation", { name: "Main navigation" });
  await expect(nav.getByRole("link", { name: "Guided lab", exact: true })).toHaveCSS("text-decoration-line", "underline");
  await expect(nav.getByRole("link", { name: "Overview", exact: true })).toHaveCSS("text-decoration-line", "none");
  const guide = page.getByRole("tab", { name: "First experiment" });
  const gallery = page.getByRole("tab", { name: "Scenario gallery" });
  await expect(guide).toHaveCSS("text-decoration-line", "underline");
  await guide.focus();
  await page.keyboard.press("ArrowRight");
  await expect(gallery).toBeFocused();
  await expect(gallery).toHaveCSS("text-decoration-line", "underline");
  await expect(guide).toHaveCSS("text-decoration-line", "none");
});

test("a failed page import preserves another page's draft until an explicit reload", async ({ page }, testInfo) => {
  await page.goto("/#scenarios");
  const draft = '{"name":"Draft retained while a page download fails"}';
  await page.getByLabel("Full recipe JSON").fill(draft);
  await page.route("**/src/pages/Agents.tsx*", route => route.abort("failed"));
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Agents", exact: true }).click();
  const alert = page.getByRole("alert").filter({ hasText: "Unable to open Agents" });
  await expect(alert).toBeVisible();
  await page.setViewportSize({ width: 390, height: 900 });
  await auditAccessibility(page, testInfo, "failed-page-mobile");
  await page.screenshot({ path: testInfo.outputPath("failed-page-mobile.png"), fullPage: true });
  await page.unroute("**/src/pages/Agents.tsx*");
  await expect(alert).toContainText("reloading clears the current drafts");
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Scenarios", exact: false }).click();
  await expect(page.getByLabel("Full recipe JSON")).toHaveValue(draft);
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Agents", exact: true }).click();
  await alert.getByRole("button", { name: "Reload dashboard" }).click();
  await expect(page.getByRole("heading", { name: "Agent workspace.", exact: true })).toBeVisible();
});
