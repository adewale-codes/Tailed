"""Background processing for one check: read -> seven checks -> wording -> evidence images -> save.

The temp folder holding the uploads is deleted in a `finally`, whether processing succeeds or
fails. Only the report and images of the pages that have a finding are stored, and only until the
check expires. Logs carry the check's stage, timings and error types; never document contents.
"""

from __future__ import annotations

import asyncio
import io
import logging
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps
from sqlalchemy import update

from app.checks import DocumentInput, run_checks
from app.checks.companies_house import CompaniesHouseClient, CompanyLookup
from app.checks.models import CHECK_ORDER, CheckResult, Report
from app.config import get_settings
from app.db.models import Check, PageImage
from app.db.session import session_scope
from app.reader import ReaderError, read_document
from app.reader.filetype import sniff
from app.report.wording import apply_wording
from app.services import analytics
from app.services.uploads import SavedUpload

log = logging.getLogger(__name__)

# Evidence images. 150 DPI keeps a highlighted statement line sharp in the phone close-up (the
# web app shows it at 1.5 CSS px per PDF point); WebP at quality 75 is about half the size of the
# old 110 DPI PNGs. Photos are scaled to fit 1600 px.
PAGE_DPI = 150
MAX_IMAGE_EDGE = 1600
IMAGE_FORMAT, IMAGE_MEDIA_TYPE = "WEBP", "image/webp"
IMAGE_OPTIONS = {"quality": 75, "method": 6}

CHECK_LABELS = {
    "balances_add_up": "Balances add up",
    "documents_agree": "Payslips match the bank statements",
    "payslip_realistic": "Payslip tax looks realistic",
    "employer_real": "Employer is a real company",
    "looks_like_real_life": "Spending looks like everyday life",
    "names_match": "Names match",
    "file_signals": "Files haven't been edited",
}


def _update(check_id: str, **values) -> None:
    with session_scope() as s:
        s.execute(update(Check).where(Check.id == check_id).values(**values))


# Memory bound: at most MAX_CONCURRENT_CHECKS checks are processed at once (see config.py). Checks
# waiting for a worker stay "queued" and hold no request thread, so the API keeps answering.
_WORKERS = ThreadPoolExecutor(max_workers=max(1, get_settings().max_concurrent_checks),
                              thread_name_prefix="check")


async def run_check(check_id: str, folder: Path, uploads: list[SavedUpload], rent: Decimal) -> None:
    """Background task for POST /checks: queue the check and wait for it to finish."""
    await asyncio.wrap_future(_WORKERS.submit(process_check, check_id, folder, uploads, rent))


def _start(check_id: str, progress: dict) -> bool:
    """queued -> reading, only if still queued (the cleanup fails checks that wait too long)."""
    with session_scope() as s:
        return s.execute(update(Check).where(Check.id == check_id, Check.status == "queued")
                         .values(status="reading", progress=progress)).rowcount == 1


def _encode(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=IMAGE_FORMAT, **IMAGE_OPTIONS)
    return buf.getvalue()


def render_pages(data: bytes, only: set[int] | None = None) -> list[tuple[int, bytes, int, int]]:
    """An image of each page in `only` (all pages if None): (page number, image, width, height).
    PDFs and photos both supported. Page numbers that don't exist are skipped."""
    if sniff(data) == "pdf":
        out = []
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            for i, page in enumerate(doc, start=1):
                if only is not None and i not in only:
                    continue
                pix = page.get_pixmap(dpi=PAGE_DPI, alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                out.append((i, _encode(img), pix.width, pix.height))
                del pix, img
        return out
    if only is not None and 1 not in only:
        return []
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
        return [(1, _encode(img), img.width, img.height)]


def evidence_pages(report: Report) -> dict[str, set[int]]:
    """The pages the evidence view can show, by file key. Mirrors findingsOf() in
    web/src/lib/report.ts: every flagged piece of evidence on a flagged check. A finding about a
    whole file (no page) is shown on page 1."""
    pages: dict[str, set[int]] = {}
    for check in report.checks:
        if check.status != "flag":
            continue
        for e in check.evidence:
            if e.flag and e.file:
                pages.setdefault(e.file, set()).add(e.page or 1)
    return pages


def store_evidence_images(check_id: str, uploads: list[SavedUpload], files: list[dict],
                          report: Report) -> dict | None:
    """Render and store only the finding pages, one file at a time. Returns the first one, which
    the checking screen shows as its preview (None when nothing was flagged)."""
    wanted = evidence_pages(report)
    first = None
    for upload in uploads:
        pages = wanted.get(upload.key)
        entry = next(f for f in files if f["key"] == upload.key)
        if "error" in entry:
            continue
        entry["images"] = []
        if not pages:
            continue
        rendered = render_pages(upload.path.read_bytes(), only=pages)
        with session_scope() as s:
            s.add_all(PageImage(check_id=check_id, file_key=upload.key, page=n, image=image,
                                media_type=IMAGE_MEDIA_TYPE, width=w, height=h)
                      for n, image, w, h in rendered)
        entry["images"] = [{"page": n, "width": w, "height": h} for n, _, w, h in rendered]
        if first is None and rendered:
            first = {"file": upload.key, "page": rendered[0][0]}
    return first


def initial_progress(total_files: int) -> dict:
    return {"stage": "queued", "documents_total": total_files, "documents_read": 0,
            "files": {}, "preview": None,
            "checks": {cid: None for cid in CHECK_ORDER}}


def process_check(check_id: str, folder: Path, uploads: list[SavedUpload], rent: Decimal,
                  companies: CompanyLookup | None = None) -> None:
    started = time.perf_counter()
    progress = initial_progress(len(uploads))
    try:
        progress["stage"] = "reading"
        if not _start(check_id, progress):
            log.info("check.skipped reason=not_queued")
            return  # the finally below still deletes the uploads

        docs: list[DocumentInput] = []
        files: list[dict] = []
        for upload in uploads:
            data = upload.path.read_bytes()
            entry: dict = {"key": upload.key, "name": upload.name}
            try:
                result = read_document(data)
            except ReaderError as err:
                log.info("check.file_unreadable code=%s", err.code)
                entry["error"] = {"code": err.code, "message": err.message}
                files.append(entry)
                progress["files"][upload.key] = {"status": "unreadable", **entry["error"]}
            else:
                entry.update(document_type=result.document_type, source=result.source,
                             pages=[p.model_dump() for p in result.pages])
                files.append(entry)
                docs.append(DocumentInput(upload.key, result))
                progress["files"][upload.key] = {"status": "read"}
            finally:
                del data
            progress["documents_read"] += 1
            _update(check_id, progress=progress)

        usable = [d for d in docs if d.result.document_type != "unknown"]
        if not usable:
            first_error = next((f["error"] for f in files if "error" in f), None)
            code, message = (first_error["code"], first_error["message"]) if first_error else (
                "no_usable_documents",
                "We couldn't find a bank statement or payslip in these files.")
            _update(check_id, status="failed", error_code=code, error_message=message,
                    rate_limit_key=None, progress={**progress, "stage": "failed"})
            log.info("check.failed code=%s ms=%d", code, (time.perf_counter() - started) * 1000)
            analytics.record("check_failed", check_id, code)
            return

        progress["stage"] = "checking"
        _update(check_id, status="checking", progress=progress)

        def done(result: CheckResult) -> None:
            progress["checks"][result.id] = result.status
            _update(check_id, progress=progress)

        lookup = companies or CompaniesHouseClient(get_settings().companies_house_api_key)
        report = run_checks(usable, rent, lookup, on_check_done=done)

        progress["stage"] = "writing"
        _update(check_id, status="writing", progress=progress)
        report = apply_wording(report)
        progress["preview"] = store_evidence_images(check_id, uploads, files, report)

        progress["stage"] = "done"
        _update(check_id, status="done", progress=progress, rate_limit_key=None,
                report={"files": files, "report": report.model_dump(mode="json")})
        analytics.record("check_finished", check_id, report.verdict)
        log.info("check.done verdict=%s flags=%s ms=%d", report.verdict,
                 ",".join(c.id for c in report.checks if c.status == "flag") or "none",
                 (time.perf_counter() - started) * 1000)
    except Exception as exc:
        log.error("check.error type=%s", type(exc).__name__)
        analytics.record("check_failed", check_id, "internal_error")
        try:
            _update(check_id, status="failed", error_code="internal_error",
                    error_message="Something went wrong while checking these documents. "
                                  "Please try again.",
                    rate_limit_key=None, progress={**progress, "stage": "failed"})
        except Exception as db_exc:
            log.error("check.error_update_failed type=%s", type(db_exc).__name__)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        if folder.exists():  # a locked file on Windows, say: try once more, then log
            time.sleep(0.2)
            shutil.rmtree(folder, ignore_errors=True)
            if folder.exists():
                log.error("check.temp_not_deleted")
