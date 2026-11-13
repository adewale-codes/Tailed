"use client";

import { motion } from "framer-motion";
import Link from "next/link";
import { useEffect, useId, useState } from "react";
import {
  type CheckResult,
  type Finding,
  type Report,
  type ReportState,
  type Verdict,
  checkWording,
  findingsOf,
  gbp,
  loadReport,
  track,
} from "@/lib/report";
import { FeedbackForm } from "./feedback-form";
import { AlertIcon, DashIcon, Spinner, TickIcon } from "./icons";
import { ReportProblem } from "./report-problem";
import { buttonSecondary } from "./state-panel";

const VERDICTS: Record<Verdict, { label: string; card: string; badge: string; Icon: typeof TickIcon }> = {
  looks_good: {
    label: "Looks good",
    card: "border-good/25 bg-good-soft",
    badge: "bg-good text-surface",
    Icon: TickIcon,
  },
  needs_a_closer_look: {
    label: "Needs a closer look",
    card: "border-closer/25 bg-closer-soft",
    badge: "bg-closer text-surface",
    Icon: AlertIcon,
  },
  high_risk: {
    label: "High risk",
    card: "border-risk/25 bg-risk-soft",
    badge: "bg-risk text-surface",
    Icon: AlertIcon,
  },
};

export function ReportScreen({ id }: { id: string }) {
  const [state, setState] = useState<ReportState | null>(null);

  useEffect(() => {
    let cancelled = false;
    loadReport(id).then((s) => {
      if (cancelled) return;
      setState(s);
      if (s.kind === "ready") track(id, "report_viewed");
    });
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (!state) {
    return (
      <p className="flex items-center gap-2 text-muted" role="status">
        <Spinner /> Loading the report…
      </p>
    );
  }
  if (state.kind !== "ready") return <ReportProblem id={id} state={state} />;
  return <ReportView report={state.report} />;
}

function ReportView({ report }: { report: Report }) {
  const { verdict, checks, summary, affordability } = report.report;
  const findings = findingsOf(report);
  const flagged = checks.filter((c) => c.status === "flag");
  const notChecked = checks.filter((c) => c.status === "not_checked");
  const passed = checks.filter((c) => c.status === "pass");
  const steps = nextSteps(report);
  const v = VERDICTS[verdict];
  const uid = useId();
  const hid = (name: string) => `${uid}-${name}`;

  return (
    <div className="space-y-10">
      {/* Verdict */}
      <section aria-labelledby={hid("verdict")} data-section="verdict" className={`rounded-2xl border p-5 sm:p-7 ${v.card}`}>
        <p className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-medium ${v.badge}`}>
          <v.Icon className="h-4 w-4" />
          {v.label}
        </p>
        <h1 id={hid("verdict")} className="mt-4 text-3xl font-semibold leading-tight tracking-tight text-ink sm:text-4xl">
          {summary?.title ?? v.label}
        </h1>
        {summary?.explanation ? <p className="mt-3 max-w-2xl text-lg text-ink">{summary.explanation}</p> : null}
        <p className="mt-4 text-sm text-muted">
          {flagged.length === 0
            ? "None of the six checks raised a problem."
            : `${flagged.length} of 6 checks raised a problem.`}
          {notChecked.length > 0 ? ` We couldn't run ${notChecked.length === 1 ? "one check" : `${notChecked.length} checks`}.` : ""}
        </p>
      </section>

      {/* What doesn't add up */}
      {flagged.length > 0 ? (
        <section aria-labelledby={hid("flags")}>
          <h2 id={hid("flags")} className="text-2xl font-semibold text-ink">
            What doesn&apos;t add up
          </h2>
          <ul className="mt-4 space-y-3">
            {flagged.map((check) => (
              <FlagCard
                key={check.id}
                reportId={report.id}
                check={check}
                tone={verdict === "high_risk" ? "risk" : "closer"}
                findings={findings.filter((f) => f.check.id === check.id)}
              />
            ))}
          </ul>
        </section>
      ) : null}

      {/* Couldn't check: shown honestly, never hidden */}
      {notChecked.length > 0 ? (
        <section aria-labelledby={hid("unchecked")}>
          <h2 id={hid("unchecked")} className="text-2xl font-semibold text-ink">
            We couldn&apos;t check this
          </h2>
          <ul className="mt-4 space-y-3">
            {notChecked.map((check) => {
              const w = checkWording(check);
              return (
                <li key={check.id} className="rounded-xl border border-line bg-surface p-4 sm:p-5">
                  <div className="flex gap-3">
                    <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-canvas text-muted">
                      <DashIcon />
                    </span>
                    <div>
                      <h3 className="font-sans text-lg font-semibold text-ink">{w.title}</h3>
                      <p className="mt-1 text-ink">{w.explanation}</p>
                      <p className="mt-2 text-sm text-muted">
                        This doesn&apos;t mean anything is wrong. It means this part of the story is unchecked.
                      </p>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {/* What looks fine */}
      {passed.length > 0 ? (
        <section aria-labelledby={hid("fine")}>
          <h2 id={hid("fine")} className="text-2xl font-semibold text-ink">
            What looks fine
          </h2>
          <ul className="mt-4 divide-y divide-line rounded-xl border border-line bg-surface">
            {passed.map((check) => {
              const w = checkWording(check);
              return (
                <li key={check.id} className="flex gap-3 p-4 sm:p-5">
                  <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-good-soft text-good">
                    <TickIcon />
                  </span>
                  <div>
                    <h3 className="font-sans font-semibold text-ink">{w.title}</h3>
                    <p className="mt-1 text-muted">{w.explanation}</p>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      <AffordabilityPanel a={affordability} />

      {steps.length > 0 ? (
        <section aria-labelledby={hid("steps")}>
          <h2 id={hid("steps")} className="text-2xl font-semibold text-ink">
            Suggested next steps
          </h2>
          <ol className="mt-4 space-y-3">
            {steps.map((step, i) => (
              <li key={step} className="flex gap-3">
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent-soft text-sm font-semibold text-accent">
                  {i + 1}
                </span>
                <span className="pt-0.5 text-ink">{step}</span>
              </li>
            ))}
          </ol>
        </section>
      ) : null}

      <section className="rounded-xl border border-line bg-surface p-4 text-sm text-muted sm:p-5">
        <p>
          <span className="font-medium text-ink">This page expires {expiryText(report.expires_at)}.</span> We
          delete the report and the page images 24 hours after the check, to protect the tenant&apos;s privacy. The
          original documents were deleted as soon as the check finished.
        </p>
        <p className="mt-2">
          Tallied checks whether the documents are consistent. It can&apos;t prove they&apos;re genuine, and it
          isn&apos;t a credit or reference check.
        </p>
      </section>

      <FeedbackForm reportId={report.id} />

      <div className="border-t border-line pt-8">
        <Link href="/" className={buttonSecondary}>
          Check another applicant
        </Link>
      </div>
    </div>
  );
}

function FlagCard({
  reportId,
  check,
  findings,
  tone,
}: {
  reportId: string;
  check: CheckResult;
  findings: Finding[];
  tone: "closer" | "risk";
}) {
  const w = checkWording(check);
  const first = findings[0];
  const colours = tone === "risk" ? "bg-risk-soft text-risk" : "bg-closer-soft text-closer";
  return (
    <li className="rounded-xl border border-line bg-surface p-4 sm:p-5">
      <div className="flex gap-3">
        <span className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${colours}`}>
          <AlertIcon />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="font-sans text-lg font-semibold text-ink">{w.title}</h3>
          <p className="mt-1 text-ink">{w.explanation}</p>
          {first ? (
            <Link
              href={`/report/${reportId}/evidence/${first.id}`}
              className="mt-3 inline-flex min-h-11 items-center gap-1 font-medium text-accent underline underline-offset-4 hover:text-accent-hover"
            >
              {first.evidence.page ? "See it on the page" : "See the evidence"}
              {findings.length > 1 ? ` (${findings.length} places)` : ""}
              <span aria-hidden="true">→</span>
            </Link>
          ) : null}
        </div>
      </div>
    </li>
  );
}

function AffordabilityPanel({ a }: { a: Report["report"]["affordability"] }) {
  const pct = a.rent_pct_of_payslip_net ?? a.rent_pct_of_account_income;
  const basis = a.rent_pct_of_payslip_net ? "take-home pay on the payslips" : "money paid into the account each month";
  const income = a.rent_pct_of_payslip_net ? a.payslip_net_pay : a.average_monthly_income_in_account;
  const value = pct === null ? null : Number(pct);
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="rounded-2xl border border-line bg-surface p-5 sm:p-7">
      <h2 id={headingId} className="text-2xl font-semibold text-ink">
        Affordability
      </h2>
      {value === null ? (
        <p className="mt-3 text-ink">
          We couldn&apos;t work out the tenant&apos;s monthly income from these documents, so we can&apos;t compare it
          with the rent of {gbp(a.monthly_rent)}.
        </p>
      ) : (
        <>
          <p className="mt-3 text-ink">
            Rent of <span className="font-semibold">{gbp(a.monthly_rent)}</span> is{" "}
            <span className="font-semibold">{value}%</span> of the {basis} ({gbp(income)} a month).
          </p>
          <div
            className="mt-4 h-3 overflow-hidden rounded-full bg-line"
            role="img"
            aria-label={`Rent is ${value}% of monthly income`}
          >
            <motion.div
              className="h-full origin-left rounded-full bg-accent"
              initial={{ scaleX: 0 }}
              whileInView={{ scaleX: Math.min(value, 100) / 100 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, ease: "easeOut" }}
            />
          </div>
          <div className="mt-1 flex justify-between text-xs text-muted" aria-hidden="true">
            <span>0%</span>
            <span>50%</span>
            <span>100%</span>
          </div>
          <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2">
            {a.payslip_net_pay ? (
              <Fact label="Average take-home pay (payslips)" value={gbp(a.payslip_net_pay)} />
            ) : null}
            {a.average_monthly_income_in_account ? (
              <Fact label="Average paid in each month (statements)" value={gbp(a.average_monthly_income_in_account)} />
            ) : null}
            {a.rent_pct_of_account_income && a.rent_pct_of_payslip_net ? (
              <Fact label="Rent as a share of money paid in" value={`${a.rent_pct_of_account_income}%`} />
            ) : null}
            {a.months_covered ? <Fact label="Months of statements" value={a.months_covered} /> : null}
          </dl>
        </>
      )}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-canvas px-3 py-2">
      <dt className="text-muted">{label}</dt>
      <dd className="font-semibold text-ink tabular-nums">{value}</dd>
    </div>
  );
}

function nextSteps(report: Report): string[] {
  const steps = [
    ...(report.report.summary?.next_steps ?? []),
    ...report.report.checks.filter((c) => c.status !== "pass").flatMap((c) => checkWording(c).next_steps),
  ];
  return [...new Set(steps)];
}

function expiryText(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "in 24 hours";
  return `at ${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })} on ${d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })}`;
}
