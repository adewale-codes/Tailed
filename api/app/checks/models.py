"""Report models produced by the checks engine."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.reader.models import BBox, Location, ReadResult

CheckId = Literal[
    "balances_add_up",
    "documents_agree",
    "payslip_realistic",
    "employer_real",
    "looks_like_real_life",
    "names_match",
]
CHECK_ORDER: list[str] = [
    "balances_add_up",
    "documents_agree",
    "payslip_realistic",
    "employer_real",
    "looks_like_real_life",
    "names_match",
]
Status = Literal["pass", "flag", "not_checked"]
Verdict = Literal["looks_good", "needs_a_closer_look", "high_risk"]


@dataclass
class DocumentInput:
    """One uploaded file after reading. `file` is the label shown in the evidence view."""

    file: str
    result: ReadResult


class Evidence(BaseModel):
    kind: str = Field(description="Machine-readable finding type, e.g. balance_mismatch")
    flag: bool = Field(description="True if this finding is a reason for the check's flag")
    file: str | None = None
    page: int | None = None
    bboxes: list[BBox] = Field(default_factory=list)
    approximate: bool = False
    numbers: dict[str, Decimal] = Field(
        default_factory=dict, description="The figures involved. These are the only numbers the "
                                          "wording step may quote.")
    details: dict[str, str] = Field(
        default_factory=dict, description="Names, dates and descriptions. Never sent to the AI.")

    primary: int = Field(default=0, description="Index in `bboxes` of the line this finding is "
                                                 "about. Any other boxes are context.")

    @classmethod
    def at(cls, kind: str, file: str | None, locations: list[Location | None], *, flag: bool,
           numbers: dict[str, Decimal] | None = None,
           details: dict[str, str] | None = None, focus: int = 0) -> Evidence:
        """`locations[focus]` is the line the finding is about. The page is that line's page, and
        only boxes on the same page are kept (the others can't be drawn on it)."""
        locs = [loc for loc in locations if loc is not None]
        key = locations[focus] if 0 <= focus < len(locations) else None
        if key is None and locs:
            key = locs[0]
        if key is None:
            return cls(kind=kind, flag=flag, file=file, numbers=numbers or {},
                       details=details or {})
        same_page = [loc for loc in locs if loc.page == key.page]
        return cls(
            kind=kind, flag=flag, file=file, page=key.page,
            bboxes=[loc.bbox for loc in same_page],
            primary=next(i for i, loc in enumerate(same_page) if loc is key),
            approximate=any(loc.approximate for loc in same_page),
            numbers=numbers or {}, details=details or {},
        )


class Wording(BaseModel):
    title: str
    explanation: str
    next_steps: list[str] = Field(default_factory=list)


class CheckResult(BaseModel):
    id: CheckId
    number: int
    status: Status
    title: str
    explanation: str
    evidence: list[Evidence] = Field(default_factory=list)
    display: Wording | None = Field(default=None, description="Friendly wording for the report")


class Affordability(BaseModel):
    monthly_rent: Decimal
    months_covered: Decimal | None = None
    average_monthly_income_in_account: Decimal | None = None
    payslip_net_pay: Decimal | None = Field(default=None, description="Average net pay across payslips")
    rent_pct_of_account_income: Decimal | None = None
    rent_pct_of_payslip_net: Decimal | None = None


class Report(BaseModel):
    verdict: Verdict
    verdict_reason: str
    checks: list[CheckResult]
    affordability: Affordability
    summary: Wording | None = None
    wording_source: Literal["ai", "engine"] = "engine"
