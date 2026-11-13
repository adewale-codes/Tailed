"""Print the product funnel from the events table.

    cd api && uv run python scripts/funnel.py [--days 30]

Counts distinct checks at each step: started -> finished -> report viewed -> evidence opened ->
feedback sent -> email left. Reads only the anonymous events table.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.analytics import funnel  # noqa: E402

LABELS = {
    "check_started": "Check started",
    "check_finished": "Check finished",
    "report_viewed": "Report viewed",
    "evidence_opened": "Evidence opened",
    "feedback_sent": "Feedback sent",
    "email_left": "Email left",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    rows = funnel(args.days)
    print(f"Funnel, last {args.days} days (distinct checks)\n")
    top = max((r["checks"] for r in rows), default=0) or 1
    for r in rows:
        pct = "" if r["pct_of_started"] is None else f"{r['pct_of_started']:5.1f}%"
        bar = "#" * round(30 * r["checks"] / top)
        print(f"{LABELS[r['step']]:<16} {r['checks']:>5}  {pct:>6}  {bar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
