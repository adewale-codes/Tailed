"""Checks engine, verdict and wording tests, judged against the fixtures' expected.json."""

from __future__ import annotations

import base64
import json
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path

import httpx
import pytest

from app.checks import DocumentInput, decide_verdict, run_checks
from app.checks.companies_house import (
    CompaniesHouseClient,
    CompanyRecord,
    StaticCompanies,
    UnavailableCompanies,
)
from app.checks.models import CheckResult
from app.checks.names_match import same_person
from app.checks.payslip_realistic import monthly_ni, monthly_tax
from app.config import Settings
from app.reader import read_document
from app.reader.models import BBox, Location, Transaction
from app.report import wording
from app.report.wording import apply_wording, numbers_in

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
CASES = ["genuine", "edited_balance", "salary_mismatch", "bad_payslip", "name_mismatch",
         "edited_metadata", "multiple_problems"]
RENT = Decimal("1100")
REGISTRY = StaticCompanies([
    CompanyRecord(**r)
    for r in json.loads((FIXTURES / "companies_house.json").read_text(encoding="utf-8"))["records"]
])


@cache
def _docs(case: str) -> tuple[DocumentInput, ...]:
    return tuple(DocumentInput(p.name, read_document(p.read_bytes()))
                 for p in sorted((FIXTURES / case).glob("*.pdf")))


def docs(case: str) -> list[DocumentInput]:
    return [DocumentInput(d.file, d.result.model_copy(deep=True)) for d in _docs(case)]


def expected(case: str) -> dict:
    return json.loads((FIXTURES / case / "expected.json").read_text(encoding="utf-8"))


def check(report, check_id: str) -> CheckResult:
    return next(c for c in report.checks if c.id == check_id)


# ---------------------------------------------------------------------------
# Every fixture gets its expected verdict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
def test_fixture_verdicts(case: str) -> None:
    want = expected(case)
    report = run_checks(docs(case), RENT, REGISTRY)
    assert report.verdict == want["expected_verdict"]
    flagged = {c.id for c in report.checks if c.status == "flag"}
    assert flagged == {k for k, v in want["expected_checks"].items() if v == "flag"}
    for c in report.checks:
        if want["expected_checks"][c.id] == "pass":
            assert c.status == "pass", (c.id, c.explanation)
    assert [c.number for c in report.checks] == [1, 2, 3, 4, 5, 6]


def test_genuine_raises_zero_flags() -> None:
    report = run_checks(docs("genuine"), RENT, REGISTRY)
    assert all(c.status == "pass" for c in report.checks)
    assert not any(e.flag for c in report.checks for e in c.evidence)
    assert report.verdict == "looks_good"


# ---------------------------------------------------------------------------
# Check 1: balances
# ---------------------------------------------------------------------------


def test_edited_balance_evidence() -> None:
    c = check(run_checks(docs("edited_balance"), RENT, REGISTRY), "balances_add_up")
    mismatch = [e for e in c.evidence if e.kind == "balance_mismatch"]
    assert len(mismatch) == 1  # judged against the line before, so no cascade
    n = mismatch[0].numbers
    assert (n["expected"], n["shown"], n["difference"]) == (
        Decimal("1429.61"), Decimal("2329.61"), Decimal("900.00"))
    assert mismatch[0].file == "statement_2026-10.pdf" and mismatch[0].page == 1
    assert len(mismatch[0].bboxes) == 2  # previous line and the edited line
    summary = next(e for e in c.evidence if e.kind == "summary_mismatch")
    assert summary.numbers["difference"] == Decimal("900.00")
    font = next(e for e in c.evidence if e.kind == "font_change")
    assert font.details["date"] == "2026-10-12" and "Vera" in font.details["font"]


def test_edited_metadata_is_supporting_evidence() -> None:
    c = check(run_checks(docs("edited_metadata"), RENT, REGISTRY), "balances_add_up")
    assert [e.kind for e in c.evidence] == ["modified_after_period"]
    assert c.evidence[0].details["producer"] == "iLovePDF"
    assert c.evidence[0].file == "statement_2026-10.pdf"


def test_penny_rounding_is_tolerated() -> None:
    d = docs("genuine")
    stmt = next(x.result.statement for x in d if x.result.statement)
    stmt.transactions[5].balance += Decimal("0.01")  # off by exactly 1p: allowed
    assert check(run_checks(d, RENT, REGISTRY), "balances_add_up").status == "pass"
    stmt.transactions[5].balance += Decimal("0.01")  # now 2p: flagged
    assert check(run_checks(d, RENT, REGISTRY), "balances_add_up").status == "flag"


# ---------------------------------------------------------------------------
# Check 2: documents agree
# ---------------------------------------------------------------------------


def test_salary_mismatch_numbers() -> None:
    c = check(run_checks(docs("salary_mismatch"), RENT, REGISTRY), "documents_agree")
    main = [e for e in c.evidence if e.kind == "salary_vs_payslip"]
    assert len(main) == 2 and all(e.flag for e in main)
    n = main[0].numbers
    assert (n["payslip_net"], n["statement_credit"], n["difference"], n["difference_pct"]) == (
        Decimal("3100.64"), Decimal("2650.00"), Decimal("-450.64"), Decimal("14.5"))
    credit = next(e for e in c.evidence if e.kind == "salary_credit")
    assert credit.file.startswith("statement_") and credit.bboxes


@pytest.mark.parametrize("factor, status", [("1.019", "pass"), ("1.021", "flag")])
def test_two_percent_threshold(factor: str, status: str) -> None:
    d = docs("genuine")
    for x in d:
        if x.result.payslip:
            x.result.payslip.net_pay.value = (Decimal("2650.00") * Decimal(factor)).quantize(Decimal("0.01"))
    assert check(run_checks(d, RENT, REGISTRY), "documents_agree").status == status


def test_missing_salary_credit_is_flagged() -> None:
    d = docs("genuine")
    for x in d:
        if x.result.statement:
            x.result.statement.transactions = [t for t in x.result.statement.transactions
                                               if t.type != "BGC"]
    c = check(run_checks(d, RENT, REGISTRY), "documents_agree")
    assert c.status == "flag" and c.evidence[0].kind == "no_matching_payment"


def test_documents_agree_not_checked_without_payslips() -> None:
    d = [x for x in docs("genuine") if x.result.statement]
    assert check(run_checks(d, RENT, REGISTRY), "documents_agree").status == "not_checked"


# ---------------------------------------------------------------------------
# Check 3: payslip realistic
# ---------------------------------------------------------------------------


def test_tax_and_ni_model() -> None:
    assert monthly_tax(Decimal("3517.40")) == Decimal("493.98")
    assert monthly_ni(Decimal("3517.40")) == Decimal("197.55")
    assert monthly_tax(Decimal("900")) == Decimal("0.00")
    assert monthly_ni(Decimal("900")) == Decimal("0.00")


def test_bad_payslip_evidence() -> None:
    c = check(run_checks(docs("bad_payslip"), RENT, REGISTRY), "payslip_realistic")
    tax = next(e for e in c.evidence if e.kind == "income_tax")
    ni = next(e for e in c.evidence if e.kind == "national_insurance")
    assert tax.flag and tax.numbers["shown"] == Decimal("310.00")
    assert ni.flag and (ni.numbers["shown"], ni.numbers["expected"]) == (Decimal("95.00"), Decimal("259.50"))
    assert tax.bboxes and tax.page == 1


def test_non_standard_tax_code_not_checked() -> None:
    d = docs("genuine")
    for x in d:
        if x.result.payslip:
            x.result.payslip.tax_code = "BR"
    c = check(run_checks(d, RENT, REGISTRY), "payslip_realistic")
    assert c.status == "not_checked"


def test_deductions_must_add_up() -> None:
    d = docs("genuine")
    for x in d:
        if x.result.payslip:
            x.result.payslip.net_pay.value = Decimal("2700.00")
    c = check(run_checks(d, RENT, REGISTRY), "payslip_realistic")
    assert c.status == "flag" and any(e.kind == "deductions_dont_add_up" for e in c.evidence)


# ---------------------------------------------------------------------------
# Check 4: employer real (Companies House)
# ---------------------------------------------------------------------------


def test_no_companies_house_key_is_not_checked_never_pass() -> None:
    for lookup in (CompaniesHouseClient(api_key=""), UnavailableCompanies()):
        report = run_checks(docs("genuine"), RENT, lookup)
        c = check(report, "employer_real")
        assert c.status == "not_checked"
        assert report.verdict == "looks_good"  # not_checked isn't a flag...
        assert c.status != "pass"              # ...and is never shown as a pass


def _transport(search: dict | None, profile: dict | None, status: int = 200, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status)
        if request.url.path == "/search/companies":
            return httpx.Response(200, json=search or {"items": []})
        return httpx.Response(200, json=profile) if profile else httpx.Response(404)
    return httpx.MockTransport(handler)


SEARCH = {"items": [{"title": "BRIGHTWATER LOGISTICS LIMITED", "company_number": "00000000",
                     "company_status": "active", "date_of_creation": "2014-03-11"}]}


def _profile(created: str = "2014-03-11", status: str = "active", accounts: bool = True) -> dict:
    profile = {"company_name": "BRIGHTWATER LOGISTICS LIMITED", "company_number": "00000000",
               "company_status": status, "date_of_creation": created, "accounts": {}}
    if accounts:
        profile["accounts"]["last_accounts"] = {"period_end_on": "2025-12-31", "type": "full"}
    return profile


def _employer(transport) -> CheckResult:
    client = CompaniesHouseClient(api_key="test-key", transport=transport)
    return check(run_checks(docs("genuine"), RENT, client), "employer_real")


def test_companies_house_match_passes_and_sends_key() -> None:
    seen: list[httpx.Request] = []
    c = _employer(_transport(SEARCH, _profile(), seen=seen))
    assert c.status == "pass"
    assert seen[0].url.params["q"] == "Brightwater Logistics Ltd"
    assert seen[0].headers["authorization"] == "Basic " + base64.b64encode(b"test-key:").decode()


@pytest.mark.parametrize("search, profile, kind", [
    ({"items": [{"title": "BRIGHTWATER CLEANING LTD", "company_number": "1"}]}, None, "employer_not_found"),
    (SEARCH, _profile(created="2026-05-01"), "employer_too_new"),
    (SEARCH, _profile(accounts=False), "employer_no_accounts"),
    (SEARCH, _profile(status="dissolved"), "employer_not_active"),
])
def test_companies_house_flags(search, profile, kind: str) -> None:
    c = _employer(_transport(search, profile))
    assert c.status == "flag"
    assert kind in {e.kind for e in c.evidence if e.flag}


@pytest.mark.parametrize("status", [401, 429, 500, 503])
def test_companies_house_errors_are_not_checked(status: int) -> None:
    assert _employer(_transport(SEARCH, _profile(), status=status)).status == "not_checked"


def test_companies_house_network_failure_is_not_checked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")
    assert _employer(httpx.MockTransport(handler)).status == "not_checked"


# ---------------------------------------------------------------------------
# Check 5: looks like real life
# ---------------------------------------------------------------------------


def _tx(day: int, desc: str, out: str) -> Transaction:
    loc = Location(page=1, bbox=BBox(x0=40, top=100 + day, x1=550, bottom=110 + day))
    return Transaction(date=date(2026, 8, day), description=desc, paid_out=Decimal(out), location=loc)


def test_repeated_round_amounts_flagged() -> None:
    d = docs("genuine")
    stmt = next(x.result.statement for x in d if x.result.statement)
    stmt.transactions = [_tx(i, "TRANSFER", "500.00") for i in range(2, 7)]
    c = check(run_checks(d, RENT, REGISTRY), "looks_like_real_life")
    assert c.status == "flag"
    run = next(e for e in c.evidence if e.kind == "repeated_round_amounts")
    assert run.numbers == {"amount": Decimal("500.00"), "run_length": Decimal(5)}
    assert len(run.bboxes) == 5


def test_three_round_amounts_is_not_enough() -> None:
    d = docs("genuine")
    stmt = next(x.result.statement for x in d if x.result.statement)
    stmt.transactions[3:3] = [_tx(9, "TRANSFER", "100.00") for _ in range(3)]
    c = check(run_checks(d, RENT, REGISTRY), "looks_like_real_life")
    assert not any(e.kind == "repeated_round_amounts" for e in c.evidence)


# ---------------------------------------------------------------------------
# Check 6: names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("a, b, same", [
    ("Mr Jordan A Hayes", "Jordan A Hayes", True),
    ("MR JORDAN A HAYES", "Jordan Hayes", True),
    ("Mr Jordan A Hayes", "J Hayes", True),
    ("Ms Siobhán O'Neill", "SIOBHÁN ONEILL", True),
    ("Mr Jordan A Hayes", "Daniel K Okafor", False),
    ("Mr Jordan A Hayes", "Jordan Hughes", False),
    ("Mr Jordan A Hayes", "Joanne Hayes", False),
])
def test_same_person(a: str, b: str, same: bool) -> None:
    assert same_person(a, b) is same


# ---------------------------------------------------------------------------
# Verdict and affordability
# ---------------------------------------------------------------------------


def _checks(**statuses: str) -> list[CheckResult]:
    ids = ["balances_add_up", "documents_agree", "payslip_realistic", "employer_real",
           "looks_like_real_life", "names_match"]
    return [CheckResult(id=i, number=n, status=statuses.get(i, "pass"), title="t", explanation="e")
            for n, i in enumerate(ids, 1)]


@pytest.mark.parametrize("statuses, verdict", [
    ({}, "looks_good"),
    ({"employer_real": "not_checked"}, "looks_good"),
    ({"names_match": "flag"}, "needs_a_closer_look"),
    ({"balances_add_up": "flag"}, "needs_a_closer_look"),
    ({"names_match": "flag", "employer_real": "flag"}, "needs_a_closer_look"),
    ({"balances_add_up": "flag", "names_match": "flag"}, "high_risk"),
    ({"documents_agree": "flag", "payslip_realistic": "flag"}, "high_risk"),
    ({"balances_add_up": "flag", "documents_agree": "flag"}, "high_risk"),
])
def test_verdict_rules(statuses: dict, verdict: str) -> None:
    assert decide_verdict(_checks(**statuses))[0] == verdict


def test_affordability() -> None:
    a = run_checks(docs("salary_mismatch"), RENT, REGISTRY).affordability
    assert a.monthly_rent == Decimal("1100.00")
    assert a.payslip_net_pay == Decimal("3100.64")
    assert a.rent_pct_of_payslip_net == Decimal("35.5")
    assert a.months_covered == Decimal("3.0")
    assert a.average_monthly_income_in_account == Decimal("2652.09")
    assert a.rent_pct_of_account_income == Decimal("41.5")


# ---------------------------------------------------------------------------
# Wording: Claude may reword, but never changes a number or a status
# ---------------------------------------------------------------------------


def _engine_numbers(c: CheckResult) -> set[Decimal]:
    return wording.allowed_numbers(c)


def _faithful_writer(payload: dict) -> str:
    """Pretends to be Claude: rewrites each check, quoting only numbers it was given."""
    checks = []
    for c in payload["checks"]:
        nums = list(c["numbers"].items())[:2]
        quoted = " and ".join(f"{k.replace('_', ' ')} {v}" for k, v in nums)
        checks.append({"id": c["id"], "title": f"Friendly: {c['engine_title']}",
                       "explanation": f"In short: {quoted}." if quoted else "Nothing to worry about.",
                       "next_steps": ["Ask the tenant about it."] if c["status"] == "flag" else []})
    return json.dumps({"headline": "Here's what we found",
                       "summary": f"Rent is {payload['affordability']['rent_pct_of_payslip_net']}% of pay.",
                       "next_steps": [], "checks": checks})


@pytest.mark.parametrize("case", CASES)
def test_wording_keeps_engine_numbers_and_statuses(case: str) -> None:
    engine = run_checks(docs(case), RENT, REGISTRY)
    worded = apply_wording(engine, writer=_faithful_writer)

    assert worded.wording_source == "ai"
    assert worded.verdict == engine.verdict
    for before, after in zip(engine.checks, worded.checks, strict=True):
        assert after.status == before.status
        assert after.evidence == before.evidence  # numbers untouched
        assert after.display.title.startswith("Friendly:")
        shown = numbers_in(" ".join([after.display.title, after.display.explanation,
                                     *after.display.next_steps]))
        assert shown <= _engine_numbers(before)


def test_wording_with_changed_number_falls_back_to_engine() -> None:
    engine = run_checks(docs("salary_mismatch"), RENT, REGISTRY)

    def sneaky(payload: dict) -> str:
        data = json.loads(_faithful_writer(payload))
        for c in data["checks"]:
            if c["id"] == "documents_agree":
                c["explanation"] = "The payslip says £3,100.64 but only £2,700.00 arrived."  # wrong
        return json.dumps(data)

    worded = apply_wording(engine, writer=sneaky)
    agree = check(worded, "documents_agree")
    assert agree.display.title == agree.title          # engine wording kept
    assert agree.display.explanation == agree.explanation
    assert "2,700" not in agree.display.explanation
    assert check(worded, "balances_add_up").display.title.startswith("Friendly:")  # others still AI


@pytest.mark.parametrize("reply", [None, "not json", '{"headline": "x"}'])
def test_wording_unavailable_or_invalid_uses_engine(reply) -> None:
    engine = run_checks(docs("bad_payslip"), RENT, REGISTRY)
    worded = apply_wording(engine, writer=lambda payload: reply)
    assert worded.wording_source == "engine"
    for c in worded.checks:
        assert (c.display.title, c.display.explanation) == (c.title, c.explanation)
    assert check(worded, "payslip_realistic").display.next_steps  # engine next steps for a flag
    assert worded.summary.title == "Some things need a closer look"


def test_wording_payload_has_no_names_or_files() -> None:
    sent: list[dict] = []
    engine = run_checks(docs("name_mismatch"), RENT, REGISTRY)
    apply_wording(engine, writer=lambda p: sent.append(p) or None)
    text = json.dumps(sent[0])
    for private in ("Hayes", "Okafor", "Brightwater", "statement_2026", "payslip_2026", "PUREGYM"):
        assert private.lower() not in text.lower()


@pytest.mark.live_ai
@pytest.mark.skipif(not Settings().anthropic_api_key, reason="ANTHROPIC_API_KEY not set")
def test_live_wording_keeps_numbers() -> None:
    for case in ("genuine", "salary_mismatch"):
        engine = run_checks(docs(case), RENT, REGISTRY)
        worded = apply_wording(engine)
        assert worded.wording_source == "ai"
        for before, after in zip(engine.checks, worded.checks, strict=True):
            assert after.status == before.status
            shown = numbers_in(" ".join([after.display.title, after.display.explanation,
                                         *after.display.next_steps]))
            assert shown <= _engine_numbers(before)
            print(f"[{case}] {after.id}: {after.display.title} | {after.display.explanation}")


def test_evidence_points_at_the_flagged_line_even_across_a_page_break() -> None:
    from app.checks.models import Evidence

    prev = Location(page=1, bbox=BBox(x0=40, top=780, x1=550, bottom=790))
    line = Location(page=2, bbox=BBox(x0=40, top=120, x1=550, bottom=130))
    ev = Evidence.at("balance_mismatch", "5", [prev, line], flag=True, focus=1)
    assert ev.page == 2 and ev.bboxes == [line.bbox] and ev.primary == 0

    same_page = Location(page=2, bbox=BBox(x0=40, top=100, x1=550, bottom=110))
    ev = Evidence.at("balance_mismatch", "5", [same_page, line], flag=True, focus=1)
    assert ev.page == 2 and ev.bboxes == [same_page.bbox, line.bbox] and ev.primary == 1

    ev = Evidence.at("modified_after_period", "5", [], flag=True)
    assert ev.page is None and ev.bboxes == [] and ev.primary == 0


def test_edited_balance_highlights_the_edited_line() -> None:
    """The primary box of the balance finding is the 12 Oct line the fixture edited."""
    report = run_checks(docs("edited_balance"), RENT, REGISTRY)
    ev = next(e for c in report.checks for e in c.evidence if e.kind == "balance_mismatch")
    font = next(e for c in report.checks for e in c.evidence if e.kind == "font_change")
    # The edited line is the one drawn in a different font: same page and same box.
    assert ev.page == font.page and ev.bboxes[ev.primary] == font.bboxes[0]
    assert str(ev.numbers["shown"]) == expected("edited_balance")["expected_flags"][0]["balance_shown"]
