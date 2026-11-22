"""Database helpers for the browser end-to-end tests (web/e2e). Local databases only.

    uv run python scripts/e2e_db.py check            # can we reach the database?
    uv run python scripts/e2e_db.py reset-limits     # clear today's per-IP check counts
    uv run python scripts/e2e_db.py expire <id>      # age a check past its 24 hours (for the 410 test)

Uses DATABASE_URL (or api/.env), like the API. Refuses to touch any database that isn't on this
machine, so it can never be pointed at production by accident.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import get_settings  # noqa: E402

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
CHECK_ID = re.compile(r"^[A-Za-z0-9_-]{40,64}$")


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in {"check", "reset-limits", "expire"}:
        print(__doc__)
        return 2
    url = get_settings().database_url
    host = urlsplit(url.replace("postgresql+psycopg://", "postgresql://")).hostname
    if host not in LOCAL_HOSTS:
        print(f"Refusing: DATABASE_URL points at '{host}', not this machine.")
        return 3
    engine = create_engine(url)
    with engine.begin() as conn:
        if argv[0] == "check":
            conn.execute(text("SELECT 1 FROM rate_limits LIMIT 1"))
            print("ok")
        elif argv[0] == "reset-limits":
            conn.execute(text("DELETE FROM rate_limits"))
        else:
            if len(argv) != 2 or not CHECK_ID.match(argv[1]):
                print("expire needs a check id")
                return 2
            conn.execute(text("UPDATE checks SET expires_at = now() - interval '1 hour' WHERE id = :i"),
                         {"i": argv[1]})
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
