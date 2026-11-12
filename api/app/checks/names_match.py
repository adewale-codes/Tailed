"""Check 6: names_match.

Normalises every name (case, punctuation, titles such as Mr/Ms) and compares each document's
name with the first bank statement's. Names match when the surname is the same and the first
name is the same or one side gives only its initial. Middle names and initials are ignored,
so "Mr Jordan A Hayes" matches "Jordan Hayes" and "J Hayes".
"""

from __future__ import annotations

from app.checks.models import CheckResult, DocumentInput, Evidence
from app.checks.util import name_tokens, payslips, statements
from app.reader.models import LocatedText


def same_person(a: str, b: str) -> bool:
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb or ta[-1] != tb[-1]:
        return False
    fa, fb = ta[0], tb[0]
    if len(ta) == 1 or len(tb) == 1:
        return True  # surname only on one side
    return fa == fb or (len(fa) == 1 and fb.startswith(fa)) or (len(fb) == 1 and fa.startswith(fb))


def check_names_match(docs: list[DocumentInput]) -> CheckResult:
    named: list[tuple[str, str, LocatedText]] = []
    for doc, s in statements(docs):
        if s.account_holder:
            named.append(("statement", doc.file, s.account_holder))
    for doc, p in payslips(docs):
        if p.employee_name:
            named.append(("payslip", doc.file, p.employee_name))
    if len(named) < 2:
        return CheckResult(id="names_match", number=6, status="not_checked",
                           title="Not enough names to compare",
                           explanation="We need a name on at least two documents to compare them.")

    _, ref_file, ref = named[0]
    evidence: list[Evidence] = []
    for kind, file, name in named[1:]:
        matches = same_person(ref.value, name.value)
        evidence.append(Evidence.at(
            "name_differs" if not matches else "name_matches", file, [name.location], flag=not matches,
            details={"document_type": kind, "name": name.value, "reference_name": ref.value,
                     "reference_file": ref_file}))
    flagged = [e for e in evidence if e.flag]
    if not flagged:
        return CheckResult(id="names_match", number=6, status="pass", evidence=evidence,
                           title="Names match",
                           explanation="The same name appears on all the bank statements and payslips.")
    kinds = {e.details["document_type"] for e in flagged}
    which = "payslips" if kinds == {"payslip"} else "documents"
    return CheckResult(id="names_match", number=6, status="flag", evidence=evidence,
                       title="Names don't match",
                       explanation=f"The name on some of the {which} is different from the name on "
                                   "the bank statements.")
