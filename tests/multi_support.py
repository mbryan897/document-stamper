"""Helpers for the Phase 4.5 tests: synthetic forms, a page too small to stamp, stamp checks.

Synthetic content only; nothing here refers to a real case.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from stamper.core.geometry import unrotated_vector_to_visible
from stamper.handlers.base import StampPlacement
from stamper.handlers.pdf import BOX_PADDING_PT
from tests.stamping import MARGIN_PT, pixel_bbox

TOL_PT = 2.0


def make_form(path: Path, tag: str) -> Path:
    """A one-page form with fields every combine must keep apart.

    ``case_no`` (text), ``agree`` (checkbox), ``choice`` (a radio group of two
    kids, ``yes`` and ``no``) and ``person`` (a parent with kids ``first``
    and ``last``). Values carry ``tag`` so each file's values are recognisable.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 60), f"Synthetic form {tag}", fontname="helv", fontsize=12)

    def text(name: str, rect: tuple[float, float, float, float], value: str) -> None:
        w = pymupdf.Widget()
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.field_name = name
        w.field_value = value
        w.rect = pymupdf.Rect(*rect)
        w.text_fontsize = 10
        page.add_widget(w)

    text("case_no", (72, 200, 300, 220), f"{tag}-case")
    box = pymupdf.Widget()
    box.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    box.field_name = "agree"
    box.rect = pymupdf.Rect(72, 240, 90, 258)
    box.field_value = False
    page.add_widget(box)
    for i in range(2):
        k = pymupdf.Widget()
        k.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
        k.field_name = f"tmp_radio_{i}"
        k.rect = pymupdf.Rect(72 + 40 * i, 280, 90 + 40 * i, 298)
        k.field_value = i == 0
        page.add_widget(k)
    text("tmp_first", (320, 200, 500, 220), f"{tag}-first")
    text("tmp_last", (320, 240, 500, 260), f"{tag}-last")
    xref = {w.field_name: w.xref for w in page.widgets()}

    parent = doc.get_new_xref()
    doc.update_object(parent, f"<</T(person)/FT/Tx/Kids[{xref['tmp_first']} 0 R {xref['tmp_last']} 0 R]>>")
    for name, partial in (("tmp_first", "first"), ("tmp_last", "last")):
        doc.xref_set_key(xref[name], "T", f"({partial})")
        doc.xref_set_key(xref[name], "FT", "null")
        doc.xref_set_key(xref[name], "Parent", f"{parent} 0 R")

    radio = doc.get_new_xref()
    kids = [xref["tmp_radio_0"], xref["tmp_radio_1"]]
    doc.update_object(radio, f"<</T(choice)/FT/Btn/Ff 49152/V/yes/Kids[{kids[0]} 0 R {kids[1]} 0 R]>>")
    for i, kid in enumerate(kids):
        state = ("yes", "no")[i]
        on = doc.xref_get_key(kid, "AP/N/Yes")[1]
        off = doc.xref_get_key(kid, "AP/N/Off")[1]
        for key in ("T", "FT", "V"):
            doc.xref_set_key(kid, key, "null")
        doc.xref_set_key(kid, "Parent", f"{radio} 0 R")
        doc.xref_set_key(kid, "AP/N", f"<</{state} {on}/Off {off}>>")
        doc.xref_set_key(kid, "AS", "/yes" if i == 0 else "/Off")

    roots = [xref["case_no"], xref["agree"], parent, radio]
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm/Fields", "[" + " ".join(f"{r} 0 R" for r in roots) + "]")
    doc.save(path)
    doc.close()
    return path


def make_tiny(path: Path) -> Path:
    """A one-page PDF 1 inch square: no stamp fits on it."""
    doc = pymupdf.open()
    page = doc.new_page(width=72, height=72)
    page.insert_text((6, 40), "tiny", fontname="helv", fontsize=8)
    doc.save(path)
    doc.close()
    return path


def root_names(doc: pymupdf.Document) -> list[str]:
    """Top-level field names in the AcroForm Fields array."""
    import re

    kind, val = doc.xref_get_key(doc.pdf_catalog(), "AcroForm/Fields")
    assert kind == "array", (kind, val)
    out = []
    for m in re.finditer(r"(\d+) 0 R", val):
        k, name = doc.xref_get_key(int(m.group(1)), "T")
        out.append(name if k == "string" else "")
    return out


def page_body(page: pymupdf.Page, stamp_marker: str) -> str:
    """The page's text without the stamp line."""
    lines = [ln for ln in page.get_text().splitlines() if stamp_marker not in ln]
    return "\n".join(lines).strip()


def assert_top_right_upright(page: pymupdf.Page, placement: StampPlacement, expected: str) -> None:
    """The Phase 2 exit-check tests for one page: text, font, direction, and ink at the top right."""
    n = page.number + 1
    assert placement.text == expected
    lines = [line for b in page.get_text("dict")["blocks"] for line in b.get("lines", [])
             if any(expected.split("  |  ")[0] in s["text"] for s in line["spans"])]
    assert len(lines) == 1, f"page {n}: expected one stamp line"
    assert "".join(s["text"] for s in lines[0]["spans"]) == expected
    assert all("Atkinson" in s["font"] for s in lines[0]["spans"])
    direction = unrotated_vector_to_visible(tuple(lines[0]["dir"]), page.rotation)
    assert direction == pytest.approx((1.0, 0.0), abs=1e-3), f"page {n} reads {direction}"
    pix = page.get_pixmap(dpi=72, alpha=False)
    ink = pixel_bbox(pix)
    assert ink is not None, f"page {n}: no stamp ink"
    w, h = page.rect.width, page.rect.height
    bx0, by0, bx1, by1 = placement.box
    assert ink.x0 >= bx0 - 1 and ink.x1 <= bx1 + 1 and ink.y0 >= by0 - 1 and ink.y1 <= by1 + 1
    assert w - MARGIN_PT - BOX_PADDING_PT - TOL_PT - 1 <= ink.x1 <= w - MARGIN_PT + 1
    assert MARGIN_PT - 1 <= ink.y0 <= MARGIN_PT + BOX_PADDING_PT + placement.font_size
    assert (ink.x0 + ink.x1) / 2 > w / 2 and ink.y1 < h / 2
