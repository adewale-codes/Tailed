"""AI fallback: send page images to Claude and get the same shape back as the parser.

Used only when the deterministic parser can't read a document (photos, scans, unusual layouts).
Bounding boxes from this path are estimates and are marked `approximate=True`.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from app.config import get_settings
from app.reader.errors import ReaderError
from app.reader.models import (
    BankStatement,
    BBox,
    Deduction,
    DocumentType,
    LocatedAmount,
    LocatedDate,
    LocatedText,
    Location,
    PageSize,
    Payslip,
    Transaction,
)

log = logging.getLogger(__name__)

# Long statements can run to hundreds of rows; streaming keeps a large ceiling safe from timeouts.
MAX_TOKENS = 64000
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class PageImage:
    page: int
    media_type: str
    data: bytes
    size: PageSize  # coordinate space boxes are converted into


# ---------------------------------------------------------------------------
# JSON schema sent to Claude (structured outputs: every object closed, every field required)
# ---------------------------------------------------------------------------


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def _obj(props: dict[str, dict]) -> dict:
    return {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}


_AMOUNT = {"type": "string",
           "description": "Amount exactly as printed, as a plain decimal: no £, no commas, two "
                          "decimal places, minus sign if negative/overdrawn. e.g. 1234.56"}
_DATE = {"type": "string", "format": "date"}
_LOC = _obj({
    "page": {"type": "integer", "description": "1-based page number from the 'Page N' label"},
    "bbox": {"type": "array", "items": {"type": "number"},
             "description": "[x0, top, x1, bottom] as fractions (0 to 1) of the page image "
                            "width and height, origin top-left, enclosing the printed text"},
})
_LOCATED_AMOUNT = _obj({"value": _AMOUNT, "location": _LOC})
_LOCATED_TEXT = _obj({"value": {"type": "string"}, "location": _LOC})

_STATEMENT = _obj({
    "account_holder": _nullable(_LOCATED_TEXT),
    "bank_name": _nullable({"type": "string"}),
    "sort_code": _nullable({"type": "string"}),
    "account_number": _nullable({"type": "string"}),
    "period_start": _DATE,
    "period_end": _DATE,
    "opening_balance": _LOCATED_AMOUNT,
    "closing_balance": _nullable(_LOCATED_AMOUNT),
    "summary_money_in": _nullable(_LOCATED_AMOUNT),
    "summary_money_out": _nullable(_LOCATED_AMOUNT),
    "transactions": {"type": "array", "items": _obj({
        "date": _DATE,
        "description": {"type": "string"},
        "type": _nullable({"type": "string"}),
        "paid_in": _nullable(_AMOUNT),
        "paid_out": _nullable(_AMOUNT),
        "balance": _nullable(_AMOUNT),
        "location": _LOC,
    })},
})

_PAYSLIP = _obj({
    "employee_name": _nullable(_LOCATED_TEXT),
    "employer_name": _nullable(_LOCATED_TEXT),
    "pay_date": _obj({"value": _DATE, "location": _LOC}),
    "gross_pay": _LOCATED_AMOUNT,
    "income_tax": _nullable(_LOCATED_AMOUNT),
    "national_insurance": _nullable(_LOCATED_AMOUNT),
    "pension": _nullable(_LOCATED_AMOUNT),
    "other_deductions": {"type": "array", "items": _obj({
        "label": {"type": "string"}, "value": _AMOUNT, "location": _LOC})},
    "net_pay": _LOCATED_AMOUNT,
    "tax_code": _nullable({"type": "string"}),
})

OUTPUT_SCHEMA = _obj({
    "document_type": {"type": "string", "enum": ["bank_statement", "payslip", "unknown"]},
    "statement": _nullable(_STATEMENT),
    "payslip": _nullable(_PAYSLIP),
})

SYSTEM_PROMPT = """You read UK bank statements and payslips from page images and transcribe them \
into JSON for a document-checking service.

Transcribe exactly what is printed. Never calculate, correct or fill in figures: if a running \
balance or total looks wrong, copy it as printed, because the service checks the arithmetic \
itself and needs the document's own numbers. Use null for anything that is not shown.

- document_type: "bank_statement", "payslip", or "unknown" for anything else. Fill only the \
matching object; set the other to null.
- Statements: list every transaction row in order, across all pages. Do not include "balance \
brought forward" or "balance carried forward" rows as transactions; use them for \
opening_balance and closing_balance. Put money leaving the account in paid_out and money \
arriving in paid_in, both as positive numbers.
- Payslips: income_tax is PAYE tax; national_insurance is employee NI. Put any other \
deductions (student loan, union fees and so on) in other_deductions. Use the current period's \
figures, not year-to-date figures.
- Dates: ISO format (YYYY-MM-DD). Two-digit years are 20xx.
- location: the page number from the "Page N" label before each image, and a box around the \
printed text as fractions of that image's width and height."""


# ---------------------------------------------------------------------------
# Validation of Claude's JSON (it is untrusted until it passes these models)
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _AiLoc(_Strict):
    page: int
    bbox: list[float]

    @field_validator("bbox")
    @classmethod
    def _four_fractions(cls, v: list[float]) -> list[float]:
        if len(v) != 4:
            raise ValueError("bbox must have 4 numbers")
        return [min(max(x, 0.0), 1.0) for x in v]


class _AiAmount(_Strict):
    value: str
    location: _AiLoc


class _AiText(_Strict):
    value: str
    location: _AiLoc


class _AiDate(_Strict):
    value: date
    location: _AiLoc


class _AiTx(_Strict):
    date: date
    description: str
    type: str | None
    paid_in: str | None
    paid_out: str | None
    balance: str | None
    location: _AiLoc


class _AiStatement(_Strict):
    account_holder: _AiText | None
    bank_name: str | None
    sort_code: str | None
    account_number: str | None
    period_start: date
    period_end: date
    opening_balance: _AiAmount
    closing_balance: _AiAmount | None
    summary_money_in: _AiAmount | None
    summary_money_out: _AiAmount | None
    transactions: list[_AiTx]


class _AiDeduction(_Strict):
    label: str
    value: str
    location: _AiLoc


class _AiPayslip(_Strict):
    employee_name: _AiText | None
    employer_name: _AiText | None
    pay_date: _AiDate
    gross_pay: _AiAmount
    income_tax: _AiAmount | None
    national_insurance: _AiAmount | None
    pension: _AiAmount | None
    other_deductions: list[_AiDeduction]
    net_pay: _AiAmount
    tax_code: str | None


class _AiOutput(_Strict):
    document_type: Literal["bank_statement", "payslip", "unknown"]
    statement: _AiStatement | None
    payslip: _AiPayslip | None


# ---------------------------------------------------------------------------
# Conversion to the public models
# ---------------------------------------------------------------------------


class _Converter:
    def __init__(self, pages: list[PageImage]) -> None:
        self.sizes = {p.page: p.size for p in pages}

    def loc(self, loc: _AiLoc) -> Location:
        size = self.sizes.get(loc.page)
        if size is None:
            raise ValueError("location refers to a page that was not sent")
        x0, top, x1, bottom = loc.bbox
        return Location(
            page=loc.page,
            bbox=BBox(x0=round(min(x0, x1) * size.width, 2), top=round(min(top, bottom) * size.height, 2),
                      x1=round(max(x0, x1) * size.width, 2), bottom=round(max(top, bottom) * size.height, 2)),
            approximate=True,
        )

    @staticmethod
    def dec(value: str | None) -> Decimal | None:
        if value is None:
            return None
        cleaned = value.replace(",", "").replace("£", "").strip()
        try:
            return Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError("amount is not a number") from exc

    def amount(self, a: _AiAmount | None) -> LocatedAmount | None:
        if a is None:
            return None
        return LocatedAmount(value=self.dec(a.value), location=self.loc(a.location))

    def text(self, t: _AiText | None) -> LocatedText | None:
        if t is None:
            return None
        return LocatedText(value=t.value, location=self.loc(t.location))

    def statement(self, s: _AiStatement) -> BankStatement:
        return BankStatement(
            account_holder=self.text(s.account_holder),
            bank_name=s.bank_name,
            sort_code=s.sort_code,
            account_number=s.account_number,
            period_start=s.period_start,
            period_end=s.period_end,
            opening_balance=self.amount(s.opening_balance),
            closing_balance=self.amount(s.closing_balance),
            summary_money_in=self.amount(s.summary_money_in),
            summary_money_out=self.amount(s.summary_money_out),
            transactions=[
                Transaction(
                    date=t.date, description=t.description, type=t.type,
                    paid_in=self.dec(t.paid_in), paid_out=self.dec(t.paid_out),
                    balance=self.dec(t.balance), location=self.loc(t.location),
                )
                for t in s.transactions
            ],
        )

    def payslip(self, p: _AiPayslip) -> Payslip:
        return Payslip(
            employee_name=self.text(p.employee_name),
            employer_name=self.text(p.employer_name),
            pay_date=LocatedDate(value=p.pay_date.value, location=self.loc(p.pay_date.location)),
            gross_pay=self.amount(p.gross_pay),
            income_tax=self.amount(p.income_tax),
            national_insurance=self.amount(p.national_insurance),
            pension=self.amount(p.pension),
            other_deductions=[
                Deduction(label=d.label, value=self.dec(d.value), location=self.loc(d.location))
                for d in p.other_deductions
            ],
            net_pay=self.amount(p.net_pay),
            tax_code=p.tax_code,
        )


# ---------------------------------------------------------------------------
# Calling Claude
# ---------------------------------------------------------------------------


@dataclass
class AiReply:
    stop_reason: str | None
    text: str


def _build_content(pages: list[PageImage]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for p in pages:
        content.append({"type": "text", "text": f"Page {p.page}"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": p.media_type,
            "data": base64.standard_b64encode(p.data).decode("ascii")}})
    content.append({"type": "text", "text": "Transcribe this document into the JSON schema."})
    return content


def call_claude(pages: list[PageImage]) -> AiReply:
    """Send the pages to Claude. Replaced in tests."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise ReaderError("ai_unavailable")
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    try:
        with client.beta.messages.stream(
            model=settings.claude_model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_content(pages)}],
            output_config={"effort": settings.claude_effort,
                           "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            message = stream.get_final_message()
    except anthropic.APIConnectionError as exc:
        log.warning("reader.ai.error type=%s", type(exc).__name__)
        raise ReaderError("ai_unavailable") from None
    except anthropic.APIStatusError as exc:
        log.warning("reader.ai.error type=%s status=%s", type(exc).__name__, exc.status_code)
        raise ReaderError("ai_unavailable") from None
    except anthropic.AnthropicError as exc:  # credentials, timeouts and other SDK-side failures
        log.warning("reader.ai.error type=%s", type(exc).__name__)
        raise ReaderError("ai_unavailable") from None
    text = "".join(b.text for b in message.content if b.type == "text")
    return AiReply(stop_reason=message.stop_reason, text=text)


def read_with_ai(pages: list[PageImage]) -> tuple[DocumentType, BankStatement | None, Payslip | None]:
    reply = call_claude(pages)
    if reply.stop_reason == "max_tokens":
        log.warning("reader.ai.truncated")
        raise ReaderError("ai_truncated")
    if reply.stop_reason == "refusal":
        log.warning("reader.ai.refused")
        raise ReaderError("ai_refused")
    try:
        raw = json.loads(reply.text)
        parsed = _AiOutput.model_validate(raw)
        conv = _Converter(pages)
        if parsed.document_type == "bank_statement":
            if parsed.statement is None:
                raise ValueError("statement missing")
            return "bank_statement", conv.statement(parsed.statement), None
        if parsed.document_type == "payslip":
            if parsed.payslip is None:
                raise ValueError("payslip missing")
            return "payslip", None, conv.payslip(parsed.payslip)
        return "unknown", None, None
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        log.warning("reader.ai.invalid type=%s", type(exc).__name__)
        raise ReaderError("ai_invalid_response") from None
