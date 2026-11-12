"""Entry point: one uploaded file in, one typed ReadResult out.

Works entirely in memory: the document is never written to disk by the reader.
Fast path: pdfplumber + PyMuPDF. AI path (Claude) only for photos, scans, or layouts the
parser can't read.
"""

from __future__ import annotations

import io
import logging
import time

import pdfplumber
import pymupdf
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import get_settings
from app.reader.ai import PageImage, read_with_ai
from app.reader.classify import classify_text
from app.reader.errors import ParseFailed, ReaderError
from app.reader.filetype import IMAGE_MEDIA_TYPES, sniff
from app.reader.metadata import read_metadata
from app.reader.models import FileMetadata, PageSize, ReadResult
from app.reader.payslip import parse_payslip
from app.reader.pdf_text import group_lines, page_words
from app.reader.statement import parse_statement

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 40  # fewer characters than this means a scanned PDF with no text layer
RENDER_DPI = 150
MAX_IMAGE_EDGE = 2000  # px; photos are downscaled to this before being sent to Claude


def read_document(data: bytes) -> ReadResult:
    settings = get_settings()
    started = time.perf_counter()
    if not data:
        raise ReaderError("empty_file")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ReaderError("file_too_large", limit_mb=settings.max_upload_mb)

    kind = sniff(data)
    if kind == "pdf":
        result = _read_pdf(data, settings.max_pages)
    elif kind in IMAGE_MEDIA_TYPES:
        result = _read_image(data)
    else:
        raise ReaderError("unsupported_file_type")

    log.info("reader.done type=%s source=%s pages=%d ms=%d", result.document_type, result.source,
             result.page_count, (time.perf_counter() - started) * 1000)
    return result


def _read_pdf(data: bytes, max_pages: int) -> ReadResult:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several types for damaged files
        log.info("reader.unreadable type=%s", type(exc).__name__)
        raise ReaderError("unreadable_file") from None

    with doc:
        if doc.needs_pass:
            raise ReaderError("password_protected")
        if doc.page_count == 0:
            raise ReaderError("unreadable_file")
        if doc.page_count > max_pages:
            raise ReaderError("too_many_pages", limit=max_pages)
        metadata = read_metadata(doc)
        sizes = [PageSize(page=i + 1, width=round(p.rect.width, 2), height=round(p.rect.height, 2),
                          unit="pt") for i, p in enumerate(doc)]

        try:
            return _parse_pdf(data, metadata, sizes)
        except ParseFailed as exc:
            log.info("reader.parser_failed reason=%s", exc)
        except Exception as exc:  # malformed content streams etc.: let the AI path try instead
            log.warning("reader.parser_error type=%s", type(exc).__name__)

        try:
            pages = [_render_page(page, i + 1, sizes[i]) for i, page in enumerate(doc)]
        except Exception as exc:
            log.info("reader.render_failed type=%s", type(exc).__name__)
            raise ReaderError("unreadable_file") from None

    doc_type, statement, payslip = read_with_ai(pages)
    return ReadResult(document_type=doc_type, source="ai", page_count=len(sizes), pages=sizes,
                      metadata=metadata, statement=statement, payslip=payslip)


def _parse_pdf(data: bytes, metadata: FileMetadata, sizes: list[PageSize]) -> ReadResult:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        words = [w for page in pdf.pages for w in page_words(page)]
    lines = group_lines(words)
    text = "\n".join(line.text for line in lines)
    if len(text.strip()) < MIN_TEXT_CHARS:
        raise ParseFailed("no text layer")

    doc_type = classify_text(text)
    result = ReadResult(document_type=doc_type, source="parser", page_count=len(sizes),
                        pages=sizes, metadata=metadata)
    if doc_type == "bank_statement":
        result.statement = parse_statement(lines)
    elif doc_type == "payslip":
        result.payslip = parse_payslip(lines)
    return result


def _render_page(page: pymupdf.Page, number: int, size: PageSize) -> PageImage:
    pix = page.get_pixmap(dpi=RENDER_DPI)
    return PageImage(page=number, media_type="image/png", data=pix.tobytes("png"), size=size)


def _read_image(data: bytes) -> ReadResult:
    """A photo or screenshot: normalise it, then use the AI path."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)  # respect phone camera rotation
            img = img.convert("RGB")
            img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=88)  # re-encoding also drops EXIF (location etc.)
            width, height = img.size
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        log.info("reader.unreadable type=%s", type(exc).__name__)
        raise ReaderError("unreadable_file") from None

    size = PageSize(page=1, width=width, height=height, unit="px")
    doc_type, statement, payslip = read_with_ai(
        [PageImage(page=1, media_type="image/jpeg", data=out.getvalue(), size=size)])
    return ReadResult(document_type=doc_type, source="ai", page_count=1, pages=[size],
                      metadata=FileMetadata(), statement=statement, payslip=payslip)
