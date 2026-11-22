import { defineConfig } from "@playwright/test";

/**
 * Browser end-to-end tests against a running Tallied on this machine (see README, "End-to-end tests").
 * They need the web app, the API and the local Postgres running; e2e/global-setup.ts checks this first.
 *
 *   E2E_BASE_URL         web app URL (default http://127.0.0.1:3000)
 *   E2E_API_URL          API URL, for the reachability check (default http://127.0.0.1:8000)
 *   E2E_BROWSER_CHANNEL  use an installed browser instead of Playwright's Chromium, e.g. "msedge" or "chrome"
 */
export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  // One worker: every test shares the API's per-IP daily limit, which the helpers reset between tests.
  workers: 1,
  fullyParallel: false,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  outputDir: "test-results",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000",
    channel: process.env.E2E_BROWSER_CHANNEL || undefined,
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
