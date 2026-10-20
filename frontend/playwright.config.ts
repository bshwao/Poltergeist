import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: { baseURL: "http://127.0.0.1:5189", trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    { command: "node scripts/e2e-backend.mjs", url: "http://127.0.0.1:8049/health", timeout: 180_000, reuseExistingServer: false },
    { command: "npm run dev -- --host 127.0.0.1 --port 5189 --strictPort", url: "http://127.0.0.1:5189", timeout: 60_000,
      env: { VITE_API_URL: "http://127.0.0.1:8049" }, reuseExistingServer: false },
  ],
});
