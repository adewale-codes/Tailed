"""Check 3: payslip_realistic.

Recomputes income tax and employee National Insurance for each payslip from its gross pay,
using the 2026/27 figures in tax_config.py (standard tax code, monthly pay). Flags tax or NI more
than 5% away from our figure, and deductions that don't add up to the net pay shown.

Pension can be taken before tax (net pay arrangement) or after (relief at source); we work out
both and use whichever is closer, so neither is wrongly flagged.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.checks import tax_config as cfg
from app.checks.models import CheckResult, DocumentInput, Evidence
from app.checks.pdf_signals import modified_after
from app.checks.util import gbp, money, payslips, pct, plural
from app.reader.models import Payslip

TWELVE = Decimal(12)


def monthly_tax(taxable_gross: Decimal) -> Decimal:
    allowance = cfg.PERSONAL_ALLOWANCE / TWELVE
    basic_band = cfg.BASIC_RATE_BAND / TWELVE
    additional_from = (cfg.ADDITIONAL_RATE_THRESHOLD - cfg.PERSONAL_ALLOWANCE) / TWELVE
    taxable = max(Decimal(0), taxable_gross - allowance)
    basic = min(taxable, basic_band)
    higher = max(Decimal(0), min(taxable, additional_from) - basic_band)
    additional = max(Decimal(0), taxable - additional_from)
    return money(basic * cfg.BASIC_RATE + higher * cfg.HIGHER_RATE + additional * cfg.ADDITIONAL_RATE)


def monthly_ni(gross: Decimal) -> Decimal:
    main = max(Decimal(0), min(gross, cfg.NI_UPPER_EARNINGS_LIMIT_MONTHLY) - cfg.NI_PRIMARY_THRESHOLD_MONTHLY)
    upper = max(Decimal(0), gross - cfg.NI_UPPER_EARNINGS_LIMIT_MONTHLY)
    return money(main * cfg.NI_MAIN_RATE + upper * cfg.NI_UPPER_RATE)


def _out_by(shown: Decimal, expected: Decimal) -> tuple[bool, Decimal]:
    diff = abs(shown - expected)
    allowed = max(expected * cfg.TOLERANCE_PCT / 100, cfg.TOLERANCE_FLOOR)
    off_pct = pct(diff / expected * 100) if expected else Decimal(100 if diff else 0)
    return diff > allowed, off_pct


def _standard_code(code: str | None) -> bool:
    if not code:
        return True  # most payslips without a printed code are on the standard code; checked below
    base = re.sub(r"\s*(W1|M1|X|W1/M1)$", "", code.upper().strip())
    return base in cfg.STANDARD_TAX_CODES


def _check_one(file: str, p: Payslip) -> tuple[list[Evidence], str | None]:
    """Returns evidence for one payslip, or a reason it couldn't be checked."""
    pay_date = p.pay_date.value
    if not cfg.TAX_YEAR_START <= pay_date <= cfg.TAX_YEAR_END:
        return [], "outside the 2026/27 tax year"
    if not _standard_code(p.tax_code):
        return [], "non-standard tax code"
    evidence: list[Evidence] = []
    gross = p.gross_pay.value
    pension = p.pension.value if p.pension else Decimal(0)

    if p.income_tax is not None:
        options = [monthly_tax(gross), monthly_tax(gross - pension)]
        expected = min(options, key=lambda e: abs(p.income_tax.value - e))
        flagged, off = _out_by(p.income_tax.value, expected)
        evidence.append(Evidence.at(
            "income_tax", file, [p.income_tax.location, p.gross_pay.location], flag=flagged,
            numbers={"gross": gross, "shown": p.income_tax.value, "expected": expected,
                     "difference": money(p.income_tax.value - expected), "difference_pct": off}))
    if p.national_insurance is not None:
        expected = monthly_ni(gross)
        flagged, off = _out_by(p.national_insurance.value, expected)
        evidence.append(Evidence.at(
            "national_insurance", file, [p.national_insurance.location, p.gross_pay.location],
            flag=flagged,
            numbers={"gross": gross, "shown": p.national_insurance.value, "expected": expected,
                     "difference": money(p.national_insurance.value - expected),
                     "difference_pct": off}))

    deductions = sum((x.value for x in (p.income_tax, p.national_insurance, p.pension) if x),
                     Decimal(0)) + sum((d.value for d in p.other_deductions), Decimal(0))
    expected_net = gross - deductions
    if abs(expected_net - p.net_pay.value) > Decimal("0.01"):
        evidence.append(Evidence.at(
            "deductions_dont_add_up", file, [p.net_pay.location], flag=True,
            numbers={"gross": gross, "deductions": deductions, "expected": expected_net,
                     "shown": p.net_pay.value, "difference": p.net_pay.value - expected_net}))
    return evidence, None


def check_payslip_realistic(docs: list[DocumentInput]) -> CheckResult:
    slips = payslips(docs)
    if not slips:
        return CheckResult(id="payslip_realistic", number=3, status="not_checked",
                           title="No payslips to check",
                           explanation="We need at least one payslip to check the tax and National "
                                       "Insurance.")
    evidence: list[Evidence] = []
    skipped: list[str] = []
    for doc, p in slips:
        found, reason = _check_one(doc.file, p)
        evidence += found
        if reason:
            skipped.append(reason)
        if (signal := modified_after(doc, p.pay_date.value)) is not None:
            evidence.append(signal)

    if not evidence:
        return CheckResult(id="payslip_realistic", number=3, status="not_checked",
                           title="We couldn't check the payslips' tax",
                           explanation=f"We can only check standard 2026/27 payslips, and these were "
                                       f"{skipped[0]}.")
    flagged = [e for e in evidence if e.flag]
    checked = len(slips) - len(skipped)
    if not flagged:
        return CheckResult(id="payslip_realistic", number=3, status="pass", evidence=evidence,
                           title="Tax and National Insurance look right",
                           explanation=f"Tax and National Insurance on {plural(checked, 'payslip')} "
                                       "are within 5% of what we'd expect for the gross pay.")
    return CheckResult(id="payslip_realistic", number=3, status="flag", evidence=evidence,
                       title=_flag_title(flagged), explanation=_flag_explanation(flagged))


def _flag_title(flagged: list[Evidence]) -> str:
    kinds = {e.kind for e in flagged}
    if kinds & {"income_tax", "national_insurance"}:
        return "Tax or National Insurance looks wrong"
    if "deductions_dont_add_up" in kinds:
        return "Payslip figures don't add up"
    return "The payslip file shows signs of editing"


def _flag_explanation(flagged: list[Evidence]) -> str:
    tax = next((e for e in flagged if e.kind == "income_tax"), None)
    ni = next((e for e in flagged if e.kind == "national_insurance"), None)
    if tax or ni:
        e = tax or ni
        n = e.numbers
        what = "income tax" if e is tax else "National Insurance"
        text = (f"For gross pay of {gbp(n['gross'])} we'd expect about {gbp(n['expected'])} "
                f"{what}, but the payslip shows {gbp(n['shown'])}")
        if tax and ni:
            text += (f", and National Insurance of {gbp(ni.numbers['shown'])} where we'd expect "
                     f"{gbp(ni.numbers['expected'])}")
        return text + "."
    adds = next((e for e in flagged if e.kind == "deductions_dont_add_up"), None)
    if adds:
        n = adds.numbers
        return (f"Gross pay of {gbp(n['gross'])} minus {gbp(n['deductions'])} of deductions should "
                f"leave {gbp(n['expected'])}, but the payslip shows {gbp(n['shown'])} net pay.")
    e = flagged[0]
    return (f"The payslip figures look right, but the file was changed on {e.details['modified']}, "
            "after the pay date, which can be a sign of editing.")
