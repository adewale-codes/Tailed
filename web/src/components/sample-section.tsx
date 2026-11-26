"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { type Sample, SAMPLES, sampleFiles, sampleUrl } from "@/lib/samples";
import type { ApiError } from "@/lib/types";
import { uploadAndStart } from "@/lib/upload";
import { rememberUpload } from "@/lib/upload-store";
import { DocumentIcon, Spinner } from "./icons";
import { buttonPrimary } from "./state-panel";

const OUTCOME_BADGE: Record<Sample["outcome"]["tone"], string> = {
  good: "bg-good-soft text-good border-good/25",
  closer: "bg-closer-soft text-closer border-closer/25",
  risk: "bg-risk-soft text-risk border-risk/25",
};

/**
 * "No documents to hand? Try a sample": runs a real check, through the normal pipeline, on
 * made-up documents. Samples have their own daily limit and need no tenant consent.
 */
export function SampleSection() {
  const router = useRouter();
  const headingId = useId();
  const statusId = useId();
  const [running, setRunning] = useState<Sample["id"] | null>(null);
  const [pct, setPct] = useState(0);
  const [error, setError] = useState<ApiError | null>(null);

  async function run(sample: Sample) {
    if (running) return;
    setRunning(sample.id);
    setPct(0);
    setError(null);
    try {
      const files = await sampleFiles(sample);
      const outcome = await uploadAndStart(files, String(sample.rent), setPct, { sample: sample.id });
      if (outcome.ok) {
        rememberUpload(outcome.id, files);
        router.push(`/check/${outcome.id}`);
        return;
      }
      setError(outcome.error);
    } catch {
      setError({ code: "network", message: "We couldn't reach Tallied. Check your connection and try again." });
    }
    setRunning(null);
  }

  return (
    <section aria-labelledby={headingId} data-section="samples" className="mt-12">
      <h2 id={headingId} className="text-2xl font-semibold text-ink">
        No documents to hand? Try a sample
      </h2>
      <p className="mt-2 max-w-2xl text-muted">
        Run a real check on made-up documents and see the live checks, the report and the evidence. The tenant, their
        employer and Northgate Bank are all fictional, so there&apos;s no one&apos;s permission to ask. Samples
        don&apos;t count towards your 3 free checks a day.
      </p>

      {error ? (
        <div role="alert" className="mt-4 rounded-xl border border-risk/30 bg-risk-soft p-4 text-ink">
          {error.message}
        </div>
      ) : null}

      <ul className="mt-5 space-y-4">
        {SAMPLES.map((sample) => {
          const titleId = `${headingId}-${sample.id}`;
          const busy = running === sample.id;
          const label = busy ? `Running the sample… ${pct}%` : "Run this sample";
          return (
            <li
              key={sample.id}
              data-sample={sample.id}
              aria-labelledby={titleId}
              className="rounded-2xl border border-line bg-surface p-5 sm:p-6"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
                <h3 id={titleId} className="text-lg font-semibold text-ink">
                  {sample.title}
                </h3>
                <p className="text-sm text-muted">
                  Result:{" "}
                  <span
                    className={`inline-flex rounded-full border px-2.5 py-0.5 text-sm font-medium ${OUTCOME_BADGE[sample.outcome.tone]}`}
                  >
                    {sample.outcome.label}
                  </span>
                </p>
              </div>
              <p className="mt-2 text-ink">{sample.summary}</p>

              <p className="mt-4 text-sm font-medium text-ink">
                {sample.docs.length} documents, monthly rent £{sample.rent.toLocaleString("en-GB")}
              </p>
              <ul className="mt-1 grid grid-cols-1 gap-x-6 sm:grid-cols-2" aria-label={`Documents in “${sample.title}”`}>
                {sample.docs.map((doc) => (
                  <li key={doc.file}>
                    <a
                      href={sampleUrl(sample, doc)}
                      target="_blank"
                      rel="noopener"
                      className="inline-flex min-h-11 items-center gap-2 text-accent underline underline-offset-4 hover:text-accent-hover"
                    >
                      <DocumentIcon className="h-4 w-4 shrink-0" />
                      {doc.label}
                      <span className="sr-only"> (PDF, opens in a new tab)</span>
                    </a>
                  </li>
                ))}
              </ul>

              <button
                type="button"
                onClick={() => run(sample)}
                disabled={running !== null}
                // Starts with the visible text, so voice control users can say what they see.
                aria-label={`${label}: ${sample.title}`}
                aria-describedby={statusId}
                className={`${buttonPrimary} mt-4 w-full gap-2 sm:w-auto`}
              >
                {busy ? <Spinner /> : null}
                {label}
              </button>
            </li>
          );
        })}
      </ul>
      <p id={statusId} className="sr-only" aria-live="polite">
        {running ? "Uploading the sample documents." : ""}
      </p>
    </section>
  );
}
