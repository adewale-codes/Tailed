import { expect, test, type Page } from "@playwright/test";
import { type MotionEvent, fixtureFiles, motionRecorder, resetLimits, takeMotionEvents, visible } from "./helpers";

/** "No documents to hand? Try a sample": made-up documents through the normal pipeline. */

const phone = { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true };
const SAMPLES = [
  { id: "genuine", title: "A genuine tenant", verdict: "Looks good", flags: 0 },
  { id: "edited_balance", title: "An edited bank statement", verdict: "Needs a closer look", flags: 1 },
  { id: "multiple_problems", title: "Several problems at once", verdict: "High risk", flags: 2 },
] as const;

test.beforeEach(() => resetLimits());

function runButton(page: Page, title: string) {
  return page.getByRole("button", { name: `Run this sample: ${title}` });
}

/** One tap on the sample, then the normal live checks screen until the report link appears. */
async function runSample(page: Page, title: string): Promise<void> {
  await page.goto("/");
  await runButton(page, title).click();
  await page.waitForURL(/\/check\/[A-Za-z0-9_-]{43}$/);
  await page.getByRole("link", { name: "View report" }).waitFor({ timeout: 90_000 });
}

test.describe("samples on a phone", () => {
  test.use(phone);

  test("three samples, each listing its documents, and every document opens as a PDF", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "No documents to hand? Try a sample" })).toBeVisible();
    const section = page.locator("[data-section=samples]");
    await expect(section).toContainText("fictional");
    await expect(section).toContainText("don't count towards your 3 free checks");
    await expect(section.locator("[data-sample]")).toHaveCount(3);

    for (const s of SAMPLES) {
      const card = section.locator(`[data-sample=${s.id}]`);
      await expect(card.getByRole("heading", { level: 3 })).toHaveText(s.title);
      await expect(card).toContainText(s.verdict);
      const links = card.getByRole("list", { name: `Documents in “${s.title}”` }).getByRole("link");
      await expect(links).toHaveCount(5);
      for (const link of await links.all()) {
        await expect(link).toHaveAttribute("target", "_blank");
        await expect(link).toHaveAccessibleName(/\(PDF, opens in a new tab\)$/);
        const res = await page.request.get((await link.getAttribute("href"))!);
        expect(res.status()).toBe(200);
        expect(res.headers()["content-type"]).toContain("application/pdf");
        expect((await res.body()).subarray(0, 5).toString()).toBe("%PDF-");
      }
    }
    // No consent box is needed for a sample, and nothing scrolls sideways on a phone.
    await expect(section.getByRole("checkbox")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  });

  test("with reduced motion on, running a sample by keyboard animates nothing", async ({ browser }) => {
    const context = await browser.newContext({ ...phone, reducedMotion: "reduce" });
    await context.addInitScript(motionRecorder);
    const page = await context.newPage();
    const events: MotionEvent[] = [];

    await page.goto("/");
    await runButton(page, "A genuine tenant").focus();
    await page.keyboard.press("Enter");
    await page.waitForURL(/\/check\//);
    await page.getByRole("link", { name: "View report" }).waitFor({ timeout: 90_000 });
    await page.waitForTimeout(600);
    events.push(...(await takeMotionEvents(page)));
    await page.getByRole("link", { name: "View report" }).click();
    await page.locator("[data-sample-notice]").waitFor();
    await page.waitForTimeout(600);
    events.push(...(await takeMotionEvents(page)));

    const named = events.map((e) => `${e.kind}  ${e.el}  (${e.what})  on ${e.path}`);
    expect(named, "elements that animated with reduced motion on").toEqual([]);
    await context.close();
  });
});

test.describe("samples on a desktop", () => {
  test.use({ viewport: { width: 1280, height: 900 } });

  test("one tap runs each sample to a report with a Sample badge, and its evidence", async ({ page }) => {
    for (const s of SAMPLES) {
      await runSample(page, s.title);
      await page.getByRole("link", { name: "View report" }).click();
      await page.waitForURL(/\/report\/[A-Za-z0-9_-]{43}$/);

      const notice = visible(page, page.locator("[data-sample-notice]"));
      await expect(notice).toContainText("Sample");
      await expect(notice).toContainText(`“${s.title}” sample`);
      await expect(notice).toContainText("The documents are made up");
      await expect(page.locator("[data-section=verdict]:visible p").first()).toHaveText(s.verdict);
      const links = page.getByRole("link", { name: /See (it on the page|the evidence)/ });
      await expect(links).toHaveCount(s.flags);

      if (s.flags) {
        await links.first().click();
        await page.waitForURL(/\/evidence\//);
        await expect(visible(page, page.locator("[data-sample-notice]"))).toContainText("made up");
        const image = page.locator("[data-section=finding]:visible figure img").first();
        await expect
          .poll(() => image.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0))
          .toBe(true);
      }
    }
  });

  test("samples don't use the 3 real checks a day", async ({ page }) => {
    const payslip = fixtureFiles("genuine").find((f) => f.endsWith("payslip_2026-09.pdf"))!;
    const fs = await import("node:fs");
    const realCheck = () =>
      page.request.post("/api/checks", {
        multipart: {
          monthly_rent: "1100",
          consent: "true",
          files: { name: "payslip.pdf", mimeType: "application/pdf", buffer: fs.readFileSync(payslip) },
        },
      });
    expect([(await realCheck()).status(), (await realCheck()).status(), (await realCheck()).status()]).toEqual([
      202, 202, 202,
    ]);

    // The real form now shows the daily limit, and points to the samples...
    await page.goto("/");
    await page.locator('input[type="file"]').setInputFiles([payslip]);
    await page.getByLabel("Monthly rent").fill("1100");
    await page.getByLabel(/tenant has agreed/).check();
    await page.locator('button[type="submit"]').click();
    await expect(page.getByRole("heading", { name: "You've reached today's limit" })).toBeVisible();
    await expect(page.getByText("You can still try the samples below")).toBeVisible();

    // ...which still run.
    await runButton(page, "An edited bank statement").click();
    await page.waitForURL(/\/check\//);
    await page.getByRole("link", { name: "View report" }).waitFor({ timeout: 90_000 });
  });
});
