"""Database tables.

Privacy by design:
- `checks` holds the report for 24 hours. After that the cleanup job wipes the report, progress
  and page images and leaves only a tombstone (id + timestamps), so GET can answer 410.
- No IP addresses are stored anywhere: `rate_limits` keys on a salted hash.
- Feedback has no foreign key to `checks`, and emails live in their own table, so deleting a
  report never deletes or exposes them, and a report never contains them.
- `events` is cookie-free product analytics: an event name, a time and a salted hash of the
  check id (so a funnel can be counted per check). No IP, user agent, cookie or document data.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Check(Base):
    __tablename__ = "checks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # random token, see ids.py
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # queued -> reading -> checking -> writing -> done | failed ; expired after cleanup
    status: Mapped[str] = mapped_column(String(16), default="queued")
    progress: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    monthly_rent: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    wiped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PageImage(Base):
    __tablename__ = "page_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    check_id: Mapped[str] = mapped_column(ForeignKey("checks.id", ondelete="CASCADE"), index=True)
    file_key: Mapped[str] = mapped_column(String(8))
    page: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    png: Mapped[bytes] = mapped_column(LargeBinary)


class RateLimit(Base):
    __tablename__ = "rate_limits"

    ip_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


class AiUsage(Base):
    __tablename__ = "ai_usage"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)


class FeedbackContact(Base):
    """Emails people volunteer with feedback. Separate from reports and from feedback text."""

    __tablename__ = "feedback_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Plain column, not a foreign key: deleting a check never touches feedback.
    check_id: Mapped[str] = mapped_column(String(64), index=True)
    vote: Mapped[str] = mapped_column(String(4))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Answer to the follow-up question shown after the vote (one of a few fixed choices).
    follow_up: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("feedback_contacts.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    """One analytics event. See the module docstring for what is (and isn't) stored."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), index=True)
    check_ref: Mapped[str] = mapped_column(String(32), index=True)
    # Small fixed vocabulary only (e.g. the verdict or the vote), never free text.
    detail: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 index=True)
