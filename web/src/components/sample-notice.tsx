import type { SampleInfo } from "@/lib/report";

/** Shown on every report and evidence view from a sample check, so nobody mistakes it for a real one. */
export function SampleNotice({ sample }: { sample: SampleInfo }) {
  return (
    <p
      data-sample-notice
      className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-accent/30 bg-accent-soft px-4 py-3 text-sm text-ink"
    >
      <span className="inline-flex rounded-full bg-accent px-2.5 py-0.5 font-semibold text-surface">Sample</span>
      <span>
        This report is from the &ldquo;{sample.title}&rdquo; sample. The documents are made up: the tenant, their employer
        and Northgate Bank are fictional.
      </span>
    </p>
  );
}
