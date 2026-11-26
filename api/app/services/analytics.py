"""Cookie-free product analytics, stored in our own `events` table.

Each event is a name, a time, a salted hash of the check id, and at most a word of fixed
vocabulary (a verdict, a vote, an error code). No IP address, user agent, cookie, document data
or free text is ever recorded. The hash lets the funnel be counted per check, and can't be joined
back to a check once the check's tombstone is gone.

Events about sample checks (made-up documents, app/samples.py) are tagged `sample=True`, looked up
from the check itself so no caller can forget. funnel() and votes() count real checks unless asked
for samples, so launch metrics never include people trying the samples.

Recording never breaks the request that triggers it: failures are logged by type and ignored.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import Check, Event
from app.db.session import session_scope

log = logging.getLogger(__name__)

FUNNEL = [
    "check_started",
    "check_finished",
    "report_viewed",
    "evidence_opened",
    "feedback_sent",
    "email_left",
]
EVENTS = set(FUNNEL) | {"check_failed"}
# Events the web app may report; the rest are recorded by the API itself.
CLIENT_EVENTS = {"report_viewed", "evidence_opened"}
# Stops one check's page from filling the table if something loops.
MAX_PER_CHECK_AND_NAME = 50


def check_ref(check_id: str) -> str:
    salt = get_settings().rate_limit_salt.encode()
    return hmac.new(salt, f"event:{check_id}".encode(), hashlib.sha256).hexdigest()[:32]


def record(name: str, check_id: str, detail: str | None = None) -> None:
    if name not in EVENTS:
        raise ValueError(f"unknown event {name}")
    try:
        ref = check_ref(check_id)
        with session_scope() as s:
            seen = s.scalar(select(func.count()).select_from(Event)
                            .where(Event.name == name, Event.check_ref == ref))
            if seen >= MAX_PER_CHECK_AND_NAME:
                return
            sample = s.scalar(select(Check.sample_id).where(Check.id == check_id)) is not None
            s.add(Event(name=name, check_ref=ref, detail=(detail or None) and detail[:32], sample=sample))
    except Exception as exc:  # analytics must never break the product
        log.error("analytics.record_failed event=%s type=%s", name, type(exc).__name__)


def funnel(days: int = 30, now: datetime | None = None, sample: bool = False) -> list[dict]:
    """For each funnel step: how many distinct checks reached it in the last `days` days.
    Real checks only, unless sample=True (then sample checks only)."""
    since = (now or datetime.now(UTC)) - timedelta(days=days)
    with session_scope() as s:
        rows = dict(s.execute(
            select(Event.name, func.count(func.distinct(Event.check_ref)))
            .where(Event.created_at >= since, Event.sample.is_(sample)).group_by(Event.name)).all())
    started = rows.get("check_started", 0)
    return [{"step": name, "checks": rows.get(name, 0),
             "pct_of_started": round(100 * rows.get(name, 0) / started, 1) if started else None}
            for name in FUNNEL]


def votes(days: int = 30, now: datetime | None = None, sample: bool = False) -> dict[str, int]:
    """Thumbs up vs down from feedback events (the vote is the event's detail). Real checks only,
    unless sample=True."""
    since = (now or datetime.now(UTC)) - timedelta(days=days)
    with session_scope() as s:
        rows = dict(s.execute(
            select(Event.detail, func.count()).where(Event.name == "feedback_sent",
                                                     Event.created_at >= since,
                                                     Event.sample.is_(sample))
            .group_by(Event.detail)).all())
    return {"up": rows.get("up", 0), "down": rows.get("down", 0)}
