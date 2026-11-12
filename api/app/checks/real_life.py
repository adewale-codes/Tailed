"""Check 5: looks_like_real_life.

Deliberately conservative: real accounts vary a lot, so these rules only catch statements that
look nothing like day-to-day banking.

- Everyday spending: flag only if there are very few outgoing payments AND almost none of them
  look like everyday spending (food, travel, bills, shopping).
- Repeated round amounts: flag a run of 4 or more consecutive transactions with the same round
  amount (a whole multiple of £10, £50 or more), a common pattern in made-up statements.
"""

from __future__ import annotations

from decimal import Decimal

from app.checks.models import CheckResult, DocumentInput, Evidence
from app.checks.util import gbp, plural, statements

CATEGORIES: dict[str, tuple[str, ...]] = {
    "food": ("TESCO", "SAINSBURY", "ASDA", "ALDI", "LIDL", "MORRISONS", "CO-OP", "COOP", "WAITROSE",
             "ICELAND", "M&S", "MARKS", "OCADO", "GREGGS", "PRET", "COSTA", "STARBUCKS", "NANDO",
             "MCDONALD", "KFC", "DELIVEROO", "JUST EAT", "UBER EATS", "WAGAMAMA", "CAFE", "RESTAURANT",
             "PIZZA", " PUB ", " TAP ", " BAR "),
    "travel": ("TFL", "BUS", "TRAIN", "RAIL", "UBER", "SHELL", " BP ", "ESSO", "PARKING", "TRAINLINE",
               "PETROL", "STAGECOACH", "ARRIVA"),
    "bills": ("ENERGY", "GAS", "ELECTRIC", "WATER", "COUNCIL", "CTAX", "VODAFONE", " EE ", " O2 ", "THREE",
              " BT ", "SKY", "VIRGIN", "INSURANCE", "BROADBAND", "TV LICENCE", "RENT"),
    "shopping": ("AMAZON", "BOOTS", "TK MAXX", "ARGOS", "PRIMARK", "SUPERDRUG", "EBAY", "IKEA", "NEXT",
                 "NETFLIX", "SPOTIFY", "DISNEY", "APPLE", "GOOGLE", "GYM"),
}
MIN_OUTGOING_PER_MONTH = 8
MIN_CATEGORIES = 2
RUN_LENGTH = 4
ROUND_UNIT = Decimal("10")
ROUND_MIN = Decimal("50")


def _category(description: str) -> str | None:
    text = f" {description.upper()} "
    for name, words in CATEGORIES.items():
        if any(w in text for w in words):
            return name
    return None


def check_looks_like_real_life(docs: list[DocumentInput]) -> CheckResult:
    stmts = statements(docs)
    if not stmts:
        return CheckResult(id="looks_like_real_life", number=5, status="not_checked",
                           title="No bank statements to check",
                           explanation="We need bank statements to look at everyday spending.")

    evidence: list[Evidence] = []
    days = sum((s.period_end - s.period_start).days + 1 for _, s in stmts)
    months = max(Decimal(days) / Decimal("30.44"), Decimal(1))
    outgoing = [(d, t) for d, s in stmts for t in s.transactions if t.paid_out]
    per_month = (Decimal(len(outgoing)) / months).quantize(Decimal("0.1"))
    categories = {c for _, t in outgoing if (c := _category(t.description))}
    if per_month < MIN_OUTGOING_PER_MONTH and len(categories) < MIN_CATEGORIES:
        evidence.append(Evidence.at(
            "little_everyday_spending", stmts[0][0].file, [], flag=True,
            numbers={"payments_out_per_month": per_month,
                     "spending_categories": Decimal(len(categories))}))

    # Runs of identical round amounts, in date order across all statements
    run: list = []
    for doc, t in [(d, t) for d, s in stmts for t in s.transactions]:
        amount = t.paid_out or t.paid_in
        is_round = amount is not None and amount >= ROUND_MIN and amount % ROUND_UNIT == 0
        if is_round and run and run[-1][1].amount == t.amount:
            run.append((doc, t))
        else:
            _close_run(run, evidence)
            run = [(doc, t)] if is_round else []
    _close_run(run, evidence)

    if not evidence:
        return CheckResult(
            id="looks_like_real_life", number=5, status="pass",
            title="Spending looks like everyday life",
            explanation=f"The statements show about {per_month} payments out a month across "
                        f"{plural(len(categories), 'everyday category', 'everyday categories')}, with "
                        "no unusual runs of identical round amounts.")
    first = evidence[0]
    if first.kind == "little_everyday_spending":
        explanation = (f"The statements show only about {first.numbers['payments_out_per_month']} "
                       "payments out a month and almost no everyday spending such as food, travel "
                       "or bills.")
    else:
        explanation = (f"There's a run of {int(first.numbers['run_length'])} transactions in a row "
                       f"for exactly {gbp(first.numbers['amount'])}, which is unusual.")
    return CheckResult(id="looks_like_real_life", number=5, status="flag", evidence=evidence,
                       title="Some activity looks unusual", explanation=explanation)


def _close_run(run: list, evidence: list[Evidence]) -> None:
    if len(run) >= RUN_LENGTH:
        doc, first = run[0]
        evidence.append(Evidence.at(
            "repeated_round_amounts", doc.file, [t.location for _, t in run], flag=True,
            numbers={"amount": abs(first.amount), "run_length": Decimal(len(run))},
            details={"from": first.date.isoformat(), "to": run[-1][1].date.isoformat()}))
