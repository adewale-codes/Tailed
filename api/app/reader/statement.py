"""Deterministic bank statement parser for text PDFs with a column table.

Finds the transaction table header ("Date ... Paid out ... Paid in ... Balance"), uses its
column positions to assign each amount on a row, and reads the account details and summary
around it. Raises ParseFailed when the layout isn't recognised, so the caller can use the AI path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from app.reader.errors import ParseFailed
from app.reader.models import BankStatement, LocatedAmount, LocatedText, Transaction
from app.reader.pdf_text import (
    Line,
    Word,
    find_phrase,
    is_amount,
    leading_date,
    location_of,
    parse_amount,
    parse_date,
    words_below,
)

OUT_WORDS = ("paid out", "money out", "debit", "debits", "withdrawals", "payments out", "out")
IN_WORDS = ("paid in", "money in", "credit", "credits", "deposits", "payments in", "in")
COLUMN_MATCH_TOLERANCE = 30  # points between an amount's right edge and its column's right edge

_PERIOD_RE = re.compile(
    r"(\d{1,2}(?:st|nd|rd|th)? [A-Za-z]+ \d{4})\s+(?:to|-|–)\s+(\d{1,2}(?:st|nd|rd|th)? [A-Za-z]+ \d{4})"
)


@dataclass
class Columns:
    paid_out_x1: float
    paid_in_x1: float
    balance_x1: float
    type_x0: float | None
    description_x0: float | None


def _phrases(line: Line, gap: float = 9) -> list[list[Word]]:
    groups: list[list[Word]] = []
    for w in line.words:
        if groups and w.x0 - groups[-1][-1].x1 <= gap:
            groups[-1].append(w)
        else:
            groups.append([w])
    return groups


def _phrase_text(words: list[Word]) -> str:
    return re.sub(r"\s*\(.*?\)\s*", " ", " ".join(w.text for w in words)).strip().lower()


def detect_columns(line: Line) -> Columns | None:
    phrases = _phrases(line)
    texts = [_phrase_text(p) for p in phrases]
    if not any(t.startswith("balance") for t in texts) or not any(t == "date" for t in texts):
        return None
    out_x1 = in_x1 = bal_x1 = None
    type_x0 = desc_x0 = None
    for words, text in zip(phrases, texts, strict=True):
        x1 = max(w.x1 for w in words)
        if text.startswith("balance"):
            bal_x1 = x1
        elif text in OUT_WORDS:
            out_x1 = x1
        elif text in IN_WORDS:
            in_x1 = x1
        elif text == "type":
            type_x0 = words[0].x0
        elif text.startswith("description") or text.startswith("details") or "details" in text:
            desc_x0 = words[0].x0
    if out_x1 is None or in_x1 is None or bal_x1 is None:
        return None
    return Columns(out_x1, in_x1, bal_x1, type_x0, desc_x0)


def _assign_amount(word: Word, cols: Columns) -> str | None:
    options = {"paid_out": cols.paid_out_x1, "paid_in": cols.paid_in_x1, "balance": cols.balance_x1}
    name, x1 = min(options.items(), key=lambda kv: abs(word.x1 - kv[1]))
    return name if abs(word.x1 - x1) <= COLUMN_MATCH_TOLERANCE else None


def _label_value(lines: list[Line], label: str) -> tuple[str, list[Word]] | None:
    """Value printed after 'Label' on the same line, separated by a column gap."""
    hit = find_phrase(lines, label)
    if not hit:
        return None
    li, _, last = hit
    rest = lines[li].words[last + 1:]
    if not rest:
        return None
    return " ".join(w.text for w in rest), rest


def _amount_below(lines: list[Line], label: str) -> LocatedAmount | None:
    hit = find_phrase(lines, label)
    if not hit:
        return None
    li, first, last = hit
    below = words_below(lines, lines[li].words[first:last + 1])
    if below and is_amount(below[0].text):
        value = parse_amount(below[0].text)
        if value is not None:
            return LocatedAmount(value=value, location=location_of(below))
    return None


def _long_date(d) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"


def parse_statement(lines: list[Line]) -> BankStatement:
    text = "\n".join(line.text for line in lines)

    # Account details
    holder = None
    found = _label_value(lines, "Account name")
    if found:
        value, words = found
        holder = LocatedText(value=value, location=location_of(words))
    sort_code = re.search(r"Sort code:?\s+(\d{2}-\d{2}-\d{2})", text, re.IGNORECASE)
    account_no = re.search(r"Account (?:number|no\.?):?\s+(\d{6,10})", text, re.IGNORECASE)

    period = _PERIOD_RE.search(text)
    if not period:
        raise ParseFailed("statement period not found")
    period_start, period_end = parse_date(period.group(1)), parse_date(period.group(2))
    if not period_start or not period_end:
        raise ParseFailed("statement period unreadable")

    # Transaction table, which may repeat its header on every page
    transactions: list[Transaction] = []
    opening: LocatedAmount | None = None
    closing: LocatedAmount | None = None
    cols: Columns | None = None
    current_page = 0
    for line in lines:
        if line.page != current_page:
            current_page, cols = line.page, None
        detected = detect_columns(line)
        if detected:
            cols = detected
            continue
        if cols is None:
            continue

        amounts: dict[str, tuple[Decimal, Word]] = {}
        others: list[Word] = []
        for w in line.words:
            column = _assign_amount(w, cols) if is_amount(w.text) else None
            value = parse_amount(w.text) if column else None
            if column and value is not None:
                amounts[column] = (value, w)
            else:
                others.append(w)

        upper = line.text.upper()
        if "BROUGHT FORWARD" in upper or "OPENING BALANCE" in upper:
            if "balance" in amounts:
                value, w = amounts["balance"]
                opening = LocatedAmount(value=value, location=line.location())
            continue
        if "CARRIED FORWARD" in upper or "CLOSING BALANCE" in upper:
            if "balance" in amounts:
                value, w = amounts["balance"]
                closing = LocatedAmount(value=value, location=line.location())
            continue

        dated = leading_date(line.words)
        if not dated or not ({"paid_in", "paid_out"} & amounts.keys()):
            continue
        tx_date, n_date_words = dated
        rest = others[n_date_words:]
        tx_type = None
        if cols.type_x0 is not None and rest:
            limit = cols.description_x0 if cols.description_x0 is not None else cols.type_x0 + 40
            if cols.type_x0 - 3 <= rest[0].x0 < limit - 3:
                tx_type, rest = rest[0].text, rest[1:]
        font, size = line.main_font
        transactions.append(Transaction(
            date=tx_date,
            description=" ".join(w.text for w in rest),
            type=tx_type,
            paid_in=amounts["paid_in"][0] if "paid_in" in amounts else None,
            paid_out=amounts["paid_out"][0] if "paid_out" in amounts else None,
            balance=amounts["balance"][0] if "balance" in amounts else None,
            location=line.location(),
            font_name=font,
            font_size=size,
            fonts=line.fonts,
        ))

    if not transactions:
        raise ParseFailed("no transactions found")

    # Summary box values (label above value)
    if opening is None:
        opening = _amount_below(lines, f"Balance on {_long_date(period_start)}")
    if opening is None:
        raise ParseFailed("opening balance not found")
    summary_closing = _amount_below(lines, f"Balance on {_long_date(period_end)}")

    return BankStatement(
        account_holder=holder,
        bank_name=_bank_name(lines),
        sort_code=sort_code.group(1) if sort_code else None,
        account_number=account_no.group(1) if account_no else None,
        period_start=period_start,
        period_end=period_end,
        opening_balance=opening,
        closing_balance=summary_closing or closing,
        summary_money_in=_amount_below(lines, "Total money in") or _amount_below(lines, "Money in"),
        summary_money_out=_amount_below(lines, "Total money out") or _amount_below(lines, "Money out"),
        transactions=transactions,
    )


def _bank_name(lines: list[Line]) -> str | None:
    """The largest text near the top of page 1 that mentions 'bank' or 'building society'."""
    candidates = [ln for ln in lines if ln.page == 1 and ln.top < 120
                  and re.search(r"\bbank\b|building society", ln.text, re.IGNORECASE)]
    if not candidates:
        return None
    best = max(candidates, key=lambda ln: max(w.size for w in ln.words))
    words = [w for w in best.words if w.size == max(x.size for x in best.words)]
    return " ".join(w.text for w in words)
