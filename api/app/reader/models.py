"""Typed output of the document reader.

Coordinates: every bounding box is in the page's own coordinate space with the origin at the
top-left. For PDFs that is PDF points (1/72 inch); for uploaded photos it is image pixels.
`ReadResult.pages` gives each page's width and height in the same units, so the web app can
scale a box onto a rendered page.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

DocumentType = Literal["bank_statement", "payslip", "unknown"]
Source = Literal["parser", "ai"]


class BBox(BaseModel):
    x0: float
    top: float
    x1: float
    bottom: float


class Location(BaseModel):
    page: int = Field(ge=1, description="1-based page number")
    bbox: BBox
    approximate: bool = Field(
        default=False, description="True when the box was estimated by the AI path, not measured"
    )


class PageSize(BaseModel):
    page: int
    width: float
    height: float
    unit: Literal["pt", "px"]


class FileMetadata(BaseModel):
    creator: str | None = None
    producer: str | None = None
    creation_date: datetime | None = None
    modification_date: datetime | None = None


class Transaction(BaseModel):
    date: date
    description: str
    type: str | None = Field(default=None, description="Bank's transaction code, e.g. DD, BGC")
    paid_in: Decimal | None = None
    paid_out: Decimal | None = None
    balance: Decimal | None = Field(default=None, description="Running balance shown on the line")
    location: Location
    font_name: str | None = Field(default=None, description="Main font used on the line")
    font_size: float | None = None
    fonts: list[str] = Field(default_factory=list, description="Every distinct font on the line")

    @property
    def amount(self) -> Decimal:
        """Signed amount: positive for money in, negative for money out."""
        return (self.paid_in or Decimal(0)) - (self.paid_out or Decimal(0))


class LocatedAmount(BaseModel):
    value: Decimal
    location: Location | None = None


class LocatedText(BaseModel):
    value: str
    location: Location | None = None


class LocatedDate(BaseModel):
    value: date
    location: Location | None = None


class BankStatement(BaseModel):
    account_holder: LocatedText | None
    bank_name: str | None = None
    sort_code: str | None = None
    account_number: str | None = None
    period_start: date
    period_end: date
    opening_balance: LocatedAmount
    closing_balance: LocatedAmount | None = None
    summary_money_in: LocatedAmount | None = None
    summary_money_out: LocatedAmount | None = None
    transactions: list[Transaction]


class Deduction(BaseModel):
    label: str
    value: Decimal
    location: Location | None = None


class Payslip(BaseModel):
    employee_name: LocatedText | None
    employer_name: LocatedText | None
    pay_date: LocatedDate
    gross_pay: LocatedAmount
    income_tax: LocatedAmount | None
    national_insurance: LocatedAmount | None
    pension: LocatedAmount | None
    other_deductions: list[Deduction] = Field(default_factory=list)
    net_pay: LocatedAmount
    tax_code: str | None = None


class ReadResult(BaseModel):
    document_type: DocumentType
    source: Source
    page_count: int
    pages: list[PageSize]
    metadata: FileMetadata
    statement: BankStatement | None = None
    payslip: Payslip | None = None
    warnings: list[str] = Field(default_factory=list)
