"""Generate the synthetic PDF test corpus.

Every file uses fabricated placeholder text only. No real names, case numbers
or case material may ever be added here.

Usage (PowerShell, from the repo root):
    python tools\\make_corpus.py            # build missing files
    python tools\\make_corpus.py --force    # rebuild everything
    python tools\\make_corpus.py --out D:\\some\\folder
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

import pymupdf

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "tests" / "corpus" / "generated"

# Page sizes in PDF points (72 per inch).
LETTER = (612.0, 792.0)
LEGAL = (612.0, 1008.0)
A4 = (595.0, 842.0)
RECEIPT = (225.0, 576.0)  # 3.125 in x 8 in thermal receipt

# Passwords for the encrypted samples. Synthetic; used only by tests.
USER_PASSWORD = "corpus-user-pw"
OWNER_PASSWORD = "corpus-owner-pw"

LARGE_PAGE_COUNT = 520

# Fabricated case numbers in each agency format, for the filename pre-fill
# tests. The 9999xx suffixes are deliberately unlikely to match a real case.
FAKE_CASE_DASHED = "99-2026-999901"
FAKE_CASE_SO = "SO261017-999902"

FILLER = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sample placeholder "
    "text for stamper testing. Nothing on this page refers to a real person, "
    "place, event or case. "
)


def _fill(
    page: pymupdf.Page,
    box: pymupdf.Rect,
    unit: str,
    fontsize: float,
    align: int = 0,
    max_repeat: int = 400,
) -> None:
    """Fill ``box`` with as many repeats of ``unit`` as fit.

    PyMuPDF's insert_textbox writes nothing at all when text overflows, so
    shrink the text until it fits and fail loudly if even one repeat does not.
    """
    width, height = page.rect.width, page.rect.height

    def fits(count: int) -> bool:
        with pymupdf.open() as scratch:
            probe = scratch.new_page(width=width, height=height)
            rc = probe.insert_textbox(box, unit * count, fontname="helv", fontsize=fontsize, align=align)
            return rc >= 0

    lo, hi, best = 1, max_repeat, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if fits(mid):
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    if best == 0:
        raise ValueError(f"text does not fit in {box}")
    page.insert_textbox(box, unit * best, fontname="helv", fontsize=fontsize, align=align)


def _body(page: pymupdf.Page, title: str, margin: float = 72.0) -> None:
    """Write a title and placeholder paragraphs inside the page margins."""
    rect = page.rect
    page.insert_text((margin, margin), title, fontname="helv", fontsize=14)
    box = pymupdf.Rect(margin, margin + 20, rect.width - margin, rect.height - margin)
    _fill(page, box, FILLER, fontsize=10, max_repeat=6)


def _new_page(doc: pymupdf.Document, size: tuple[float, float], title: str) -> pymupdf.Page:
    page = doc.new_page(width=size[0], height=size[1])
    _body(page, title)
    return page


def _save(doc: pymupdf.Document, path: Path, **kwargs: object) -> None:
    doc.save(path, garbage=3, deflate=True, **kwargs)
    doc.close()


# --- Individual builders ---------------------------------------------------


def build_letter_portrait(path: Path) -> None:
    doc = pymupdf.open()
    for i in range(3):
        _new_page(doc, LETTER, f"Letter portrait - page {i + 1}")
    _save(doc, path)


def build_letter_landscape(path: Path) -> None:
    doc = pymupdf.open()
    for i in range(3):
        _new_page(doc, (LETTER[1], LETTER[0]), f"Letter landscape - page {i + 1}")
    _save(doc, path)


def _build_rotated(path: Path, rotation: int) -> None:
    doc = pymupdf.open()
    for i in range(3):
        page = _new_page(doc, LETTER, f"/Rotate {rotation} - page {i + 1}")
        page.set_rotation(rotation)
    _save(doc, path)


def build_rotate_90(path: Path) -> None:
    _build_rotated(path, 90)


def build_rotate_180(path: Path) -> None:
    _build_rotated(path, 180)


def build_rotate_270(path: Path) -> None:
    _build_rotated(path, 270)


def build_rotate_mixed(path: Path) -> None:
    doc = pymupdf.open()
    for i, rotation in enumerate((0, 90, 180, 270)):
        page = _new_page(doc, LETTER, f"Mixed rotation - page {i + 1} at /Rotate {rotation}")
        page.set_rotation(rotation)
    _save(doc, path)


def build_cropbox_offset(path: Path) -> None:
    """CropBox smaller than and offset from the MediaBox (not centered)."""
    doc = pymupdf.open()
    for i in range(2):
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        # Outline the MediaBox edge so a misplaced stamp is easy to spot.
        page.draw_rect(page.mediabox, color=(0.8, 0.2, 0.2), width=2)
        _body(page, f"CropBox offset - page {i + 1}", margin=110)
        page.set_cropbox(pymupdf.Rect(36, 54, 540, 720))
    _save(doc, path)


def build_mixed_sizes(path: Path) -> None:
    doc = pymupdf.open()
    for name, size in (("Letter", LETTER), ("Legal", LEGAL), ("A4", A4), ("Receipt", RECEIPT)):
        page = doc.new_page(width=size[0], height=size[1])
        margin = 18.0 if name == "Receipt" else 72.0
        _body(page, f"Mixed sizes - {name}", margin=margin)
    _save(doc, path)


def build_scanned_image_only(path: Path) -> None:
    """Pages that are a single raster image with no text layer."""
    source = pymupdf.open()
    for i in range(2):
        _new_page(source, LETTER, f"Scanned image page {i + 1}")
    doc = pymupdf.open()
    for src_page in source:
        pix = src_page.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_image(page.rect, pixmap=pix)
    source.close()
    _save(doc, path)


def build_text_top_right(path: Path) -> None:
    """Dense text running right up to the top and right edges."""
    doc = pymupdf.open()
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    _body(page, "Text into the top-right corner")
    corner = pymupdf.Rect(page.rect.width / 2, 0, page.rect.width, 56)
    page.draw_rect(corner, color=None, fill=(0.93, 0.93, 0.93))
    _fill(page, corner, "CORNER TEXT ", fontsize=8, align=pymupdf.TEXT_ALIGN_RIGHT)
    _save(doc, path)


def build_encrypted_user_password(path: Path) -> None:
    doc = pymupdf.open()
    for i in range(2):
        _new_page(doc, LETTER, f"Password to open - page {i + 1}")
    _save(
        doc,
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw=USER_PASSWORD,
        owner_pw=OWNER_PASSWORD,
    )


def build_owner_restrictions(path: Path) -> None:
    """Opens without a password; printing, copying and editing restricted."""
    doc = pymupdf.open()
    for i in range(2):
        _new_page(doc, LETTER, f"Owner restrictions only - page {i + 1}")
    _save(
        doc,
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="",
        owner_pw=OWNER_PASSWORD,
        permissions=int(pymupdf.PDF_PERM_ACCESSIBILITY),
    )


FORM_VALUES: dict[str, str] = {
    "reference_id": "SAMPLE-REF-0001",
    "submitted_by": "Placeholder Name",
    "notes": "Filled value that must survive stamping.",
}


def build_acroform(path: Path) -> None:
    doc = pymupdf.open()
    page = _new_page(doc, LETTER, "AcroForm with filled fields")
    y = 420.0
    for name, value in FORM_VALUES.items():
        page.insert_text((72, y - 4), name, fontname="helv", fontsize=9)
        widget = pymupdf.Widget()
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.field_name = name
        widget.field_value = value
        widget.rect = pymupdf.Rect(180, y - 16, 540, y + 4)
        widget.text_fontsize = 10
        page.add_widget(widget)
        y += 40
    # A field touching the top-right corner, under where a stamp would land.
    corner = pymupdf.Widget()
    corner.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    corner.field_name = "corner_field"
    corner.field_value = "Corner value"
    corner.rect = pymupdf.Rect(400, 10, 602, 34)
    corner.text_fontsize = 10
    page.add_widget(corner)
    _save(doc, path)


def build_large(path: Path) -> None:
    doc = pymupdf.open()
    for i in range(LARGE_PAGE_COUNT):
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((72, 72), f"Large file - page {i + 1} of {LARGE_PAGE_COUNT}", fontname="helv", fontsize=14)
        rc = page.insert_textbox(pymupdf.Rect(72, 100, 540, 720), FILLER * 3, fontname="helv", fontsize=10)
        if rc < 0:
            raise ValueError("large-file filler does not fit")
    _save(doc, path)


def _build_named_case(path: Path) -> None:
    doc = pymupdf.open()
    _new_page(doc, LETTER, "Filename contains a fabricated case number")
    _save(doc, path)


# --- Special copies (not in the manifest; built from a corpus file on demand) ---


def signed_copy(src: Path, dst: Path) -> Path:
    """A copy whose first form field becomes a filled signature field.

    For detection and the confirmation flow, not cryptographic validity.
    """
    with pymupdf.open(src) as d:
        w = next(iter(d[0].widgets()))
        sig = d.get_new_xref()
        d.update_object(sig, "<</Type/Sig/Filter/Adobe.PPKLite/SubFilter/adbe.pkcs7.detached"
                             "/Contents<00>/ByteRange[0 0 0 0]>>")
        d.xref_set_key(w.xref, "FT", "/Sig")
        d.xref_set_key(w.xref, "V", f"{sig} 0 R")
        d.xref_set_key(d.pdf_catalog(), "AcroForm/SigFlags", "3")
        d.save(dst)
    return dst


def xfa_copy(src: Path, dst: Path) -> Path:
    """A copy of an AcroForm file with an /XFA entry added."""
    with pymupdf.open(src) as d:
        x = d.get_new_xref()
        d.update_object(x, "<<>>")
        d.update_stream(x, b"<xdp:xdp xmlns:xdp='http://ns.adobe.com/xdp/'/>")
        d.xref_set_key(d.pdf_catalog(), "AcroForm/XFA", f"{x} 0 R")
        d.save(dst)
    return dst


# --- Manifest ----------------------------------------------------------------

Builder = Callable[[Path], None]

CORPUS: dict[str, Builder] = {
    "letter_portrait.pdf": build_letter_portrait,
    "letter_landscape.pdf": build_letter_landscape,
    "rotate_90.pdf": build_rotate_90,
    "rotate_180.pdf": build_rotate_180,
    "rotate_270.pdf": build_rotate_270,
    "rotate_mixed.pdf": build_rotate_mixed,
    "cropbox_offset.pdf": build_cropbox_offset,
    "mixed_sizes.pdf": build_mixed_sizes,
    "scanned_image_only.pdf": build_scanned_image_only,
    "text_top_right.pdf": build_text_top_right,
    "encrypted_user_password.pdf": build_encrypted_user_password,
    "owner_restrictions.pdf": build_owner_restrictions,
    "acroform_filled.pdf": build_acroform,
    "large_520_pages.pdf": build_large,
    f"provider_return_{FAKE_CASE_DASHED}.pdf": _build_named_case,
    f"{FAKE_CASE_SO}_provider_return.pdf": _build_named_case,
}

# Files that need a password to open, by filename.
PASSWORDS: dict[str, str] = {
    "encrypted_user_password.pdf": USER_PASSWORD,
}

# Files that are encrypted but open without a password.
OWNER_ONLY: frozenset[str] = frozenset({"owner_restrictions.pdf"})


def generate(out_dir: Path = DEFAULT_OUT, force: bool = False) -> list[Path]:
    """Build every corpus file into ``out_dir`` and return their paths.

    Existing files are kept unless ``force`` is true.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, builder in CORPUS.items():
        path = out_dir / name
        if force or not path.exists():
            tmp = path.with_suffix(".tmp")
            builder(tmp)
            tmp.replace(path)  # never leave a half-written file behind
        written.append(path)
    return written


def is_complete(out_dir: Path = DEFAULT_OUT) -> bool:
    """Return True if every corpus file exists in ``out_dir``."""
    return all((out_dir / name).is_file() for name in CORPUS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the synthetic PDF test corpus.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder")
    parser.add_argument("--force", action="store_true", help="rebuild files that already exist")
    args = parser.parse_args(argv)
    paths = generate(args.out, force=args.force)
    for path in paths:
        sys.stdout.write(f"{path.stat().st_size:>12,}  {path.name}\n")
    sys.stdout.write(f"{len(paths)} files in {args.out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
