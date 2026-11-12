"""Runs fixtures/generate.py into a temp folder and checks the output is complete and correct."""

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pdfplumber
import pytest
from pypdf import PdfReader

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "fixtures" / "generate.py"
CASES = ["genuine", "edited_balance", "salary_mismatch", "bad_payslip", "name_mismatch",
         "edited_metadata"]
CHECK_IDS = {"balances_add_up", "documents_agree", "payslip_realistic", "employer_real",
             "looks_like_real_life", "names_match"}


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("fixtures")
    result = subprocess.run([sys.executable, str(GENERATOR), "--out", str(out)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return out


def load(out_dir: Path, case: str) -> dict:
    return json.loads((out_dir / case / "expected.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES)
def test_case_folder_complete(out_dir: Path, case: str) -> None:
    folder = out_dir / case
    assert folder.is_dir()
    data = load(out_dir, case)
    assert data["case"] == case
    assert data["synthetic"] is True
    assert set(data["expected_checks"]) == CHECK_IDS
    assert data["expected_verdict"] in {"looks_good", "needs_a_closer_look", "high_risk"}

    files = [s["file"] for s in data["truth"]["statements"]]
    files += [p["file"] for p in data["truth"]["payslips"]]
    assert len(files) == 5
    assert sorted(files) == sorted(p.name for p in folder.glob("*.pdf"))
    for name in files:
        with pdfplumber.open(folder / name) as pdf:
            assert pdf.pages, name
            assert pdf.pages[0].extract_text().strip(), name


def test_only_genuine_passes(out_dir: Path) -> None:
    for case in CASES:
        data = load(out_dir, case)
        flagged = {k for k, v in data["expected_checks"].items() if v == "flag"}
        if case == "genuine":
            assert data["expected_verdict"] == "looks_good" and not flagged
        else:
            assert data["expected_verdict"] != "looks_good" and flagged
            assert {f["check"] for f in data["expected_flags"]} == flagged


def _balance_rows(pdf_path: Path) -> list[tuple[str, Decimal]]:
    """Return (date text, balance) for each transaction row, read back from the PDF."""
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for line in page.extract_text().splitlines():
                parts = line.split()
                if len(parts) > 4 and parts[0].isdigit() and len(parts[0]) == 2:
                    try:
                        rows.append((" ".join(parts[:3]), Decimal(parts[-1].replace(",", ""))))
                    except ArithmeticError:
                        continue
    return rows


def test_genuine_statements_add_up(out_dir: Path) -> None:
    data = load(out_dir, "genuine")
    statements = data["truth"]["statements"]
    for prev, cur in zip(statements, statements[1:], strict=False):
        assert prev["closing_balance_true"] == cur["opening_balance"]
    for s in statements:
        assert s["closing_balance_true"] == s["closing_balance_shown"]
        opening = Decimal(s["opening_balance"])
        assert opening + Decimal(s["money_in"]) - Decimal(s["money_out"]) == Decimal(
            s["closing_balance_true"])
        assert [c["amount"] for c in s["salary_credits"]] == ["2650.00"]
        assert s["salary_credits"][0]["date"].endswith("-27")
        # The last balance printed in the PDF is the closing balance.
        rows = _balance_rows(out_dir / "genuine" / s["file"])
        assert rows[-1][1] == Decimal(s["closing_balance_true"])
    for p in data["truth"]["payslips"]:
        assert p["net"] == "2650.00"


def test_edited_balance_line_uses_different_font(out_dir: Path) -> None:
    path = out_dir / "edited_balance" / "statement_2026-10.pdf"
    fonts_by_line: dict[str, set[str]] = {}
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for word in page.extract_words(extra_attrs=["fontname"]):
                fonts_by_line.setdefault(f"{page.page_number}:{round(word['top'])}", set()).add(
                    word["fontname"])
    odd = {k: v for k, v in fonts_by_line.items() if any("Vera" in f for f in v)}
    assert len(odd) == 1, odd

    flag = load(out_dir, "edited_balance")["expected_flags"][0]
    assert Decimal(flag["balance_shown"]) - Decimal(flag["balance_expected"]) == Decimal("900.00")


def test_edited_metadata(out_dir: Path) -> None:
    edited = PdfReader(out_dir / "edited_metadata" / "statement_2026-10.pdf").metadata
    original = PdfReader(out_dir / "genuine" / "statement_2026-10.pdf").metadata
    assert edited["/Producer"] == "iLovePDF"
    assert edited["/ModDate"] > edited["/CreationDate"]
    assert original["/Producer"].startswith("Northgate")
    assert original["/ModDate"] == original["/CreationDate"]


def test_salary_mismatch_payslip_figures(out_dir: Path) -> None:
    slips = load(out_dir, "salary_mismatch")["truth"]["payslips"]
    for p in slips:
        assert (p["gross"], p["tax"], p["ni"], p["pension"], p["net"]) == (
            "4190.00", "628.50", "251.36", "209.50", "3100.64")


def test_name_mismatch(out_dir: Path) -> None:
    truth = load(out_dir, "name_mismatch")["truth"]
    names = {p["employee_name"] for p in truth["payslips"]}
    assert names and truth["account_holder"] not in names
