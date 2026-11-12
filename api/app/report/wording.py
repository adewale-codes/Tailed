"""Friendly wording for the report: one Claude call, with guard rails.

Claude rewrites titles and explanations and suggests next steps. It cannot change anything that
matters:
  - Statuses and the verdict are not in its output, so they can't change.
  - Every number in its text must be one the engine produced for that check (or, for the
    summary, anywhere in the report). Text that quotes any other number is thrown away and the
    engine's own wording is used for that check.
  - It is only sent check IDs, statuses, the engine's sentences and the numbers. Names, file
    names and transaction descriptions are never sent.
If Claude is unavailable, the engine's wording is used and the report is still complete.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

import anthropic
from pydantic import BaseModel, ConfigDict, ValidationError

from app.checks.models import CHECK_ORDER, CheckResult, Report, Wording
from app.config import get_settings

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000

NEXT_STEPS: dict[str, dict[str, list[str]]] = {
    "balances_add_up": {
        "flag": ["Ask the tenant to download the statements again directly from their bank, or "
                 "share them from their banking app.",
                 "Compare the highlighted lines with the original statements."],
    },
    "documents_agree": {
        "flag": ["Ask the tenant to explain the difference between their payslips and what was "
                 "paid into their account.",
                 "Ask for an employer reference confirming their take-home pay."],
    },
    "payslip_realistic": {
        "flag": ["Ask for a P60 or a letter from the employer confirming pay and deductions."],
    },
    "employer_real": {
        "flag": ["Ask for the employer's full registered name and company number.",
                 "Ask for a reference sent from the employer's own email address."],
        "not_checked": ["Look the employer up yourself on the Companies House website."],
    },
    "looks_like_real_life": {
        "flag": ["Ask whether this is the tenant's main account, and for statements from it if not."],
    },
    "names_match": {
        "flag": ["Ask the tenant to explain why the names differ, for example a recent name change."],
    },
}

HEADLINES = {
    "looks_good": Wording(
        title="The documents look consistent",
        explanation="All the checks we could run passed. This isn't a guarantee, so use it "
                    "alongside your usual referencing."),
    "needs_a_closer_look": Wording(
        title="Some things need a closer look",
        explanation="At least one check found something worth asking the tenant about. It isn't "
                    "proof of anything wrong; there is often a simple explanation.",
        next_steps=["Go through the flagged checks below and ask the tenant about each one."]),
    "high_risk": Wording(
        title="Several serious problems found",
        explanation="The documents disagree with each other in more than one way. We'd recommend "
                    "not relying on them until the tenant can explain the differences.",
        next_steps=["Ask the tenant for documents straight from their bank and employer.",
                    "Go through the flagged checks below with the tenant."]),
}

SYSTEM_PROMPT = """You write the wording for a tenant-referencing report read by UK landlords and \
letting agents. A rules engine has already checked a tenant's bank statements and payslips. You \
rewrite its findings in plain, calm, friendly British English.

Rules:
- Never change, round, add or calculate numbers. Only use numbers that appear in the input for \
that check, written the same way (for example £2,650.00 or 14.5%). If you don't need a number, \
leave it out.
- Keep each check's meaning and status exactly. A "flag" means something worth asking about, \
not proof of fraud. "not_checked" means we couldn't check it, never that it passed. "pass" \
means the check found nothing wrong.
- Don't name people, companies or documents; you haven't been given them.
- title: up to 8 words. explanation: one or two short sentences. next_steps: 0 to 3 short, \
practical actions for the landlord (none for a pass).
- headline and summary describe the overall verdict; summary is one or two sentences."""

_WORDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "summary", "next_steps", "checks"],
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "next_steps": {"type": "array", "items": {"type": "string"}},
        "checks": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "title", "explanation", "next_steps"],
            "properties": {
                "id": {"type": "string", "enum": CHECK_ORDER},
                "title": {"type": "string"},
                "explanation": {"type": "string"},
                "next_steps": {"type": "array", "items": {"type": "string"}},
            },
        }},
    },
}


class _AiCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    explanation: str
    next_steps: list[str]


class _AiWording(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headline: str
    summary: str
    next_steps: list[str]
    checks: list[_AiCheck]


# ---------------------------------------------------------------------------
# Number guard
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[Decimal]:
    found = set()
    for token in _NUMBER.findall(text):
        try:
            found.add(Decimal(token.replace(",", "")).normalize())
        except InvalidOperation:
            continue
    return found


def allowed_numbers(check: CheckResult) -> set[Decimal]:
    allowed = numbers_in(check.title) | numbers_in(check.explanation)
    for e in check.evidence:
        for value in e.numbers.values():
            allowed.add(value.normalize())
            allowed.add(abs(value).normalize())
    return allowed


def report_numbers(report: Report) -> set[Decimal]:
    allowed: set[Decimal] = set()
    for c in report.checks:
        allowed |= allowed_numbers(c)
    for value in report.affordability.model_dump().values():
        if isinstance(value, Decimal):
            allowed.add(value.normalize())
    return allowed


def uses_only(texts: list[str], allowed: set[Decimal]) -> bool:
    return all(numbers_in(t) <= allowed for t in texts)


# ---------------------------------------------------------------------------
# Applying wording
# ---------------------------------------------------------------------------


def engine_wording(report: Report) -> Report:
    report = report.model_copy(deep=True)
    for c in report.checks:
        c.display = Wording(title=c.title, explanation=c.explanation,
                            next_steps=NEXT_STEPS.get(c.id, {}).get(c.status, []))
    report.summary = HEADLINES[report.verdict].model_copy()
    report.wording_source = "engine"
    return report


def _prompt_input(report: Report) -> dict[str, Any]:
    def numbers(c: CheckResult) -> dict[str, str]:
        merged: dict[str, str] = {}
        for e in c.evidence:
            if e.flag or c.status == "pass":
                for k, v in e.numbers.items():
                    merged.setdefault(k, str(v))
        return merged

    afford = {k: str(v) for k, v in report.affordability.model_dump().items() if v is not None}
    return {
        "verdict": report.verdict,
        "affordability": afford,
        "checks": [{"id": c.id, "status": c.status, "engine_title": c.title,
                    "engine_explanation": c.explanation, "numbers": numbers(c)}
                   for c in report.checks],
    }


WordingWriter = Callable[[dict[str, Any]], str | None]


def call_claude_wording(payload: dict[str, Any]) -> str | None:
    """One Claude call. Returns the JSON text, or None if Claude can't be used. Replaced in tests."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    try:
        message = client.beta.messages.create(
            model=settings.claude_model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload, indent=1)}],
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": _WORDING_SCHEMA}},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
    except anthropic.AnthropicError as exc:
        log.warning("wording.ai.error type=%s", type(exc).__name__)
        return None
    if message.stop_reason != "end_turn":
        log.warning("wording.ai.stop reason=%s", message.stop_reason)
        return None
    return "".join(b.text for b in message.content if b.type == "text")


def apply_wording(report: Report, writer: WordingWriter = call_claude_wording) -> Report:
    worded = engine_wording(report)
    raw = writer(_prompt_input(report))
    if raw is None:
        return worded
    try:
        ai = _AiWording.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        log.warning("wording.ai.invalid type=%s", type(exc).__name__)
        return worded

    by_id = {c.id: c for c in ai.checks}
    used_ai = False
    for check in worded.checks:
        suggestion = by_id.get(check.id)
        if suggestion is None:
            continue
        texts = [suggestion.title, suggestion.explanation, *suggestion.next_steps]
        if not uses_only(texts, allowed_numbers(check)):
            log.warning("wording.ai.rejected check=%s reason=numbers", check.id)
            continue
        check.display = Wording(title=suggestion.title, explanation=suggestion.explanation,
                                next_steps=suggestion.next_steps[:3])
        used_ai = True

    if uses_only([ai.headline, ai.summary, *ai.next_steps], report_numbers(report)):
        worded.summary = Wording(title=ai.headline, explanation=ai.summary, next_steps=ai.next_steps[:3])
        used_ai = True
    else:
        log.warning("wording.ai.rejected check=summary reason=numbers")
    worded.wording_source = "ai" if used_ai else "engine"
    return worded
