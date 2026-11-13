"""Check ids, per-IP rate limits and the global daily AI budget, all stored in Postgres.

IP addresses are never stored: rate limits key on HMAC-SHA256(salt, ip).
Counters use one atomic INSERT ... ON CONFLICT ... WHERE count < limit, so concurrent requests
can't slip past the limit.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import UTC, date, datetime

from fastapi import Request
from sqlalchemy import select, text

from app.config import get_settings
from app.db.models import RateLimit
from app.db.session import session_scope

log = logging.getLogger(__name__)


def new_check_id() -> str:
    """256 random bits, URL-safe (43 characters). Unguessable and not sequential."""
    return secrets.token_urlsafe(32)


def today() -> date:
    return datetime.now(UTC).date()


def client_ip(request: Request) -> str:
    settings = get_settings()
    secret = settings.web_proxy_secret
    sent = request.headers.get("x-tallied-proxy-secret", "")
    if secret and sent and hmac.compare_digest(sent.encode(), secret.encode()):
        forwarded_ip = request.headers.get("x-tallied-client-ip", "").strip()
        if forwarded_ip:
            return forwarded_ip
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded.strip():
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def ip_hash(ip: str) -> str:
    salt = get_settings().rate_limit_salt.encode()
    return hmac.new(salt, ip.encode(), hashlib.sha256).hexdigest()


_CONSUME = text("""
    INSERT INTO rate_limits (ip_hash, day, count) VALUES (:h, :d, 1)
    ON CONFLICT (ip_hash, day) DO UPDATE SET count = rate_limits.count + 1
    WHERE rate_limits.count < :limit
    RETURNING count
""")


def checks_used_today(hashed: str) -> int:
    with session_scope() as s:
        row = s.scalar(select(RateLimit.count).where(RateLimit.ip_hash == hashed,
                                                       RateLimit.day == today()))
    return row or 0


def consume_check(hashed: str) -> bool:
    """Count one check for this IP today. False if the daily limit was already reached."""
    limit = get_settings().checks_per_ip_per_day
    with session_scope() as s:
        return s.execute(_CONSUME, {"h": hashed, "d": today(), "limit": limit}).first() is not None


_AI = text("""
    INSERT INTO ai_usage (day, calls) VALUES (:d, 1)
    ON CONFLICT (day) DO UPDATE SET calls = ai_usage.calls + 1
    WHERE ai_usage.calls < :cap
    RETURNING calls
""")


def reserve_ai_call() -> bool:
    """Postgres-backed AI guard (installed with app.ai_guard.set_guard at startup)."""
    cap = get_settings().ai_daily_call_cap
    if cap <= 0:
        return False
    try:
        with session_scope() as s:
            allowed = s.execute(_AI, {"d": today(), "cap": cap}).first() is not None
    except Exception as exc:  # if we can't count, don't spend
        log.warning("ai_budget.error type=%s", type(exc).__name__)
        return False
    if not allowed:
        log.warning("ai_budget.cap_reached")
    return allowed
