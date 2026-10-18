"""Bounded work on unusual inputs (D-122): preview pixel cap and soft size notes."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

import stamper.handlers.pdf as pdf
from stamper.handlers.pdf import MAX_PREVIEW_PIXELS, PdfHandler, preview_zoom, size_notes
from tests.stamping import default_job


def _page_pdf(path: Path, width: float, height: float, pages: int = 1) -> Path:
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=width, height=height)
        page.insert_text((min(20, width / 4), min(30, height / 2)), "x", fontsize=min(11, height / 4))
    doc.save(path)
    doc.close()
    return path


def _pixels(png: bytes) -> int:
    pix = pymupdf.Pixmap(png)
    return pix.width * pix.height


def test_huge_page_preview_stays_under_the_cap(tmp_path: Path) -> None:
    src = _page_pdf(tmp_path / "huge_page.pdf", 14_400, 14_400)  # the PDF maximum, 200 in square
    png = PdfHandler().render_preview(src, 1, default_job(), dpi=150)
    assert _pixels(png) <= MAX_PREVIEW_PIXELS
    assert _pixels(png) > MAX_PREVIEW_PIXELS * 0.9  # lowered only as far as needed


def test_ordinary_page_preview_is_unchanged(corpus_dir: Path) -> None:
    rect = pymupdf.Rect(0, 0, 612, 792)
    assert preview_zoom(rect, 150) == pytest.approx(150 / 72)
    png = PdfHandler().render_preview(corpus_dir / "letter_portrait.pdf", 1, default_job(), dpi=72)
    pix = pymupdf.Pixmap(png)
    assert (pix.width, pix.height) == (612, 792)


def test_huge_page_gets_a_note(tmp_path: Path) -> None:
    src = _page_pdf(tmp_path / "huge_page.pdf", 14_400, 14_400)
    notes = size_notes(PdfHandler().inspect(src))
    assert notes == ["Page 1 is over 100 inches on a side, so the preview is drawn at a lower resolution."]
    plan = PdfHandler().plan(src, default_job())
    assert notes[0] in plan.warnings


def test_tiny_pages_get_a_note(tmp_path: Path) -> None:
    src = _page_pdf(tmp_path / "tiny.pdf", 50, 50, pages=7)
    (note,) = size_notes(PdfHandler().inspect(src))
    assert note == "Pages 1, 2, 3, 4, 5 and 2 more are under 1 inch on a side. Check the stamp there carefully."


def test_many_pages_and_big_files_get_notes(corpus_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    info = PdfHandler().inspect(corpus_dir / "large_520_pages.pdf")
    assert size_notes(info) == []
    monkeypatch.setattr(pdf, "LARGE_PAGE_COUNT", 500)
    monkeypatch.setattr(pdf, "LARGE_FILE_BYTES", 1)
    notes = size_notes(info)
    assert any("has 520 pages; checking and saving take longer" in n for n in notes)
    assert any("MB; checking and saving take longer" in n for n in notes)
