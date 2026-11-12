"""Deterministic payslip parser for text PDFs.

Reads labelled values ("Total gross pay  4,190.00", "NET PAY  £3,100.64") and the rows of the
Deductions table. Raises ParseFailed when the required fields can't be found.
"""

from __future__ import annotations

import re

from app.reader.errors import ParseFailed
from app.reader.models import Deduction, LocatedAmount, LocatedDate, LocatedText, Payslip
from app.reader.pdf_text import (
    Line,
    Word,
    amount_right_of,
    find_phrase,
    is_amount,
    location_of,
    parse_amount,
    parse_date,
    words_below,
)

GROSS_LABELS = ["Total gross pay", "Gross pay", "Total gross", "Gross"]
NET_LABELS = ["Net pay", "Net payment", "Take home pay"]
EMPLOYEE_LABELS = ["Employee name", "Employee"]
EMPLOYER_LABELS = ["Employer name", "Company name"]


def _label_words(lines: list[Line], label: str) -> list[Word] | None:
    hit = find_phrase(lines, label)
    if not hit:
        return None
    li, first, last = hit
    return lines[li].words[first:last + 1]


def _labelled_amount(lines: list[Line], words: list[Word], labels: list[str]) -> LocatedAmount | None:
    for label in labels:
        lw = _label_words(lines, label)
        if not lw:
            continue
        found = amount_right_of(words, lw)
        if found:
            value, w = found
            return LocatedAmount(value=value, location=location_of(lw + [w]))
        below = words_below(lines, lw)
        if below and is_amount(below[0].text):
            value = parse_amount(below[0].text)
            if value is not None:
                return LocatedAmount(value=value, location=location_of(below))
    return None


def _labelled_text(lines: list[Line], labels: list[str]) -> LocatedText | None:
    for label in labels:
        lw = _label_words(lines, label)
        if not lw:
            continue
        below = words_below(lines, lw)
        if below:
            return LocatedText(value=" ".join(w.text for w in below), location=location_of(below))
    return None


def _employer_from_header(lines: list[Line]) -> LocatedText | None:
    """Largest text at the top of page 1, ignoring the word 'payslip'."""
    top = [ln for ln in lines if ln.page == 1 and ln.top < 140]
    best: list[Word] = []
    best_size = 0.0
    for ln in top:
        words = [w for w in ln.words if not re.fullmatch(r"pay\s?slip", w.text, re.IGNORECASE)]
        if not words:
            continue
        size = max(w.size for w in words)
        if size > best_size:
            best_size, best = size, [w for w in words if w.size == size]
    if not best:
        return None
    return LocatedText(value=" ".join(w.text for w in best), location=location_of(best))


def _deductions(lines: list[Line]) -> list[tuple[str, LocatedAmount]]:
    """Rows of the Deductions table, between its header and 'Total deductions'."""
    header = find_phrase(lines, "Deductions")
    if not header:
        return []
    hi, first, _ = header
    left = lines[hi].words[first].x0 - 10
    end = find_phrase(lines, "Total deductions", start=hi + 1)
    end_index = end[0] if end else min(hi + 12, len(lines))
    rows: list[tuple[str, LocatedAmount]] = []
    for line in lines[hi + 1:end_index]:
        cell = [w for w in line.words if w.x0 >= left]
        amounts = [w for w in cell if is_amount(w.text)]
        label_words = [w for w in cell if not is_amount(w.text)]
        if not amounts or not label_words:
            continue
        value = parse_amount(amounts[-1].text)
        if value is None:
            continue
        rows.append((" ".join(w.text for w in label_words),
                     LocatedAmount(value=value, location=location_of(cell))))
    return rows


def parse_payslip(lines: list[Line]) -> Payslip:
    words = [w for ln in lines for w in ln.words]
    text = "\n".join(ln.text for ln in lines)

    pay_date = None
    for line in lines:
        m = re.search(r"(?:pay date|date paid|payment date):?\s+(.+)$", line.text, re.IGNORECASE)
        if m and (d := parse_date(m.group(1))):
            pay_date = LocatedDate(value=d, location=line.location())
            break
    if pay_date is None:
        raise ParseFailed("pay date not found")

    gross = _labelled_amount(lines, words, GROSS_LABELS)
    net = _labelled_amount(lines, words, NET_LABELS)
    if gross is None or net is None:
        raise ParseFailed("gross or net pay not found")

    tax = ni = pension = None
    others: list[Deduction] = []
    for label, amount in _deductions(lines):
        low = label.lower()
        if "pension" in low:
            pension = amount
        elif "national insurance" in low or re.search(r"\bni\b|\bnic\b", low):
            ni = amount
        elif "tax" in low:
            tax = amount
        else:
            others.append(Deduction(label=label, value=amount.value, location=amount.location))

    inline_code = re.search(r"Tax code:?\s+([0-9]{1,5}[A-Z]{1,2}|BR|D0|D1|NT|0T|K\d+)\b", text,
                            re.IGNORECASE)
    code = _labelled_text(lines, ["Tax code"])

    return Payslip(
        employee_name=_labelled_text(lines, EMPLOYEE_LABELS),
        employer_name=_labelled_text(lines, EMPLOYER_LABELS) or _employer_from_header(lines),
        pay_date=pay_date,
        gross_pay=gross,
        income_tax=tax,
        national_insurance=ni,
        pension=pension,
        other_deductions=others,
        net_pay=net,
        tax_code=code.value if code else (inline_code.group(1) if inline_code else None),
    )
