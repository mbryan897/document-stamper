"""Helpers shared by the Phase 2 tests. Synthetic values only."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from stamper.core.config import Config, load_config
from stamper.handlers.base import StampJob
from tools import make_corpus

REPO = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO / "config" / "default.json"
RECORDS_CONFIG = REPO / "tests" / "fixtures" / "records_sample.json"
MANUAL_DIR = REPO / "tests" / "corpus" / "manual"

FAKE_ID = make_corpus.FAKE_CASE_DASHED  # 99-2026-999901
DEFAULT_VALUES = {"identifier": FAKE_ID, "doc_type": "Report", "doc_title": ""}
RECORDS_VALUES = {"identifier": FAKE_ID, "doc_type": "Search warrant return", "doc_title": "", "received": ""}

MARGIN_PT = 18.0  # 0.25 in
INK = (0x2B, 0x3A, 0x8F)


def default_config() -> Config:
    """The shipped generic config."""
    return load_config(DEFAULT_CONFIG)


def records_config() -> Config:
    """The synthetic agency-style fixture."""
    return load_config(RECORDS_CONFIG)


def default_job(**kw: object) -> StampJob:
    """A job from the shipped config's default profile."""
    cfg = default_config()
    return StampJob.from_profile(cfg, cfg.default_profile, DEFAULT_VALUES, **kw)  # type: ignore[arg-type]


def records_job(**kw: object) -> StampJob:
    """A job from the fixture's Records submission profile."""
    cfg = records_config()
    return StampJob.from_profile(cfg, cfg.default_profile, RECORDS_VALUES, **kw)  # type: ignore[arg-type]


def password_for(name: str) -> str | None:
    """The corpus password for a file, if it has one."""
    return make_corpus.PASSWORDS.get(name)


def open_pdf(path: Path, password: str | None = None) -> pymupdf.Document:
    """Open a PDF, authenticating if needed."""
    doc = pymupdf.open(path)
    if doc.needs_pass:
        assert password and doc.authenticate(password), f"can't open {path.name}"
    return doc


def sha256(path: Path) -> str:
    """SHA-256 of a file (tests only; the product doesn't hash, D-007)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_ink(r: int, g: int, b: int) -> bool:
    """True for the stamp-ink blue and its anti-aliased blends with white.

    Corpus content is black, grey, white and (outside the CropBox) red, so a
    clearly blue pixel can only come from the stamp.
    """
    return b - r > 30 and b - g > 20


def is_dark(r: int, g: int, b: int) -> bool:
    """Black or grey page content: neutral (not bluish ink) and not white."""
    return max(r, g, b) - min(r, g, b) < 12 and max(r, g, b) < 240


@dataclass(frozen=True)
class PixelBox:
    """Bounding box of matching pixels, in pixels; x1/y1 exclusive."""

    x0: int
    y0: int
    x1: int
    y1: int


def pixel_bbox(pix: pymupdf.Pixmap, test=is_ink) -> PixelBox | None:  # noqa: ANN001
    """Bounding box of the pixels for which ``test(r, g, b)`` is true."""
    n, w, h, stride = pix.n, pix.width, pix.height, pix.stride
    data = pix.samples
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        row = data[y * stride: y * stride + w * n]
        hit = [x for x, (r, g, b) in enumerate(zip(row[0::n], row[1::n], row[2::n])) if test(r, g, b)]
        if hit:
            y0 = min(y0, y)
            y1 = y
            x0 = min(x0, hit[0])
            x1 = max(x1, hit[-1])
    if x1 < 0:
        return None
    return PixelBox(x0, y0, x1 + 1, y1 + 1)


def count_pixels(pix: pymupdf.Pixmap, rect: tuple[float, float, float, float], test, scale: float) -> int:  # noqa: ANN001
    """How many pixels inside ``rect`` (points) pass ``test``."""
    n, stride = pix.n, pix.stride
    data = pix.samples
    x0, y0 = int(rect[0] * scale) + 1, int(rect[1] * scale) + 1
    x1, y1 = int(rect[2] * scale) - 1, int(rect[3] * scale) - 1
    total = 0
    for y in range(y0, y1):
        row = data[y * stride + x0 * n: y * stride + x1 * n]
        total += sum(1 for r, g, b in zip(row[0::n], row[1::n], row[2::n]) if test(r, g, b))
    return total
