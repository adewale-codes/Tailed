import { AlertIcon } from "./icons";

type Tone = "closer" | "risk" | "neutral";

const TONES: Record<Tone, string> = {
  closer: "border-closer/30 bg-closer-soft text-closer",
  risk: "border-risk/30 bg-risk-soft text-risk",
  neutral: "border-line bg-surface text-ink",
};

/** A friendly full-width message for limit, unreadable-file and server-error states. */
export function StatePanel({
  tone = "neutral",
  title,
  children,
  actions,
  headingLevel = 2,
}: {
  tone?: Tone;
  title: string;
  children: React.ReactNode;
  actions?: React.ReactNode;
  headingLevel?: 1 | 2;
}) {
  const Heading = headingLevel === 1 ? "h1" : "h2";
  return (
    <section role="alert" className={`rounded-xl border p-5 sm:p-6 ${TONES[tone]}`}>
      <div className="flex gap-3">
        <span className="mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface">
          <AlertIcon />
        </span>
        <div className="min-w-0">
          <Heading className="text-xl font-semibold text-ink">{title}</Heading>
          <div className="mt-2 space-y-2 text-ink">{children}</div>
          {actions ? <div className="mt-5 flex flex-wrap gap-3">{actions}</div> : null}
        </div>
      </div>
    </section>
  );
}

export const buttonPrimary =
  "inline-flex min-h-11 items-center justify-center rounded-lg bg-accent px-5 py-2.5 font-medium text-surface transition-colors duration-150 hover:bg-accent-hover disabled:cursor-not-allowed disabled:bg-line disabled:text-muted";

export const buttonSecondary =
  "inline-flex min-h-11 items-center justify-center rounded-lg border border-line bg-surface px-5 py-2.5 font-medium text-ink transition-colors duration-150 hover:border-accent hover:text-accent";
