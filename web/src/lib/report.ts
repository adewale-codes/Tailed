import type { ApiError, CheckStatus } from "./types";

export type Verdict = "looks_good" | "needs_a_closer_look" | "high_risk";

export type BBox = { x0: number; top: number; x1: number; bottom: number };

export type Evidence = {
  kind: string;
  flag: boolean;
  file: string | null;
  page: number | null;
  bboxes: BBox[];
  primary?: number;
  approximate: boolean;
  numbers: Record<string, string>;
  details: Record<string, string>;
};

export type Wording = { title: string; explanation: string; next_steps: string[] };

export type CheckResult = {
  id: string;
  number: number;
  status: CheckStatus;
  title: string;
  explanation: string;
  evidence: Evidence[];
  display: Wording | null;
};

export type ReportFile = {
  key: string;
  name: string;
  document_type?: string;
  pages?: { page: number; width: number; height: number; unit: string }[];
  images?: { page: number; width: number; height: number }[];
  error?: ApiError;
};

export type Report = {
  id: string;
  status: "done";
  created_at: string;
  expires_at: string;
  files: ReportFile[];
  report: {
    verdict: Verdict;
    verdict_reason: string;
    checks: CheckResult[];
    affordability: {
      monthly_rent: string;
      months_covered: string | null;
      average_monthly_income_in_account: string | null;
      payslip_net_pay: string | null;
      rent_pct_of_account_income: string | null;
      rent_pct_of_payslip_net: string | null;
    };
    summary: Wording | null;
    wording_source: "ai" | "engine";
  };
};

export type ReportState =
  | { kind: "ready"; report: Report }
  | { kind: "processing" }
  | { kind: "failed"; error: ApiError }
  | { kind: "expired"; message: string }
  | { kind: "not_found" }
  | { kind: "error" };

// One request per report per tab: the report and evidence screens share it, so switching
// between them never refetches.
const cache = new Map<string, Promise<ReportState>>();

export function loadReport(id: string, { fresh = false } = {}): Promise<ReportState> {
  if (fresh) cache.delete(id);
  let pending = cache.get(id);
  if (!pending) {
    pending = fetchReport(id);
    cache.set(id, pending);
    // Only keep successful results; anything else is retried on the next visit.
    pending.then((s) => s.kind !== "ready" && cache.delete(id));
  }
  return pending;
}

async function fetchReport(id: string): Promise<ReportState> {
  try {
    const res = await fetch(`/api/checks/${id}`, { cache: "no-store" });
    const data = await res.json().catch(() => null);
    if (res.status === 200 && data?.report) return { kind: "ready", report: data as Report };
    if (res.status === 202) return { kind: "processing" };
    if (res.status === 410) return { kind: "expired", message: data?.error?.message ?? "" };
    if (res.status === 404) return { kind: "not_found" };
    if (res.status === 422 && data?.error) return { kind: "failed", error: data.error };
    return { kind: "error" };
  } catch {
    return { kind: "error" };
  }
}

/** Fire-and-forget, cookie-free analytics. Never throws, never blocks the page. */
export function track(id: string, name: "report_viewed" | "evidence_opened"): void {
  try {
    void fetch(`/api/checks/${id}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
      keepalive: true,
    }).catch(() => {});
  } catch {
    // ignore
  }
}

// ---------------------------------------------------------------------------
// Findings: every flagged piece of evidence on a flagged check gets its own evidence view.
// ---------------------------------------------------------------------------

/** Evidence kinds that are about how a file was made, shown as supporting signals. */
const SIGNAL_KINDS = new Set(["font_change", "modified_after_period"]);

export type Finding = {
  id: string; // "<check id>-<index in that check's evidence>", stable for a given report
  check: CheckResult;
  evidence: Evidence;
  file: ReportFile | undefined;
  title: string;
};

export function findingsOf(report: Report): Finding[] {
  const out: Finding[] = [];
  for (const check of report.report.checks) {
    if (check.status !== "flag") continue;
    check.evidence.forEach((evidence, index) => {
      if (!evidence.flag) return;
      out.push({
        id: `${check.id}-${index}`,
        check,
        evidence,
        file: report.files.find((f) => f.key === evidence.file),
        title: describe(evidence).title,
      });
    });
  }
  return out;
}

/** Signals (font or metadata changes) in the same file as this finding, other than itself. */
export function supportingSignals(report: Report, finding: Finding): Evidence[] {
  return report.report.checks
    .flatMap((c) => c.evidence)
    .filter(
      (e) => e !== finding.evidence && e.flag && SIGNAL_KINDS.has(e.kind) && e.file === finding.evidence.file,
    );
}

// ---------------------------------------------------------------------------
// Presentation
// ---------------------------------------------------------------------------

export function gbp(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "–";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return n.toLocaleString("en-GB", { style: "currency", currency: "GBP" });
}

export function shortDate(iso: string | undefined): string {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00Z` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

export type Comparison = { expectedLabel: string; expected: string; shownLabel: string; shown: string; difference: string | null };

export type Described = {
  title: string;
  comparison?: Comparison;
  facts: { label: string; value: string }[];
};

function diffText(raw: string | undefined): string | null {
  if (raw === undefined) return null;
  const n = Number(raw);
  if (!Number.isFinite(n) || n === 0) return gbp(raw);
  return `${gbp(Math.abs(n))} ${n > 0 ? "more" : "less"}`;
}

/** Turns one piece of evidence into a title, a should-be/shown/difference block and facts. */
export function describe(e: Evidence): Described {
  const n = e.numbers;
  const d = e.details;
  switch (e.kind) {
    case "balance_mismatch":
      return {
        title: `Balance on ${shortDate(d.date)} doesn't follow`,
        comparison: {
          expectedLabel: "Should be",
          expected: gbp(n.expected),
          shownLabel: "Shown",
          shown: gbp(n.shown),
          difference: diffText(n.difference),
        },
        facts: [
          { label: "Balance on the line before", value: gbp(n.previous_balance) },
          ...(Number(n.paid_in) ? [{ label: "Paid in on this line", value: gbp(n.paid_in) }] : []),
          ...(Number(n.paid_out) ? [{ label: "Paid out on this line", value: gbp(n.paid_out) }] : []),
        ],
      };
    case "summary_mismatch":
      return {
        title: "Closing balance in the summary doesn't add up",
        comparison: {
          expectedLabel: "Should be",
          expected: gbp(n.expected),
          shownLabel: "Shown",
          shown: gbp(n.shown),
          difference: diffText(n.difference),
        },
        facts: [
          { label: "Opening balance", value: gbp(n.opening) },
          { label: "Money in", value: gbp(n.money_in) },
          { label: "Money out", value: gbp(n.money_out) },
        ],
      };
    case "statements_dont_join":
      return {
        title: "Statements don't join up",
        comparison: {
          expectedLabel: "Previous closing balance",
          expected: gbp(n.previous_closing),
          shownLabel: "This opening balance",
          shown: gbp(n.opening),
          difference: diffText(n.difference),
        },
        facts: [],
      };
    case "salary_vs_payslip":
    case "salary_credit":
      return {
        title: e.kind === "salary_vs_payslip" ? `Payslip net pay (${shortDate(d.pay_date)})` : `Salary paid in (${shortDate(d.credit_date)})`,
        comparison: {
          expectedLabel: "Payslip says",
          expected: gbp(n.payslip_net),
          shownLabel: "Paid into the account",
          shown: gbp(n.statement_credit),
          difference: n.difference_pct ? `${diffText(n.difference)} (${Math.abs(Number(n.difference_pct))}%)` : diffText(n.difference),
        },
        facts: [
          ...(d.pay_date ? [{ label: "Payslip pay date", value: shortDate(d.pay_date) }] : []),
          ...(d.credit_date ? [{ label: "Date paid in", value: shortDate(d.credit_date) }] : []),
        ],
      };
    case "no_matching_payment":
      return {
        title: "No salary payment matches this payslip",
        facts: [
          { label: "Payslip net pay", value: gbp(n.payslip_net) },
          ...(d.pay_date ? [{ label: "Pay date", value: shortDate(d.pay_date) }] : []),
        ],
      };
    case "income_tax":
    case "national_insurance": {
      const what = e.kind === "income_tax" ? "Income tax" : "National Insurance";
      return {
        title: `${what} looks wrong`,
        comparison: {
          expectedLabel: "We'd expect about",
          expected: gbp(n.expected),
          shownLabel: "Payslip shows",
          shown: gbp(n.shown),
          difference: n.difference_pct ? `${diffText(n.difference)} (${Math.abs(Number(n.difference_pct))}% off)` : diffText(n.difference),
        },
        facts: [{ label: "Gross pay", value: gbp(n.gross) }],
      };
    }
    case "deductions_dont_add_up":
      return {
        title: "Payslip figures don't add up",
        comparison: {
          expectedLabel: "Net pay should be",
          expected: gbp(n.expected),
          shownLabel: "Payslip shows",
          shown: gbp(n.shown),
          difference: diffText(n.difference),
        },
        facts: [
          { label: "Gross pay", value: gbp(n.gross) },
          { label: "Total deductions", value: gbp(n.deductions) },
        ],
      };
    case "name_differs":
      return {
        title: `Different name on the ${d.document_type === "payslip" ? "payslip" : "statement"}`,
        facts: [
          { label: "Name on this document", value: d.name ?? "–" },
          { label: "Name on the other documents", value: d.reference_name ?? "–" },
        ],
      };
    case "font_change":
      return {
        title: `A line in a different font${d.date ? ` (${shortDate(d.date)})` : ""}`,
        facts: [
          { label: "This line", value: `${d.font ?? "?"}, size ${n.font_size ?? "?"}` },
          { label: "Rest of the page", value: `${d.page_font ?? "?"}, size ${n.page_font_size ?? "?"}` },
        ],
      };
    case "modified_after_period":
      return {
        title: "File changed after the statement was issued",
        facts: [
          ...(d.period_end ? [{ label: "Statement period ends", value: shortDate(d.period_end) }] : []),
          ...(d.created ? [{ label: "File created", value: shortDate(d.created) }] : []),
          ...(d.modified ? [{ label: "File last changed", value: shortDate(d.modified) }] : []),
          ...(d.producer ? [{ label: "Saved by", value: d.producer }] : []),
          ...(d.creator ? [{ label: "Created with", value: d.creator }] : []),
        ],
      };
    case "little_everyday_spending":
      return {
        title: "Very little everyday spending",
        facts: [
          { label: "Payments out a month", value: n.payments_out_per_month ?? "–" },
          { label: "Spending categories", value: n.spending_categories ?? "–" },
        ],
      };
    case "repeated_round_amounts":
      return {
        title: `The same round amount, again and again`,
        facts: [
          { label: "Amount", value: gbp(n.amount) },
          { label: "Times in a row", value: n.run_length ?? "–" },
          ...(d.from ? [{ label: "From", value: shortDate(d.from) }] : []),
          ...(d.to ? [{ label: "To", value: shortDate(d.to) }] : []),
        ],
      };
    case "employer_not_found":
    case "employer_not_active":
    case "employer_too_new":
    case "employer_no_accounts":
      return {
        title:
          e.kind === "employer_not_found"
            ? "Employer not found on Companies House"
            : e.kind === "employer_not_active"
              ? "Employer isn't an active company"
              : e.kind === "employer_too_new"
                ? "Employer was set up very recently"
                : "Employer has never filed accounts",
        facts: Object.entries(d).map(([k, v]) => ({ label: humanise(k), value: v })),
      };
    default:
      return {
        title: humanise(e.kind),
        facts: [
          ...Object.entries(n).map(([k, v]) => ({ label: humanise(k), value: v })),
          ...Object.entries(d).map(([k, v]) => ({ label: humanise(k), value: v })),
        ],
      };
  }
}

function humanise(key: string): string {
  const s = key.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function checkWording(check: CheckResult): Wording {
  return check.display ?? { title: check.title, explanation: check.explanation, next_steps: [] };
}

export function fileLabel(file: ReportFile | undefined): string {
  if (!file) return "Document";
  return file.name || `File ${file.key}`;
}
