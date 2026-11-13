"use client";

import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import {
  type BBox,
  type Evidence,
  type Finding,
  type Report,
  type ReportState,
  checkWording,
  describe,
  fileLabel,
  findingsOf,
  loadReport,
  supportingSignals,
  track,
} from "@/lib/report";
import { usePrefersReducedMotion } from "@/lib/use-prefers-reduced-motion";
import { AlertIcon, Spinner } from "./icons";
import { ReportProblem } from "./report-problem";
import { StatePanel, buttonPrimary } from "./state-panel";

type Tone = "closer" | "risk";

export function EvidenceScreen({ id, findingId }: { id: string; findingId: string }) {
  const [state, setState] = useState<ReportState | null>(null);

  useEffect(() => {
    let cancelled = false;
    loadReport(id).then((s) => {
      if (cancelled) return;
      setState(s);
      if (s.kind === "ready") track(id, "evidence_opened");
    });
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (!state) {
    return (
      <p className="flex items-center gap-2 text-muted" role="status">
        <Spinner /> Loading the evidence…
      </p>
    );
  }
  if (state.kind !== "ready") return <ReportProblem id={id} state={state} />;
  return <EvidenceView report={state.report} initialId={findingId} />;
}

function EvidenceView({ report, initialId }: { report: Report; initialId: string }) {
  const findings = findingsOf(report);
  const pathname = usePathname();
  // The URL is the source of truth, so back/forward and shared links work. Switching uses
  // history.pushState, which Next.js folds into the router without reloading the page.
  const fromUrl = pathname?.split("/evidence/")[1] ?? initialId;
  const selected = findings.find((f) => f.id === decodeURIComponent(fromUrl));
  const tone: Tone = report.report.verdict === "high_risk" ? "risk" : "closer";
  const headingRef = useRef<HTMLHeadingElement>(null);

  function select(f: Finding) {
    if (f.id === selected?.id) return;
    window.history.pushState(null, "", `/report/${report.id}/evidence/${f.id}`);
    requestAnimationFrame(() => headingRef.current?.focus({ preventScroll: true }));
  }

  if (findings.length === 0 || !selected) {
    return (
      <StatePanel
        title={findings.length === 0 ? "Nothing to show here" : "We couldn't find that finding"}
        headingLevel={1}
        actions={
          <Link href={`/report/${report.id}`} className={buttonPrimary}>
            Back to the report
          </Link>
        }
      >
        <p>
          {findings.length === 0
            ? "None of the checks flagged a problem, so there's no evidence to show."
            : "The link may be out of date. Pick a finding from the report instead."}
        </p>
      </StatePanel>
    );
  }

  return (
    <div className="mx-auto max-w-6xl">
      <Link
        href={`/report/${report.id}`}
        className="inline-flex min-h-11 items-center gap-1 font-medium text-accent underline-offset-4 hover:text-accent-hover hover:underline"
      >
        <span aria-hidden="true">←</span> Back to the report
      </Link>

      <div className="mt-4 grid gap-6 lg:grid-cols-[17rem_minmax(0,1fr)] lg:gap-10">
        <FindingsNav findings={findings} selected={selected} onSelect={select} tone={tone} />

        <div className="min-w-0">
          <FindingDetail
            key={selected.id}
            report={report}
            finding={selected}
            tone={tone}
            headingRef={headingRef}
          />
        </div>
      </div>
    </div>
  );
}

function FindingsNav({
  findings,
  selected,
  onSelect,
  tone,
}: {
  findings: Finding[];
  selected: Finding;
  onSelect: (f: Finding) => void;
  tone: Tone;
}) {
  const active = tone === "risk" ? "border-risk bg-risk-soft" : "border-closer bg-closer-soft";
  const listRef = useRef<HTMLUListElement>(null);
  useEffect(() => {
    // On phones the list scrolls sideways: keep the chosen finding in view.
    const el = listRef.current?.querySelector<HTMLElement>('[aria-current="true"]');
    el?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [selected.id]);
  return (
    <nav aria-label="Findings" className="min-w-0">
      <h2 className="font-sans text-sm font-semibold uppercase tracking-wide text-muted">
        {findings.length} finding{findings.length === 1 ? "" : "s"}
      </h2>
      {/* A swipeable row on phones, a list beside the page on wide screens. */}
      <ul ref={listRef} className="-mx-4 mt-2 flex snap-x scroll-px-4 gap-2 overflow-x-auto px-4 pb-2 lg:mx-0 lg:flex-col lg:overflow-visible lg:px-0">
        {findings.map((f) => {
          const current = f.id === selected.id;
          return (
            <li key={f.id} className="w-60 shrink-0 snap-start lg:w-auto">
              <button
                type="button"
                onClick={() => onSelect(f)}
                aria-current={current ? "true" : undefined}
                className={`flex h-full w-full flex-col items-start rounded-lg border px-3 py-2.5 text-left transition-colors duration-150 ${
                  current ? active : "border-line bg-surface hover:border-accent"
                }`}
              >
                <span className="text-xs text-muted">
                  Check {f.check.number} · {checkWording(f.check).title}
                </span>
                <span className="mt-0.5 font-medium text-ink">{f.title}</span>
                <span className="mt-0.5 truncate text-xs text-muted max-w-full">
                  {fileLabel(f.file)}
                  {f.evidence.page ? `, page ${f.evidence.page}` : ""}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

function FindingDetail({
  report,
  finding,
  tone,
  headingRef,
}: {
  report: Report;
  finding: Finding;
  tone: Tone;
  headingRef: React.RefObject<HTMLHeadingElement | null>;
}) {
  const e = finding.evidence;
  const info = describe(e);
  const signals = supportingSignals(report, finding);
  const toneText = tone === "risk" ? "text-risk" : "text-closer";
  const page = e.page ?? 1;
  const size = finding.file?.pages?.find((p) => p.page === page) ?? null;
  const headingId = useId();
  const signalsId = useId();

  return (
    <motion.article
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.2 }}
      aria-labelledby={headingId}
      data-section="finding"
    >
      <p className="text-sm text-muted">
        Check {finding.check.number}: {checkWording(finding.check).title}
      </p>
      <h1
        id={headingId}
        ref={headingRef}
        tabIndex={-1}
        className="mt-1 text-2xl font-semibold leading-tight tracking-tight text-ink sm:text-3xl"
      >
        {info.title}
      </h1>
      <p className="mt-1 text-muted">
        {fileLabel(finding.file)}
        {e.page ? `, page ${e.page}` : ""}
      </p>

      {info.comparison ? (
        <dl className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Figure label={info.comparison.expectedLabel} value={info.comparison.expected} />
          <Figure label={info.comparison.shownLabel} value={info.comparison.shown} strong />
          {info.comparison.difference ? (
            <Figure label="Difference" value={info.comparison.difference} className={toneText} />
          ) : null}
        </dl>
      ) : null}

      {info.facts.length > 0 ? (
        <dl className="mt-4 divide-y divide-line rounded-xl border border-line bg-surface text-sm">
          {info.facts.map((f) => (
            <div key={f.label} className="flex justify-between gap-4 px-4 py-2.5">
              <dt className="text-muted">{f.label}</dt>
              <dd className="text-right font-medium text-ink tabular-nums">{f.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {signals.length > 0 ? (
        <section className="mt-4 rounded-xl border border-line bg-surface p-4" aria-labelledby={signalsId}>
          <h2 id={signalsId} className="font-sans font-semibold text-ink">
            Supporting signals
          </h2>
          <ul className="mt-2 space-y-3 text-sm">
            {signals.map((s, i) => (
              <Signal key={i} evidence={s} />
            ))}
          </ul>
        </section>
      ) : null}

      {e.page && e.bboxes.length > 0 && size && finding.file ? (
        <PageWithHighlight
          reportId={report.id}
          fileKey={finding.file.key}
          page={e.page}
          width={size.width}
          height={size.height}
          boxes={e.bboxes}
          primary={e.primary ?? 0}
          approximate={e.approximate}
          tone={tone}
        />
      ) : (
        <div className="mt-6">
          <p className="rounded-xl border border-line bg-surface p-4 text-ink">
            {e.kind === "modified_after_period"
              ? "This finding is about the file's hidden details (when it was created and changed, and by which program), not a line on the page. Here is the first page for reference."
              : "This finding isn't tied to one line, so there's nothing to highlight. Here is the page for reference."}
          </p>
          {finding.file && size ? (
            <PageWithHighlight
              reportId={report.id}
              fileKey={finding.file.key}
              page={page}
              width={size.width}
              height={size.height}
              boxes={[]}
              primary={0}
              approximate={false}
              tone={tone}
            />
          ) : null}
        </div>
      )}
    </motion.article>
  );
}

function Figure({ label, value, strong, className = "" }: { label: string; value: string; strong?: boolean; className?: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface px-4 py-3">
      <dt className="text-sm text-muted">{label}</dt>
      <dd className={`mt-0.5 text-xl tabular-nums ${strong ? "font-semibold text-ink" : "text-ink"} ${className}`}>{value}</dd>
    </div>
  );
}

function Signal({ evidence }: { evidence: Evidence }) {
  const info = describe(evidence);
  return (
    <li className="flex gap-2">
      <AlertIcon className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
      <div>
        <p className="font-medium text-ink">{info.title}</p>
        <p className="text-muted">{info.facts.map((f) => `${f.label}: ${f.value}`).join(" · ")}</p>
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// The page image and its highlight
// ---------------------------------------------------------------------------

/** Box as percentages of the page, padded a little so the line isn't clipped. */
function toPercent(b: BBox, width: number, height: number) {
  const padX = width * 0.006;
  const padY = height * 0.003;
  const x0 = Math.max(0, b.x0 - padX);
  const x1 = Math.min(width, b.x1 + padX);
  const top = Math.max(0, b.top - padY);
  const bottom = Math.min(height, b.bottom + padY);
  return {
    left: `${(x0 / width) * 100}%`,
    top: `${(top / height) * 100}%`,
    width: `${((x1 - x0) / width) * 100}%`,
    height: `${((bottom - top) / height) * 100}%`,
  };
}

function PageWithHighlight({
  reportId,
  fileKey,
  page,
  width,
  height,
  boxes,
  primary,
  approximate,
  tone,
}: {
  reportId: string;
  fileKey: string;
  page: number;
  width: number;
  height: number;
  boxes: BBox[];
  primary: number;
  approximate: boolean;
  tone: Tone;
}) {
  const src = `/api/checks/${reportId}/pages/${fileKey}/${page}`;
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const key = boxes[primary];

  // Close-up window around the flagged line: the full text width, a few lines above and below.
  const crop = key
    ? (() => {
        const xs = boxes.flatMap((b) => [b.x0, b.x1]);
        const left = Math.max(0, Math.min(...xs) - width * 0.03);
        const right = Math.min(width, Math.max(...xs) + width * 0.03);
        const lineH = Math.max(8, key.bottom - key.top);
        const top = Math.max(0, Math.min(...boxes.map((b) => b.top)) - lineH * 2.5);
        const bottom = Math.min(height, Math.max(...boxes.map((b) => b.bottom)) + lineH * 2.5);
        return { left, top, w: right - left, h: bottom - top };
      })()
    : null;

  const overlays = (scaleTo: { left: number; top: number; w: number; h: number }) =>
    boxes.map((b, i) => {
      const shifted = { x0: b.x0 - scaleTo.left, x1: b.x1 - scaleTo.left, top: b.top - scaleTo.top, bottom: b.bottom - scaleTo.top };
      return (
        <Highlight
          key={i}
          style={toPercent(shifted, scaleTo.w, scaleTo.h)}
          primary={i === primary}
          approximate={approximate}
          tone={tone}
        />
      );
    });

  return (
    <div className="mt-6 space-y-4">
      {crop && status !== "error" ? (
        <CloseUp
          src={src}
          pageWidth={width}
          crop={crop}
          focusRight={key ? key.x1 : crop.left + crop.w}
          ready={status === "ready"}
        >
          {status === "ready" ? overlays(crop) : null}
        </CloseUp>
      ) : null}

      <figure>
        <figcaption className="mb-2 flex flex-wrap items-baseline justify-between gap-2 text-sm">
          <span className="font-medium text-ink">Page {page}</span>
          {key ? (
            <span className="text-muted">
              {approximate
                ? "Highlighted area is our best estimate from a photo or scan."
                : boxes.length > 1
                  ? "The solid box is the line in question. The dashed box is the line before it."
                  : "The highlighted line is the one in question."}
            </span>
          ) : null}
        </figcaption>
        <div
          className="relative w-full overflow-hidden rounded-lg border border-line bg-surface shadow-sm"
          style={{ aspectRatio: `${width} / ${height}` }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={src}
            alt={`Page ${page} of the document${key ? ", with the flagged line highlighted" : ""}`}
            onLoad={() => setStatus("ready")}
            onError={() => setStatus("error")}
            className="absolute inset-0 h-full w-full select-none"
          />
          {status === "loading" ? (
            <div className="absolute inset-0 flex items-center justify-center text-muted">
              <Spinner className="h-6 w-6" />
            </div>
          ) : null}
          {status === "error" ? (
            <div className="absolute inset-0 flex items-center justify-center p-6 text-center text-ink">
              We couldn&apos;t load this page image. It may have been deleted with the report.
            </div>
          ) : null}
          {status === "ready" ? overlays({ left: 0, top: 0, w: width, h: height }) : null}
        </div>
      </figure>
    </div>
  );
}

// Close-up text is drawn at about 1.5 CSS pixels per PDF point (8.5pt type becomes ~13px), the
// size of normal reading text. On narrow screens the strip scrolls sideways.
const CLOSE_UP_SCALE = 1.5;

function CloseUp({
  src,
  pageWidth,
  crop,
  focusRight,
  ready,
  children,
}: {
  src: string;
  pageWidth: number;
  crop: { left: number; top: number; w: number; h: number };
  focusRight: number;
  ready: boolean;
  children: React.ReactNode;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const [scrollable, setScrollable] = useState(false);

  useEffect(() => {
    const el = scroller.current;
    if (!el || !ready) return;
    const overflow = el.scrollWidth > el.clientWidth + 1;
    setScrollable(overflow);
    if (overflow) {
      // Amounts sit at the right of a line, so start with its right end in view.
      const px = ((focusRight - crop.left) / crop.w) * el.scrollWidth;
      el.scrollLeft = Math.max(0, px - el.clientWidth + 16);
    }
  }, [ready, focusRight, crop.left, crop.w]);

  return (
    <figure>
      <figcaption className="mb-2 flex flex-wrap items-baseline justify-between gap-2 text-sm">
        <span className="font-medium text-ink">Close-up</span>
        {scrollable ? <span className="text-muted">Swipe or scroll sideways to see the whole line.</span> : null}
      </figcaption>
      <div
        ref={scroller}
        className="overflow-x-auto rounded-lg border border-line bg-surface"
        tabIndex={scrollable ? 0 : -1}
        role={scrollable ? "region" : undefined}
        aria-label={scrollable ? "Close-up of the flagged line, scrollable" : undefined}
      >
        <div
          className="relative overflow-hidden"
          style={{ width: `max(100%, ${Math.round(crop.w * CLOSE_UP_SCALE)}px)`, aspectRatio: `${crop.w} / ${crop.h}` }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={src}
            alt=""
            aria-hidden="true"
            className="absolute max-w-none select-none"
            style={{
              width: `${(pageWidth / crop.w) * 100}%`,
              left: `${(-crop.left / crop.w) * 100}%`,
              top: `${(-crop.top / crop.h) * 100}%`,
            }}
          />
          {children}
        </div>
      </div>
    </figure>
  );
}

function Highlight({
  style,
  primary,
  approximate,
  tone,
}: {
  style: React.CSSProperties;
  primary: boolean;
  approximate: boolean;
  tone: Tone;
}) {
  const reduce = usePrefersReducedMotion();
  const colours =
    tone === "risk" ? "border-risk bg-risk/15" : "border-closer bg-closer/15";
  if (!primary) {
    return (
      <div
        aria-hidden="true"
        className="pointer-events-none absolute rounded-sm border border-dashed border-muted/70 bg-muted/5"
        style={style}
      />
    );
  }
  return (
    <div aria-hidden="true" className="pointer-events-none absolute" style={style} data-highlight="primary">
      <div className={`absolute inset-0 rounded-sm border-2 mix-blend-multiply ${approximate ? "border-dashed" : ""} ${colours}`} />
      <AnimatePresence>
        {!reduce ? (
          // A gentle pulse when it appears: an outline that grows and fades, twice.
          <motion.div
            className={`absolute -inset-1 rounded-md border-2 ${tone === "risk" ? "border-risk" : "border-closer"}`}
            initial={{ opacity: 0.7, scale: 1 }}
            animate={{ opacity: 0, scale: 1.04 }}
            transition={{ duration: 0.5, ease: "easeOut", repeat: 1, repeatDelay: 0.1 }}
          />
        ) : null}
      </AnimatePresence>
    </div>
  );
}
