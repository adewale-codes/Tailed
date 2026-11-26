"""Sample checks: fixed sets of made-up documents anyone can run to see Tallied work.

The documents are the synthetic fixtures (fictional people, a fictional employer and the fictional
Northgate Bank). The web app serves copies as static files (web/public/samples/<id>/) and uploads
them through the normal pipeline with `sample=<id>`. A check only counts as a sample if the
uploaded files are exactly that sample's files, checked by SHA-256 here. So a real upload can't be
labelled a sample to get round the daily limit.

The hashes are constants here, inside api/, because the image is built from api/ alone (heroku.yml):
nothing outside api/ exists in production. They are the bytes git stores, which is what Vercel
serves. .gitattributes marks *.pdf as binary so no checkout can change those bytes.
tests/test_samples.py keeps these hashes, the fixtures, the web copies and .gitattributes in step,
and api/scripts/image_test.py runs the samples against the real production image.
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from app.services.errors import ApiError
from app.services.uploads import SavedUpload

log = logging.getLogger(__name__)

@dataclass(frozen=True)
class Sample:
    id: str
    title: str
    fixture: str  # folder in /fixtures the files come from
    rent: Decimal  # the rent the sample is run with
    files: tuple[tuple[str, str], ...]  # (file name, SHA-256)

    def public(self) -> dict:
        """What reports and progress say about the sample."""
        return {"id": self.id, "title": self.title}


_PAYSLIPS = (("payslip_2026-09.pdf", "fdd730b30d46ad4199ac7de9324c1ff0c90e317a17d1a21e3562adef48ecc5af"),
             ("payslip_2026-10.pdf", "88b1efb0347b1b0ec47b3f5410f2891cefe8db459c4635b1627719de96751835"))
_STATEMENTS_AUG_SEP = (
    ("statement_2026-08.pdf", "c9acdb5d7533190138222c0f3746b501e9daa94b5666da9d72d8dfc49a506206"),
    ("statement_2026-09.pdf", "59de274817b799483a488c502b268612f29be20cc23afd37f818f3e71729e43f"))

SAMPLES: dict[str, Sample] = {s.id: s for s in [
    Sample("genuine", "A genuine tenant", "genuine", Decimal("1100.00"), (
        *_STATEMENTS_AUG_SEP,
        ("statement_2026-10.pdf", "628b376bde1f38a21f631a635969c597e1a241d0d83942b9a9aa7dbeeff0d1b3"),
        *_PAYSLIPS)),
    Sample("edited_balance", "An edited bank statement", "edited_balance", Decimal("1100.00"), (
        *_STATEMENTS_AUG_SEP,
        ("statement_2026-10.pdf", "9d5877f90eeb9f0a44e8a9235c1371f5de73919ae222e57c278f0d73826ac46c"),
        *_PAYSLIPS)),
    Sample("multiple_problems", "Several problems at once", "multiple_problems", Decimal("1100.00"), (
        ("statement_2026-08.pdf", "293db7981a40ae15b3778ab106aa10142ac614c607e59879e11b41d2dad18787"),
        ("statement_2026-09.pdf", "bbfe453747a29cc8ec9203a6db8c19a3e2a1a1ab15ced307858d681ceb0a9079"),
        ("statement_2026-10.pdf", "57ebf455b93da0c615136b536b388d80eabe180e7d24f1181b9f85d866b09de3"),
        ("payslip_2026-09.pdf", "b1c9c2035a0b43a25c1866395595b57e55e264aceafee975f4f79df82e3bcaa9"),
        ("payslip_2026-10.pdf", "9ea58a5cc10001275d6ab15b68a7e38f8a8ea8abf8ee733c65a8ab7b13b37542"))),
]}


def get_sample(sample_id: str | None) -> Sample | None:
    """None when no sample was asked for; 404 for an unknown one."""
    if not sample_id:
        return None
    sample = SAMPLES.get(sample_id.strip())
    if sample is None:
        raise ApiError("sample_not_found")
    return sample


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_sample_files(sample: Sample, uploads: list[SavedUpload]) -> None:
    """The uploads must be exactly this sample's documents (any order), or 400.

    Every rejection logs one short reason code, the sample id and numbers only (never a file's
    name or contents): sample_hashes_unavailable, sample_file_count_mismatch, or
    sample_hash_mismatch with the 1-based position of the first upload that isn't in the sample.
    """
    if not sample.files:  # a broken build: nothing to compare with
        log.error("sample.rejected sample=%s reason=sample_hashes_unavailable", sample.id)
        raise ApiError("internal_error")
    if len(uploads) != len(sample.files):
        log.warning("sample.rejected sample=%s reason=sample_file_count_mismatch expected=%d got=%d",
                    sample.id, len(sample.files), len(uploads))
        raise ApiError("not_a_sample")
    remaining = Counter(h for _, h in sample.files)
    for position, upload in enumerate(uploads, start=1):
        digest = sha256_file(upload.path)
        if remaining[digest] == 0:
            log.warning("sample.rejected sample=%s reason=sample_hash_mismatch position=%d",
                        sample.id, position)
            raise ApiError("not_a_sample")
        remaining[digest] -= 1
