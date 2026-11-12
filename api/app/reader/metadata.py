"""PDF file metadata (creator, producer, dates) via PyMuPDF."""

import re
from datetime import UTC, datetime, timedelta, timezone

import pymupdf

from app.reader.models import FileMetadata

_PDF_DATE = re.compile(
    r"^D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:([Zz+\-])(\d{2})?'?(\d{2})?'?)?"
)


def parse_pdf_date(value: str | None) -> datetime | None:
    if not value:
        return None
    m = _PDF_DATE.match(value.strip())
    if not m:
        return None
    year, month, day, hour, minute, second, sign, tzh, tzm = m.groups()
    try:
        dt = datetime(int(year), int(month or 1), int(day or 1), int(hour or 0), int(minute or 0),
                      int(second or 0))
    except ValueError:
        return None
    if sign in ("+", "-"):
        offset = timedelta(hours=int(tzh or 0), minutes=int(tzm or 0))
        return dt.replace(tzinfo=timezone(offset if sign == "+" else -offset))
    if sign in ("Z", "z"):
        return dt.replace(tzinfo=UTC)
    return dt


def read_metadata(doc: pymupdf.Document) -> FileMetadata:
    meta = doc.metadata or {}
    return FileMetadata(
        creator=meta.get("creator") or None,
        producer=meta.get("producer") or None,
        creation_date=parse_pdf_date(meta.get("creationDate")),
        modification_date=parse_pdf_date(meta.get("modDate")),
    )
