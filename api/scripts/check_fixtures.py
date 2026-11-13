"""Run every fixture through reader + checks and print expected vs actual verdicts.

    cd api && uv run python scripts/check_fixtures.py [--rent 1100] [--no-companies-house]

Companies House is answered from fixtures/companies_house.json (the employer is fictional), or
treated as unavailable with --no-companies-house. Prints no document contents beyond the table.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.checks import DocumentInput, run_checks  # noqa: E402
from app.checks.companies_house import CompanyRecord, StaticCompanies, UnavailableCompanies  # noqa: E402
from app.reader import read_document  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
CASES = ["genuine", "edited_balance", "salary_mismatch", "bad_payslip", "name_mismatch",
         "edited_metadata", "multiple_problems"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rent", type=Decimal, default=Decimal("1100"))
    parser.add_argument("--no-companies-house", action="store_true")
    args = parser.parse_args()
    logging.disable(logging.INFO)

    if args.no_companies_house:
        companies = UnavailableCompanies()
    else:
        records = json.loads((FIXTURES / "companies_house.json").read_text(encoding="utf-8"))["records"]
        companies = StaticCompanies([CompanyRecord(**r) for r in records])

    rows = []
    for case in CASES:
        docs = [DocumentInput(p.name, read_document(p.read_bytes()))
                for p in sorted((FIXTURES / case).glob("*.pdf"))]
        report = run_checks(docs, args.rent, companies)
        want = json.loads((FIXTURES / case / "expected.json").read_text(encoding="utf-8"))
        flags = [f"{c.number}.{c.id}" for c in report.checks if c.status == "flag"]
        not_checked = [f"{c.number}.{c.id}" for c in report.checks if c.status == "not_checked"]
        rows.append((case, want["expected_verdict"], report.verdict,
                     ", ".join(flags) or "none", ", ".join(not_checked) or "none"))

    headers = ("fixture", "expected verdict", "actual verdict", "flags raised", "not checked")
    widths = [max(len(str(r[i])) for r in [headers, *rows]) for i in range(len(headers))]
    line = lambda r: "| " + " | ".join(str(v).ljust(w) for v, w in zip(r, widths, strict=True)) + " |"  # noqa: E731
    print(line(headers))
    print("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    for r in rows:
        print(line(r))
    mismatches = [r[0] for r in rows if r[1] != r[2]]
    print(f"\n{len(rows) - len(mismatches)}/{len(rows)} fixtures got their expected verdict.")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
