"""Document reader tests, judged against what fixtures/generate.py put into each PDF."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter

from app.config import Settings
from app.reader import ReaderError, read_document
from app.reader import ai as ai_module
from app.reader import reader as reader_module
from app.reader.ai import OUTPUT_SCHEMA, AiReply

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures"
CASES = ["genuine", "edited_balance", "salary_mismatch", "bad_payslip", "name_mismatch",
         "edited_metadata"]
PHOTO = FIXTURES / "photo_statement" / "statement_2026-08_page1_photo.jpg"


def _load_generator():
    spec = importlib.util.spec_from_file_location("tallied_fixture_generate", FIXTURES / "generate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gen = _load_generator()
GROUND_TRUTH = {make().name: make() for make in gen.CASES}


@pytest.fixture(scope="module", autouse=True)
def fixtures_are_current() -> None:
    """The committed fixture PDFs must match the generator; regenerate if they don't exist."""
    if not all((FIXTURES / c / "expected.json").exists() for c in CASES):
        gen.generate(FIXTURES)


def _read(case: str, name: str):
    return read_document((FIXTURES / case / name).read_bytes())


def _assert_location_on_page(loc, pages) -> None:
    size = pages[loc.page - 1]
    b = loc.bbox
    assert 0 <= b.x0 < b.x1 <= size.width
    assert 0 <= b.top < b.bottom <= size.height


# ---------------------------------------------------------------------------
# Parser path, every fixture folder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
def test_statements_match_generator(case: str) -> None:
    truth = GROUND_TRUTH[case]
    for stmt in truth.statements:
        name = gen.statement_file(stmt)
        result = _read(case, name)
        assert result.source == "parser", name
        assert result.document_type == "bank_statement"
        s = result.statement
        assert s.account_holder.value == truth.holder.display
        assert (s.sort_code, s.account_number) == (gen.ACCOUNT.sort_code, gen.ACCOUNT.number)
        assert s.bank_name == "Northgate Bank"
        assert (s.period_start, s.period_end) == (stmt.period_start, stmt.period_end)
        assert s.opening_balance.value == stmt.opening
        assert s.closing_balance.value == stmt.closing_shown
        assert s.summary_money_in.value == stmt.money_in
        assert s.summary_money_out.value == stmt.money_out

        assert len(s.transactions) == len(stmt.txs), name
        for got, want in zip(s.transactions, stmt.txs, strict=True):
            assert got.date == want.day
            assert got.description == want.description
            assert got.type == want.type
            assert got.paid_in == want.paid_in
            assert got.paid_out == want.paid_out
            assert got.balance == want.balance
            assert got.location.page >= 1 and not got.location.approximate
            _assert_location_on_page(got.location, result.pages)
            expected_font = "BitstreamVeraSans-Roman" if want.font else "Helvetica"
            assert got.font_name == expected_font
            assert got.font_size == (gen.EDIT_SIZE if want.font else gen.BODY_SIZE)


@pytest.mark.parametrize("case", CASES)
def test_payslips_match_generator(case: str) -> None:
    for slip in GROUND_TRUTH[case].payslips:
        result = _read(case, gen.payslip_file(slip))
        assert result.source == "parser"
        assert result.document_type == "payslip"
        p = result.payslip
        assert p.employee_name.value == slip.employee.full_name
        assert p.employer_name.value == slip.employer.name
        assert p.pay_date.value == slip.pay_date
        assert p.gross_pay.value == slip.gross
        assert p.income_tax.value == slip.tax
        assert p.national_insurance.value == slip.ni
        assert p.pension.value == slip.pension
        assert p.other_deductions == []
        assert p.net_pay.value == slip.net
        assert p.tax_code == "1257L"
        for field in (p.employee_name, p.employer_name, p.pay_date, p.gross_pay, p.income_tax,
                      p.national_insurance, p.pension, p.net_pay):
            _assert_location_on_page(field.location, result.pages)


def test_edited_line_is_the_only_odd_font() -> None:
    s = _read("edited_balance", "statement_2026-10.pdf").statement
    odd = [t for t in s.transactions if t.font_name != "Helvetica"]
    assert len(odd) == 1
    assert odd[0].date.isoformat() == "2026-10-12"
    assert odd[0].balance - (s.transactions[s.transactions.index(odd[0]) - 1].balance
                             - odd[0].paid_out) == Decimal("900.00")


@pytest.mark.parametrize("case", CASES)
def test_metadata(case: str) -> None:
    truth = GROUND_TRUTH[case]
    for stmt in truth.statements:
        name = gen.statement_file(stmt)
        meta = _read(case, name).metadata
        want = truth.metadata_overrides.get(name, gen.statement_metadata(stmt))
        assert meta.producer == want["/Producer"]
        assert meta.creator == want["/Creator"]
        assert meta.creation_date.strftime("D:%Y%m%d%H%M%S") == want["/CreationDate"][:16]
        assert meta.modification_date.strftime("D:%Y%m%d%H%M%S") == want["/ModDate"][:16]
    edited = _read("edited_metadata", "statement_2026-10.pdf").metadata
    assert edited.producer == "iLovePDF"
    assert edited.modification_date > edited.creation_date


# ---------------------------------------------------------------------------
# Rejections with friendly codes
# ---------------------------------------------------------------------------


def _password_protected_pdf() -> bytes:
    writer = PdfWriter(clone_from=PdfReader(FIXTURES / "genuine" / "statement_2026-08.pdf"))
    writer.encrypt("secret")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _text_pdf(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


@pytest.mark.parametrize("data, code", [
    (b"", "empty_file"),
    (b"PK\x03\x04 this is a zip file", "unsupported_file_type"),
    (b"%PDF-1.7\n this is not really a pdf", "unreadable_file"),
    (b"\xff\xd8\xff\xe0 broken jpeg", "unreadable_file"),
])
def test_rejects_bad_files(data: bytes, code: str) -> None:
    with pytest.raises(ReaderError) as err:
        read_document(data)
    assert err.value.code == code
    assert err.value.message


def test_rejects_password_protected() -> None:
    with pytest.raises(ReaderError) as err:
        read_document(_password_protected_pdf())
    assert err.value.code == "password_protected"


def test_rejects_too_large_and_too_many_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reader_module, "get_settings", lambda: Settings(max_upload_mb=0, max_pages=1))
    with pytest.raises(ReaderError) as err:
        read_document(b"%PDF-1.7 tiny")
    assert err.value.code == "file_too_large"
    monkeypatch.setattr(reader_module, "get_settings", lambda: Settings(max_pages=1))
    with pytest.raises(ReaderError) as err:
        _read("genuine", "statement_2026-08.pdf")  # 2 pages
    assert err.value.code == "too_many_pages"


def test_unrelated_pdf_is_unknown_without_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_module, "call_claude", lambda pages: pytest.fail("AI should not be called"))
    result = read_document(_text_pdf("Minutes of the residents association meeting held on Tuesday."))
    assert (result.document_type, result.source) == ("unknown", "parser")


# ---------------------------------------------------------------------------
# AI path (Claude mocked)
# ---------------------------------------------------------------------------


def _loc(page: int = 1, top: float = 0.4) -> dict:
    return {"page": page, "bbox": [0.1, top, 0.9, top + 0.012]}


def _ai_statement_json() -> dict:
    truth = json.loads((PHOTO.parent / "expected.json").read_text(encoding="utf-8"))["truth"]
    return {
        "document_type": "bank_statement",
        "payslip": None,
        "statement": {
            "account_holder": {"value": truth["account_holder"], "location": _loc(top=0.2)},
            "bank_name": "Northgate Bank", "sort_code": "60-91-23", "account_number": "71839264",
            "period_start": truth["period_start"], "period_end": truth["period_end"],
            "opening_balance": {"value": truth["opening_balance"], "location": _loc(top=0.3)},
            "closing_balance": None, "summary_money_in": None, "summary_money_out": None,
            "transactions": [
                {"date": t["date"], "description": t["description"], "type": None,
                 "paid_in": t["paid_in"], "paid_out": t["paid_out"], "balance": t["balance"],
                 "location": _loc(top=0.4 + i * 0.013)}
                for i, t in enumerate(truth["transactions"])
            ],
        },
    }


def _fake_claude(reply: AiReply, seen: list | None = None):
    def fake(pages):
        if seen is not None:
            seen.extend(pages)
        return reply
    return fake


def test_photo_goes_through_ai_path_and_returns_same_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list = []
    payload = _ai_statement_json()
    monkeypatch.setattr(ai_module, "call_claude",
                        _fake_claude(AiReply("end_turn", json.dumps(payload)), sent))
    result = read_document(PHOTO.read_bytes())

    assert (result.document_type, result.source, result.page_count) == ("bank_statement", "ai", 1)
    assert len(sent) == 1 and sent[0].media_type == "image/jpeg"
    page = result.pages[0]
    assert page.unit == "px" and max(page.width, page.height) <= reader_module.MAX_IMAGE_EDGE
    s = result.statement
    assert s.opening_balance.value == Decimal("2486.73")
    assert len(s.transactions) == len(payload["statement"]["transactions"]) == 32
    first = s.transactions[0]
    assert first.paid_out == Decimal("950.00") and first.balance == Decimal("1536.73")
    for t in s.transactions:
        assert t.location.approximate and t.location.page == 1
        _assert_location_on_page(t.location, result.pages)
    assert first.location.bbox.x0 == pytest.approx(0.1 * page.width, abs=0.01)


def test_scanned_pdf_goes_to_ai_with_rendered_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    # A PDF containing only an image of the page, no text layer
    doc = pymupdf.open()
    doc.new_page(width=595, height=842).insert_image(pymupdf.Rect(0, 0, 595, 842),
                                                     filename=str(PHOTO))
    sent: list = []
    monkeypatch.setattr(ai_module, "call_claude",
                        _fake_claude(AiReply("end_turn", json.dumps(_ai_statement_json())), sent))
    result = read_document(doc.tobytes())
    assert result.source == "ai" and result.pages[0].unit == "pt"
    assert sent[0].media_type == "image/png"
    assert result.statement.transactions[0].location.bbox.x1 == pytest.approx(0.9 * 595, abs=0.5)


@pytest.mark.parametrize("reply, code", [
    (AiReply("max_tokens", '{"document_type": "bank_statement", "statement": {"transa'), "ai_truncated"),
    (AiReply("refusal", ""), "ai_refused"),
    (AiReply("end_turn", "not json at all"), "ai_invalid_response"),
    (AiReply("end_turn", '{"document_type": "bank_statement", "statement": null, "payslip": null}'),
     "ai_invalid_response"),
    (AiReply("end_turn", '{"document_type": "invoice", "statement": null, "payslip": null}'),
     "ai_invalid_response"),
])
def test_bad_ai_replies_become_friendly_errors(monkeypatch, reply: AiReply, code: str) -> None:
    monkeypatch.setattr(ai_module, "call_claude", _fake_claude(reply))
    with pytest.raises(ReaderError) as err:
        read_document(PHOTO.read_bytes())
    assert err.value.code == code


def test_ai_bad_amount_and_bad_page_are_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    for mutate in (lambda p: p["statement"]["transactions"][0].update(paid_out="nine fifty"),
                   lambda p: p["statement"]["transactions"][0].update(location=_loc(page=7))):
        payload = _ai_statement_json()
        mutate(payload)
        monkeypatch.setattr(ai_module, "call_claude", _fake_claude(AiReply("end_turn", json.dumps(payload))))
        with pytest.raises(ReaderError) as err:
            read_document(PHOTO.read_bytes())
        assert err.value.code == "ai_invalid_response"


def test_ai_without_api_key_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_module, "get_settings", lambda: Settings(anthropic_api_key=""))
    with pytest.raises(ReaderError) as err:
        read_document(PHOTO.read_bytes())
    assert err.value.code == "ai_unavailable"


def test_output_schema_is_strict() -> None:
    """Structured outputs need every object closed with every property required."""
    def walk(node) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(OUTPUT_SCHEMA)


# ---------------------------------------------------------------------------
# Live AI test: real Claude call. Runs only with ANTHROPIC_API_KEY set (api/.env or env).
# ---------------------------------------------------------------------------


@pytest.mark.live_ai
@pytest.mark.skipif(not Settings().anthropic_api_key, reason="ANTHROPIC_API_KEY not set")
def test_live_photo_through_claude() -> None:
    truth = json.loads((PHOTO.parent / "expected.json").read_text(encoding="utf-8"))["truth"]
    result = read_document(PHOTO.read_bytes())
    assert (result.document_type, result.source) == ("bank_statement", "ai")
    s = result.statement
    assert s.opening_balance.value == Decimal(truth["opening_balance"])
    assert "Hayes" in s.account_holder.value
    assert len(s.transactions) == len(truth["transactions"])
    matched = 0
    for got, want in zip(s.transactions, truth["transactions"], strict=True):
        _assert_location_on_page(got.location, result.pages)
        same = (got.date.isoformat() == want["date"]
                and str(got.paid_out) == str(want["paid_out"])
                and str(got.paid_in) == str(want["paid_in"])
                and str(got.balance) == want["balance"])
        matched += same
        if not same:
            print("mismatch:", got.date, got.paid_out, got.paid_in, got.balance, "vs", want)
    print(f"live AI matched {matched}/{len(truth['transactions'])} transactions exactly")
    assert matched == len(truth["transactions"])
