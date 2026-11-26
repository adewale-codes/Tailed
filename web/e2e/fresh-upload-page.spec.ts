import fs from "node:fs";
import { type Browser, type Page, chromium, expect, test } from "@playwright/test";
import { fillUpload, fixtureFiles, resetLimits, visible } from "./helpers";

/**
 * The upload page must always start fresh: no "Running the sample… 100%", every button enabled, no
 * leftover files, rent, consent, errors or limit message from an earlier run.
 *
 * Why this needs testing: with cacheComponents, Next.js doesn't unmount a page you leave. It hides it
 * with React's <Activity> and shows the same instance, state and all, when you come back (up to 3
 * routes). The browser's own back/forward cache (bfcache) can also restore a whole frozen page.
 */

const SAMPLE_TITLES = ["A genuine tenant", "An edited bank statement", "Several problems at once"];
const genuine = fixtureFiles("genuine");
const payslip = genuine.find((f) => f.endsWith("payslip_2026-09.pdf"))!;

test.beforeEach(() => resetLimits());
test.use({ viewport: { width: 1280, height: 900 } });

/** The visible upload page is exactly what a first visit shows. */
async function expectFreshUploadPage(page: Page): Promise<void> {
  await expect(page).toHaveURL(/\/$/);
  for (const title of SAMPLE_TITLES) {
    const button = page.getByRole("button", { name: `Run this sample: ${title}` });
    await expect(button, `${title}: button is back to its first state`).toBeEnabled();
    await expect(button).toHaveText("Run this sample");
  }
  await expect(page.getByText(/Running the sample/)).toHaveCount(0);
  // No error from an earlier run. (Scoped to <main>: Next.js's route announcer is also a role=alert.)
  await expect(page.locator("main").getByRole("alert")).toHaveCount(0);
  // The real form: nothing picked, typed or ticked, and no limit message.
  await expect(page.locator("[data-remove-file]:visible")).toHaveCount(0);
  await expect(visible(page, page.getByLabel("Monthly rent"))).toHaveValue("");
  await expect(visible(page, page.getByLabel(/tenant has agreed/))).not.toBeChecked();
  await expect(page.locator('button[type="submit"]:visible')).toHaveText("Add a document and tick the consent box");
  await expect(page.getByRole("heading", { name: "You've reached today's limit" })).toHaveCount(0);
}

async function runSampleToReport(page: Page, title: string): Promise<void> {
  await page.goto("/");
  await page.getByRole("button", { name: `Run this sample: ${title}` }).click();
  await page.waitForURL(/\/check\//);
  const view = page.getByRole("link", { name: "View report" });
  await view.waitFor({ timeout: 90_000 });
  await view.click();
  await page.waitForURL(/\/report\//);
  await visible(page, page.locator("[data-sample-notice]")).waitFor();
}

test("after a sample, 'Check another applicant' shows a fresh upload page", async ({ page }) => {
  await runSampleToReport(page, "A genuine tenant");
  await page.getByRole("link", { name: "Check another applicant" }).click();
  await expectFreshUploadPage(page);
  // And another sample really runs.
  await page.getByRole("button", { name: "Run this sample: An edited bank statement" }).click();
  await page.waitForURL(/\/check\//);
});

test("after a sample, the browser back button returns to a fresh upload page", async ({ page }) => {
  await runSampleToReport(page, "Several problems at once");
  await page.goBack(); // report -> live checks
  await page.waitForURL(/\/check\//);
  await page.goBack(); // live checks -> upload page
  await expectFreshUploadPage(page);
});

test("after a real check, 'Check another applicant' shows an empty form", async ({ page }) => {
  await fillUpload(page, genuine);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(/\/check\//);
  await page.getByRole("link", { name: "View report" }).waitFor({ timeout: 90_000 });
  await page.getByRole("link", { name: "View report" }).click();
  await page.getByRole("link", { name: "Check another applicant" }).click();
  await expectFreshUploadPage(page);
});

test("a limit message from an earlier attempt is gone after navigating away and back", async ({ page }) => {
  const realCheck = () =>
    page.request.post("/api/checks", {
      multipart: {
        monthly_rent: "1100",
        consent: "true",
        files: { name: "payslip.pdf", mimeType: "application/pdf", buffer: fs.readFileSync(payslip) },
      },
    });
  for (let i = 0; i < 3; i++) expect((await realCheck()).status()).toBe(202);
  await fillUpload(page, [payslip]);
  await page.locator('button[type="submit"]').click();
  await expect(page.getByRole("heading", { name: "You've reached today's limit" })).toBeVisible();
  await page.getByRole("link", { name: "Privacy" }).click(); // client-side: the page is hidden, not unmounted
  await page.waitForURL(/\/privacy$/);
  await page.goBack();
  await expectFreshUploadPage(page);
});

test.describe("browser back/forward cache", () => {
  let bfBrowser: Browser;
  test.beforeAll(async () => {
    // Playwright turns the bfcache off by default; this browser has it on, like a real one.
    bfBrowser = await chromium.launch({
      channel: process.env.E2E_BROWSER_CHANNEL || undefined,
      ignoreDefaultArgs: ["--disable-back-forward-cache"],
    });
  });
  test.afterAll(() => bfBrowser.close());

  test("a page restored from the bfcache starts fresh", async () => {
    const context = await bfBrowser.newContext({ baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000" });
    await context.addInitScript(() => {
      addEventListener("pageshow", (e) => {
        if (e.persisted) (window as unknown as { __fromBfcache: boolean }).__fromBfcache = true;
      });
    });
    const page = await context.newPage();
    for (let i = 0; i < 3; i++) {
      const res = await page.request.post("/api/checks", {
        multipart: {
          monthly_rent: "1100",
          consent: "true",
          files: { name: "payslip.pdf", mimeType: "application/pdf", buffer: fs.readFileSync(payslip) },
        },
      });
      expect(res.status()).toBe(202);
    }
    // Leave the upload page while it shows a picked file, a limit message and a typed rent...
    await fillUpload(page, [payslip]);
    await page.locator('button[type="submit"]').click();
    await expect(page.getByRole("heading", { name: "You've reached today's limit" })).toBeVisible();
    await page.goto("about:blank"); // a full navigation to another document
    // ...and come back through the browser's back/forward cache. A restored page doesn't fire "load",
    // so wait only for the navigation to commit, then for the restored document.
    await page.goBack({ waitUntil: "commit" });
    // The page came back from the bfcache (otherwise this test proves nothing).
    await page.waitForFunction(() => (window as unknown as { __fromBfcache?: boolean }).__fromBfcache === true, null, {
      timeout: 15_000,
    });
    await expectFreshUploadPage(page);
    await context.close();
  });
});
