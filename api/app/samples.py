"""Sample checks: fixed sets of made-up documents anyone can run to see Tallied work.

The documents are the synthetic fixtures (fictional people, a fictional employer and the fictional
Northgate Bank). The web app serves copies as static files (web/public/samples/<id>/) and uploads
them through the normal pipeline with `sample=<id>`. A check only counts as a sample if the
uploaded files are exactly that sample's files, checked by SHA-256 here. So a real upload can't be
labelled a sample to get round the daily limit. tests/test_samples.py keeps these hashes, the
fixtures and the web copies in step.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from app.services.errors import ApiError
from app.services.uploads import SavedUpload


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


_PAYSLIPS = (("payslip_2026-09.pdf", "bac4a35855c6e6190e421cab9d1dd6d92c58c41c7f994f98e0ec61be7bab202a"),
             ("payslip_2026-10.pdf", "3fce1c54a1e7c23c7530caf89b5f03f28a693ae0be7f0f2befc3f65fe72515e6"))
_STATEMENTS_AUG_SEP = (
    ("statement_2026-08.pdf", "4dfdab8d1dc24f02fb38839b859d33ebb51f186783964d5758d54fa3abdf1947"),
    ("statement_2026-09.pdf", "db3de116e78a7dcce3393afea9553991c9fd53b0a479616a2410014e7a9ff5b5"))

SAMPLES: dict[str, Sample] = {s.id: s for s in [
    Sample("genuine", "A genuine tenant", "genuine", Decimal("1100.00"), (
        *_STATEMENTS_AUG_SEP,
        ("statement_2026-10.pdf", "744c6b7b551f41439048ba91e059464453215fd37ef8235afa9fe9cd697f7fd1"),
        *_PAYSLIPS)),
    Sample("edited_balance", "An edited bank statement", "edited_balance", Decimal("1100.00"), (
        *_STATEMENTS_AUG_SEP,
        ("statement_2026-10.pdf", "9d5877f90eeb9f0a44e8a9235c1371f5de73919ae222e57c278f0d73826ac46c"),
        *_PAYSLIPS)),
    Sample("multiple_problems", "Several problems at once", "multiple_problems", Decimal("1100.00"), (
        ("statement_2026-08.pdf", "07d3800a96dd69a3a42b378695c2cd93871cb69439bbc566c288b6822fe0e70f"),
        ("statement_2026-09.pdf", "1369e766b01f9f90583bde04718d52539638f4a3346828308d79a00bffbafb14"),
        ("statement_2026-10.pdf", "57ebf455b93da0c615136b536b388d80eabe180e7d24f1181b9f85d866b09de3"),
        ("payslip_2026-09.pdf", "60a46bd3df2cb03432da9d30fb6548956aef3c8e80580cde24d4e1f25b1b848d"),
        ("payslip_2026-10.pdf", "abcc245e848f6cd132e48e30fd2caa4c871e75391fbb23b2c0fd6c2378eb4c59"))),
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
    """The uploads must be exactly this sample's documents (any order), or 400."""
    want = Counter(h for _, h in sample.files)
    got = Counter(sha256_file(u.path) for u in uploads)
    if got != want:
        raise ApiError("not_a_sample")
