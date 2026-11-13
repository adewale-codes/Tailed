"use client";

import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiError, CheckStatus, Progress } from "@/lib/types";
import { UNREADABLE_CODES } from "@/lib/types";
import { recallUpload } from "@/lib/upload-store";
import { usePrefersReducedMotion } from "@/lib/use-prefers-reduced-motion";
import { AlertIcon, DashIcon, Spinner, TickIcon } from "./icons";
import { StatePanel, buttonPrimary, buttonSecondary } from "./state-panel";

const POLL_MS = 1000;
const MAX_FAILED_POLLS = 4;

type Problem =
  | { kind: "not_found" }
  | { kind: "expired"; message: string }
  | { kind: "server"; message: string };

const RESULT: Record<CheckStatus, { text: string; badge: string; Icon: typeof TickIcon }> = {
  pass: { text: "Looks fine", badge: "bg-good-soft text-good", Icon: TickIcon },
  flag: { text: "Worth a closer look", badge: "bg-closer-soft text-closer", Icon: AlertIcon },
  not_checked: { text: "Couldn't check", badge: "bg-canvas text-muted", Icon: DashIcon },
};

/** 0-100. Reading is the first 30%, the six checks the next 60%, writing the report the rest. */
export function percentDone(p: Progress | null): number {
  if (!p) return 0;
  if (p.status === "done") return 100;
  const docs = p.documents.total ? p.documents.read / p.documents.total : 0;
  const checks = p.checks.filter((c) => c.done).length / p.checks.length;
  const writing = p.status === "writing" ? 0.05 : 0;
  return Math.round((docs * 0.3 + checks * 0.6 + writing) * 100);
}

function stageText(p: Progress | null): string {
  if (!p || p.status === "queued") return "Getting started";
  if (p.status === "reading") return `Reading documents (${p.documents.read} of ${p.documents.total})`;
  if (p.status === "checking") {
    return `Running checks (${p.checks.filter((c) => c.done).length} of ${p.checks.length})`;
  }
  if (p.status === "writing") return "Writing the report";
  if (p.status === "done") return "All checks done";
  return "Stopped";
}

export function CheckingScreen({ id }: { id: string }) {
  const [progress, setProgress] = useState<Progress | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [upload] = useState(() => recallUpload(id));
  const reportLinkRef = useRef<HTMLAnchorElement>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;

    async function poll() {
      try {
        const res = await fetch(`/api/checks/${id}/progress`, { cache: "no-store" });
        const data = await res.json().catch(() => null);
        if (cancelled) return;
        if (res.ok && data) {
          failures = 0;
          setProgress(data as Progress);
          if (data.status === "done" || data.status === "failed") return; // stop polling
        } else if (res.status === 404) {
          setProblem({ kind: "not_found" });
          return;
        } else if (res.status === 410) {
          setProblem({ kind: "expired", message: data?.error?.message ?? "" });
          return;
        } else if (++failures >= MAX_FAILED_POLLS) {
          setProblem({ kind: "server", message: data?.error?.message ?? "" });
          return;
        }
      } catch {
        if (cancelled) return;
        if (++failures >= MAX_FAILED_POLLS) {
          setProblem({ kind: "server", message: "" });
          return;
        }
      }
      timer = setTimeout(poll, POLL_MS);
    }

    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [id, attempt]);

  const done = progress?.status === "done";
  useEffect(() => {
    if (done) reportLinkRef.current?.focus();
  }, [done]);

  const retry = useCallback(() => {
    setProblem(null);
    setAttempt((n) => n + 1);
  }, []);

  if (problem) return <ProblemState problem={problem} onRetry={retry} />;
  if (progress?.status === "failed") return <FailedState error={progress.error} names={upload?.names} />;

  const percent = percentDone(progress);
  const unreadable = (progress?.files ?? []).filter((f) => f.status === "unreadable");
  const running = !done;
  const finished = (progress?.checks ?? []).filter((c) => c.status);
  const latest = finished.at(-1);
  const announcement = done
    ? "All six checks are done. Your report is ready."
    : latest?.status
      ? `${finished.length} of 6 checks done. ${latest.label}: ${RESULT[latest.status].text}.`
      : "";

  return (
    <div>
      <h1 className="text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
        {done ? "The checks are done" : "Checking the documents"}
      </h1>
      <p className="mt-2 text-lg text-muted">
        {done
          ? "Your report is ready."
          : "This usually takes under a minute. You can keep this page open while we work."}
      </p>
      <p className="sr-only" aria-live="polite">
        {announcement}
      </p>

      <div className="mt-6 grid gap-6 sm:mt-8 sm:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <DocumentPreview id={id} progress={progress} localUrl={upload?.previewUrl ?? null} scanning={running} />

        <div className="min-w-0">
          <ProgressBar percent={percent} label={stageText(progress)} />

          <ol className="mt-5 divide-y divide-line rounded-xl border border-line bg-surface" aria-label="The six checks">
            {(progress?.checks ?? placeholderChecks()).map((check) => (
              <CheckRow
                key={check.id}
                number={check.number}
                label={check.label}
                status={check.status}
                active={progress?.status === "checking" && !check.done}
              />
            ))}
          </ol>

          {unreadable.length > 0 ? (
            <div className="mt-5 rounded-xl border border-closer/30 bg-closer-soft p-4 text-ink" role="status">
              <p className="font-medium">
                We couldn&apos;t read {unreadable.length === 1 ? "one file" : `${unreadable.length} files`}, so
                we&apos;re checking the rest.
              </p>
              <ul className="mt-2 space-y-1 text-sm">
                {unreadable.map((f) => (
                  <li key={f.key}>
                    <span className="font-medium">{fileName(upload?.names, f.key)}:</span>{" "}
                    {f.status === "unreadable" ? f.message : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <AnimatePresence>
            {done ? (
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3 }}
                className="mt-6"
              >
                <Link ref={reportLinkRef} href={`/report/${id}`} className={`${buttonPrimary} w-full sm:w-auto`}>
                  View report
                </Link>
              </motion.div>
            ) : null}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

function fileName(names: string[] | undefined, key: string): string {
  return names?.[Number(key) - 1] ?? `File ${key}`;
}

function placeholderChecks(): Progress["checks"] {
  return [
    "Balances add up",
    "Payslips match the bank statements",
    "Payslip tax looks realistic",
    "Employer is a real company",
    "Spending looks like everyday life",
    "Names match",
  ].map((label, i) => ({ id: `pending-${i}`, number: i + 1, label, done: false, status: null }));
}

function ProgressBar({ percent, label }: { percent: number; label: string }) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="font-medium text-ink">{label}</span>
        <span className="tabular-nums text-muted">{percent}%</span>
      </div>
      <div
        role="progressbar"
        aria-label="Check progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={`${percent}%, ${label}`}
        className="mt-2 h-2 overflow-hidden rounded-full bg-line"
      >
        <motion.div
          className="h-full origin-left rounded-full bg-accent"
          initial={false}
          animate={{ scaleX: percent / 100 }}
          transition={{ duration: 0.4, ease: "easeOut" }}
        />
      </div>
    </div>
  );
}

function CheckRow({
  number,
  label,
  status,
  active,
}: {
  number: number;
  label: string;
  status: CheckStatus | null;
  active: boolean;
}) {
  const result = status ? RESULT[status] : null;
  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <span className="relative flex h-7 w-7 shrink-0 items-center justify-center">
        <AnimatePresence mode="wait" initial={false}>
          {result ? (
            <motion.span
              key="result"
              initial={{ opacity: 0, scale: 0.6 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.25, ease: "easeOut" }}
              className={`flex h-7 w-7 items-center justify-center rounded-full ${result.badge}`}
            >
              <result.Icon />
            </motion.span>
          ) : (
            <motion.span
              key="waiting"
              exit={{ opacity: 0, scale: 0.6 }}
              transition={{ duration: 0.15 }}
              className={`flex h-7 w-7 items-center justify-center rounded-full ${active ? "text-accent" : "text-line"}`}
            >
              {active ? <Spinner className="h-5 w-5" /> : <span className="h-2.5 w-2.5 rounded-full bg-line" />}
            </motion.span>
          )}
        </AnimatePresence>
      </span>
      <span className="min-w-0 flex-1">
        <span className="sr-only">Check {number}: </span>
        <span className="text-ink">{label}</span>
      </span>
      <span className={`shrink-0 text-right text-sm ${result ? "text-ink" : "text-muted"}`}>
        {result ? result.text : active ? "Checking…" : "Waiting"}
      </span>
    </li>
  );
}

function DocumentPreview({
  id,
  progress,
  localUrl,
  scanning,
}: {
  id: string;
  progress: Progress | null;
  localUrl: string | null;
  scanning: boolean;
}) {
  const reduce = usePrefersReducedMotion();
  const preview = progress?.preview;
  const src = preview ? `/api/checks/${id}/pages/${preview.file}/${preview.page}` : localUrl;
  const [loaded, setLoaded] = useState<string | null>(null);

  return (
    <figure className="relative mx-auto aspect-[1/1.414] w-full max-w-36 overflow-hidden rounded-lg border border-line bg-surface shadow-sm sm:max-w-none">
      <SkeletonPage />
      {src ? (
        // A rendered page from our own API (or the user's own photo); next/image adds nothing here.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt=""
          onLoad={() => setLoaded(src)}
          className={`absolute inset-0 h-full w-full object-cover object-top transition-opacity duration-300 ${
            loaded === src ? "opacity-100" : "opacity-0"
          }`}
        />
      ) : null}
      {scanning && !reduce ? (
        <motion.div
          aria-hidden="true"
          className="absolute inset-x-0 h-12 border-b-2 border-accent bg-accent/10"
          initial={{ top: "-3rem" }}
          animate={{ top: "100%" }}
          transition={{ duration: 2.4, ease: "easeInOut", repeat: Infinity, repeatDelay: 0.3 }}
        />
      ) : null}
      <figcaption className="sr-only">Preview of the first document</figcaption>
    </figure>
  );
}

/** A plain drawing of a statement, shown until the real first page is available. */
function SkeletonPage() {
  const widths = ["w-1/2", "w-5/6", "w-4/6", "w-5/6", "w-3/6", "w-4/6", "w-5/6", "w-2/6", "w-4/6", "w-5/6"];
  return (
    <div aria-hidden="true" className="absolute inset-0 p-[8%]">
      <div className="h-3 w-1/3 rounded-sm bg-ink/15" />
      <div className="mt-2 h-2 w-1/4 rounded-sm bg-line" />
      <div className="mt-[12%] space-y-[6%]">
        {widths.map((w, i) => (
          <div key={i} className="flex items-center justify-between gap-2">
            <div className={`h-1.5 rounded-sm bg-line ${w}`} />
            <div className="h-1.5 w-1/6 rounded-sm bg-line" />
          </div>
        ))}
      </div>
    </div>
  );
}

function FailedState({ error, names }: { error?: ApiError; names?: string[] }) {
  const unreadable = error && UNREADABLE_CODES.has(error.code);
  if (unreadable) {
    return (
      <StatePanel
        tone="closer"
        title="We couldn't read these documents"
        headingLevel={1}
        actions={
          <Link href="/" className={buttonPrimary}>
            Try different files
          </Link>
        }
      >
        <p>{error.message}</p>
        <p className="text-muted">
          {names && names.length > 0 ? `Files: ${names.join(", ")}. ` : ""}
          PDFs downloaded straight from online banking or the payroll portal work best. The files
          have already been deleted.
        </p>
      </StatePanel>
    );
  }
  return (
    <StatePanel
      tone="risk"
      title="Something went wrong on our side"
      headingLevel={1}
      actions={
        <Link href="/" className={buttonPrimary}>
          Start a new check
        </Link>
      }
    >
      <p>{error?.message ?? "We couldn't finish checking these documents."}</p>
      <p className="text-muted">Your documents have already been deleted. Please try again.</p>
    </StatePanel>
  );
}

function ProblemState({ problem, onRetry }: { problem: Problem; onRetry: () => void }) {
  if (problem.kind === "not_found") {
    return (
      <StatePanel
        title="We couldn't find this check"
        headingLevel={1}
        actions={
          <Link href="/" className={buttonPrimary}>
            Start a new check
          </Link>
        }
      >
        <p>The link may be mistyped. Check links are long, so make sure you copied all of it.</p>
      </StatePanel>
    );
  }
  if (problem.kind === "expired") {
    return (
      <StatePanel
        title="This check has been deleted"
        headingLevel={1}
        actions={
          <Link href="/" className={buttonPrimary}>
            Start a new check
          </Link>
        }
      >
        <p>
          {problem.message ||
            "We delete every report and its documents 24 hours after the check, to protect the tenant's privacy."}
        </p>
      </StatePanel>
    );
  }
  return (
    <StatePanel
      tone="risk"
      title="We've lost touch with the checking service"
      headingLevel={1}
      actions={
        <>
          <button type="button" onClick={onRetry} className={buttonPrimary}>
            Try again
          </button>
          <Link href="/" className={buttonSecondary}>
            Start a new check
          </Link>
        </>
      }
    >
      <p>
        {problem.message ||
          "This is a problem on our side. Your check may still be running, so try again in a moment."}
      </p>
    </StatePanel>
  );
}
