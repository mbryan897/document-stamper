"""Phase 0 exit check: PyMuPDF opens and re-saves every corpus file intact.

For each generated file: open (with the password where needed), save to a temp
folder, reopen, and assert page count, page boxes and rotation are unchanged,
that encryption survives, and that the source file was not modified.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf
import pytest

from tools import make_corpus

PageGeometry = tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...], int]


def _box(rect: pymupdf.Rect) -> tuple[float, ...]:
    return tuple(round(v, 3) for v in rect)


def _geometry(doc: pymupdf.Document) -> list[PageGeometry]:
    """MediaBox, CropBox, visible rect and /Rotate for every page."""
    return [(_box(p.mediabox), _box(p.cropbox), _box(p.rect), p.rotation) for p in doc]


def _open(path: Path) -> pymupdf.Document:
    doc = pymupdf.open(path)
    if doc.needs_pass:
        password = make_corpus.PASSWORDS[path.name]
        assert doc.authenticate(password), f"password rejected for {path.name}"
    return doc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_corpus_is_complete(corpus_dir: Path) -> None:
    missing = [n for n in make_corpus.CORPUS if not (corpus_dir / n).is_file()]
    assert not missing, f"missing corpus files: {missing}"


@pytest.mark.parametrize("name", list(make_corpus.CORPUS))
def test_open_save_reopen_unchanged(name: str, corpus_dir: Path, tmp_path: Path) -> None:
    src = corpus_dir / name
    before_hash = _sha256(src)

    doc = _open(src)
    expected_pages = doc.page_count
    expected_geometry = _geometry(doc)
    expected_encryption = doc.metadata.get("encryption")

    out = tmp_path / name
    assert out.resolve() != src.resolve()
    # PDF_ENCRYPT_KEEP: PyMuPDF's default save silently removes encryption.
    doc.save(out, garbage=1, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    doc.close()

    reopened = _open(out)
    try:
        assert reopened.page_count == expected_pages
        assert _geometry(reopened) == expected_geometry
        assert reopened.metadata.get("encryption") == expected_encryption
    finally:
        reopened.close()

    assert _sha256(src) == before_hash, "source file was modified"


def test_user_password_required(corpus_dir: Path) -> None:
    doc = pymupdf.open(corpus_dir / "encrypted_user_password.pdf")
    assert doc.needs_pass
    assert not doc.authenticate("wrong-password")
    assert doc.authenticate(make_corpus.USER_PASSWORD)


@pytest.mark.parametrize("name", sorted(make_corpus.OWNER_ONLY))
def test_owner_only_opens_without_password_but_is_restricted(name: str, corpus_dir: Path) -> None:
    doc = pymupdf.open(corpus_dir / name)
    assert not doc.needs_pass
    assert doc.metadata.get("encryption")
    assert not doc.permissions & pymupdf.PDF_PERM_MODIFY
    assert not doc.permissions & pymupdf.PDF_PERM_PRINT


def test_rotation_files_have_expected_rotation(corpus_dir: Path) -> None:
    for angle in (90, 180, 270):
        with pymupdf.open(corpus_dir / f"rotate_{angle}.pdf") as doc:
            assert all(p.rotation == angle for p in doc)
    with pymupdf.open(corpus_dir / "rotate_mixed.pdf") as doc:
        assert [p.rotation for p in doc] == [0, 90, 180, 270]


def test_cropbox_is_smaller_and_offset(corpus_dir: Path) -> None:
    with pymupdf.open(corpus_dir / "cropbox_offset.pdf") as doc:
        for page in doc:
            assert page.mediabox.contains(page.cropbox)
            assert page.cropbox != page.mediabox
            assert page.cropbox.x0 > 0 and page.cropbox.y0 > 0


def test_mixed_sizes(corpus_dir: Path) -> None:
    expected = [make_corpus.LETTER, make_corpus.LEGAL, make_corpus.A4, make_corpus.RECEIPT]
    with pymupdf.open(corpus_dir / "mixed_sizes.pdf") as doc:
        assert [(p.rect.width, p.rect.height) for p in doc] == expected


def test_scanned_pages_have_no_text_layer(corpus_dir: Path) -> None:
    with pymupdf.open(corpus_dir / "scanned_image_only.pdf") as doc:
        for page in doc:
            assert page.get_text().strip() == ""
            assert len(page.get_images()) == 1


def test_top_right_corner_has_text_to_cover(corpus_dir: Path) -> None:
    with pymupdf.open(corpus_dir / "text_top_right.pdf") as doc:
        page = doc[0]
        # The 1.5 in x 0.5 in corner where the default stamp lands.
        corner = pymupdf.Rect(page.rect.width - 108, 0, page.rect.width, 36)
        assert "CORNER" in page.get_text(clip=corner)


def test_acroform_values_survive_resave(corpus_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "form.pdf"
    with pymupdf.open(corpus_dir / "acroform_filled.pdf") as doc:
        assert doc.is_form_pdf
        doc.save(out)
    with pymupdf.open(out) as doc:
        values = {w.field_name: w.field_value for w in doc[0].widgets()}
    for name, value in make_corpus.FORM_VALUES.items():
        assert values[name] == value


def test_large_file_page_count(corpus_dir: Path) -> None:
    with pymupdf.open(corpus_dir / "large_520_pages.pdf") as doc:
        assert doc.page_count >= 500


def test_case_number_filenames_present(corpus_dir: Path) -> None:
    names = [p.name for p in corpus_dir.glob("*.pdf")]
    assert any(make_corpus.FAKE_CASE_DASHED in n for n in names)
    assert any(make_corpus.FAKE_CASE_SO in n for n in names)
