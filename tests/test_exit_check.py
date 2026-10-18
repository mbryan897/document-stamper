"""Phase 2 exit check, automated.

Every generated corpus file is stamped with the shipped default profile, with
the white box on and off. Each output page is rendered in its visible
orientation and checked:

1. Stamp-ink pixels (#2B3A8F and its anti-aliased blends) exist, and their
   bounding box lies inside the visible page, inside the placement box the
   engine reported, and at the top-right: right edge and top edge within a
   few points of the 0.25 in margin.
2. The stamp's extracted text is the expected text, set in the bundled
   font, and its reading direction, mapped into visible coordinates, is left
   to right (the right way up).
3. The original file is byte-identical afterwards.

The 520-page file is rendered on 10 sample pages (rendering is the slow part);
its text and direction are checked on every page.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stamper.core.geometry import unrotated_vector_to_visible
from stamper.handlers.pdf import BOX_PADDING_PT, PdfHandler
from tests.stamping import (
    FAKE_ID,
    MARGIN_PT,
    default_job,
    open_pdf,
    password_for,
    pixel_bbox,
    sha256,
)
from tools import make_corpus

SAMPLE_PAGES = 10
TOL_PT = 2.0  # rendering and glyph side-bearing slack


def _pages_to_render(n: int) -> list[int]:
    if n <= SAMPLE_PAGES:
        return list(range(n))
    step = (n - 1) / (SAMPLE_PAGES - 1)
    return sorted({round(i * step) for i in range(SAMPLE_PAGES)})


@pytest.mark.parametrize("box", [True, False], ids=["box-on", "box-off"])
@pytest.mark.parametrize("name", list(make_corpus.CORPUS))
def test_every_corpus_file_stamps_top_right_upright(
    name: str, box: bool, corpus_dir: Path, tmp_path: Path
) -> None:
    src = corpus_dir / name
    before = sha256(src)
    pw = password_for(name)
    out = tmp_path / f"stamped_{name}"

    result = PdfHandler().apply(
        src, default_job(background_box=box), out,
        password=pw, accept_restrictions=True,
    )
    assert sha256(src) == before, "the original was modified"
    assert out.is_file()

    by_page = {p.page: p for p in result.placements}
    with open_pdf(out, pw) as doc, open_pdf(src, pw) as orig:
        assert doc.page_count == orig.page_count == result.pages_stamped
        total = doc.page_count
        render = set(_pages_to_render(total))
        for page in doc:
            n = page.number + 1
            pl = by_page[n]
            assert pl.box_drawn is box
            expected = f"{FAKE_ID}  |  Report  |  Page {n} of {total}"
            assert pl.text == expected

            # Text: present once, in our font, reading left to right on screen.
            spans = [
                s for b in page.get_text("dict")["blocks"] for line in b.get("lines", [])
                for s in line["spans"] if "Page " in s["text"] and FAKE_ID in s["text"]
            ]
            lines = [
                line for b in page.get_text("dict")["blocks"] for line in b.get("lines", [])
                if any(FAKE_ID in s["text"] for s in line["spans"])
            ]
            assert len(lines) == 1, f"page {n}: expected one stamp line"
            assert "".join(s["text"] for s in lines[0]["spans"]) == expected
            assert all("Atkinson" in s["font"] for s in spans), {s["font"] for s in spans}
            direction = unrotated_vector_to_visible(tuple(lines[0]["dir"]), page.rotation)
            assert direction == pytest.approx((1.0, 0.0), abs=1e-3), f"page {n} reads {direction}"

            if page.number not in render:
                continue
            pix = page.get_pixmap(dpi=72, alpha=False)  # 1 px = 1 pt, visible orientation
            assert (pix.width, pix.height) == (round(page.rect.width), round(page.rect.height))
            ink = pixel_bbox(pix)
            assert ink is not None, f"page {n}: no stamp ink found"
            w, h = page.rect.width, page.rect.height
            # Inside the visible page and inside the reported box.
            assert 0 <= ink.x0 and ink.x1 <= w and 0 <= ink.y0 and ink.y1 <= h
            bx0, by0, bx1, by1 = pl.box
            assert ink.x0 >= bx0 - 1 and ink.x1 <= bx1 + 1, f"page {n}: ink {ink} outside box {pl.box}"
            assert ink.y0 >= by0 - 1 and ink.y1 <= by1 + 1, f"page {n}: ink {ink} outside box {pl.box}"
            # Top-right: right and top edges at the margin (plus padding).
            assert w - MARGIN_PT - BOX_PADDING_PT - TOL_PT - 1 <= ink.x1 <= w - MARGIN_PT + 1, (
                f"page {n}: ink right edge {ink.x1} not at the right margin of {w}")
            assert MARGIN_PT - 1 <= ink.y0 <= MARGIN_PT + BOX_PADDING_PT + pl.font_size, (
                f"page {n}: ink top {ink.y0} not at the top margin")
            # Nothing stray: the ink is in the top-right quarter of the page.
            assert (ink.x0 + ink.x1) / 2 > w / 2 and ink.y1 < h / 2


@pytest.mark.parametrize("name", list(make_corpus.CORPUS))
def test_page_geometry_and_count_unchanged(name: str, corpus_dir: Path, tmp_path: Path) -> None:
    src = corpus_dir / name
    pw = password_for(name)
    out = tmp_path / name
    PdfHandler().apply(src, default_job(), out, password=pw, accept_restrictions=True)
    with open_pdf(src, pw) as a, open_pdf(out, pw) as b:
        assert a.page_count == b.page_count
        for pa, pb in zip(a, b):
            assert pa.mediabox == pb.mediabox
            assert pa.cropbox == pb.cropbox
            assert pa.rotation == pb.rotation
