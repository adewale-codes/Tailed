"""Generate synthetic test documents for Tallied.

Everything here is fictional: "Northgate Bank", the employers, the people and the
account numbers. NI numbers use the "QQ" prefix, which HMRC never issues.

Usage (from the repo root):
    cd api && uv run python ../fixtures/generate.py            # writes into fixtures/
    cd api && uv run python ../fixtures/generate.py --out DIR  # writes into DIR

Each case is written to its own folder with an expected.json describing the right answer.
The output is deterministic: the same seed always produces the same transactions.
"""

from __future__ import annotations

import argparse
import calendar
import json
import random
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

# ---------------------------------------------------------------------------
# Money helpers
# ---------------------------------------------------------------------------

CENT = Decimal("0.01")


def money(x: float | int | str | Decimal) -> Decimal:
    return Decimal(str(x)).quantize(CENT, ROUND_HALF_UP)


def fmt(x: Decimal) -> str:
    return f"{x:,.2f}"


def gbp(x: Decimal) -> str:
    return f"£{x:,.2f}"


# ---------------------------------------------------------------------------
# UK PAYE / NI model (2026/27, England, tax code 1257L, NI category A, monthly)
# Pension: 5% employee contribution under relief at source, so it does not
# reduce taxable pay. These are the rules the payslip check will be judged on.
# ---------------------------------------------------------------------------

MONTHLY_PERSONAL_ALLOWANCE = Decimal("1047.50")  # 12,570 / 12
MONTHLY_BASIC_BAND = Decimal("3141.67")  # 37,700 / 12
NI_PRIMARY_THRESHOLD = Decimal("1048")  # monthly
NI_UPPER_EARNINGS_LIMIT = Decimal("4189")  # monthly
PENSION_RATE = Decimal("0.05")
EMPLOYER_PENSION_RATE = Decimal("0.03")


def expected_tax(gross: Decimal) -> Decimal:
    taxable = max(Decimal(0), gross - MONTHLY_PERSONAL_ALLOWANCE)
    basic = min(taxable, MONTHLY_BASIC_BAND)
    higher = max(Decimal(0), taxable - MONTHLY_BASIC_BAND)
    return money(basic * Decimal("0.20") + higher * Decimal("0.40"))


def expected_ni(gross: Decimal) -> Decimal:
    main = max(Decimal(0), min(gross, NI_UPPER_EARNINGS_LIMIT) - NI_PRIMARY_THRESHOLD)
    upper = max(Decimal(0), gross - NI_UPPER_EARNINGS_LIMIT)
    return money(main * Decimal("0.08") + upper * Decimal("0.02"))


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Person:
    title: str
    full_name: str
    address: tuple[str, ...]

    @property
    def display(self) -> str:
        return f"{self.title} {self.full_name}"


@dataclass(frozen=True)
class Employer:
    name: str
    address: tuple[str, ...]
    paye_ref: str
    bank_reference: str  # how the salary credit appears on the statement


@dataclass
class Tx:
    day: date
    type: str
    description: str
    paid_out: Decimal | None = None
    paid_in: Decimal | None = None
    balance: Decimal = Decimal(0)
    font: str | None = None  # override font for this row (used to simulate an edit)


@dataclass
class Statement:
    year: int
    month: int
    opening: Decimal
    txs: list[Tx]
    closing_shown: Decimal = Decimal(0)

    @property
    def period_start(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def period_end(self) -> date:
        return date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])

    @property
    def issue_date(self) -> date:
        return self.period_end + timedelta(days=1)

    @property
    def money_in(self) -> Decimal:
        return sum((t.paid_in or Decimal(0) for t in self.txs), Decimal(0))

    @property
    def money_out(self) -> Decimal:
        return sum((t.paid_out or Decimal(0) for t in self.txs), Decimal(0))

    @property
    def true_closing(self) -> Decimal:
        return self.opening + self.money_in - self.money_out


@dataclass
class Payslip:
    employee: Person
    employer: Employer
    pay_date: date
    tax_period: int  # month number in the tax year (April = 1)
    gross: Decimal
    tax: Decimal
    ni: Decimal
    pension: Decimal
    employee_number: str = "BW-04417"
    ni_number: str = "QQ 12 34 56 C"
    tax_code: str = "1257L"
    account_last4: str = "9264"

    @property
    def total_deductions(self) -> Decimal:
        return self.tax + self.ni + self.pension

    @property
    def net(self) -> Decimal:
        return self.gross - self.total_deductions

    @property
    def employer_pension(self) -> Decimal:
        return money(self.gross * EMPLOYER_PENSION_RATE)


@dataclass
class Account:
    sort_code: str = "60-91-23"
    number: str = "71839264"
    product: str = "Classic Current Account"


@dataclass
class Case:
    name: str
    description: str
    statements: list[Statement]
    payslips: list[Payslip]
    holder: Person
    expected_verdict: str
    expected_checks: dict[str, str]
    expected_flags: list[dict]
    metadata_overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Fictional people and employers
# ---------------------------------------------------------------------------

APPLICANT = Person(
    title="Mr",
    full_name="Jordan A Hayes",
    address=("Flat 3", "27 Brudenell Grove", "Leeds", "LS6 1HR"),
)
OTHER_PERSON = Person(
    title="Mr",
    full_name="Daniel K Okafor",
    address=("14 Cardigan Lane", "Leeds", "LS4 2LE"),
)
EMPLOYER = Employer(
    name="Brightwater Logistics Ltd",
    address=("Unit 4, Kirkstall Trade Park", "Leeds", "LS5 3BT"),
    paye_ref="475/BW38216",
    bank_reference="BRIGHTWATER LOGISTI SALARY",
)

ACCOUNT = Account()
MONTHS = [(2026, 8), (2026, 9), (2026, 10)]
PAY_DAY = 27
OPENING_BALANCE_AUG = money("2486.73")

# Gross chosen so that net pay comes out at exactly £2,650.00 under the model above.
GENUINE_GROSS = money("3517.40")

# ---------------------------------------------------------------------------
# Transaction generation
# ---------------------------------------------------------------------------

VARIABLE_SPEND = [
    # (count per month, type, descriptions, low, high)
    (9, "DEB", ["TESCO STORES 2871 LEEDS", "SAINSBURYS S/MKTS LEEDS", "ALDI 72 LEEDS",
                "CO-OP GROUP 070417 LEEDS"], 9.40, 68.90),
    (11, "DEB", ["PRET A MANGER LEEDS", "GREGGS 1823 LEEDS", "COSTA COFFEE 4419 LEEDS"],
     3.20, 8.90),
    (8, "DEB", ["FIRST BUS WEST YORKS", "NORTHERN TRAINS LTD"], 2.50, 14.60),
    (3, "DEB", ["AMAZON.CO.UK*{code}"], 7.99, 42.50),
    (4, "DEB", ["NANDOS LEEDS TRINITY", "THE BREWERY TAP LEEDS", "WAGAMAMA LEEDS"],
     18.00, 56.00),
    (2, "DEB", ["DELIVEROO.CO.UK"], 14.00, 31.00),
    (2, "DEB", ["BOOTS 1142 LEEDS", "TK MAXX LEEDS WHITE ROSE"], 4.00, 34.00),
]


def month_transactions(year: int, month: int, salary_net: Decimal) -> list[Tx]:
    # Seed depends only on the month, so every case shares the same everyday spending.
    rng = random.Random(year * 100 + month)
    last_day = calendar.monthrange(year, month)[1]
    txs: list[Tx] = []

    def add(day: int, typ: str, desc: str, out=None, inn=None) -> None:
        txs.append(Tx(
            day=date(year, month, day),
            type=typ,
            description=desc,
            paid_out=money(out) if out is not None else None,
            paid_in=money(inn) if inn is not None else None,
        ))

    # Fixed outgoings and income.
    add(1, "SO", "R HOLLOWAY LETTINGS RENT", out="950.00")
    add(1, "DD", "LEEDS CITY COUNCIL CTAX", out="142.00")
    add(3, "DD", "OCTOPUS ENERGY", out="78.40")
    add(5, "DD", "YORKSHIRE WATER", out="38.20")
    add(8, "DD", "VODAFONE LTD", out="22.00")
    add(12, "DD", "PUREGYM LTD", out="24.99")
    add(15, "DEB", "NETFLIX.COM", out="10.99")
    add(19, "DEB", "SPOTIFY UK", out="11.99")
    add(20, "DD", "AVIVA INSURANCE LTD", out="18.45")
    add(PAY_DAY, "BGC", EMPLOYER.bank_reference, inn=salary_net)
    add(28, "FPO", "J HAYES SAVINGS 40118823", out="250.00")

    # Everyday spending.
    for count, typ, descs, low, high in VARIABLE_SPEND:
        for _ in range(count):
            desc = rng.choice(descs)
            if "{code}" in desc:
                code = "".join(rng.choice("ABCDEFGHJKLMNPRTUVWXYZ23456789") for _ in range(6))
                desc = desc.format(code=code)
            add(rng.randint(1, last_day), typ, desc, out=f"{rng.uniform(low, high):.2f}")

    # Cash and the odd payment from a friend.
    add(rng.randint(2, last_day), "CPT", "NORTHGATE HEADROW LEEDS", out=rng.choice(["40.00", "50.00"]))
    add(rng.randint(2, last_day), "FPI", rng.choice(["S PATEL DINNER", "M CLARKE TICKETS"]),
        inn=f"{rng.choice([18, 22.5, 25, 30]):.2f}")

    txs.sort(key=lambda t: t.day)  # stable: fixed items stay first within a day
    return txs


def build_statements(salary_net: Decimal) -> list[Statement]:
    statements = []
    opening = OPENING_BALANCE_AUG
    for year, month in MONTHS:
        txs = month_transactions(year, month, salary_net)
        balance = opening
        for t in txs:
            balance = balance + (t.paid_in or 0) - (t.paid_out or 0)
            t.balance = balance
        stmt = Statement(year, month, opening, txs, closing_shown=balance)
        statements.append(stmt)
        opening = balance
    return statements


def build_payslips(employee: Person, gross: Decimal, tax: Decimal | None = None,
                   ni: Decimal | None = None) -> list[Payslip]:
    """Payslips for September and October 2026 (tax months 6 and 7)."""
    tax = expected_tax(gross) if tax is None else tax
    ni = expected_ni(gross) if ni is None else ni
    pension = money(gross * PENSION_RATE)
    return [
        Payslip(employee, EMPLOYER, date(2026, 9, PAY_DAY), 6, gross, tax, ni, pension),
        Payslip(employee, EMPLOYER, date(2026, 10, PAY_DAY), 7, gross, tax, ni, pension),
    ]


# ---------------------------------------------------------------------------
# PDF rendering: statements
# ---------------------------------------------------------------------------

NAVY = HexColor("#13294B")
TEAL = HexColor("#1F7A8C")
GREY = HexColor("#5B6573")
LIGHT = HexColor("#F2F4F7")
RULE = HexColor("#C9CED6")

PAGE_W, PAGE_H = A4
LEFT, RIGHT = 40, PAGE_W - 40
ROW_H = 13
BOTTOM_LIMIT = 84
FIRST_PAGE_TABLE_TOP = 528
OTHER_PAGE_TABLE_TOP = 762

# Columns: x for left-aligned, right edge for right-aligned
COL_DATE, COL_TYPE, COL_DESC = 46, 104, 136
COL_OUT, COL_IN, COL_BAL = 400, 474, 550

BODY_FONT, BODY_SIZE = "Helvetica", 8.5
EDIT_FONT, EDIT_SIZE = "Vera", 8.3  # Bitstream Vera Sans ships with reportlab

_FONTS_REGISTERED = False


def register_fonts() -> None:
    global _FONTS_REGISTERED
    if not _FONTS_REGISTERED:
        pdfmetrics.registerFont(TTFont("Vera", "Vera.ttf"))
        _FONTS_REGISTERED = True


def d_short(d: date) -> str:
    return d.strftime("%d %b %y")


def d_long(d: date) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"


def rows_per_page(first: bool) -> int:
    top = FIRST_PAGE_TABLE_TOP if first else OTHER_PAGE_TABLE_TOP
    return int((top - ROW_H - BOTTOM_LIMIT) // ROW_H)


def paginate(rows: list) -> list[list]:
    pages, i, first = [], 0, True
    while i < len(rows):
        n = rows_per_page(first)
        pages.append(rows[i:i + n])
        i += n
        first = False
    return pages


def draw_logo(c: canvas.Canvas, x: float, y: float, size: float = 30) -> None:
    c.setFillColor(NAVY)
    c.roundRect(x, y, size, size, 4, stroke=0, fill=1)
    c.setFillColor(TEAL)
    c.rect(x, y, size, size * 0.18, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", size * 0.62)
    c.drawCentredString(x + size / 2, y + size * 0.3, "N")


def draw_table_header(c: canvas.Canvas, y: float) -> None:
    c.setFillColor(NAVY)
    c.rect(LEFT, y - 4, RIGHT - LEFT, 16, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(COL_DATE, y + 1, "Date")
    c.drawString(COL_TYPE, y + 1, "Type")
    c.drawString(COL_DESC, y + 1, "Description")
    c.drawRightString(COL_OUT, y + 1, "Paid out (£)")
    c.drawRightString(COL_IN, y + 1, "Paid in (£)")
    c.drawRightString(COL_BAL, y + 1, "Balance (£)")


def draw_statement_footer(c: canvas.Canvas, page: int, pages: int) -> None:
    c.setStrokeColor(RULE)
    c.setLineWidth(0.5)
    c.line(LEFT, 66, RIGHT, 66)
    c.setFillColor(GREY)
    c.setFont("Helvetica", 6.5)
    c.drawString(LEFT, 56, "Transaction types: BGC Bank Giro Credit  CPT Cashpoint  DD Direct Debit  "
                           "DEB Debit Card  FPI Faster Payment In  FPO Faster Payment Out  "
                           "SO Standing Order")
    c.drawString(LEFT, 47, "Northgate Bank plc. Registered office: 1 Northgate Square, Leeds LS1 4NB. "
                           "Registered in England and Wales no. 00000000.")
    c.drawString(LEFT, 38, "Northgate Bank is a fictional bank. This is a synthetic test document "
                           "created for Tallied and does not relate to any real person.")
    c.setFont("Helvetica", 7.5)
    c.drawRightString(RIGHT, 47, f"Page {page} of {pages}")


def draw_row(c: canvas.Canvas, y: float, row: Tx | tuple, shade: bool) -> None:
    if shade:
        c.setFillColor(LIGHT)
        c.rect(LEFT, y - 3.5, RIGHT - LEFT, ROW_H, stroke=0, fill=1)
    c.setFillColor(HexColor("#1B1F24"))
    if isinstance(row, tuple):  # ("BALANCE BROUGHT FORWARD", date, balance)
        label, d, bal = row
        c.setFont("Helvetica-Bold", BODY_SIZE)
        c.drawString(COL_DATE, y, d_short(d))
        c.drawString(COL_DESC, y, label)
        c.drawRightString(COL_BAL, y, fmt(bal))
        return
    if row.font:
        c.setFont(row.font, EDIT_SIZE)
    else:
        c.setFont(BODY_FONT, BODY_SIZE)
    c.drawString(COL_DATE, y, d_short(row.day))
    c.drawString(COL_TYPE, y, row.type)
    c.drawString(COL_DESC, y, row.description[:42])
    if row.paid_out is not None:
        c.drawRightString(COL_OUT, y, fmt(row.paid_out))
    if row.paid_in is not None:
        c.drawRightString(COL_IN, y, fmt(row.paid_in))
    c.drawRightString(COL_BAL, y, fmt(row.balance))


def render_statement(path: Path, stmt: Statement, holder: Person, account: Account) -> None:
    register_fonts()
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    c.setTitle(f"Statement {stmt.period_start:%B %Y}")
    c.setAuthor("Northgate Bank plc")

    rows: list = [("BALANCE BROUGHT FORWARD", stmt.period_start, stmt.opening)]
    rows += stmt.txs
    rows.append(("BALANCE CARRIED FORWARD", stmt.period_end, stmt.closing_shown))
    pages = paginate(rows)
    period = f"{d_long(stmt.period_start)} to {d_long(stmt.period_end)}"

    for p, page_rows in enumerate(pages, start=1):
        if p == 1:
            draw_logo(c, LEFT, 770, 34)
            c.setFillColor(NAVY)
            c.setFont("Helvetica-Bold", 17)
            c.drawString(LEFT + 44, 790, "Northgate Bank")
            c.setFillColor(GREY)
            c.setFont("Helvetica", 8.5)
            c.drawString(LEFT + 44, 776, "northgatebank.example  |  0300 555 0182")

            c.setFillColor(NAVY)
            c.setFont("Helvetica-Bold", 12)
            c.drawRightString(RIGHT, 792, account.product)
            c.setFillColor(GREY)
            c.setFont("Helvetica", 8.5)
            c.drawRightString(RIGHT, 778, f"Statement date: {d_long(stmt.issue_date)}")
            c.drawRightString(RIGHT, 766, f"Statement number: {stmt.year}/{stmt.month:02d}")

            # Address block
            c.setFillColor(HexColor("#1B1F24"))
            c.setFont("Helvetica", 9.5)
            y = 712
            for line in (holder.display.upper(), *[a.upper() for a in holder.address]):
                c.drawString(LEFT, y, line)
                y -= 12

            # Account details box
            bx, by, bw, bh = 330, 650, RIGHT - 330, 78
            c.setStrokeColor(RULE)
            c.setLineWidth(0.75)
            c.rect(bx, by, bw, bh, stroke=1, fill=0)
            c.setFont("Helvetica-Bold", 8.5)
            c.setFillColor(NAVY)
            c.drawString(bx + 10, by + bh - 15, "Your account details")
            c.setFont("Helvetica", 8.5)
            c.setFillColor(HexColor("#1B1F24"))
            details = [
                ("Account name", holder.display),
                ("Sort code", account.sort_code),
                ("Account number", account.number),
                ("Statement period", f"{d_short(stmt.period_start)} - {d_short(stmt.period_end)}"),
            ]
            yy = by + bh - 30
            for k, v in details:
                c.setFillColor(GREY)
                c.drawString(bx + 10, yy, k)
                c.setFillColor(HexColor("#1B1F24"))
                c.drawRightString(bx + bw - 10, yy, v)
                yy -= 12

            # Summary
            sy = 568
            c.setFillColor(LIGHT)
            c.rect(LEFT, sy, RIGHT - LEFT, 62, stroke=0, fill=1)
            c.setFillColor(NAVY)
            c.setFont("Helvetica-Bold", 9.5)
            c.drawString(LEFT + 10, sy + 46, f"Your account summary for {period}")
            cols = [
                (f"Balance on {d_long(stmt.period_start)}", stmt.opening),
                ("Total money in", stmt.money_in),
                ("Total money out", stmt.money_out),
                (f"Balance on {d_long(stmt.period_end)}", stmt.closing_shown),
            ]
            colw = (RIGHT - LEFT - 20) / 4
            for i, (label, val) in enumerate(cols):
                x = LEFT + 10 + i * colw
                c.setFillColor(GREY)
                c.setFont("Helvetica", 7.5)
                c.drawString(x, sy + 28, label)
                c.setFillColor(HexColor("#1B1F24"))
                c.setFont("Helvetica-Bold", 11)
                c.drawString(x, sy + 12, gbp(val))

            c.setFillColor(NAVY)
            c.setFont("Helvetica-Bold", 9.5)
            c.drawString(LEFT, 546, "Your transactions")
            top = FIRST_PAGE_TABLE_TOP
        else:
            draw_logo(c, LEFT, 790, 20)
            c.setFillColor(NAVY)
            c.setFont("Helvetica-Bold", 11)
            c.drawString(LEFT + 28, 796, "Northgate Bank")
            c.setFillColor(GREY)
            c.setFont("Helvetica", 8)
            c.drawRightString(RIGHT, 802, f"{holder.display}  |  Sort code {account.sort_code}  |  "
                                          f"Account {account.number}")
            c.drawRightString(RIGHT, 791, f"Statement period {period}")
            top = OTHER_PAGE_TABLE_TOP

        draw_table_header(c, top)
        y = top - ROW_H - 2
        for i, row in enumerate(page_rows):
            draw_row(c, y, row, shade=i % 2 == 1)
            y -= ROW_H
        draw_statement_footer(c, p, len(pages))
        c.showPage()
    c.save()


# ---------------------------------------------------------------------------
# PDF rendering: payslips
# ---------------------------------------------------------------------------


def render_payslip(path: Path, slip: Payslip) -> None:
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    c.setTitle(f"Payslip {slip.pay_date:%B %Y}")
    c.setAuthor(slip.employer.name)
    ink = HexColor("#1B1F24")
    green = HexColor("#2E5E4E")
    pale = HexColor("#EEF3F1")

    # Header
    c.setFillColor(green)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(LEFT, 790, slip.employer.name)
    c.setFillColor(GREY)
    c.setFont("Helvetica", 8.5)
    y = 776
    for line in slip.employer.address:
        c.drawString(LEFT, y, line)
        y -= 11
    c.setFillColor(ink)
    c.setFont("Helvetica-Bold", 20)
    c.drawRightString(RIGHT, 788, "PAYSLIP")
    c.setFont("Helvetica", 9)
    c.drawRightString(RIGHT, 773, f"Pay date: {d_long(slip.pay_date)}")
    c.drawRightString(RIGHT, 761, f"Tax year 2026/27  |  Month {slip.tax_period}")

    # Employee details grid
    gy, gh = 670, 56
    c.setStrokeColor(RULE)
    c.setLineWidth(0.75)
    c.rect(LEFT, gy, RIGHT - LEFT, gh, stroke=1, fill=0)
    c.line(LEFT, gy + gh / 2, RIGHT, gy + gh / 2)
    cells = [
        [("Employee name", slip.employee.full_name), ("Employee no.", slip.employee_number),
         ("NI number", slip.ni_number), ("Department", "Operations")],
        [("Tax code", slip.tax_code), ("NI category", "A"),
         ("Pay frequency", "Monthly"), ("Payment method", "BACS")],
    ]
    cw = (RIGHT - LEFT) / 4
    for r, row in enumerate(cells):
        top = gy + gh - r * gh / 2
        for i, (label, val) in enumerate(row):
            x = LEFT + i * cw
            if i:
                c.line(x, gy, x, gy + gh)
            c.setFillColor(GREY)
            c.setFont("Helvetica", 7)
            c.drawString(x + 6, top - 10, label)
            c.setFillColor(ink)
            c.setFont("Helvetica-Bold", 9)
            c.drawString(x + 6, top - 22, val)

    # Payments and deductions tables
    ty = 640
    mid = (LEFT + RIGHT) / 2

    def table_header(x0: float, x1: float, title: str, amount_label: str = "Amount (£)") -> None:
        c.setFillColor(green)
        c.rect(x0, ty - 4, x1 - x0, 16, stroke=0, fill=1)
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(x0 + 6, ty + 1, title)
        c.drawRightString(x1 - 6, ty + 1, amount_label)

    table_header(LEFT, mid - 6, "Payments")
    table_header(mid + 6, RIGHT, "Deductions")
    c.setFillColor(ink)
    c.setFont("Helvetica", 9)
    ry = ty - 20
    c.drawString(LEFT + 6, ry, "Basic salary")
    c.drawRightString(mid - 12, ry, fmt(slip.gross))
    deductions = [("PAYE income tax", slip.tax), ("National Insurance", slip.ni),
                  ("Pension (employee 5%)", slip.pension)]
    for label, val in deductions:
        c.drawString(mid + 12, ry, label)
        c.drawRightString(RIGHT - 6, ry, fmt(val))
        ry -= 14

    # Totals
    toty = 560
    c.setFillColor(pale)
    c.rect(LEFT, toty - 5, mid - 6 - LEFT, 18, stroke=0, fill=1)
    c.rect(mid + 6, toty - 5, RIGHT - mid - 6, 18, stroke=0, fill=1)
    c.setFillColor(ink)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(LEFT + 6, toty, "Total gross pay")
    c.drawRightString(mid - 12, toty, fmt(slip.gross))
    c.drawString(mid + 12, toty, "Total deductions")
    c.drawRightString(RIGHT - 6, toty, fmt(slip.total_deductions))

    # Net pay box
    ny = 500
    c.setFillColor(green)
    c.rect(mid + 6, ny, RIGHT - mid - 6, 40, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(mid + 16, ny + 15, "NET PAY")
    c.setFont("Helvetica-Bold", 16)
    c.drawRightString(RIGHT - 10, ny + 13, gbp(slip.net))
    c.setFillColor(GREY)
    c.setFont("Helvetica", 8)
    c.drawString(LEFT, ny + 26, f"Paid by BACS to Northgate Bank account ending {slip.account_last4}")
    c.drawString(LEFT, ny + 14, f"Employer pension contribution (3%): {gbp(slip.employer_pension)}")

    # Year to date
    yy = 460
    c.setFillColor(ink)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(LEFT, yy, f"Year to date (6 April 2026 to {d_long(slip.pay_date)})")
    n = slip.tax_period
    ytd = [
        ("Gross pay", slip.gross * n), ("Taxable pay", slip.gross * n),
        ("Income tax", slip.tax * n), ("Employee NI", slip.ni * n),
        ("Employee pension", slip.pension * n), ("Employer pension", slip.employer_pension * n),
    ]
    c.setStrokeColor(RULE)
    c.rect(LEFT, yy - 52, RIGHT - LEFT, 42, stroke=1, fill=0)
    cw = (RIGHT - LEFT) / len(ytd)
    for i, (label, val) in enumerate(ytd):
        x = LEFT + i * cw
        if i:
            c.line(x, yy - 52, x, yy - 10)
        c.setFillColor(GREY)
        c.setFont("Helvetica", 7)
        c.drawString(x + 6, yy - 22, label)
        c.setFillColor(ink)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(x + 6, yy - 38, fmt(money(val)))

    # Footer
    c.setFillColor(GREY)
    c.setFont("Helvetica", 7)
    c.drawString(LEFT, 380, f"Employer PAYE reference: {slip.employer.paye_ref}   |   "
                            "Queries: payroll@brightwater-logistics.example")
    c.drawString(LEFT, 370, "Brightwater Logistics Ltd is a fictional company. This is a synthetic "
                            "test document created for Tallied and does not relate to any real person.")
    c.showPage()
    c.save()


# ---------------------------------------------------------------------------
# PDF metadata
# ---------------------------------------------------------------------------


def pdf_date(dt: datetime) -> str:
    return dt.strftime("D:%Y%m%d%H%M%S+00'00'")


def statement_metadata(stmt: Statement) -> dict[str, str]:
    produced = datetime.combine(stmt.issue_date, datetime.min.time()) + timedelta(hours=4, minutes=12)
    return {
        "/Producer": "Northgate Document Services 7.3",
        "/Creator": "NGB Statement Engine",
        "/CreationDate": pdf_date(produced),
        "/ModDate": pdf_date(produced),
    }


def payslip_metadata(slip: Payslip) -> dict[str, str]:
    produced = datetime.combine(slip.pay_date - timedelta(days=3), datetime.min.time()) + timedelta(
        hours=16, minutes=40)
    return {
        "/Producer": "PayRun Cloud Payroll 2026.2",
        "/Creator": "PayRun Cloud Payroll",
        "/CreationDate": pdf_date(produced),
        "/ModDate": pdf_date(produced),
    }


def write_metadata(path: Path, info: dict[str, str]) -> None:
    reader = PdfReader(str(path))
    writer = PdfWriter(clone_from=reader)
    existing = {k: str(v) for k, v in (reader.metadata or {}).items()}
    writer.metadata = None
    writer.add_metadata({**existing, **info})
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as fh:
        writer.write(fh)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

CHECK_IDS = [
    "balance_arithmetic",   # running balances and summary totals add up
    "document_integrity",   # fonts and PDF metadata show no sign of editing
    "income_match",         # salary credits on statements match payslip net pay and date
    "payslip_maths",        # tax and NI are plausible for gross pay, totals add up
    "name_match",           # same person on statements and payslips
    "employer_check",       # employer found on Companies House
]


def all_pass(**overrides: str) -> dict[str, str]:
    checks = {c: "pass" for c in CHECK_IDS}
    # Employer is fictional, so the Companies House lookup cannot be asserted from fixtures.
    checks["employer_check"] = "not_asserted"
    checks.update(overrides)
    return checks


def statement_file(stmt: Statement) -> str:
    return f"statement_{stmt.year}-{stmt.month:02d}.pdf"


def payslip_file(slip: Payslip) -> str:
    return f"payslip_{slip.pay_date:%Y-%m}.pdf"


def case_genuine() -> Case:
    return Case(
        name="genuine",
        description="Three monthly statements and two payslips that all agree.",
        statements=build_statements(money("2650.00")),
        payslips=build_payslips(APPLICANT, GENUINE_GROSS),
        holder=APPLICANT,
        expected_verdict="pass",
        expected_checks=all_pass(),
        expected_flags=[],
    )


def case_edited_balance() -> Case:
    statements = build_statements(money("2650.00"))
    oct_stmt = statements[2]
    bump = money("900.00")
    edited_index = next(i for i, t in enumerate(oct_stmt.txs) if t.day == date(2026, 10, 12))
    edited = oct_stmt.txs[edited_index]
    true_balance = edited.balance
    for t in oct_stmt.txs[edited_index:]:
        t.balance += bump
    edited.font = EDIT_FONT
    oct_stmt.closing_shown = oct_stmt.true_closing + bump
    return Case(
        name="edited_balance",
        description="Same as genuine, but the first balance on 12 Oct 2026 is £900 too high and "
                    "every later balance (and the closing balance in the summary) carries it. "
                    "That one line is rendered in a different font.",
        statements=statements,
        payslips=build_payslips(APPLICANT, GENUINE_GROSS),
        holder=APPLICANT,
        expected_verdict="flag",
        expected_checks=all_pass(balance_arithmetic="flag", document_integrity="flag"),
        expected_flags=[
            {
                "check": "balance_arithmetic",
                "file": statement_file(oct_stmt),
                "date": "2026-10-12",
                "description": edited.description,
                "balance_shown": str(edited.balance),
                "balance_expected": str(true_balance),
                "difference": str(bump),
            },
            {
                "check": "balance_arithmetic",
                "file": statement_file(oct_stmt),
                "detail": "summary closing balance does not equal opening + money in - money out",
                "closing_shown": str(oct_stmt.closing_shown),
                "closing_expected": str(oct_stmt.true_closing),
            },
            {
                "check": "document_integrity",
                "file": statement_file(oct_stmt),
                "date": "2026-10-12",
                "detail": f"one transaction line uses font '{EDIT_FONT}' while the rest of the "
                          f"table uses '{BODY_FONT}'",
            },
        ],
    )


def case_salary_mismatch() -> Case:
    gross = money("4190.00")
    slips = build_payslips(APPLICANT, gross, tax=money("628.50"), ni=money("251.36"))
    for s in slips:
        s.pension = money("209.50")
    assert slips[0].net == money("3100.64")
    return Case(
        name="salary_mismatch",
        description="Statements show £2,650.00 salary but payslips say net pay £3,100.64 "
                    "(gross £4,190, tax £628.50, NI £251.36, pension £209.50).",
        statements=build_statements(money("2650.00")),
        payslips=slips,
        holder=APPLICANT,
        expected_verdict="flag",
        expected_checks=all_pass(income_match="flag"),
        expected_flags=[
            {
                "check": "income_match",
                "file": payslip_file(s),
                "pay_date": s.pay_date.isoformat(),
                "payslip_net": str(s.net),
                "statement_credit": "2650.00",
                "difference": str(s.net - money("2650.00")),
            }
            for s in slips
        ],
        notes=[
            "The payslip deductions themselves are plausible for £4,190 gross "
            f"(model: tax {expected_tax(gross)}, NI {expected_ni(gross)}), so payslip_maths "
            "should pass within a small tolerance.",
        ],
    )


def case_bad_payslip() -> Case:
    gross = money("4600.00")
    tax, ni = money("310.00"), money("95.00")
    slips = build_payslips(APPLICANT, gross, tax=tax, ni=ni)
    net = slips[0].net
    assert net == money("3965.00")
    return Case(
        name="bad_payslip",
        description="Payslips show gross £4,600 with tax £310.00 and NI £95.00, far below what "
                    "tax code 1257L and NI category A would give. The payslip totals add up and "
                    "the statements show the same £3,965.00 net salary, so only the tax/NI "
                    "plausibility check should fail.",
        statements=build_statements(net),
        payslips=slips,
        holder=APPLICANT,
        expected_verdict="flag",
        expected_checks=all_pass(payslip_maths="flag"),
        expected_flags=[
            {
                "check": "payslip_maths",
                "file": payslip_file(s),
                "gross": str(s.gross),
                "tax_shown": str(s.tax),
                "tax_expected": str(expected_tax(gross)),
                "ni_shown": str(s.ni),
                "ni_expected": str(expected_ni(gross)),
            }
            for s in slips
        ],
    )


def case_name_mismatch() -> Case:
    return Case(
        name="name_mismatch",
        description=f"Statements are in the name of {APPLICANT.full_name}; payslips are in the "
                    f"name of {OTHER_PERSON.full_name}. Amounts and dates otherwise agree.",
        statements=build_statements(money("2650.00")),
        payslips=build_payslips(OTHER_PERSON, GENUINE_GROSS),
        holder=APPLICANT,
        expected_verdict="flag",
        expected_checks=all_pass(name_match="flag"),
        expected_flags=[
            {
                "check": "name_match",
                "statement_name": APPLICANT.full_name,
                "payslip_name": OTHER_PERSON.full_name,
                "files": [payslip_file(s) for s in build_payslips(OTHER_PERSON, GENUINE_GROSS)],
            }
        ],
    )


EDITOR_PRODUCER = "iLovePDF"
EDITOR_CREATOR = "PDF Editor Online"
EDITED_AT = datetime(2026, 11, 9, 21, 47, 33)


def case_edited_metadata() -> Case:
    statements = build_statements(money("2650.00"))
    oct_stmt = statements[2]
    base = statement_metadata(oct_stmt)
    override = {**base, "/Producer": EDITOR_PRODUCER, "/Creator": EDITOR_CREATOR,
                "/ModDate": pdf_date(EDITED_AT)}
    return Case(
        name="edited_metadata",
        description="Genuine content, but the October statement's PDF metadata says it was "
                    f"modified by a consumer PDF editor ({EDITOR_PRODUCER}) on "
                    f"{EDITED_AT:%d %b %Y}, after the statement date.",
        statements=statements,
        payslips=build_payslips(APPLICANT, GENUINE_GROSS),
        holder=APPLICANT,
        expected_verdict="flag",
        expected_checks=all_pass(document_integrity="flag"),
        expected_flags=[
            {
                "check": "document_integrity",
                "file": statement_file(oct_stmt),
                "producer": EDITOR_PRODUCER,
                "creator": EDITOR_CREATOR,
                "creation_date": base["/CreationDate"],
                "mod_date": override["/ModDate"],
                "statement_date": oct_stmt.issue_date.isoformat(),
            }
        ],
        metadata_overrides={statement_file(oct_stmt): override},
    )


CASES = [case_genuine, case_edited_balance, case_salary_mismatch, case_bad_payslip,
         case_name_mismatch, case_edited_metadata]
CASE_NAMES = ["genuine", "edited_balance", "salary_mismatch", "bad_payslip", "name_mismatch",
              "edited_metadata"]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def expected_json(case: Case) -> dict:
    return {
        "case": case.name,
        "synthetic": True,
        "description": case.description,
        "expected_verdict": case.expected_verdict,
        "expected_checks": case.expected_checks,
        "expected_flags": case.expected_flags,
        "notes": case.notes,
        "truth": {
            "account_holder": case.holder.full_name,
            "sort_code": ACCOUNT.sort_code,
            "account_number": ACCOUNT.number,
            "bank": "Northgate Bank",
            "employer": EMPLOYER.name,
            "pay_day": PAY_DAY,
            "statements": [
                {
                    "file": statement_file(s),
                    "period_start": s.period_start.isoformat(),
                    "period_end": s.period_end.isoformat(),
                    "statement_date": s.issue_date.isoformat(),
                    "opening_balance": str(s.opening),
                    "money_in": str(s.money_in),
                    "money_out": str(s.money_out),
                    "closing_balance_true": str(s.true_closing),
                    "closing_balance_shown": str(s.closing_shown),
                    "transaction_count": len(s.txs),
                    "salary_credits": [
                        {"date": t.day.isoformat(), "amount": str(t.paid_in),
                         "description": t.description}
                        for t in s.txs if t.type == "BGC"
                    ],
                }
                for s in case.statements
            ],
            "payslips": [
                {
                    "file": payslip_file(p),
                    "employee_name": p.employee.full_name,
                    "pay_date": p.pay_date.isoformat(),
                    "tax_period": p.tax_period,
                    "tax_code": p.tax_code,
                    "gross": str(p.gross),
                    "tax": str(p.tax),
                    "ni": str(p.ni),
                    "pension": str(p.pension),
                    "net": str(p.net),
                }
                for p in case.payslips
            ],
        },
    }


def write_case(case: Case, out_dir: Path) -> Path:
    folder = out_dir / case.name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)

    for stmt in case.statements:
        path = folder / statement_file(stmt)
        render_statement(path, stmt, case.holder, ACCOUNT)
        info = case.metadata_overrides.get(path.name, statement_metadata(stmt))
        write_metadata(path, info)
    for slip in case.payslips:
        path = folder / payslip_file(slip)
        render_payslip(path, slip)
        write_metadata(path, case.metadata_overrides.get(path.name, payslip_metadata(slip)))

    data = expected_json(case)
    (folder / "expected.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return folder


def generate(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return [write_case(make(), out_dir) for make in CASES]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent,
                        help="output directory (default: the fixtures folder)")
    args = parser.parse_args()
    for folder in generate(args.out):
        print(f"wrote {folder}")


if __name__ == "__main__":
    main()
