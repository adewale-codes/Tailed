import { Suspense } from "react";
import { CheckingScreen } from "@/components/checking-screen";

/** Not async: the shell renders immediately and the id streams in behind Suspense. */
export default function CheckPage({ params }: PageProps<"/check/[id]">) {
  return (
    <div className="mx-auto max-w-3xl">
      <Suspense fallback={<p className="text-muted">Loading your check…</p>}>
      {params.then(({ id }) => (
        <CheckingScreen id={id} />
      ))}
      </Suspense>
    </div>
  );
}
