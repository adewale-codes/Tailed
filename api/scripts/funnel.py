"""Print the product funnel from the events table, real checks and sample checks separately.

    cd api && uv run python scripts/funnel.py [--days 30]

Counts distinct checks at each step: started -> finished -> report viewed -> evidence opened ->
feedback sent -> email left. The first table, real checks, is the launch metric. Sample checks
(people trying the made-up documents on the upload page) are shown after it and never mixed in.
Reads only the anonymous events table.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.analytics import funnel, votes  # noqa: E402

LABELS = {
    "check_started": "Check started",
    "check_finished": "Check finished",
    "report_viewed": "Report viewed",
    "evidence_opened": "Evidence opened",
    "feedback_sent": "Feedback sent",
    "email_left": "Email left",
}


def print_funnel(title: str, days: int, sample: bool) -> None:
    rows = funnel(days, sample=sample)
    print(f"{title}, last {days} days (distinct checks)\n")
    top = max((r["checks"] for r in rows), default=0) or 1
    for r in rows:
        pct = "" if r["pct_of_started"] is None else f"{r['pct_of_started']:5.1f}%"
        bar = "#" * round(30 * r["checks"] / top)
        print(f"{LABELS[r['step']]:<16} {r['checks']:>5}  {pct:>6}  {bar}")
    v = votes(days, sample=sample)
    total = v["up"] + v["down"]
    share = f" ({100 * v['up'] / total:.0f}% useful)" if total else ""
    print()
    print(f"Was it useful?   Yes {v['up']}, Not really {v['down']}{share}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    print_funnel("REAL CHECKS (launch metrics)", args.days, sample=False)
    print("\n" + "-" * 60 + "\n")
    print_funnel("Sample checks (made-up documents, not in the launch metrics)", args.days, sample=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
