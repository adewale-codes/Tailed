"""Scheduled cleanup: makes the 24-hour deletion promise true.

Every run (idempotent, safe to run from several workers or a cron at once):
1. Checks older than 24h: delete page images; wipe report, progress, rent and error text.
   A tombstone (id + timestamps) is kept so GET can say 410 rather than 404.
2. Tombstones older than 30 days are removed entirely.
3. Checks stuck processing for over 15 minutes (e.g. after a crash) are marked failed.
4. Upload folders older than 1 hour are deleted (a crash mid-processing could leave one).
5. Old rate-limit and AI-usage counters are removed.

Runs inside the API every CLEANUP_INTERVAL_MINUTES, and can also run on its own:
    uv run python -m app.services.cleanup
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select, update

from app.config import get_settings
from app.db.models import AiUsage, Check, Event, PageImage, RateLimit
from app.db.session import session_scope

log = logging.getLogger(__name__)

TOMBSTONE_DAYS = 30
STUCK_AFTER = timedelta(minutes=15)
STALE_UPLOAD_AFTER = timedelta(hours=1)
PROCESSING = ("queued", "reading", "checking", "writing")


@dataclass
class CleanupResult:
    wiped: int = 0
    tombstones_removed: int = 0
    stuck_failed: int = 0
    temp_folders_removed: int = 0


def run_cleanup(now: datetime | None = None) -> CleanupResult:
    now = now or datetime.now(UTC)
    result = CleanupResult()
    with session_scope() as s:
        expired = list(s.scalars(select(Check.id).where(Check.expires_at <= now,
                                                         Check.wiped_at.is_(None))))
        if expired:
            s.execute(delete(PageImage).where(PageImage.check_id.in_(expired)))
            s.execute(update(Check).where(Check.id.in_(expired)).values(
                status="expired", report=None, progress=None, monthly_rent=None,
                error_message=None, wiped_at=now))
        result.wiped = len(expired)

        result.tombstones_removed = s.execute(
            delete(Check).where(Check.wiped_at < now - timedelta(days=TOMBSTONE_DAYS))).rowcount

        result.stuck_failed = s.execute(
            update(Check).where(Check.status.in_(PROCESSING), Check.created_at < now - STUCK_AFTER)
            .values(status="failed", error_code="internal_error",
                    error_message="Something went wrong while checking these documents. "
                                  "Please try again.")).rowcount

        s.execute(delete(RateLimit).where(RateLimit.day < (now - timedelta(days=2)).date()))
        s.execute(delete(AiUsage).where(AiUsage.day < (now - timedelta(days=90)).date()))
        # Anonymous analytics: keep about a year for year-on-year comparison.
        s.execute(delete(Event).where(Event.created_at < now - timedelta(days=400)))

    result.temp_folders_removed = remove_stale_uploads(now)
    log.info("cleanup.done wiped=%d tombstones=%d stuck=%d temp=%d", result.wiped,
             result.tombstones_removed, result.stuck_failed, result.temp_folders_removed)
    return result


def remove_stale_uploads(now: datetime) -> int:
    root = Path(get_settings().upload_temp_dir)
    if not root.exists():
        return 0
    cutoff = (now - STALE_UPLOAD_AFTER).timestamp()
    removed = 0
    for folder in root.iterdir():
        if folder.is_dir() and folder.stat().st_mtime < cutoff:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    return removed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_cleanup()
