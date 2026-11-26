"""/uploads endpoints: chunked uploads for the web app (each request stays under 4.5 MB).

    POST /uploads[?sample=<id>]        -> {"upload_id": ..., "max_chunk_bytes": ...}
    POST /uploads/{id}/chunks          multipart: index, offset, name, chunk
    POST /checks  (upload_id=..., monthly_rent, consent | sample)   starts the check
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Request, UploadFile

from app.config import get_settings
from app.samples import get_sample
from app.services.errors import ApiError
from app.services.limits import checks_used_today, client_ip, limit_for
from app.services.uploads import MAX_CHUNK_BYTES, new_session, save_chunk

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("", status_code=201)
def create_upload(request: Request, sample: str | None = None) -> dict:
    settings = get_settings()
    # Refuse before any bytes are sent if today's checks (or sample checks) are already used up.
    spec = get_sample(sample)
    hashed, limit, code = limit_for(client_ip(request), sample=spec is not None)
    if checks_used_today(hashed) >= limit:
        raise ApiError(code, checks_per_ip_per_day=limit, sample_checks_per_ip_per_day=limit)
    return {"upload_id": new_session(), "max_chunk_bytes": MAX_CHUNK_BYTES,
            "max_files": settings.max_files, "max_file_mb": settings.max_file_mb}


@router.post("/{upload_id}/chunks")
async def upload_chunk(
    upload_id: str,
    index: int = Form(...),
    offset: int = Form(...),
    name: str | None = Form(default=None),
    chunk: UploadFile = File(...),
) -> dict:
    received = await save_chunk(upload_id, index, offset, name, chunk)
    return {"index": index, "received": received}
