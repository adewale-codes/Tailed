"""PDF editing signals, used as supporting evidence inside the relevant check.

- A transaction line whose font differs from the rest of its page.
- A file modified after it was created, at a date after the period it covers.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.checks.models import DocumentInput, Evidence
from app.checks.util import long_date
from app.reader.models import BankStatement

# Gap between creation and modification that we ignore (some systems stamp them separately).
MODIFICATION_GRACE = timedelta(hours=1)
MIN_LINES_FOR_FONT_CHECK = 5
HOUSE_STYLE_SHARE = 0.8  # the main font must cover this share of a page's lines before we judge


def _size(value: float | None) -> Decimal:
    return Decimal(str(round(value or 0, 1)))


def font_changes(doc: DocumentInput, statement: BankStatement) -> list[Evidence]:
    found: list[Evidence] = []
    by_page: dict[int, list] = {}
    for t in statement.transactions:
        if t.font_name:  # AI-read documents have no font information
            by_page.setdefault(t.location.page, []).append(t)
    for page, txs in by_page.items():
        if len(txs) < MIN_LINES_FOR_FONT_CHECK:
            continue
        styles = Counter((t.font_name, _size(t.font_size)) for t in txs)
        (main_font, main_size), main_count = styles.most_common(1)[0]
        if main_count < len(txs) * HOUSE_STYLE_SHARE:  # no clear house style: don't guess
            continue
        for t in txs:
            if (t.font_name, _size(t.font_size)) != (main_font, main_size):
                found.append(Evidence.at(
                    "font_change", doc.file, [t.location], flag=True,
                    numbers={"font_size": _size(t.font_size), "page_font_size": main_size},
                    details={"date": t.date.isoformat(), "font": t.font_name or "",
                             "page_font": main_font or "", "page": str(page)},
                ))
    return found


def modified_after(doc: DocumentInput, period_end: date) -> Evidence | None:
    meta = doc.result.metadata
    created, modified = meta.creation_date, meta.modification_date
    if modified is None:
        return None
    if created is not None and _utc(modified) - _utc(created) <= MODIFICATION_GRACE:
        return None
    if modified.date() <= period_end:
        return None
    return Evidence.at(
        "modified_after_period", doc.file, [], flag=True,
        details={
            "modified": long_date(modified.date()),
            "created": long_date(created.date()) if created else "unknown",
            "period_end": long_date(period_end),
            "producer": meta.producer or "unknown",
            "creator": meta.creator or "unknown",
        },
    )


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) - (dt.utcoffset() or timedelta(0))
