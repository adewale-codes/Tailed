import Link from "next/link";
import { Suspense } from "react";
import { buttonSecondary } from "@/components/state-panel";

// Placeholder until the report screen is built (next phase). The API report is already live at
// GET /checks/{id}.
export default function ReportPage({ params }: PageProps<"/report/[id]">) {
  return (
    <div>
      <h1 className="text-3xl font-semibold tracking-tight text-ink">Your report</h1>
      <p className="mt-3 text-lg text-muted">The report screen is coming in the next build.</p>
      <Suspense fallback={null}>
        {params.then(({ id }) => (
          <Link href={`/check/${id}`} className={`${buttonSecondary} mt-6`}>
            Back to the checks
          </Link>
        ))}
      </Suspense>
    </div>
  );
}
