import { expect, test, type Page } from "@playwright/test";
import { CASES, type Case, expectedVerdict, expireCheck, fillUpload, fixtureFiles, resetLimits, visible } from "./helpers";

type Evidence = {
  kind: string;
  flag: boolean;
  file: string | null;
  page: number | null;
  bboxes: { x0: number; top: number; x1: number; bottom: number }[];
  primary?: number;
};
type ReportJson = {
  id: string;
  files: { key: string; pages?: { page: number; width: number; height: number }[] }[];
  report: {
    verdict: string;
    checks: { id: string; status: string; evidence: Evidence[] }[];
    affordability: { rent_pct_of_payslip_net: string | null; rent_pct_of_account_income: string | null };
  };
};

const PHONE = { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true };
const DESKTOP = { viewport: { width: 1280, height: 900 } };
// Feedback is sent on three reports: a "Yes" with an email, a "Not really" without one, and one with.
const FEEDBACK: Partial<Record<Case, { vote: "Yes" | "Not really"; question: string; answer: string; email: boolean }>> = {
  genuine: { vote: "Yes", question: "What helped most?", answer: "Seeing the problem on the page", email: true },
  salary_mismatch: { vote: "Not really", question: "What went wrong?", answer: "It missed something I noticed", email: false },
  multiple_problems: { vote: "Not really", question: "What went wrong?", answer: "It missed something I noticed", email: true },
};

test.beforeEach(() => resetLimits());

/** Upload a fixture, wait for the checks, open the report. Returns the report JSON. */
async function runToReport(page: Page, c: Case): Promise<ReportJson> {
  await fillUpload(page, fixtureFiles(c));
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(/\/check\//);
  await page.getByRole("link", { name: "View report" }).click({ timeout: 90_000 });
  await page.waitForURL(/\/report\/[^/]+$/);
  await page.locator("[data-section=verdict]:visible h1").waitFor();
  const id = page.url().split("/report/")[1];
  return page.evaluate(async (checkId) => (await fetch(`/api/checks/${checkId}`)).json(), id);
}

for (const [i, c] of CASES.entries()) {
  const onPhone = i % 2 === 0; // alternate phone and desktop widths across the fixtures
  test.describe(`${c} (${onPhone ? "phone" : "desktop"})`, () => {
    test.use(onPhone ? PHONE : DESKTOP);

    test("report, every finding highlighted on its page, and feedback", async ({ page }) => {
      const report = await runToReport(page, c);
      const r = report.report;

      // Verdict
      expect(r.verdict).toBe(expectedVerdict(c));
      const badge = { looks_good: "Looks good", needs_a_closer_look: "Needs a closer look", high_risk: "High risk" }[r.verdict];
      await expect(page.locator("[data-section=verdict]:visible p").first()).toHaveText(badge!);

      // not_checked is shown honestly, never hidden
      if (r.checks.some((x) => x.status === "not_checked")) {
        await expect(page.getByRole("heading", { name: "We couldn't check this" })).toBeVisible();
      }
      await expect(visible(page, page.getByText(/This page expires/))).toBeVisible();
      await expect(page.getByRole("link", { name: "Check another applicant" })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(onPhone ? 390 : 1280);

      // Affordability bar fills to the reported percentage once scrolled into view
      const bar = page.locator('[role=img][aria-label^="Rent is"]:visible > div');
      await bar.scrollIntoViewIfNeeded();
      const pct = Number(r.affordability.rent_pct_of_payslip_net ?? r.affordability.rent_pct_of_account_income);
      await expect
        .poll(async () =>
          bar.evaluate((el) => (el.getBoundingClientRect().width / el.parentElement!.getBoundingClientRect().width) * 100),
        )
        .toBeCloseTo(pct, 0);
      await page.evaluate(() => window.scrollTo(0, 0));

      // One evidence link per flagged check
      const flagged = r.checks.filter((x) => x.status === "flag");
      const links = page.getByRole("link", { name: /See (it on the page|the evidence)/ });
      await expect(links).toHaveCount(flagged.length);

      if (flagged.length) {
        await links.first().click();
        await page.waitForURL(/\/evidence\//);
        await page.locator("[data-section=finding]:visible h1").waitFor();
        await page.evaluate(() => ((window as unknown as { __noReload: boolean }).__noReload = true));

        const buttons = page.locator('nav[aria-label="Findings"]:visible button');
        const n = await buttons.count();
        expect(n).toBeGreaterThan(0);
        for (let k = 0; k < n; k++) {
          await buttons.nth(k).click();
          await expect(buttons.nth(k)).toHaveAttribute("aria-current", "true");
          const fid = decodeURIComponent(page.url().split("/evidence/")[1]);
          const checkId = fid.slice(0, fid.lastIndexOf("-"));
          const ev = r.checks.find((x) => x.id === checkId)!.evidence[Number(fid.slice(fid.lastIndexOf("-") + 1))];
          const title = await page.locator("[data-section=finding]:visible h1").innerText();

          if (ev.page && ev.bboxes.length) {
            await test.step(`"${title}": highlight sits exactly on its bounding box`, async () => {
              const fig = page.locator("[data-section=finding]:visible figure").last();
              await fig.locator("[data-highlight=primary]").waitFor();
              const geo = await fig.evaluate((el) => {
                const img = el.querySelector("img")!.getBoundingClientRect();
                const box = el.querySelector("[data-highlight=primary]")!.getBoundingClientRect();
                return { img: { x: img.x, y: img.y, w: img.width, h: img.height }, box: { x: box.x, y: box.y, w: box.width, h: box.height } };
              });
              const size = report.files.find((f) => f.key === ev.file)!.pages!.find((p) => p.page === ev.page)!;
              const b = ev.bboxes[ev.primary ?? 0];
              const sx = geo.img.w / size.width;
              const sy = geo.img.h / size.height;
              const want = { x: geo.img.x + b.x0 * sx, y: geo.img.y + b.top * sy, x1: geo.img.x + b.x1 * sx, y1: geo.img.y + b.bottom * sy };
              // The drawn box is padded by 0.6% of the page width and 0.3% of its height on each side.
              const padX = geo.img.w * 0.006 + 1;
              const padY = geo.img.h * 0.003 + 1;
              expect(geo.box.x).toBeLessThanOrEqual(want.x + 0.5);
              expect(geo.box.x).toBeGreaterThanOrEqual(want.x - padX);
              expect(geo.box.y).toBeLessThanOrEqual(want.y + 0.5);
              expect(geo.box.y).toBeGreaterThanOrEqual(want.y - padY);
              expect(geo.box.x + geo.box.w).toBeGreaterThanOrEqual(want.x1 - 0.5);
              expect(geo.box.x + geo.box.w).toBeLessThanOrEqual(want.x1 + padX);
              expect(geo.box.y + geo.box.h).toBeGreaterThanOrEqual(want.y1 - 0.5);
              expect(geo.box.y + geo.box.h).toBeLessThanOrEqual(want.y1 + padY);
            });
          } else {
            await expect(visible(page, page.getByText(/hidden details|isn't tied to one line/)), `"${title}" explains why nothing is highlighted`).toBeVisible();
          }
        }
        expect(await page.evaluate(() => (window as unknown as { __noReload?: boolean }).__noReload), "findings switch without a page reload").toBe(true);
        await page.goBack();
        expect(page.url()).toMatch(/\/(evidence|report)\//);
      }

      // Feedback on some reports
      const fb = FEEDBACK[c];
      if (fb) {
        await page.goto(`/report/${report.id}`);
        await page.locator("[data-section=verdict]:visible h1").waitFor();
        await page.getByRole("button", { name: fb.vote, exact: true }).click();
        await expect(visible(page, page.getByText(fb.question))).toBeVisible();
        await visible(page, page.getByLabel(fb.answer)).check();
        await visible(page, page.getByLabel("Anything else?")).fill(`E2E test comment for ${c}`);
        if (fb.email) await visible(page, page.getByLabel("Email for launch news")).fill(`e2e-${c}@example.com`);
        await page.getByRole("button", { name: "Send feedback" }).click();
        await expect(page.getByRole("heading", { name: "Thank you" })).toBeVisible();
      }
    });
  });
}

test.describe("expired report", () => {
  test.use(PHONE);

  test("a report older than 24 hours shows the deleted page, and the API answers 410", async ({ page }) => {
    const report = await runToReport(page, "genuine");
    expireCheck(report.id);
    const res = await page.request.get(`/api/checks/${report.id}`);
    expect(res.status()).toBe(410);
    await page.goto(`/report/${report.id}`);
    await expect(page.getByRole("heading", { name: "This report has been deleted" })).toBeVisible();
    await page.goto(`/report/${report.id}/evidence/x-0`);
    await expect(page.getByRole("heading", { name: "This report has been deleted" })).toBeVisible();
  });
});
