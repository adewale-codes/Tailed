"use client";

import Link from "next/link";
import type { ReportState } from "@/lib/report";
import { StatePanel, buttonPrimary, buttonSecondary } from "./state-panel";

/** Every non-ready report state: still running, failed, deleted (410), unknown or unreachable. */
export function ReportProblem({ id, state }: { id: string; state: Exclude<ReportState, { kind: "ready" }> }) {
  const newCheck = (
    <Link href="/" className={buttonPrimary}>
      Check another applicant
    </Link>
  );
  switch (state.kind) {
    case "expired":
      return (
        <StatePanel title="This report has been deleted" headingLevel={1} actions={newCheck}>
          <p>
            We delete every report 24 hours after the check, to protect the tenant&apos;s privacy. The documents, the
            page images and the report are all gone.
          </p>
          <p className="text-muted">To see the results again, run a new check with the same documents.</p>
        </StatePanel>
      );
    case "processing":
      return (
        <StatePanel
          title="This report isn't ready yet"
          headingLevel={1}
          actions={
            <Link href={`/check/${id}`} className={buttonPrimary}>
              Watch the checks
            </Link>
          }
        >
          <p>We&apos;re still checking the documents. It usually takes under a minute.</p>
        </StatePanel>
      );
    case "failed":
      return (
        <StatePanel tone="closer" title="We couldn't check these documents" headingLevel={1} actions={newCheck}>
          <p>{state.error.message}</p>
        </StatePanel>
      );
    case "not_found":
      return (
        <StatePanel title="We couldn't find this report" headingLevel={1} actions={newCheck}>
          <p>The link may be mistyped. Report links are long, so make sure you copied all of it.</p>
        </StatePanel>
      );
    default:
      return (
        <StatePanel
          tone="risk"
          title="We couldn't load this report"
          headingLevel={1}
          actions={
            <>
              <button type="button" className={buttonPrimary} onClick={() => window.location.reload()}>
                Try again
              </button>
              <Link href="/" className={buttonSecondary}>
                Check another applicant
              </Link>
            </>
          }
        >
          <p>This is a problem on our side. Please try again in a moment.</p>
        </StatePanel>
      );
  }
}
