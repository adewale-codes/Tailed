"""Check 1: balances_add_up.

Recomputes every running balance from the previous balance shown and the transaction amount.
A line more than 1p out is a flag. Each line is judged against the balance printed on the line
before it, so one edited figure produces one finding rather than a cascade. Also checks the
summary totals, the hand-over between consecutive statements, and PDF editing signals.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app.checks.models import CheckResult, DocumentInput, Evidence
from app.checks.pdf_signals import font_changes, modified_after
from app.checks.util import closing_balance, gbp, long_date, plural, statements

TOLERANCE = Decimal("0.01")


def check_balances(docs: list[DocumentInput]) -> CheckResult:
    stmts = statements(docs)
    if not stmts:
        return CheckResult(id="balances_add_up", number=1, status="not_checked",
                           title="No bank statements to check",
                           explanation="We need at least one bank statement to check the balances.")

    evidence: list[Evidence] = []
    lines_checked = 0
    for doc, s in stmts:
        previous = s.opening_balance.value
        prev_location = s.opening_balance.location
        for t in s.transactions:
            if t.balance is None:
                previous = previous + t.amount  # balance not printed on this line
                continue
            lines_checked += 1
            expected = previous + t.amount
            diff = t.balance - expected
            if abs(diff) > TOLERANCE:
                evidence.append(Evidence.at(
                    "balance_mismatch", doc.file, [prev_location, t.location], flag=True, focus=1,
                    numbers={"previous_balance": previous, "paid_in": t.paid_in or Decimal(0),
                             "paid_out": t.paid_out or Decimal(0), "expected": expected,
                             "shown": t.balance, "difference": diff},
                    details={"date": t.date.isoformat()},
                ))
            previous, prev_location = t.balance, t.location

        money_in = sum((t.paid_in or Decimal(0) for t in s.transactions), Decimal(0))
        money_out = sum((t.paid_out or Decimal(0) for t in s.transactions), Decimal(0))
        if s.closing_balance is not None:
            expected_close = s.opening_balance.value + money_in - money_out
            diff = s.closing_balance.value - expected_close
            if abs(diff) > TOLERANCE:
                evidence.append(Evidence.at(
                    "summary_mismatch", doc.file, [s.closing_balance.location], flag=True,
                    numbers={"opening": s.opening_balance.value, "money_in": money_in,
                             "money_out": money_out, "expected": expected_close,
                             "shown": s.closing_balance.value, "difference": diff},
                ))

        evidence += font_changes(doc, s)
        if (signal := modified_after(doc, s.period_end)) is not None:
            evidence.append(signal)

    # Consecutive statements: one month's closing balance should be the next month's opening.
    for (doc_a, a), (doc_b, b) in zip(stmts, stmts[1:], strict=False):
        same_account = (a.account_number or "") == (b.account_number or "")
        close_a = closing_balance(a)
        if same_account and b.period_start - a.period_end == timedelta(days=1) and close_a is not None:
            diff = b.opening_balance.value - close_a
            if abs(diff) > TOLERANCE:
                evidence.append(Evidence.at(
                    "statements_dont_join", doc_b.file, [b.opening_balance.location], flag=True,
                    numbers={"previous_closing": close_a, "opening": b.opening_balance.value,
                             "difference": diff},
                    details={"previous_file": doc_a.file},
                ))

    if not evidence:
        return CheckResult(
            id="balances_add_up", number=1, status="pass",
            title="Balances add up",
            explanation=f"All {lines_checked} running balances across "
                        f"{plural(len(stmts), 'statement')} follow correctly from the line before.",
        )
    return CheckResult(id="balances_add_up", number=1, status="flag",
                       title=_flag_title(evidence), explanation=_flag_explanation(evidence),
                       evidence=evidence)


def _flag_title(evidence: list[Evidence]) -> str:
    kinds = {e.kind for e in evidence}
    if kinds & {"balance_mismatch", "summary_mismatch", "statements_dont_join"}:
        return "Some balances don't add up"
    return "The statement file shows signs of editing"


def _flag_explanation(evidence: list[Evidence]) -> str:
    mismatches = [e for e in evidence if e.kind == "balance_mismatch"]
    fonts = [e for e in evidence if e.kind == "font_change"]
    edits = [e for e in evidence if e.kind == "modified_after_period"]
    if mismatches:
        first = mismatches[0].numbers
        text = (f"{plural(len(mismatches), 'balance')} didn't follow from the line before: "
                f"{gbp(first['shown'])} is shown where we calculated {gbp(first['expected'])}, "
                f"a difference of {gbp(first['difference'])}")
        if fonts:
            text += (f", and {plural(len(fonts), 'line')} on the same statements "
                     f"{'uses' if len(fonts) == 1 else 'use'} a different font")
        return text + "."
    summary = [e for e in evidence if e.kind in ("summary_mismatch", "statements_dont_join")]
    if summary:
        n = summary[0].numbers
        return (f"The statement totals don't agree: {gbp(n.get('shown', n.get('opening')))} is shown "
                f"where we expected {gbp(n.get('expected', n.get('previous_closing')))}.")
    if fonts:
        return (f"The balances add up, but {plural(len(fonts), 'transaction line')} "
                f"{'uses' if len(fonts) == 1 else 'use'} a different font from the rest of the page, "
                "which can be a sign of editing.")
    return (f"The balances add up, but {plural(len(edits), 'statement file')} "
            f"{'was' if len(edits) == 1 else 'were'} changed after the statement period ended "
            f"(last change {edits[0].details['modified']}), which can be a sign of editing.")


__all__ = ["check_balances", "long_date"]
