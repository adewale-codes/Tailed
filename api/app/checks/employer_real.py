"""Check 4: employer_real.

Looks each payslip employer up on Companies House. Flags no match, a company that isn't
active, one incorporated less than 6 months before the first payslip, or one with no filed
accounts. If Companies House can't be asked, the result is not_checked, never a pass.
"""

from __future__ import annotations

from datetime import date

from app.checks.companies_house import CompanyLookup, LookupUnavailable
from app.checks.models import CheckResult, DocumentInput, Evidence
from app.checks.util import company_tokens, long_date, payslips

MIN_AGE_MONTHS = 6


def months_before(d: date, months: int) -> date:
    year, month = divmod(d.year * 12 + d.month - 1 - months, 12)
    month += 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def check_employer_real(docs: list[DocumentInput], companies: CompanyLookup) -> CheckResult:
    slips = [(d, p) for d, p in payslips(docs) if p.employer_name]
    if not slips:
        return CheckResult(id="employer_real", number=4, status="not_checked",
                           title="No employer to look up",
                           explanation="We couldn't find an employer name on any payslip.")

    employers: dict[tuple[str, ...], list] = {}
    for d, p in slips:
        employers.setdefault(tuple(company_tokens(p.employer_name.value)), []).append((d, p))

    evidence: list[Evidence] = []
    for group in employers.values():
        doc, first = group[0]
        name = first.employer_name.value
        first_pay = min(p.pay_date.value for _, p in group)
        where = [first.employer_name.location]
        try:
            record = companies.find(name)
        except LookupUnavailable:
            return CheckResult(
                id="employer_real", number=4, status="not_checked",
                title="Employer not checked",
                explanation="We couldn't reach Companies House just now, so we haven't checked "
                            "whether the employer is a registered company.")
        if record is None:
            evidence.append(Evidence.at("employer_not_found", doc.file, where, flag=True,
                                        details={"employer": name}))
            continue
        details = {"employer": name, "company_number": record.number, "registered_name": record.name,
                   "status": record.status or "unknown",
                   "incorporated": (long_date(record.date_of_creation)
                                    if record.date_of_creation else "unknown"),
                   "first_payslip": long_date(first_pay)}
        if (record.status or "").lower() != "active":
            evidence.append(Evidence.at("employer_not_active", doc.file, where, flag=True, details=details))
        if record.date_of_creation and record.date_of_creation > months_before(first_pay, MIN_AGE_MONTHS):
            evidence.append(Evidence.at("employer_too_new", doc.file, where, flag=True, details=details))
        if not record.has_filed_accounts:
            evidence.append(Evidence.at("employer_no_accounts", doc.file, where, flag=True, details=details))
        if not any(e.flag and e.details.get("employer") == name for e in evidence):
            evidence.append(Evidence.at("employer_found", doc.file, where, flag=False, details=details))

    flagged = [e for e in evidence if e.flag]
    if not flagged:
        return CheckResult(id="employer_real", number=4, status="pass", evidence=evidence,
                           title="Employer is a registered company",
                           explanation="The employer on the payslips is an active company on "
                                       "Companies House with filed accounts, registered more than "
                                       "6 months before the first payslip.")
    explanation = {
        "employer_not_found": "We couldn't find the employer named on the payslips on Companies House.",
        "employer_not_active": "The employer named on the payslips is not an active company on "
                               "Companies House.",
        "employer_too_new": "The employer was registered on Companies House less than 6 months "
                            "before the first payslip.",
        "employer_no_accounts": "The employer is on Companies House but has never filed accounts.",
    }[flagged[0].kind]
    return CheckResult(id="employer_real", number=4, status="flag", evidence=evidence,
                       title="We couldn't confirm the employer", explanation=explanation)
