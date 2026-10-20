"""PDF handler: integrity, protection, metadata, font, box, fit and errors.

Signed, XFA and damaged files are built synthetically here from corpus
files (a signature-shaped field, an /XFA key, a broken startxref). They test
detection and error handling, not cryptographic validity. Hand-made samples
in tests/corpus/manual/ are used too when present.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

import pymupdf
import pytest

from stamper.core.config import StampSpec
from stamper.handlers.base import (
    Cancelled,
    ConfirmationRequired,
    MissingGlyph,
    OutputRefused,
    PasswordRequired,
    StampDoesNotFit,
    StampJob,
    UnsupportedDocument,
    WrongPassword,
)
from stamper.handlers.pdf import FONT_FILE, PdfHandler, font_path, same_file
from tests.stamping import (
    DEFAULT_VALUES,
    FAKE_ID,
    MANUAL_DIR,
    count_pixels,
    default_config,
    default_job,
    is_dark,
    is_ink,
    pixel_bbox,
    records_job,
    sha256,
)
from tools import make_corpus

H = PdfHandler()
WINDOWS = sys.platform == "win32"


def _copy(corpus_dir: Path, name: str, tmp_path: Path, new_name: str | None = None) -> Path:
    dst = tmp_path / (new_name or name)
    shutil.copyfile(corpus_dir / name, dst)
    return dst


def _info_dict(doc: pymupdf.Document) -> dict[str, str]:
    kind, val = doc.xref_get_key(-1, "Info")
    if kind != "xref":
        return {}
    xref = int(val.split()[0])
    out = {}
    for key in doc.xref_get_keys(xref):
        k, v = doc.xref_get_key(xref, key)
        out[key] = v
    return out


# ------------------------------------------------------------------ fixtures built from the corpus


@pytest.fixture
def signed_pdf(corpus_dir: Path, tmp_path: Path) -> Path:
    """AcroForm file whose first field is turned into a filled signature field."""
    path = tmp_path / "signed_synthetic.pdf"
    with pymupdf.open(corpus_dir / "acroform_filled.pdf") as d:
        w = next(iter(d[0].widgets()))
        sig = d.get_new_xref()
        d.update_object(sig, "<</Type/Sig/Filter/Adobe.PPKLite/SubFilter/adbe.pkcs7.detached"
                             "/Contents<00>/ByteRange[0 0 0 0]>>")
        d.xref_set_key(w.xref, "FT", "/Sig")
        d.xref_set_key(w.xref, "V", f"{sig} 0 R")
        d.xref_set_key(d.pdf_catalog(), "AcroForm/SigFlags", "3")
        d.save(path)
    return path


@pytest.fixture
def xfa_pdf(corpus_dir: Path, tmp_path: Path) -> Path:
    """AcroForm file with an /XFA entry added."""
    path = tmp_path / "xfa_synthetic.pdf"
    with pymupdf.open(corpus_dir / "acroform_filled.pdf") as d:
        x = d.get_new_xref()
        d.update_object(x, "<<>>")
        d.update_stream(x, b"<xdp:xdp xmlns:xdp='http://ns.adobe.com/xdp/'/>")
        d.xref_set_key(d.pdf_catalog(), "AcroForm/XFA", f"{x} 0 R")
        d.save(path)
    return path


@pytest.fixture
def damaged_pdf(corpus_dir: Path, tmp_path: Path) -> Path:
    """letter_portrait.pdf with its startxref offset broken."""
    data = (corpus_dir / "letter_portrait.pdf").read_bytes()
    i = data.rfind(b"startxref")
    path = tmp_path / "damaged_xref.pdf"
    path.write_bytes(data[:i] + re.sub(rb"startxref\s+\d+", b"startxref\n999999", data[i:]))
    return path


# ------------------------------------------------------------------ inspect


def test_inspect_corpus_flags(corpus_dir: Path) -> None:
    info = H.inspect(corpus_dir / "rotate_mixed.pdf")
    assert info.page_count == 4
    assert [p.rotation for p in info.pages] == [0, 90, 180, 270]
    assert (info.pages[1].width, info.pages[1].height) == (792, 612)
    assert not (info.encrypted or info.signed or info.xfa or info.restricted or info.needs_password)

    owner = H.inspect(corpus_dir / "owner_restrictions.pdf")
    assert owner.encrypted and owner.restricted and not owner.needs_password

    with pytest.raises(PasswordRequired):
        H.inspect(corpus_dir / "encrypted_user_password.pdf")
    user = H.inspect(corpus_dir / "encrypted_user_password.pdf", make_corpus.USER_PASSWORD)
    assert user.encrypted and user.needs_password and not user.restricted

    crop = H.inspect(corpus_dir / "cropbox_offset.pdf")
    assert (crop.pages[0].width, crop.pages[0].height) == (504, 666)
    assert H.inspect(corpus_dir / "acroform_filled.pdf").has_form_fields


def test_can_open(corpus_dir: Path, tmp_path: Path) -> None:
    assert H.can_open(corpus_dir / "letter_portrait.pdf")
    txt = tmp_path / "notes.pdf"
    txt.write_text("not a pdf", encoding="utf-8")
    assert not H.can_open(txt)
    assert not H.can_open(tmp_path / "missing.pdf")


def test_not_a_pdf_and_missing(tmp_path: Path) -> None:
    txt = tmp_path / "notes.pdf"
    txt.write_text("not a pdf", encoding="utf-8")
    with pytest.raises(UnsupportedDocument, match="isn't a PDF"):
        H.apply(txt, default_job(), tmp_path / "out.pdf")
    with pytest.raises(UnsupportedDocument, match="Can't find"):
        H.inspect(tmp_path / "missing.pdf")


def test_unrepairable_file_is_a_clear_error(tmp_path: Path) -> None:
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"%PDF-1.7\n this is not a real PDF body")
    with pytest.raises(UnsupportedDocument, match="damaged"):
        H.inspect(junk)


# ------------------------------------------------------------------ originals never modified (D-008)


@pytest.mark.parametrize("name", list(make_corpus.CORPUS))
def test_original_bytes_identical(name: str, corpus_dir: Path, tmp_path: Path) -> None:
    src = corpus_dir / name
    before = sha256(src)
    stat = src.stat()
    H.apply(src, default_job(), tmp_path / "out.pdf",
            password=make_corpus.PASSWORDS.get(name), accept_restrictions=True)
    assert sha256(src) == before
    assert src.stat().st_mtime_ns == stat.st_mtime_ns


def test_refuses_output_equal_to_input(corpus_dir: Path, tmp_path: Path) -> None:
    src = _copy(corpus_dir, "letter_portrait.pdf", tmp_path)
    before = sha256(src)
    with pytest.raises(OutputRefused, match="over the original"):
        H.apply(src, default_job(), src, overwrite=True)
    assert sha256(src) == before


def test_refuses_relative_path_to_input(corpus_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _copy(corpus_dir, "letter_portrait.pdf", tmp_path)
    (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path / "sub")
    with pytest.raises(OutputRefused):
        H.apply(src, default_job(), Path("..") / "letter_portrait.pdf", overwrite=True)
    with pytest.raises(OutputRefused):
        H.apply(src, default_job(), Path(".") / ".." / "sub" / ".." / "letter_portrait.pdf", overwrite=True)


def test_refuses_hard_link_to_input(corpus_dir: Path, tmp_path: Path) -> None:
    src = _copy(corpus_dir, "letter_portrait.pdf", tmp_path)
    link = tmp_path / "another_name.pdf"
    os.link(src, link)
    with pytest.raises(OutputRefused, match="over the original"):
        H.apply(src, default_job(), link, overwrite=True)


@pytest.mark.skipif(not WINDOWS, reason="case-insensitive paths, \\\\?\\ and 8.3 names are Windows-only")
def test_refuses_windows_path_variants(corpus_dir: Path, tmp_path: Path) -> None:
    import ctypes

    src = _copy(corpus_dir, "letter_portrait.pdf", tmp_path, "Letter Portrait Long Name.pdf")
    before = sha256(src)
    variants = [
        Path(str(src).upper()),
        Path(str(src).lower()),
        Path("\\\\?\\" + str(src.resolve())),
    ]
    buf = ctypes.create_unicode_buffer(1024)
    if ctypes.windll.kernel32.GetShortPathNameW(str(src), buf, 1024) and buf.value != str(src):
        variants.append(Path(buf.value))  # 8.3 name, when the volume has them
    for v in variants:
        with pytest.raises(OutputRefused):
            H.apply(src, default_job(), v, overwrite=True)
    assert sha256(src) == before


def test_same_file_helper(tmp_path: Path) -> None:
    a = tmp_path / "a.pdf"
    a.write_bytes(b"%PDF-1.7")
    assert same_file(a, tmp_path / "." / "a.pdf")
    assert not same_file(a, tmp_path / "b.pdf")


def test_refuses_existing_output_unless_overwrite(corpus_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    out.write_bytes(b"existing")
    with pytest.raises(OutputRefused, match="already exists"):
        H.apply(corpus_dir / "letter_portrait.pdf", default_job(), out)
    assert out.read_bytes() == b"existing"
    H.apply(corpus_dir / "letter_portrait.pdf", default_job(), out, overwrite=True)
    assert out.read_bytes().startswith(b"%PDF")


def test_refuses_missing_output_folder(corpus_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(OutputRefused, match="doesn't exist"):
        H.apply(corpus_dir / "letter_portrait.pdf", default_job(), tmp_path / "nope" / "out.pdf")


def _leftovers(folder: Path) -> list[str]:
    return [p.name for p in folder.iterdir() if p.name.startswith("~stamper-")]


def test_no_partial_file_after_failed_save(corpus_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_save = pymupdf.Document.save

    def broken_save(self: pymupdf.Document, filename: object, *a: object, **k: object) -> None:
        Path(str(filename)).write_bytes(b"%PDF-1.7 half written")
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(pymupdf.Document, "save", broken_save)
    out = tmp_path / "out.pdf"
    with pytest.raises(OSError, match="simulated"):
        H.apply(corpus_dir / "letter_portrait.pdf", default_job(), out)
    monkeypatch.setattr(pymupdf.Document, "save", real_save)
    assert not out.exists()
    assert _leftovers(tmp_path) == []


def test_cancel_writes_nothing(corpus_dir: Path, tmp_path: Path) -> None:
    calls: list[tuple[int, int]] = []
    out = tmp_path / "out.pdf"
    with pytest.raises(Cancelled):
        H.apply(corpus_dir / "large_520_pages.pdf", default_job(), out,
                progress=lambda d, t: calls.append((d, t)), cancel=lambda: len(calls) >= 3)
    assert calls == [(1, 520), (2, 520), (3, 520)]
    assert not out.exists()
    assert _leftovers(tmp_path) == []


def test_large_file_reports_progress(corpus_dir: Path, tmp_path: Path) -> None:
    calls: list[tuple[int, int]] = []
    result = H.apply(corpus_dir / "large_520_pages.pdf", default_job(), tmp_path / "out.pdf",
                     progress=lambda d, t: calls.append((d, t)))
    assert result.pages_stamped == make_corpus.LARGE_PAGE_COUNT
    assert calls == [(i, 520) for i in range(1, 521)]


# ------------------------------------------------------------------ font and text


def test_font_is_bundled_with_license() -> None:
    assert font_path().name == FONT_FILE and font_path().is_file()
    assert (font_path().parent / "OFL.txt").is_file()


@pytest.mark.parametrize("name", ["letter_portrait.pdf", "scanned_image_only.pdf", "rotate_mixed.pdf"])
def test_font_embedded_and_nothing_else_added(name: str, corpus_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    H.apply(corpus_dir / name, default_job(), out)
    with pymupdf.open(corpus_dir / name) as a, pymupdf.open(out) as b:
        for pa, pb in zip(a, b):
            before = {f[3] for f in pa.get_fonts()}
            added = [f for f in pb.get_fonts() if f[3] not in before]
            assert len(added) == 1, added
            xref, ext, ftype, basefont = added[0][:4]
            assert ext == "ttf" and "Atkinson" in basefont
            assert b.extract_font(xref)[3], "font program not embedded"
            # The stamp's spans all use the embedded font: no Helvetica fallback.
            stamp_spans = [s for blk in pb.get_text("dict")["blocks"] for ln in blk.get("lines", [])
                           for s in ln["spans"] if FAKE_ID in s["text"] or "Report" in s["text"]]
            assert stamp_spans and all("Atkinson" in s["font"] for s in stamp_spans)


def test_stamp_text_searchable_on_scanned_pages(corpus_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    H.apply(corpus_dir / "scanned_image_only.pdf", default_job(), out)
    with pymupdf.open(out) as d:
        for page in d:
            assert page.search_for(FAKE_ID)
            assert f"Page {page.number + 1} of 2" in page.get_text()


def test_missing_glyph_refused(corpus_dir: Path, tmp_path: Path) -> None:
    cfg = default_config()
    job = StampJob.from_profile(cfg, cfg.default_profile, {**DEFAULT_VALUES, "identifier": "99-2026-999901案"})
    out = tmp_path / "out.pdf"
    with pytest.raises(MissingGlyph, match="Identifier") as e:
        H.apply(corpus_dir / "letter_portrait.pdf", job, out)
    assert e.value.char == "案"
    assert not out.exists()


def test_line_break_refused(corpus_dir: Path, tmp_path: Path) -> None:
    cfg = default_config()
    job = StampJob.from_profile(cfg, cfg.default_profile, {**DEFAULT_VALUES, "identifier": "99-2026\n999901"})
    with pytest.raises(MissingGlyph, match="single line"):
        H.apply(corpus_dir / "letter_portrait.pdf", job, tmp_path / "out.pdf")


@pytest.mark.parametrize("ch", ["\t", "\u2028", "\u2029", "\x85"])
def test_other_line_breaks_refused(corpus_dir: Path, tmp_path: Path, ch: str) -> None:
    cfg = default_config()
    job = StampJob.from_profile(cfg, cfg.default_profile, {**DEFAULT_VALUES, "doc_type": f"Court{ch}order"})
    with pytest.raises(MissingGlyph, match="single line"):
        H.plan(corpus_dir / "letter_portrait.pdf", job)


def test_non_breaking_space_is_drawn(corpus_dir: Path) -> None:
    # Pasted from Word: U+00A0 is a space the font has, not a line break.
    cfg = default_config()
    job = StampJob.from_profile(cfg, cfg.default_profile, {**DEFAULT_VALUES, "doc_type": "Court\u00a0order"})
    assert H.plan(corpus_dir / "letter_portrait.pdf", job).page_count >= 1


# ------------------------------------------------------------------ background box


def test_background_box_covers_and_switches_off(corpus_dir: Path, tmp_path: Path) -> None:
    src = corpus_dir / "text_top_right.pdf"
    scale = 2.0
    results = {}
    for box in (True, False):
        out = tmp_path / f"box_{box}.pdf"
        r = H.apply(src, default_job(background_box=box), out)
        pl = r.placements[0]
        with pymupdf.open(out) as d:
            pix = d[0].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            results[box] = (
                count_pixels(pix, pl.box, is_dark, scale),
                count_pixels(pix, pl.box, is_ink, scale),
                d[0].get_text(clip=pymupdf.Rect(pl.box)),
            )
    dark_on, ink_on, text_on = results[True]
    dark_off, ink_off, _ = results[False]
    assert dark_on == 0, "content shows through the box"
    assert dark_off > 100, "content should show through with the box off"
    assert ink_on > 50 and ink_off > 50
    # The box hides content visually; it doesn't delete it from the file.
    assert "CORNER" in text_on


def test_box_rectangle_reported_per_page(corpus_dir: Path, tmp_path: Path) -> None:
    r = H.apply(corpus_dir / "mixed_sizes.pdf", default_job(), tmp_path / "out.pdf")
    assert [p.page for p in r.placements] == [1, 2, 3, 4]
    widths = {(round(p.box[2]), p.page) for p in r.placements}
    # Each page's box ends 0.25 in + nothing from its own right edge.
    assert widths == {(612 - 18, 1), (612 - 18, 2), (595 - 18, 3), (225 - 18, 4)}


# ------------------------------------------------------------------ fit, shrink, overlap


def test_receipt_shrinks_with_warning(corpus_dir: Path, tmp_path: Path) -> None:
    r = H.apply(corpus_dir / "mixed_sizes.pdf", default_job(), tmp_path / "out.pdf")
    receipt = r.placements[3]
    assert 6 <= receipt.font_size < 9
    assert all(p.font_size == 9 for p in r.placements[:3])
    assert any(w.startswith("Page 4, stamp 1: font reduced") for w in r.warnings)


def test_does_not_fit_names_page_and_stamp(corpus_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    with pytest.raises(StampDoesNotFit) as e:
        H.apply(corpus_dir / "mixed_sizes.pdf", records_job(), out)
    msg = str(e.value)
    assert e.value.page == 4 and e.value.stamp == 1
    assert msg.startswith("Page 4 (3.1 × 8.0 in), stamp 1")
    assert "Search warrant return" in msg and "at 6 pt" in msg and "available" in msg
    assert not out.exists() and _leftovers(tmp_path) == []


def test_along_right_fits_the_receipt(corpus_dir: Path, tmp_path: Path) -> None:
    cfg = default_config()
    stamp = StampSpec(text="{identifier}  |  {doc_type}  |  Page {page} of {total}", direction="along-right")
    job = StampJob.from_profile(cfg, cfg.default_profile, DEFAULT_VALUES, stamps=[stamp])
    r = H.apply(corpus_dir / "mixed_sizes.pdf", job, tmp_path / "out.pdf")
    assert all(p.font_size == 9 for p in r.placements)
    with pymupdf.open(tmp_path / "out.pdf") as d:
        line = next(ln for b in d[3].get_text("dict")["blocks"] for ln in b.get("lines", [])
                    if any(FAKE_ID in s["text"] for s in ln["spans"]))
        assert tuple(round(v, 3) for v in line["dir"]) == (0.0, 1.0)  # top to bottom


def test_second_stamp_for_page_numbers(corpus_dir: Path, tmp_path: Path) -> None:
    """D-013 as the schema expresses it: a second stamp at the bottom."""
    cfg = default_config()
    stamps = [StampSpec(text="{identifier}  |  {doc_type}"),
              StampSpec(text="Page {page} of {total}", anchor="bottom-center", background_box=False)]
    job = StampJob.from_profile(cfg, cfg.default_profile, DEFAULT_VALUES, stamps=stamps)
    r = H.apply(corpus_dir / "rotate_mixed.pdf", job, tmp_path / "out.pdf")
    bottoms = [p for p in r.placements if p.stamp == 2]
    assert [p.text for p in bottoms] == [f"Page {i} of 4" for i in range(1, 5)]
    assert all(not p.box_drawn for p in bottoms)
    for p, info in zip(bottoms, H.inspect(corpus_dir / "rotate_mixed.pdf").pages):
        assert info.height - p.box[3] == pytest.approx(18)
        assert (p.box[0] + p.box[2]) / 2 == pytest.approx(info.width / 2)


def test_overlapping_stamps_refused(corpus_dir: Path, tmp_path: Path) -> None:
    cfg = default_config()
    stamps = [StampSpec(text="{identifier}"), StampSpec(text="Page {page} of {total}")]
    job = StampJob.from_profile(cfg, cfg.default_profile, DEFAULT_VALUES, stamps=stamps)
    with pytest.raises(StampDoesNotFit, match="stamps 1 and 2 overlap"):
        H.apply(corpus_dir / "letter_portrait.pdf", job, tmp_path / "out.pdf")


def test_empty_stamp_is_skipped(corpus_dir: Path, tmp_path: Path) -> None:
    cfg = default_config()
    stamps = [StampSpec(text="{doc_title}"), StampSpec(text="{identifier}", anchor="bottom-right")]
    job = StampJob.from_profile(cfg, cfg.default_profile, DEFAULT_VALUES, stamps=stamps)
    r = H.apply(corpus_dir / "letter_portrait.pdf", job, tmp_path / "out.pdf")
    assert {p.stamp for p in r.placements} == {2}


# ------------------------------------------------------------------ forms, signatures, XFA, damage


def test_acroform_values_unchanged_and_overlap_warned(corpus_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    r = H.apply(corpus_dir / "acroform_filled.pdf", default_job(), out)
    with pymupdf.open(out) as d:
        values = {w.field_name: w.field_value for w in d[0].widgets()}
    for name, value in make_corpus.FORM_VALUES.items():
        assert values[name] == value
    assert values["corner_field"] == "Corner value"
    assert any("form field or annotation" in w for w in r.warnings)


def test_signed_needs_confirmation(signed_pdf: Path, tmp_path: Path) -> None:
    assert H.inspect(signed_pdf).signed
    out = tmp_path / "out.pdf"
    with pytest.raises(ConfirmationRequired) as e:
        H.apply(signed_pdf, default_job(), out)
    assert e.value.reason == "signature" and not out.exists()
    before = sha256(signed_pdf)
    r = H.apply(signed_pdf, default_job(), out, accept_signature_loss=True)
    assert any("signature" in w for w in r.warnings)
    assert sha256(signed_pdf) == before


def test_xfa_refused(xfa_pdf: Path, tmp_path: Path) -> None:
    assert H.inspect(xfa_pdf).xfa
    with pytest.raises(UnsupportedDocument, match="XFA"):
        H.apply(xfa_pdf, default_job(), tmp_path / "out.pdf")


def test_damaged_file_repaired_with_warning(damaged_pdf: Path, tmp_path: Path) -> None:
    assert H.inspect(damaged_pdf).repaired
    r = H.apply(damaged_pdf, default_job(), tmp_path / "out.pdf")
    assert any("repaired" in w for w in r.warnings)
    assert r.pages_stamped == 3


@pytest.mark.parametrize(
    ("filename", "check"),
    [("signed.pdf", "signed"), ("xfa_form.pdf", "xfa"), ("damaged_xref.pdf", "repaired")],
)
def test_manual_samples(filename: str, check: str, tmp_path: Path) -> None:
    path = MANUAL_DIR / filename
    if not path.is_file():
        pytest.skip(f"hand-made sample tests/corpus/manual/{filename} isn't present (see tests/corpus/README.md)")
    info = H.inspect(path)
    assert getattr(info, check)
    out = tmp_path / "out.pdf"
    if check == "xfa":
        with pytest.raises(UnsupportedDocument, match="XFA"):
            H.apply(path, default_job(), out)
    else:
        before = sha256(path)
        H.apply(path, default_job(), out, accept_signature_loss=True)
        assert sha256(path) == before and out.is_file()


# ------------------------------------------------------------------ encryption (D-050)


def test_user_password_kept(corpus_dir: Path, tmp_path: Path) -> None:
    src = corpus_dir / "encrypted_user_password.pdf"
    out = tmp_path / "out.pdf"
    r = H.apply(src, default_job(), out, password=make_corpus.USER_PASSWORD)
    assert r.encryption_kept
    with pymupdf.open(src) as a, pymupdf.open(out) as b:
        assert b.needs_pass
        assert not b.authenticate("wrong")
        a.authenticate(make_corpus.USER_PASSWORD)
        enc_a = a.metadata["encryption"]
        perm_a = a.permissions
        assert b.authenticate(make_corpus.USER_PASSWORD)
        assert b.metadata["encryption"] == enc_a
        assert b.permissions == perm_a
        # Content streams decrypt cleanly (guards the needs_pass bug, D-056).
        for page in b:
            text = page.get_text()
            assert "Password to open" in text and FAKE_ID in text
    with pymupdf.open(out) as b:
        assert b.authenticate(make_corpus.OWNER_PASSWORD)


def test_owner_restrictions_need_confirmation_and_are_kept(corpus_dir: Path, tmp_path: Path) -> None:
    src = corpus_dir / "owner_restrictions.pdf"
    out = tmp_path / "out.pdf"
    with pytest.raises(ConfirmationRequired) as e:
        H.apply(src, default_job(), out)
    assert e.value.reason == "restrictions" and not out.exists()
    r = H.apply(src, default_job(), out, accept_restrictions=True)
    assert r.encryption_kept
    with pymupdf.open(src) as a, pymupdf.open(out) as b:
        assert not b.needs_pass
        assert b.metadata["encryption"] == a.metadata["encryption"]
        assert b.permissions == a.permissions
        assert not b.permissions & pymupdf.PDF_PERM_MODIFY
        assert FAKE_ID in b[0].get_text()


def test_remove_encryption_is_explicit(corpus_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    r = H.apply(corpus_dir / "encrypted_user_password.pdf", default_job(), out,
                password=make_corpus.USER_PASSWORD, remove_encryption=True)
    assert not r.encryption_kept
    assert any("isn't password-protected" in w for w in r.warnings)
    with pymupdf.open(out) as d:
        assert not d.needs_pass and d.metadata["encryption"] is None


def test_password_errors(corpus_dir: Path, tmp_path: Path) -> None:
    src = corpus_dir / "encrypted_user_password.pdf"
    with pytest.raises(PasswordRequired):
        H.apply(src, default_job(), tmp_path / "out.pdf")
    with pytest.raises(WrongPassword, match="doesn't open"):
        H.apply(src, default_job(), tmp_path / "out.pdf", password="not-it")
    assert not (tmp_path / "out.pdf").exists()


def test_owner_password_opens_without_restriction_prompt(corpus_dir: Path, tmp_path: Path) -> None:
    r = H.apply(corpus_dir / "owner_restrictions.pdf", default_job(), tmp_path / "out.pdf",
                password=make_corpus.OWNER_PASSWORD)
    assert r.encryption_kept
    assert not any("restrictions" in w for w in r.warnings)
    with pymupdf.open(tmp_path / "out.pdf") as d:
        assert not d.permissions & pymupdf.PDF_PERM_MODIFY  # copy keeps the restrictions
        assert FAKE_ID in d[0].get_text()
    with pytest.raises(WrongPassword):
        H.apply(corpus_dir / "owner_restrictions.pdf", default_job(), tmp_path / "out2.pdf", password="nope")


# ------------------------------------------------------------------ metadata (D-051)


@pytest.fixture
def with_info(corpus_dir: Path, tmp_path: Path) -> Path:
    """letter_portrait.pdf with Info entries and XMP, as a real scanner might write."""
    path = tmp_path / "with_info.pdf"
    with pymupdf.open(corpus_dir / "letter_portrait.pdf") as d:
        d.set_metadata({"title": "Sample title", "producer": "Sample Producer 1.0",
                        "creationDate": "D:20250101120000Z"})
        _, val = d.xref_get_key(-1, "Info")
        x = int(val.split()[0])
        d.xref_set_key(x, "CaseNumber", pymupdf.get_pdf_str("00-0000-000000"))
        d.xref_set_key(x, "Department", pymupdf.get_pdf_str("Sample"))
        d.set_xml_metadata("<x:xmpmeta xmlns:x='adobe:ns:meta/'><sample/></x:xmpmeta>")
        d.save(path)
    return path


def test_metadata_written_into_info(corpus_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    r = H.apply(corpus_dir / "letter_portrait.pdf", records_job(), out)
    assert r.metadata_written == {"CaseNumber": FAKE_ID}
    with pymupdf.open(out) as d:
        info = _info_dict(d)
        assert info["CaseNumber"] == FAKE_ID
        assert info["ModDate"].startswith("D:")
        assert "Producer" not in info


def test_metadata_existing_kept_and_conflict_warned(with_info: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    with pymupdf.open(with_info) as d:
        before = _info_dict(d)
        xmp_before = d.get_xml_metadata()
    r = H.apply(with_info, records_job(), out)
    assert r.metadata_replaced == {"CaseNumber": ("00-0000-000000", FAKE_ID)}
    assert any('was "00-0000-000000"' in w for w in r.warnings)
    assert any("XMP" in w for w in r.warnings)
    with pymupdf.open(out) as d:
        after = _info_dict(d)
        assert d.get_xml_metadata() == xmp_before  # XMP untouched
    for key in ("Title", "Producer", "CreationDate", "Department"):
        assert after[key] == before[key]
    assert after["CaseNumber"] == FAKE_ID
    assert after["ModDate"] != before.get("ModDate")


def test_metadata_with_encryption(corpus_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    H.apply(corpus_dir / "encrypted_user_password.pdf", records_job(), out, password=make_corpus.USER_PASSWORD)
    with pymupdf.open(out) as d:
        d.authenticate(make_corpus.USER_PASSWORD)
        assert _info_dict(d)["CaseNumber"] == FAKE_ID


def test_empty_metadata_value_not_written(corpus_dir: Path, tmp_path: Path) -> None:
    cfg = default_config()
    job = StampJob(stamps=cfg.default_profile.stamps, values=DEFAULT_VALUES,
                   metadata={"Title": "{doc_title}", "Identifier": "{identifier}"})
    r = H.apply(corpus_dir / "letter_portrait.pdf", job, tmp_path / "out.pdf")
    assert r.metadata_written == {"Identifier": FAKE_ID}


# ------------------------------------------------------------------ preview


def test_preview_png_with_outline(corpus_dir: Path) -> None:
    src = corpus_dir / "rotate_90.pdf"
    before = sha256(src)
    png = H.render_preview(src, 2, default_job(), dpi=72)
    assert png.startswith(b"\x89PNG")
    pix = pymupdf.Pixmap(png)
    assert (pix.width, pix.height) == (792, 612)

    def amber(r: int, g: int, b: int) -> bool:
        return r > 180 and 100 < g < 180 and b < 80

    outline = pixel_bbox(pix, amber)
    ink = pixel_bbox(pix)
    assert outline is not None and ink is not None
    assert outline.x0 <= ink.x0 and outline.x1 >= ink.x1  # outline surrounds the stamp
    assert sha256(src) == before


def test_preview_page_out_of_range(corpus_dir: Path) -> None:
    with pytest.raises(ValueError):
        H.render_preview(corpus_dir / "letter_portrait.pdf", 9, default_job())
