"""Make a simulated phone photo of a printed synthetic statement page (for the AI path).

Renders page 1 of genuine/statement_2026-08.pdf, then adds what a phone photo of paper on a
table looks like: perspective tilt, a slight rotation, a drop shadow, uneven lighting, blur,
sensor noise and JPEG compression. Writes photo_statement/ with the JPEG and an expected.json.

Run after generate.py:
    cd api && uv run python ../fixtures/make_photo.py
"""

from __future__ import annotations

import importlib.util
import io
import json
import random
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "genuine" / "statement_2026-08.pdf"


def _load_generator():
    spec = importlib.util.spec_from_file_location("tallied_fixture_generate", HERE / "generate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination for the 8x8 perspective system (no numpy needed)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col], strict=True)]
    return [m[i][n] / m[i][i] for i in range(n)]


def _perspective_coeffs(dst: list[tuple[float, float]], src: list[tuple[float, float]]) -> list[float]:
    """Coefficients mapping output (dst) points back to input (src) points, for Image.transform."""
    a, b = [], []
    for (x, y), (u, v) in zip(dst, src, strict=True):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b += [u, v]
    return _solve(a, b)


def make_photo(out_dir: Path, seed: int = 7) -> Path:
    rng = random.Random(seed)
    with pymupdf.open(SOURCE) as doc:
        pix = doc[0].get_pixmap(dpi=170)
        page = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    pw, ph = page.size

    # Slightly off-white paper
    page = ImageChops.multiply(page, Image.new("RGB", page.size, (250, 246, 238)))

    # Table background with texture
    W, H = int(pw * 1.22), int(ph * 1.14)
    bg = Image.new("RGB", (W, H), (96, 78, 62))
    noise = Image.effect_noise((W, H), 28).convert("RGB")
    bg = Image.blend(bg, ImageChops.multiply(bg, noise), 0.35)

    # Where the paper corners land in the photo (tilted, phone held at a slight angle)
    mx, my = (W - pw) / 2, (H - ph) / 2
    # Mostly keystone (top edge further from the camera), with a gentle row slope (~1/3 of a row
    # across the page) so rows stay unambiguous, as in a reasonably careful phone photo.
    dst = [
        (mx + 45, my + 12),            # top-left
        (mx + pw - 40, my + 22),       # top-right
        (mx + pw + 12, my + ph - 14),  # bottom-right
        (mx - 12, my + ph - 22),       # bottom-left
    ]
    src = [(0, 0), (pw, 0), (pw, ph), (0, ph)]
    coeffs = _perspective_coeffs(dst, src)
    warped = page.transform((W, H), Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC)
    mask = Image.new("L", (pw, ph), 255).transform(
        (W, H), Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC)

    # Drop shadow
    shadow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shadow).polygon([(x + 18, y + 22) for x, y in dst], fill=150)
    shadow = shadow.filter(ImageFilter.GaussianBlur(25))
    bg = Image.composite(Image.new("RGB", (W, H), (30, 22, 16)), bg, shadow)
    photo = Image.composite(warped, bg, mask)

    # Uneven lighting: brighter top-left, darker bottom-right
    light = Image.linear_gradient("L").rotate(-35, expand=False).resize((W, H))
    light = light.point(lambda v: 255 - int(v * 0.30))
    photo = ImageChops.multiply(photo, Image.merge("RGB", (light, light, light)))

    # Lens softness, sensor noise, then phone-style JPEG
    photo = photo.filter(ImageFilter.GaussianBlur(0.9))
    grain = Image.effect_noise((W, H), 18).convert("RGB")
    photo = Image.blend(photo, ImageChops.overlay(photo, grain), 0.25)
    photo = photo.rotate(rng.uniform(-0.4, 0.4), resample=Image.Resampling.BICUBIC, fillcolor=(60, 48, 38))

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "statement_2026-08_page1_photo.jpg"
    photo.save(path, format="JPEG", quality=80)
    return path


def write_expected(out_dir: Path) -> None:
    gen = _load_generator()
    case = gen.case_genuine()
    stmt = case.statements[0]
    rows_on_page_1 = gen.rows_per_page(first=True) - 1  # minus the "brought forward" row
    txs = stmt.txs[:rows_on_page_1]
    expected = {
        "case": "photo_statement",
        "synthetic": True,
        "description": "Simulated phone photo of page 1 of genuine/statement_2026-08.pdf. "
                       "Must go through the AI path.",
        "source_pdf": "genuine/statement_2026-08.pdf",
        "page": 1,
        "truth": {
            "document_type": "bank_statement",
            "account_holder": case.holder.display,
            "period_start": stmt.period_start.isoformat(),
            "period_end": stmt.period_end.isoformat(),
            "opening_balance": str(stmt.opening),
            "transactions": [
                {"date": t.day.isoformat(), "description": t.description,
                 "paid_in": str(t.paid_in) if t.paid_in is not None else None,
                 "paid_out": str(t.paid_out) if t.paid_out is not None else None,
                 "balance": str(t.balance)}
                for t in txs
            ],
        },
    }
    (out_dir / "expected.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    out = HERE / "photo_statement"
    print(f"wrote {make_photo(out)}")
    write_expected(out)
    print(f"wrote {out / 'expected.json'}")
