"""Background processing for one check: read -> render pages -> six checks -> wording -> save.

The temp folder holding the uploads is deleted in a `finally`, whether processing succeeds or
fails. Only the report and rendered page images are stored, and only until the check expires.
Logs carry the check's stage, timings and error types; never document contents.
"""

from __future__ import annotations

import io
import logging
import shutil
import time
from decimal import Decimal
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps
from sqlalchemy import update

from app.checks import DocumentInput, run_checks
from app.checks.companies_house import CompaniesHouseClient, CompanyLookup
from app.checks.models import CHECK_ORDER, CheckResult
from app.config import get_settings
from app.db.models import Check, PageImage
from app.db.session import session_scope
from app.reader import ReaderError, read_document
from app.reader.filetype import sniff
from app.report.wording import apply_wording
from app.services.uploads import SavedUpload

log = logging.getLogger(__name__)

PAGE_DPI = 110
MAX_IMAGE_EDGE = 1600

CHECK_LABELS = {
    "balances_add_up": "Balances add up",
    "documents_agree": "Payslips match the bank statements",
    "payslip_realistic": "Payslip tax looks realistic",
    "employer_real": "Employer is a real company",
    "looks_like_real_life": "Spending looks like everyday life",
    "names_match": "Names match",
}


def _update(check_id: str, **values) -> None:
    with session_scope() as s:
        s.execute(update(Check).where(Check.id == check_id).values(**values))


def render_pages(data: bytes) -> list[tuple[int, bytes, int, int]]:
    """PNG for each page: (page number, png, width, height). PDFs and photos both supported."""
    if sniff(data) == "pdf":
        out = []
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            for i, page in enumerate(doc, start=1):
                pix = page.get_pixmap(dpi=PAGE_DPI)
                out.append((i, pix.tobytes("png"), pix.width, pix.height))
        return out
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return [(1, buf.getvalue(), img.width, img.height)]


def initial_progress(total_files: int) -> dict:
    return {"stage": "queued", "documents_total": total_files, "documents_read": 0,
            "checks": {cid: None for cid in CHECK_ORDER}}


def process_check(check_id: str, folder: Path, uploads: list[SavedUpload], rent: Decimal,
                  companies: CompanyLookup | None = None) -> None:
    started = time.perf_counter()
    progress = initial_progress(len(uploads))
    try:
        progress["stage"] = "reading"
        _update(check_id, status="reading", progress=progress)

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
            else:
                pages = render_pages(data)
                with session_scope() as s:
                    s.add_all(PageImage(check_id=check_id, file_key=upload.key, page=n, png=png,
                                        width=w, height=h) for n, png, w, h in pages)
                entry.update(document_type=result.document_type, source=result.source,
                             pages=[p.model_dump() for p in result.pages],
                             images=[{"page": n, "width": w, "height": h} for n, _, w, h in pages])
                files.append(entry)
                docs.append(DocumentInput(upload.key, result))
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
                    progress={**progress, "stage": "failed"})
            log.info("check.failed code=%s ms=%d", code, (time.perf_counter() - started) * 1000)
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

        progress["stage"] = "done"
        _update(check_id, status="done", progress=progress,
                report={"files": files, "report": report.model_dump(mode="json")})
        log.info("check.done verdict=%s flags=%s ms=%d", report.verdict,
                 ",".join(c.id for c in report.checks if c.status == "flag") or "none",
                 (time.perf_counter() - started) * 1000)
    except Exception as exc:
        log.error("check.error type=%s", type(exc).__name__)
        try:
            _update(check_id, status="failed", error_code="internal_error",
                    error_message="Something went wrong while checking these documents. "
                                  "Please try again.",
                    progress={**progress, "stage": "failed"})
        except Exception as db_exc:
            log.error("check.error_update_failed type=%s", type(db_exc).__name__)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        if folder.exists():  # a locked file on Windows, say: try once more, then log
            time.sleep(0.2)
            shutil.rmtree(folder, ignore_errors=True)
            if folder.exists():
                log.error("check.temp_not_deleted")
