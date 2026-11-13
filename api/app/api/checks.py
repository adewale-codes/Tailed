"""/checks endpoints: upload, progress, report, page images, feedback."""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.checks.models import CHECK_ORDER
from app.config import get_settings
from app.db.models import Check, Feedback, FeedbackContact, PageImage
from app.db.session import session_scope
from app.services.errors import ApiError
from app.services.limits import checks_used_today, client_ip, consume_check, ip_hash, new_check_id
from app.services.processing import CHECK_LABELS, initial_progress, process_check
from app.services.uploads import save_uploads

router = APIRouter(prefix="/checks", tags=["checks"])

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40,64}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def _parse_rent(raw: str | None) -> Decimal:
    try:
        rent = Decimal((raw or "").replace("£", "").replace(",", "").strip())
    except InvalidOperation:
        raise ApiError("invalid_rent") from None
    if not rent.is_finite() or rent <= 0 or rent > 100000:
        raise ApiError("invalid_rent")
    return rent.quantize(Decimal("0.01"))


def _load(check_id: str) -> Check:
    """Fetch a live check, or raise 404 / 410. Expired checks never return data, even before
    the cleanup job has wiped them."""
    if not TOKEN_RE.match(check_id):
        raise ApiError("check_not_found")
    with session_scope() as s:
        check = s.get(Check, check_id)
        if check is None:
            raise ApiError("check_not_found")
        if check.wiped_at is not None or check.expires_at <= datetime.now(UTC):
            raise ApiError("check_expired")
        s.expunge(check)
    return check


@router.post("", status_code=202)
async def create_check(
    request: Request,
    background: BackgroundTasks,
    files: list[UploadFile] = File(default=[]),
    monthly_rent: str | None = Form(default=None),
    consent: str | None = Form(default=None),
) -> dict:
    settings = get_settings()
    if (consent or "").strip().lower() not in {"true", "1", "yes", "on"}:
        raise ApiError("consent_required")
    rent = _parse_rent(monthly_rent)

    hashed = ip_hash(client_ip(request))
    if checks_used_today(hashed) >= settings.checks_per_ip_per_day:  # cheap early refusal
        raise ApiError("rate_limited", checks_per_ip_per_day=settings.checks_per_ip_per_day)

    folder, uploads = await save_uploads(files)
    try:
        if not consume_check(hashed):  # atomic; catches concurrent requests
            raise ApiError("rate_limited", checks_per_ip_per_day=settings.checks_per_ip_per_day)
        check_id = new_check_id()
        now = datetime.now(UTC)
        with session_scope() as s:
            s.add(Check(id=check_id, created_at=now,
                        expires_at=now + timedelta(hours=settings.report_ttl_hours),
                        status="queued", progress=initial_progress(len(uploads)),
                        monthly_rent=rent, file_count=len(uploads)))
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise

    background.add_task(process_check, check_id, folder, uploads, rent)
    return {"id": check_id, "status": "queued",
            "progress_url": f"/checks/{check_id}/progress", "report_url": f"/checks/{check_id}",
            "expires_at": (now + timedelta(hours=settings.report_ttl_hours)).isoformat()}


@router.get("/{check_id}/progress")
def get_progress(check_id: str) -> dict:
    check = _load(check_id)
    progress = check.progress or initial_progress(check.file_count)
    statuses = progress.get("checks", {})
    body = {
        "id": check.id,
        "status": check.status,
        "stage": progress.get("stage", check.status),
        "documents": {"read": progress.get("documents_read", 0),
                      "total": progress.get("documents_total", check.file_count)},
        # Per-file outcome by upload number ("1", "2"...). Never filenames or contents.
        "files": [{"key": str(n), **progress.get("files", {}).get(str(n), {"status": "pending"})}
                  for n in range(1, check.file_count + 1)],
        "preview": progress.get("preview"),
        "checks": [{"id": cid, "number": n, "label": CHECK_LABELS[cid],
                    "done": statuses.get(cid) is not None, "status": statuses.get(cid)}
                   for n, cid in enumerate(CHECK_ORDER, start=1)],
    }
    if check.status == "failed":
        body["error"] = {"code": check.error_code, "message": check.error_message}
    return body


@router.get("/{check_id}")
def get_report(check_id: str) -> JSONResponse:
    check = _load(check_id)
    if check.status == "failed":
        return JSONResponse(status_code=422, content={"error": {
            "code": check.error_code or "internal_error",
            "message": check.error_message or "We couldn't check these documents."}})
    if check.status != "done" or check.report is None:
        return JSONResponse(status_code=202, content={
            "id": check.id, "status": check.status,
            "message": "We're still checking the documents.",
            "progress_url": f"/checks/{check.id}/progress"})
    return JSONResponse(content={
        "id": check.id, "status": "done",
        "created_at": check.created_at.isoformat(), "expires_at": check.expires_at.isoformat(),
        "files": check.report["files"], "report": check.report["report"]},
        headers={"Cache-Control": "no-store"})


@router.get("/{check_id}/pages/{file_key}/{page}.png")
def get_page(check_id: str, file_key: str, page: int) -> Response:
    _load(check_id)
    with session_scope() as s:
        png = s.scalar(select(PageImage.png).where(PageImage.check_id == check_id,
                                                    PageImage.file_key == file_key,
                                                    PageImage.page == page))
    if png is None:
        raise ApiError("page_not_found")
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


class FeedbackIn(BaseModel):
    vote: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=2000)
    email: str | None = Field(default=None, max_length=320)


@router.post("/{check_id}/feedback", status_code=201)
def post_feedback(check_id: str, body: FeedbackIn) -> dict:
    check = _load(check_id)
    email = (body.email or "").strip() or None
    if email and not EMAIL_RE.match(email):
        raise ApiError("invalid_email")
    verdict = (check.report or {}).get("report", {}).get("verdict")
    with session_scope() as s:
        contact = None
        if email:
            contact = FeedbackContact(email=email)
            s.add(contact)
            s.flush()
        s.add(Feedback(check_id=check_id, vote=body.vote, comment=(body.comment or "").strip() or None,
                       verdict=verdict, contact_id=contact.id if contact else None))
    return {"ok": True}
