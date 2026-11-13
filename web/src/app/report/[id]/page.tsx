import { Suspense } from "react";
import { ReportScreen } from "@/components/report-screen";

export default function ReportPage({ params }: PageProps<"/report/[id]">) {
  return (
    <div className="mx-auto max-w-3xl">
      <Suspense fallback={<p className="text-muted">Loading the report…</p>}>
      {params.then(({ id }) => (
        <ReportScreen id={id} />
      ))}
      </Suspense>
    </div>
  );
}
