import { randomUUID } from "node:crypto";
import { defineConfig, devices } from "@playwright/test";

process.env.POLTERGEIST_HOSTED_FIXTURE_TOKEN ??= randomUUID();
export default defineConfig({
  testDir: "./hosted-e2e", workers: 1, fullyParallel: false, retries: 0,
  timeout: 120_000, expect: { timeout: 20_000 },
  outputDir: "test-results/hosted", reporter: [["list"], ["html", { outputFolder: "playwright-report/hosted", open: "never" }]],
  use: { baseURL: "https://localhost:8058", ignoreHTTPSErrors: true,
    trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [{ name: "hosted-chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: { command: "node scripts/hosted-e2e-backend.mjs", url: "http://127.0.0.1:8059/healthz",
    timeout: 90_000, reuseExistingServer: false,
    env: { POLTERGEIST_HOSTED_FIXTURE_TOKEN: process.env.POLTERGEIST_HOSTED_FIXTURE_TOKEN } },
});
