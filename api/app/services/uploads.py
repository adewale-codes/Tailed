"""Saving uploads into a private temp folder, checking each file's real type and size.

Files are written as 1.upload, 2.upload... (never the original names) inside a fresh random
folder under settings.upload_temp_dir. Whoever creates the folder must delete it; on any
validation error here it is deleted before the error is raised.

Two ways in:
- `save_uploads`: one multipart request with every file (curl, tests).
- Upload sessions: the web app sends each file in chunks of under 4 MB (Vercel caps a request
  body at 4.5 MB), then `claim_session` turns the session folder into the check's folder. A session
  is a temp folder like any other: claimed once, and removed by the cleanup job if abandoned.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from app.config import get_settings
from app.reader.filetype import sniff
from app.services.errors import ApiError

ALLOWED_KINDS = {"pdf", "jpeg", "png"}
CHUNK = 1024 * 1024
# Largest piece of a file accepted per request; the web app sends 3.5 MB pieces.
MAX_CHUNK_BYTES = 4 * 1024 * 1024
SESSION_PREFIX = "session-"
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{40,64}$")


@dataclass
class SavedUpload:
    key: str       # "1", "2", ... used in URLs and as the evidence file label
    name: str      # original filename, sanitised, shown in the report only
    path: Path


def temp_root() -> Path:
    root = Path(get_settings().upload_temp_dir)
    # Created at runtime by whichever user the app runs as (Heroku ignores the Dockerfile's USER).
    # 700: only that user can list or read uploads.
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def display_name(filename: str | None, key: str) -> str:
    name = Path(filename or "").name
    name = re.sub(r"[^\w .()\-]", "_", name).strip()[:120]
    return name or f"Document {key}"


async def save_uploads(files: list[UploadFile]) -> tuple[Path, list[SavedUpload]]:
    settings = get_settings()
    files = [f for f in files if f.filename or f.size]
    if not files:
        raise ApiError("no_files")
    if len(files) > settings.max_files:
        raise ApiError("too_many_files", max_files=settings.max_files)

    limit = settings.max_file_mb * 1024 * 1024
    folder = Path(tempfile.mkdtemp(prefix="check-", dir=temp_root()))
    saved: list[SavedUpload] = []
    try:
        for i, upload in enumerate(files, start=1):
            key = str(i)
            path = folder / f"{key}.upload"
            size = 0
            with path.open("wb") as out:
                first = True
                while chunk := await upload.read(CHUNK):
                    if first:
                        if sniff(chunk) not in ALLOWED_KINDS:  # real type, not the extension
                            raise ApiError("unsupported_file_type")
                        first = False
                    size += len(chunk)
                    if size > limit:
                        raise ApiError("file_too_large", max_file_mb=settings.max_file_mb)
                    out.write(chunk)
            if size == 0:
                raise ApiError("unsupported_file_type")
            saved.append(SavedUpload(key=key, name=display_name(upload.filename, key), path=path))
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        for upload in files:
            await upload.close()
    return folder, saved


# ---------------------------------------------------------------------------
# Chunked upload sessions
# ---------------------------------------------------------------------------


def new_session() -> str:
    """A new, empty upload session. The id is 256 random bits, like a check id."""
    upload_id = secrets.token_urlsafe(32)
    (temp_root() / f"{SESSION_PREFIX}{upload_id}").mkdir()
    return upload_id


def _session_dir(upload_id: str) -> Path:
    if not SESSION_RE.match(upload_id or ""):
        raise ApiError("upload_not_found")
    folder = temp_root() / f"{SESSION_PREFIX}{upload_id}"
    if not folder.is_dir():
        raise ApiError("upload_not_found")
    return folder


async def save_chunk(upload_id: str, index: int, offset: int, name: str | None,
                     chunk: UploadFile) -> int:
    """Append one piece of file `index` at byte `offset`. Returns the file's size so far."""
    settings = get_settings()
    try:
        folder = _session_dir(upload_id)
        if not 1 <= index <= settings.max_files:
            raise ApiError("too_many_files", max_files=settings.max_files)
        data = await chunk.read(MAX_CHUNK_BYTES + 1)
        if len(data) > MAX_CHUNK_BYTES:
            raise ApiError("chunk_too_large")
        if not data:
            raise ApiError("invalid_request")
        path = folder / f"{index}.upload"
        current = path.stat().st_size if path.exists() else 0
        if offset != current:  # pieces must arrive in order, with nothing missing
            raise ApiError("upload_out_of_order")
        if offset == 0:
            if sniff(data) not in ALLOWED_KINDS:  # real type, not the extension
                raise ApiError("unsupported_file_type")
            (folder / f"{index}.name").write_text(display_name(name, str(index)), encoding="utf-8")
        limit = settings.max_file_mb * 1024 * 1024
        if current + len(data) > limit:
            path.unlink(missing_ok=True)
            raise ApiError("file_too_large", max_file_mb=settings.max_file_mb)
        with path.open("ab") as out:
            out.write(data)
        return current + len(data)
    finally:
        await chunk.close()


def claim_session(upload_id: str) -> tuple[Path, list[SavedUpload]]:
    """Turn a finished session into a check folder. Works once: a second claim gets 404."""
    settings = get_settings()
    folder = _session_dir(upload_id)
    claimed = temp_root() / f"check-{secrets.token_hex(8)}"
    try:
        os.rename(folder, claimed)  # atomic, so two requests can't both claim it
    except OSError:
        raise ApiError("upload_not_found") from None
    try:
        keys = sorted(int(p.stem) for p in claimed.glob("*.upload") if p.stem.isdigit())
        if not keys:
            raise ApiError("no_files")
        if keys != list(range(1, len(keys) + 1)) or len(keys) > settings.max_files:
            raise ApiError("too_many_files", max_files=settings.max_files)
        saved = []
        for n in keys:
            key = str(n)
            path = claimed / f"{key}.upload"
            if path.stat().st_size == 0:
                raise ApiError("unsupported_file_type")
            name_file = claimed / f"{key}.name"
            name = name_file.read_text(encoding="utf-8") if name_file.exists() else f"Document {key}"
            saved.append(SavedUpload(key=key, name=name, path=path))
        return claimed, saved
    except BaseException:
        shutil.rmtree(claimed, ignore_errors=True)
        raise
