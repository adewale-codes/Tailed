"""Scheduled cleanup: makes the 24-hour deletion promise true.

Every run (idempotent, safe to run from several workers or a cron at once):
1. Checks older than 24h: delete page images; wipe report, progress, rent and error text.
   A tombstone (id + timestamps) is kept so GET can say 410 rather than 404.
2. Tombstones older than 30 days are removed entirely.
3. Checks stuck processing for over 15 minutes (e.g. after a crash) are marked failed, and the
   visitor's daily check is given back.
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
from app.services import analytics

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
    stuck_refunded: int = 0
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
                error_message=None, rate_limit_key=None, wiped_at=now))
        result.wiped = len(expired)

        result.tombstones_removed = s.execute(
            delete(Check).where(Check.wiped_at < now - timedelta(days=TOMBSTONE_DAYS))).rowcount

        # skip_locked: a row a worker is writing right now is left for the next run.
        stuck = _processing_rows(s, Check.created_at < now - STUCK_AFTER, skip_locked=True)
        result.stuck_failed = len(stuck)
        result.stuck_refunded = _fail_and_give_back(s, stuck, STUCK_CODE, STUCK_MESSAGE)

        s.execute(delete(RateLimit).where(RateLimit.day < (now - timedelta(days=2)).date()))
        s.execute(delete(AiUsage).where(AiUsage.day < (now - timedelta(days=90)).date()))
        # Anonymous analytics: keep about a year for year-on-year comparison.
        s.execute(delete(Event).where(Event.created_at < now - timedelta(days=400)))

    result.temp_folders_removed = remove_stale_uploads(now)
    log.info("cleanup.done wiped=%d tombstones=%d stuck=%d refunded=%d temp=%d", result.wiped,
             result.tombstones_removed, result.stuck_failed, result.stuck_refunded,
             result.temp_folders_removed)
    return result


STUCK_CODE = "internal_error"
STUCK_MESSAGE = ("Something went wrong while checking these documents. Please try again. "
                 "This one hasn't counted towards today's limit.")
INTERRUPTED_CODE = "check_interrupted"
INTERRUPTED_MESSAGE = ("Something interrupted this check. Please try again. "
                       "This one hasn't counted towards today's limit.")


def _processing_rows(s, *where, skip_locked: bool = False) -> list:
    """Checks still processing (and not yet wiped), locked for update."""
    return s.execute(select(Check.id, Check.rate_limit_key, Check.created_at, Check.progress)
                     .where(Check.status.in_(PROCESSING), Check.wiped_at.is_(None), *where)
                     .with_for_update(skip_locked=skip_locked)).all()


def _fail_and_give_back(s, rows: list, code: str, message: str) -> int:
    """Mark these checks failed, clear their link to the visitor's IP hash, and give back the daily
    check each one used (on the day it was counted, never below zero). Returns how many were given
    back. The key is cleared in the same update, so a check can never be given back twice."""
    refunded = 0
    for row in rows:
        s.execute(update(Check).where(Check.id == row.id).values(
            status="failed", error_code=code, error_message=message, rate_limit_key=None,
            progress={**(row.progress or {}), "stage": "failed"}))
        if row.rate_limit_key:
            refunded += s.execute(
                update(RateLimit).where(RateLimit.ip_hash == row.rate_limit_key,
                                        RateLimit.day == row.created_at.astimezone(UTC).date(),
                                        RateLimit.count > 0)
                .values(count=RateLimit.count - 1)).rowcount
    return refunded


def fail_interrupted_checks() -> int:
    """Run once when the API starts. Any check still processing belonged to the previous API
    process (a restart, deploy or crash killed it), so it will never finish: mark it failed now
    instead of after STUCK_AFTER, and give the visitor's daily check back.

    Only safe because the API runs as ONE instance, and Heroku Basic dynos have no preboot (the old
    dyno is stopped before the new one starts). With two instances, this would fail the other
    instance's live checks.
    """
    with session_scope() as s:
        rows = _processing_rows(s)
        refunded = _fail_and_give_back(s, rows, INTERRUPTED_CODE, INTERRUPTED_MESSAGE)
    for row in rows:
        analytics.record("check_failed", row.id, "interrupted")
    log.info("startup.interrupted_checks failed=%d refunded=%d", len(rows), refunded)
    return len(rows)


def remove_stale_uploads(now: datetime) -> int:
    root = Path(get_settings().upload_temp_dir)
    if not root.exists():
        return 0
    cutoff = (now - STALE_UPLOAD_AFTER).timestamp()
    removed = 0
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        # Newest change anywhere in the folder: appending to a file doesn't touch the folder.
        newest = max([folder.stat().st_mtime, *(p.stat().st_mtime for p in folder.iterdir())])
        if newest < cutoff:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    return removed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_cleanup()
