"""Decide whether extracted text is a bank statement, a payslip or neither."""

from app.reader.models import DocumentType

STATEMENT_SIGNALS = [
    "sort code", "account number", "statement period", "brought forward", "carried forward",
    "paid in", "paid out", "money in", "money out", "balance", "statement",
]
PAYSLIP_SIGNALS = [
    "payslip", "pay slip", "net pay", "gross pay", "tax code", "national insurance", "paye",
    "ni number", "ni category", "year to date", "deductions",
]


def classify_text(text: str) -> DocumentType:
    t = text.lower()
    statement = sum(s in t for s in STATEMENT_SIGNALS)
    payslip = sum(s in t for s in PAYSLIP_SIGNALS)
    if max(statement, payslip) < 3:
        return "unknown"
    return "payslip" if payslip > statement else "bank_statement"
