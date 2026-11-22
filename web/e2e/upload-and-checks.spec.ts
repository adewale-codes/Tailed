import fs from "node:fs";
import { expect, test } from "@playwright/test";
import {
  type MotionEvent,
  fillUpload,
  fixtureFiles,
  motionRecorder,
  resetLimits,
  takeMotionEvents,
  visible,
} from "./helpers";

const genuine = fixtureFiles("genuine");
const phone = { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true };

test.beforeEach(() => resetLimits());

test.describe("upload screen and live checks", () => {
  test.use(phone);

  test("a fixture runs end to end, and the button can't be clicked without files and consent", async ({ page }) => {
    const posts: string[] = [];
    page.on("request", (r) => {
      if (r.method() === "POST" && r.url().includes("/api/")) posts.push(r.url());
    });
    await page.goto("/");
    const submit = page.locator('button[type="submit"]');

    await expect(submit).toBeDisabled();
    await expect(submit).toHaveText("Add a document and tick the consent box");
    await submit.click({ force: true }).catch(() => {});
    await page.waitForTimeout(300);
    expect(posts, "clicking the disabled button sends nothing").toEqual([]);

    await page.locator('input[type="file"]').setInputFiles(genuine);
    await expect(page.locator("[data-remove-file]")).toHaveCount(5);
    await expect(submit).toHaveText("Enter the monthly rent");
    await visible(page, page.getByLabel("Monthly rent")).fill("1,100");
    await expect(submit).toHaveText("Tick the consent box to continue");
    await expect(submit).toBeDisabled();
    await submit.click({ force: true }).catch(() => {});
    expect(posts, "still nothing sent without consent").toEqual([]);

    // The remove buttons work, and re-adding the same files skips duplicates.
    await page.locator("[data-remove-file]").first().click();
    await expect(page.locator("[data-remove-file]")).toHaveCount(4);
    await page.locator('input[type="file"]').setInputFiles(genuine);
    await expect(page.locator("[data-remove-file]")).toHaveCount(5);

    await visible(page, page.getByLabel(/tenant has agreed/)).check();
    await expect(submit).toBeEnabled();
    await expect(submit).toHaveText("Check 5 documents");
    expect(await page.evaluate(() => document.documentElement.scrollWidth), "no sideways scroll at 390px").toBeLessThanOrEqual(390);

    await submit.click();
    await page.waitForURL(/\/check\/[A-Za-z0-9_-]{43}$/);
    const viewReport = page.getByRole("link", { name: "View report" });
    await viewReport.waitFor({ timeout: 90_000 });

    const rows = page.locator('ol[aria-label="The checks"] li');
    await expect(rows).toHaveCount(7);
    for (const text of await rows.allInnerTexts()) expect(text).not.toMatch(/Waiting|Checking…/);
    await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    expect(await page.locator("figure img").evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

    await viewReport.click();
    await page.waitForURL(/\/report\/[A-Za-z0-9_-]{43}$/);
  });

  test("with reduced motion on, nothing animates", async ({ browser }) => {
    const context = await browser.newContext({ ...phone, reducedMotion: "reduce" });
    await context.addInitScript(motionRecorder);
    const page = await context.newPage();
    const events: MotionEvent[] = [];

    await page.goto("/");
    await page.locator('input[type="file"]').setInputFiles(genuine);
    await page.locator("[data-remove-file]").first().click(); // the row's exit path
    await page.locator('input[type="file"]').setInputFiles(genuine);
    await visible(page, page.getByLabel("Monthly rent")).fill("1100");
    await visible(page, page.getByLabel(/tenant has agreed/)).check();
    events.push(...(await takeMotionEvents(page)));

    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/check\//);
    await page.getByRole("link", { name: "View report" }).waitFor({ timeout: 90_000 });
    await page.waitForTimeout(600); // the "View report" entrance and the last result icons
    events.push(...(await takeMotionEvents(page)));

    const named = events.map((e) => `${e.kind}  ${e.el}  (${e.what})  on ${e.path}`);
    expect(named, "elements that animated with reduced motion on").toEqual([]);
    const spinning = await page.evaluate(
      () => [...document.querySelectorAll("svg.animate-spin")].filter((e) => getComputedStyle(e).animationName !== "none").length,
    );
    expect(spinning, "spinners don't spin").toBe(0);
    await context.close();
  });

  test("the recorder does see animations when motion is allowed (control)", async ({ browser }) => {
    const context = await browser.newContext({ ...phone, reducedMotion: "no-preference" });
    await context.addInitScript(motionRecorder);
    const page = await context.newPage();
    await fillUpload(page, genuine);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/check\//);
    await page.getByRole("link", { name: "View report" }).waitFor({ timeout: 90_000 });
    await page.waitForTimeout(600);
    expect((await takeMotionEvents(page)).length).toBeGreaterThan(5);
    await context.close();
  });

  test("friendly states: unreadable file, one unreadable file, daily limit, server errors, unknown check", async ({
    page,
  }, testInfo) => {
    const broken = testInfo.outputPath("broken.pdf");
    fs.writeFileSync(broken, "%PDF-1.7 this is not really a pdf");

    // Only an unreadable file: the check fails with a friendly explanation.
    await fillUpload(page, [broken]);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/check\//);
    await expect(page.getByRole("heading", { name: "We couldn't read these documents" })).toBeVisible({ timeout: 30_000 });

    // One unreadable file among good ones: the rest are checked, and the notice names the file.
    await fillUpload(page, [...genuine.slice(0, 2), broken]);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/check\//);
    await page.getByRole("link", { name: "View report" }).waitFor({ timeout: 90_000 });
    await expect(page.getByRole("status").filter({ hasText: "couldn't read" })).toContainText("broken.pdf");

    // Third check today goes through; the fourth gets the daily-limit state.
    await fillUpload(page, genuine.slice(0, 1));
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/check\//);
    await fillUpload(page, genuine.slice(0, 1));
    await page.locator('button[type="submit"]').click();
    await expect(page.getByRole("heading", { name: "You've reached today's limit" })).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/");

    // Repeated server errors while polling.
    const unknownId = "A".repeat(43);
    await page.route("**/api/checks/*/progress", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "internal_error", message: "Something went wrong on our side. Please try again." } }),
      }),
    );
    await page.goto(`/check/${unknownId}`);
    await expect(page.getByRole("heading", { name: "We've lost touch with the checking service" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
    await page.unroute("**/api/checks/*/progress");

    // An id that doesn't exist.
    await page.goto(`/check/${unknownId}`);
    await expect(page.getByRole("heading", { name: "We couldn't find this check" })).toBeVisible();
  });
});

test.describe("desktop", () => {
  test.use({ viewport: { width: 1280, height: 900 } });

  test("keyboard only: visible focus, file picker, remove, consent", async ({ page }) => {
    await page.goto("/");
    const stops: { tag: string; label: string; outline: boolean }[] = [];
    for (let i = 0; i < 8; i++) {
      await page.keyboard.press("Tab");
      stops.push(
        await page.evaluate(() => {
          const el = document.activeElement as HTMLElement;
          const s = getComputedStyle(el);
          return {
            tag: el.tagName,
            label: (el.getAttribute("aria-label") || el.textContent || el.id || "").trim().slice(0, 40),
            outline: s.outlineStyle !== "none" && parseFloat(s.outlineWidth) > 0,
          };
        }),
      );
    }
    // BODY is focus wrapping back to the browser after the last stop, not a page element.
    const missing = stops.filter((s) => s.tag !== "BODY" && !s.outline).map((s) => `${s.tag} "${s.label}"`);
    expect(missing, "tab stops without a visible focus outline").toEqual([]);

    await page.getByLabel("Monthly rent").focus();
    await page.keyboard.type("950");
    const chooser = page.waitForEvent("filechooser");
    await page.getByRole("button", { name: /browse your files/ }).focus();
    await page.keyboard.press("Enter");
    await (await chooser).setFiles(genuine.slice(0, 2));
    await expect(page.locator("[data-remove-file]")).toHaveCount(2);

    await page.locator("[data-remove-file]").first().focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("[data-remove-file]")).toHaveCount(1);
    await expect(page.locator(":focus")).toHaveAttribute("aria-label", /^Remove /);

    await page.getByLabel(/tenant has agreed/).focus();
    await page.keyboard.press("Space");
    await expect(page.getByLabel(/tenant has agreed/)).toBeChecked();
  });

  test("with motion allowed, the scanning line runs and stops when done", async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, reducedMotion: "no-preference" });
    const page = await context.newPage();
    await fillUpload(page, genuine);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/check\//);
    await expect(page.locator("figure > div.border-accent")).toHaveCount(1, { timeout: 3_000 });
    await page.getByRole("link", { name: "View report" }).waitFor({ timeout: 90_000 });
    await expect(page.locator("figure > div.border-accent")).toHaveCount(0);
    await context.close();
  });
});
