"""Words and lines from a text PDF, with positions and fonts (via pdfplumber)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pdfplumber

from app.reader.models import BBox, Location

LINE_TOLERANCE = 2.5  # points: words whose tops differ by less than this share a line

AMOUNT_RE = re.compile(r"^\(?-?£?-?[\d,]*\d\.\d{2}\)?(CR|DR)?$", re.IGNORECASE)


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    font: str
    size: float
    page: int

    @property
    def mid_y(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass
class Line:
    page: int
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def top(self) -> float:
        return min(w.top for w in self.words)

    @property
    def bottom(self) -> float:
        return max(w.bottom for w in self.words)

    @property
    def fonts(self) -> list[str]:
        return sorted({clean_font(w.font) for w in self.words})

    @property
    def main_font(self) -> tuple[str, float]:
        counts = Counter((clean_font(w.font), round(w.size, 2)) for w in self.words)
        return counts.most_common(1)[0][0]

    def location(self) -> Location:
        return location_of(self.words)


def clean_font(name: str) -> str:
    """Drop the random subset prefix, e.g. 'AAAAAA+Vera' -> 'Vera'."""
    return name.split("+", 1)[1] if re.match(r"^[A-Z]{6}\+", name) else name


def location_of(words: list[Word]) -> Location:
    return Location(
        page=words[0].page,
        bbox=BBox(
            x0=round(min(w.x0 for w in words), 2),
            top=round(min(w.top for w in words), 2),
            x1=round(max(w.x1 for w in words), 2),
            bottom=round(max(w.bottom for w in words), 2),
        ),
    )


def page_words(page: pdfplumber.page.Page) -> list[Word]:
    raw = page.extract_words(extra_attrs=["fontname", "size"], keep_blank_chars=False)
    return [
        Word(w["text"], w["x0"], w["x1"], w["top"], w["bottom"], w["fontname"], w["size"],
             page.page_number)
        for w in raw
    ]


def group_lines(words: list[Word]) -> list[Line]:
    lines: list[Line] = []
    for w in sorted(words, key=lambda w: (w.page, round(w.top, 1), w.x0)):
        if lines and lines[-1].page == w.page and abs(lines[-1].words[0].top - w.top) <= LINE_TOLERANCE:
            lines[-1].words.append(w)
        else:
            lines.append(Line(page=w.page, words=[w]))
    for line in lines:
        line.words.sort(key=lambda w: w.x0)
    return lines


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------


def find_phrase(lines: list[Line], phrase: str, start: int = 0) -> tuple[int, int, int] | None:
    """Find a phrase (case-insensitive, word by word). Returns (line index, first word, last word)."""
    target = phrase.lower().split()
    n = len(target)
    for li in range(start, len(lines)):
        texts = [w.text.lower().rstrip(":") for w in lines[li].words]
        for i in range(len(texts) - n + 1):
            if texts[i:i + n] == target:
                return li, i, i + n - 1
    return None


def is_amount(text: str) -> bool:
    return bool(AMOUNT_RE.match(text))


def parse_amount(text: str) -> Decimal | None:
    t = text.strip().upper()
    negative = t.startswith("(") or "-" in t or t.endswith("DR")
    t = re.sub(r"[£,()\-]|CR$|DR$", "", t)
    try:
        value = Decimal(t)
    except InvalidOperation:
        return None
    return -value if negative else value


def words_right_of(words: list[Word], label: list[Word], max_gap: float = 400) -> list[Word]:
    """Words on the same visual row as `label`, to its right, nearest first."""
    top = min(w.top for w in label)
    bottom = max(w.bottom for w in label)
    x1 = max(w.x1 for w in label)
    page = label[0].page
    row = [
        w for w in words
        if w.page == page and w.x0 > x1 and w.x0 - x1 < max_gap
        and top - 6 <= w.mid_y <= bottom + 6
    ]
    return sorted(row, key=lambda w: w.x0)


def amount_right_of(words: list[Word], label: list[Word]) -> tuple[Decimal, Word] | None:
    for w in words_right_of(words, label):
        if is_amount(w.text):
            value = parse_amount(w.text)
            if value is not None:
                return value, w
    return None


def words_below(lines: list[Line], label: list[Word], max_drop: float = 28,
                tolerance: float = 4) -> list[Word]:
    """The value printed under a label in a grid cell: next line, starting at the label's x."""
    x0 = label[0].x0
    bottom = max(w.bottom for w in label)
    page = label[0].page
    for line in lines:
        if line.page != page or line.top <= bottom or line.top - bottom > max_drop:
            continue
        start = [i for i, w in enumerate(line.words) if abs(w.x0 - x0) <= tolerance]
        if not start:
            continue
        i = start[0]
        picked = [line.words[i]]
        for w in line.words[i + 1:]:
            if w.x0 - picked[-1].x1 > 8:  # a bigger gap means the next cell
                break
            picked.append(w)
        return picked
    return []


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

DATE_FORMATS = ["%d %b %y", "%d %b %Y", "%d %B %Y", "%d %B %y", "%d/%m/%Y", "%d/%m/%y",
                "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"]


def parse_date(text: str) -> date | None:
    t = re.sub(r"\s+", " ", text.strip().rstrip(",."))
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def leading_date(words: list[Word]) -> tuple[date, int] | None:
    """A date at the start of a line, e.g. '01 Aug 26' or '01/08/2026'. Returns (date, n words)."""
    for n in (3, 2, 1):
        if len(words) >= n:
            d = parse_date(" ".join(w.text for w in words[:n]))
            if d:
                return d, n
    return None
