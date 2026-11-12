"""Identify uploads by their first bytes, never by filename or client-supplied type."""

from typing import Literal

FileKind = Literal["pdf", "jpeg", "png", "webp", "unknown"]

IMAGE_MEDIA_TYPES = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def sniff(data: bytes) -> FileKind:
    head = data[:1024]
    if b"%PDF-" in head:
        return "pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "unknown"
