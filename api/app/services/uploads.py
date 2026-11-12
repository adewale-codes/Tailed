"""Saving uploads into a private temp folder, checking each file's real type and size.

Files are written as 1.upload, 2.upload... (never the original names) inside a fresh random
folder under settings.upload_temp_dir. Whoever creates the folder must delete it; on any
validation error here it is deleted before the error is raised.
"""

from __future__ import annotations

import re
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


@dataclass
class SavedUpload:
    key: str       # "1", "2", ... used in URLs and as the evidence file label
    name: str      # original filename, sanitised, shown in the report only
    path: Path


def temp_root() -> Path:
    root = Path(get_settings().upload_temp_dir)
    root.mkdir(parents=True, exist_ok=True)
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
