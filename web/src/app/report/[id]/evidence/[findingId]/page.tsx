import { Suspense } from "react";
import { EvidenceScreen } from "@/components/evidence-screen";

export default function EvidencePage({ params }: PageProps<"/report/[id]/evidence/[findingId]">) {
  return (
    <Suspense fallback={<p className="text-muted">Loading the evidence…</p>}>
      {params.then(({ id, findingId }) => (
        <EvidenceScreen id={id} findingId={findingId} />
      ))}
    </Suspense>
  );
}
