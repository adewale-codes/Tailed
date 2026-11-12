"""Shared helpers for the checks."""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.checks.models import DocumentInput
from app.reader.models import BankStatement, Payslip

PENNY = Decimal("0.01")


def money(x: Decimal) -> Decimal:
    return x.quantize(PENNY, ROUND_HALF_UP)


def gbp(x: Decimal) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}£{abs(money(x)):,.2f}"


def pct(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.1"), ROUND_HALF_UP)


def long_date(d: date) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"


def plural(n: int, word: str, plural_word: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural_word or word + 's')}"


def statements(docs: list[DocumentInput]) -> list[tuple[DocumentInput, BankStatement]]:
    found = [(d, d.result.statement) for d in docs if d.result.statement is not None]
    return sorted(found, key=lambda pair: pair[1].period_start)


def payslips(docs: list[DocumentInput]) -> list[tuple[DocumentInput, Payslip]]:
    found = [(d, d.result.payslip) for d in docs if d.result.payslip is not None]
    return sorted(found, key=lambda pair: pair[1].pay_date.value)


def closing_balance(s: BankStatement) -> Decimal | None:
    if s.closing_balance is not None:
        return s.closing_balance.value
    with_balance = [t.balance for t in s.transactions if t.balance is not None]
    return with_balance[-1] if with_balance else None


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

TITLES = {"MR", "MRS", "MS", "MISS", "MX", "DR", "PROF", "SIR", "DAME", "REV"}
COMPANY_SUFFIXES = {"LTD", "LIMITED", "PLC", "LLP", "UK", "THE", "CO", "COMPANY", "GROUP"}


def name_tokens(name: str) -> list[str]:
    tokens = re.sub(r"[^A-Z\s'-]", " ", name.upper()).replace("'", "").replace("-", " ").split()
    return [t for t in tokens if t not in TITLES]


def company_tokens(name: str) -> list[str]:
    tokens = re.sub(r"[^A-Z0-9\s]", " ", name.upper().replace("&", " AND ")).split()
    return [t for t in tokens if t not in COMPANY_SUFFIXES]


def tokens_overlap(a: str, b: str, min_len: int = 5) -> bool:
    """True if two words match, allowing bank truncation ('LOGISTI' vs 'LOGISTICS')."""
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return len(short) >= min_len and long_.startswith(short)
