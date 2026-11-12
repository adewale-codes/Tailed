"""Check 2: documents_agree.

Finds the salary credits on the statements (same payer, similar amount, roughly monthly) and
compares each payslip's net pay with the credit nearest its pay date. Flags a difference of
more than 2%, or a payslip whose pay date is covered by a statement but has no matching credit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from app.checks.models import CheckResult, DocumentInput, Evidence
from app.checks.util import company_tokens, gbp, money, payslips, pct, plural, statements, tokens_overlap
from app.reader.models import Transaction

MAX_DIFF_PCT = Decimal("2")
PAY_DATE_WINDOW = timedelta(days=4)  # weekends and bank holidays move pay days
AMOUNT_SIMILARITY = Decimal("0.10")  # credits within 10% of each other count as "similar"
MONTHLY_GAP = (20, 40)  # days between consecutive credits for "roughly monthly"
PAYER_NOISE = {"SALARY", "WAGES", "WAGE", "PAY", "PAYROLL", "BGC", "FPI", "BACS", "CREDIT", "FROM",
               "LTD", "LIMITED", "PLC", "REF"}


@dataclass
class Credit:
    file: str
    tx: Transaction

    @property
    def payer(self) -> str:
        words = re.sub(r"[^A-Z ]", " ", self.tx.description.upper()).split()
        return " ".join(w for w in words if w not in PAYER_NOISE)


def _salary_streams(credits: list[Credit]) -> list[list[Credit]]:
    groups: dict[str, list[Credit]] = {}
    for c in credits:
        if c.payer:
            groups.setdefault(c.payer, []).append(c)
    streams = []
    for group in groups.values():
        mid = Decimal(str(median([c.tx.paid_in for c in group])))
        similar = sorted((c for c in group if abs(c.tx.paid_in - mid) <= mid * AMOUNT_SIMILARITY),
                         key=lambda c: c.tx.date)
        gaps = [(b.tx.date - a.tx.date).days for a, b in zip(similar, similar[1:], strict=False)]
        if len(similar) >= 2 and all(MONTHLY_GAP[0] <= g <= MONTHLY_GAP[1] for g in gaps):
            streams.append(similar)
    return streams


def _matches_employer(payer: str, employer: str | None) -> bool:
    if not employer:
        return False
    return any(tokens_overlap(p, e) for p in payer.split() for e in company_tokens(employer))


def check_documents_agree(docs: list[DocumentInput]) -> CheckResult:
    stmts, slips = statements(docs), payslips(docs)
    if not stmts or not slips:
        return CheckResult(id="documents_agree", number=2, status="not_checked",
                           title="Nothing to compare",
                           explanation="We need both bank statements and payslips to compare pay.")

    credits = [Credit(doc.file, t) for doc, s in stmts for t in s.transactions if t.paid_in]
    streams = _salary_streams(credits)

    evidence: list[Evidence] = []
    compared = 0
    for slip_doc, slip in slips:
        pay_date = slip.pay_date.value
        if not any(s.period_start <= pay_date <= s.period_end + PAY_DATE_WINDOW for _, s in stmts):
            continue  # no statement covers this payslip, so there's nothing to compare
        employer = slip.employer_name.value if slip.employer_name else None
        candidates = _candidates(streams, credits, employer, pay_date)
        net = slip.net_pay.value
        if not candidates:
            evidence.append(Evidence.at(
                "no_matching_payment", slip_doc.file, [slip.net_pay.location], flag=True,
                numbers={"payslip_net": net}, details={"pay_date": pay_date.isoformat()}))
            continue
        compared += 1
        credit = min(candidates, key=lambda c: (abs((c.tx.date - pay_date).days),
                                                abs(c.tx.paid_in - net)))
        diff = credit.tx.paid_in - net
        diff_pct = pct(abs(diff) / net * 100) if net else Decimal(0)
        numbers = {"payslip_net": net, "statement_credit": credit.tx.paid_in,
                   "difference": money(diff), "difference_pct": diff_pct}
        dates = {"pay_date": pay_date.isoformat(), "credit_date": credit.tx.date.isoformat()}
        flag = diff_pct > MAX_DIFF_PCT
        # Two items: the payslip's net pay and the matching line on the statement
        evidence.append(Evidence.at("salary_vs_payslip", slip_doc.file, [slip.net_pay.location],
                                    flag=flag, numbers=numbers, details=dates))
        evidence.append(Evidence.at("salary_credit", credit.file, [credit.tx.location],
                                    flag=flag, numbers=numbers, details=dates))

    if not evidence:
        return CheckResult(id="documents_agree", number=2, status="not_checked",
                           title="Payslips and statements don't overlap",
                           explanation="None of the payslip dates fall within the statement periods, "
                                       "so we couldn't compare them.")
    flagged = [e for e in evidence if e.flag and e.kind != "salary_credit"]
    if not flagged:
        n = evidence[0].numbers
        return CheckResult(
            id="documents_agree", number=2, status="pass", evidence=evidence,
            title="Payslips match the bank statements",
            explanation=f"{plural(compared, 'payslip')} matched a salary payment on the statements, "
                        f"for example {gbp(n['payslip_net'])} net pay and {gbp(n['statement_credit'])} "
                        "paid in.")
    first = flagged[0]
    if first.kind == "no_matching_payment":
        explanation = (f"We couldn't find a payment on the statements matching the "
                       f"{gbp(first.numbers['payslip_net'])} net pay on "
                       f"{plural(len(flagged), 'payslip')}, even though the statements cover that date.")
    else:
        n = first.numbers
        explanation = (f"{plural(len(flagged), 'payslip')} {'shows' if len(flagged) == 1 else 'show'} "
                       f"net pay of {gbp(n['payslip_net'])} but the statement shows "
                       f"{gbp(n['statement_credit'])} paid in, a {n['difference_pct']}% difference.")
    return CheckResult(id="documents_agree", number=2, status="flag", evidence=evidence,
                       title="Payslips don't match the bank statements", explanation=explanation)


def _candidates(streams: list[list[Credit]], credits: list[Credit], employer: str | None,
                pay_date: date) -> list[Credit]:
    near = lambda c: abs(c.tx.date - pay_date) <= PAY_DATE_WINDOW  # noqa: E731
    # 1. A regular monthly stream from the employer named on the payslip
    for stream in streams:
        found = [c for c in stream if near(c)]
        if found and _matches_employer(stream[0].payer, employer):
            return found
    # 2. Any credit near the pay date from the employer
    if found := [c for c in credits if near(c) and _matches_employer(c.payer, employer)]:
        return found
    # 3. Any regular monthly stream (bank descriptions don't always name the employer)
    return [c for stream in streams for c in stream if near(c)]
