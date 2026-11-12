"""Runs the six checks, decides the verdict and works out affordability.

The verdict is decided here, by code. The wording step (app/report/wording.py) only rewrites
the text afterwards and can't change a status, a number or the verdict.

Verdict rules:
  - high_risk: check 1 (balances_add_up) or check 2 (documents_agree) flagged, plus at least one
    other check flagged.
  - needs_a_closer_look: any check flagged.
  - looks_good: no flags. (not_checked is not a flag, and is never shown as a pass.)
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from app.checks.balances import check_balances
from app.checks.companies_house import CompanyLookup
from app.checks.documents_agree import check_documents_agree
from app.checks.employer_real import check_employer_real
from app.checks.models import Affordability, CheckResult, DocumentInput, Report, Verdict
from app.checks.names_match import check_names_match
from app.checks.payslip_realistic import check_payslip_realistic
from app.checks.real_life import check_looks_like_real_life
from app.checks.util import money, payslips, pct, statements

KEY_CHECKS = {"balances_add_up", "documents_agree"}
AVG_DAYS_PER_MONTH = Decimal("30.44")


def run_checks(docs: list[DocumentInput], monthly_rent: Decimal, companies: CompanyLookup,
               on_check_done: Callable[[CheckResult], None] | None = None) -> Report:
    steps: list[Callable[[], CheckResult]] = [
        lambda: check_balances(docs),
        lambda: check_documents_agree(docs),
        lambda: check_payslip_realistic(docs),
        lambda: check_employer_real(docs, companies),
        lambda: check_looks_like_real_life(docs),
        lambda: check_names_match(docs),
    ]
    checks = []
    for step in steps:
        result = step()
        checks.append(result)
        if on_check_done is not None:
            on_check_done(result)
    verdict, reason = decide_verdict(checks)
    return Report(verdict=verdict, verdict_reason=reason, checks=checks,
                  affordability=affordability(docs, monthly_rent))


def decide_verdict(checks: list[CheckResult]) -> tuple[Verdict, str]:
    flagged = [c.id for c in checks if c.status == "flag"]
    key = [c for c in flagged if c in KEY_CHECKS]
    if key and len(flagged) >= 2:
        return "high_risk", (f"{', '.join(key)} flagged together with "
                             f"{', '.join(c for c in flagged if c != key[0])}")
    if flagged:
        return "needs_a_closer_look", f"flagged: {', '.join(flagged)}"
    return "looks_good", "no checks flagged"


def affordability(docs: list[DocumentInput], monthly_rent: Decimal) -> Affordability:
    result = Affordability(monthly_rent=money(monthly_rent))
    stmts = statements(docs)
    if stmts:
        days = sum((s.period_end - s.period_start).days + 1 for _, s in stmts)
        months = Decimal(days) / AVG_DAYS_PER_MONTH
        money_in = sum((t.paid_in or Decimal(0) for _, s in stmts for t in s.transactions), Decimal(0))
        result.months_covered = months.quantize(Decimal("0.1"))
        result.average_monthly_income_in_account = money(money_in / months)
    slips = payslips(docs)
    if slips:
        result.payslip_net_pay = money(sum((p.net_pay.value for _, p in slips), Decimal(0)) / len(slips))
    if result.average_monthly_income_in_account:
        result.rent_pct_of_account_income = pct(monthly_rent / result.average_monthly_income_in_account * 100)
    if result.payslip_net_pay:
        result.rent_pct_of_payslip_net = pct(monthly_rent / result.payslip_net_pay * 100)
    return result
