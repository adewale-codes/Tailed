import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import type { Page } from "@playwright/test";

export const REPO = path.resolve(__dirname, "..", "..");
const API_DIR = path.join(REPO, "api");

export const CASES = [
  "genuine",
  "edited_balance",
  "salary_mismatch",
  "bad_payslip",
  "name_mismatch",
  "edited_metadata",
  "multiple_problems",
] as const;
export type Case = (typeof CASES)[number];

/** The synthetic fixture PDFs for one case (never real documents). */
export function fixtureFiles(c: Case): string[] {
  const dir = path.join(REPO, "fixtures", c);
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".pdf"))
    .sort()
    .map((f) => path.join(dir, f));
}

export function expectedVerdict(c: Case): string {
  const file = path.join(REPO, "fixtures", c, "expected.json");
  return (JSON.parse(fs.readFileSync(file, "utf8")) as { expected_verdict: string }).expected_verdict;
}

/** Runs api/scripts/e2e_db.py (local databases only). */
function db(...args: string[]): string {
  return execFileSync("uv", ["run", "python", "scripts/e2e_db.py", ...args], { cwd: API_DIR, encoding: "utf8" });
}

/** Clear today's per-IP check counts, so each test starts with its 3 free checks. */
export function resetLimits(): void {
  db("reset-limits");
}

/** Age a check past its 24 hours, so the API answers 410. */
export function expireCheck(id: string): void {
  db("expire", id);
}

export function checkDatabase(): void {
  db("check");
}

/** Fill the upload form with files, a rent and consent (the submit button is then enabled). */
export async function fillUpload(page: Page, files: string[], rent = "1100"): Promise<void> {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles(files);
  await visible(page, page.getByLabel("Monthly rent")).fill(rent);
  await visible(page, page.getByLabel(/tenant has agreed/)).check();
}

/**
 * The app keeps previous pages mounted but hidden for fast back navigation, so the same label or
 * heading can exist twice. This narrows a locator to the visible copy.
 */
export function visible<T extends ReturnType<Page["locator"]>>(page: Page, locator: T): T {
  return locator.and(page.locator(":visible")) as T;
}

// ---------------------------------------------------------------------------
// Reduced-motion animation recorder
// ---------------------------------------------------------------------------

export type MotionEvent = { t: number; kind: string; el: string; what: string; path: string };

/**
 * Injected before any page script. Records every CSS animation and transition start, every Web
 * Animations API call (framer-motion uses these), and any element whose inline style changes 3+
 * times within 250 ms (frame-by-frame JavaScript animation). Each event names the element.
 * Read the events back with `takeMotionEvents`.
 */
export function motionRecorder(): void {
  const name = (el: Element | EventTarget | null): string => {
    if (!el || !(el instanceof Element)) return String(el);
    const bits = [el.tagName.toLowerCase()];
    if (el.id) bits.push("#" + el.id);
    for (const a of ["aria-label", "role", "data-highlight", "data-remove-file", "data-section"]) {
      const v = el.getAttribute(a);
      if (v !== null) bits.push(`[${a}="${v.slice(0, 30)}"]`);
    }
    const cls = typeof el.className === "string" ? el.className.split(/\s+/).filter(Boolean).slice(0, 4) : [];
    if (cls.length) bits.push("." + cls.join("."));
    const text = (el.textContent || "").trim().slice(0, 30);
    return bits.join("") + (text ? ` "${text}"` : "");
  };
  const log: MotionEvent[] = [];
  (window as unknown as { __motionLog: MotionEvent[] }).__motionLog = log;
  const record = (kind: string, el: Element | EventTarget | null, what: string) =>
    log.push({ t: Math.round(performance.now()), kind, el: name(el), what, path: location.pathname });

  document.addEventListener("animationstart", (e) => record("css-animation", e.target, e.animationName), true);
  document.addEventListener("transitionstart", (e) => record("css-transition", e.target, e.propertyName), true);

  const realAnimate = Element.prototype.animate;
  Element.prototype.animate = function (
    this: Element,
    keyframes: Keyframe[] | PropertyIndexedKeyframes | null,
    options?: number | KeyframeAnimationOptions,
  ) {
    const props = Array.isArray(keyframes) ? Object.keys(keyframes[0] ?? {}) : Object.keys(keyframes ?? {});
    const duration = typeof options === "number" ? options : options?.duration;
    if (duration === undefined || Number(duration) > 0) record("web-animation", this, `${props.join(",")} ${String(duration)}ms`);
    return realAnimate.call(this, keyframes, options);
  };

  const styleChanges = new WeakMap<Node, number[]>();
  new MutationObserver((mutations) => {
    const now = performance.now();
    for (const m of mutations) {
      const times = (styleChanges.get(m.target) ?? []).filter((t) => now - t < 250);
      times.push(now);
      styleChanges.set(m.target, times);
      if (times.length === 3) {
        record("js-style-tween", m.target, ((m.target as Element).getAttribute("style") ?? "").slice(0, 60));
      }
    }
  }).observe(document, { attributes: true, attributeFilter: ["style"], subtree: true });
}

/** Returns (and clears) the events recorded on the current page so far. */
export async function takeMotionEvents(page: Page): Promise<MotionEvent[]> {
  return page
    .evaluate(() => (window as unknown as { __motionLog?: MotionEvent[] }).__motionLog?.splice(0) ?? [])
    .catch(() => []);
}
