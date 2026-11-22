"""Check 7: file_signals.

Looks at how each file was made, not at its figures:
- a statement line printed in a different font from the rest of its page;
- a file changed after the date it covers (a statement's period end, a payslip's pay date).

A check only flags what it actually tested. So these signals are never part of check 1 or 3. They
are judged here, after the other checks, and:
- if another check already flagged the same document, each signal is attached to that check as
  supporting evidence (flag=False) and doesn't count again towards the verdict;
- otherwise this check flags it as its own finding.
"""

from __future__ import annotations

from app.checks.models import CheckResult, DocumentInput, Evidence
from app.checks.pdf_signals import font_changes, modified_after
from app.checks.util import payslips, plural, statements

ID, NUMBER = "file_signals", 7


def collect_signals(docs: list[DocumentInput]) -> list[Evidence]:
    found: list[Evidence] = []
    for doc, s in statements(docs):
        found += font_changes(doc, s)
        if (signal := modified_after(doc, s.period_end)) is not None:
            found.append(signal)
    for doc, p in payslips(docs):
        if (signal := modified_after(doc, p.pay_date.value)) is not None:
            found.append(signal)
    return found


def check_file_signals(docs: list[DocumentInput], others: list[CheckResult]) -> CheckResult:
    signals = collect_signals(docs)
    own: list[Evidence] = []
    hosts: dict[str, list[Evidence]] = {}
    for signal in signals:
        host = _host_for(signal.file, others)
        if host is None:
            own.append(signal)
            continue
        host.evidence.append(signal.model_copy(update={"flag": False}))
        hosts.setdefault(host.id, []).append(signal)
    for host_id, attached in hosts.items():
        host = next(c for c in others if c.id == host_id)
        host.explanation = f"{host.explanation} {_supporting_sentence(attached)}"

    if own:
        return CheckResult(id=ID, number=NUMBER, status="flag", evidence=own,
                           title=_title(own), explanation=_explanation(own))
    if signals:
        # Every signal was on a document another check already flagged, and is shown there.
        names = sorted({c.title for c in others if c.id in hosts})
        return CheckResult(
            id=ID, number=NUMBER, status="pass",
            title="Signs of editing are shown with another check",
            explanation=f"We found signs of editing only in documents already flagged above, so "
                        f"they're shown as supporting evidence under \"{names[0]}\".")
    return CheckResult(id=ID, number=NUMBER, status="pass",
                       title="No signs the files were edited",
                       explanation="The fonts are consistent and no file was changed after the date "
                                   "it covers.")


def _host_for(file: str | None, others: list[CheckResult]) -> CheckResult | None:
    if not file:
        return None
    for check in others:
        if check.status == "flag" and any(e.flag and e.file == file for e in check.evidence):
            return check
    return None


def _title(own: list[Evidence]) -> str:
    if any(e.kind == "modified_after_period" for e in own):
        return "This file was edited after it was issued"
    return "Part of this file looks edited"


def _explanation(own: list[Evidence]) -> str:
    edits = [e for e in own if e.kind == "modified_after_period"]
    fonts = [e for e in own if e.kind == "font_change"]
    parts = []
    if edits:
        d = edits[0].details
        parts.append(
            f"{plural(len(edits), 'file')} {'was' if len(edits) == 1 else 'were'} changed after the "
            f"date {'it covers' if len(edits) == 1 else 'they cover'} (last changed "
            f"{d['modified']}, saved by {d['producer']})")
    if fonts:
        parts.append(f"{plural(len(fonts), 'statement line')} "
                     f"{'uses' if len(fonts) == 1 else 'use'} a different font from the rest of the page")
    sentence = " and ".join(parts)
    return (f"No other check found a problem in this document, but {sentence}. That can be a sign "
            "it was edited.")


def _supporting_sentence(attached: list[Evidence]) -> str:
    fonts = sum(e.kind == "font_change" for e in attached)
    edits = sum(e.kind == "modified_after_period" for e in attached)
    bits = []
    if fonts:
        bits.append(f"{plural(fonts, 'line')} in a different font")
    if edits:
        bits.append("the file was changed after the date it covers")
    return f"The same file also shows signs of editing: {' and '.join(bits)}."
