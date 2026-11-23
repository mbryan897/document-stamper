"""Handler.plan: the whole-document check before saving (D-074)."""

from __future__ import annotations

import dataclasses
import io
import time
from datetime import date
from pathlib import Path

import pymupdf
import pytest

from stamper.cli import ExitCode, main
from stamper.handlers import pdf
from stamper.handlers.base import Cancelled, StampDoesNotFit, StampJob, UnsupportedDocument
from stamper.handlers.batch import BatchItem, check_batch, run_batch
from stamper.handlers.combine import CombineInput, combine_pdfs
from stamper.handlers.pdf import COVERS_TEXT_NOTE_END, PROPERTIES_NOTE_PREFIX, PdfHandler
from tests.gui_support import copy_to
from tests.stamping import (
    FAKE_ID,
    default_job,
    make_xfa_pdf,
    password_for,
    records_config,
    sha256,
)
from tools import make_corpus

H = PdfHandler()
CONFIRMATION_NOTES = ("digitally signed", "owner restrictions")


@pytest.mark.parametrize("name", list(make_corpus.CORPUS))
def test_plan_matches_apply(name: str, corpus_dir: Path, tmp_path: Path) -> None:
    src = corpus_dir / name
    pw = password_for(name)
    before = sha256(src)
    plan = H.plan(src, default_job(), password=pw)
    assert sha256(src) == before
    result = H.apply(src, default_job(), tmp_path / name, password=pw, accept_restrictions=True)
    assert plan.page_count == result.pages_stamped
    assert plan.placements == result.placements
    applied = tuple(w for w in result.warnings if not any(n in w for n in CONFIRMATION_NOTES))
    # The properties note names values, and the copy's "Last changed" is only known once saved (UX-046).
    assert [w for w in plan.warnings if not w.startswith(PROPERTIES_NOTE_PREFIX)] == \
        [w for w in applied if not w.startswith(PROPERTIES_NOTE_PREFIX)]
    assert sum(w.startswith(PROPERTIES_NOTE_PREFIX) for w in plan.warnings) == 1
    assert sum(w.startswith(PROPERTIES_NOTE_PREFIX) for w in applied) == 1
    assert plan.metadata_written == result.metadata_written
    assert plan.metadata_replaced == result.metadata_replaced


def test_plan_reports_form_field_overlap(corpus_dir: Path) -> None:
    plan = H.plan(corpus_dir / "acroform_filled.pdf", default_job())
    assert any("overlaps a form field" in w for w in plan.warnings)


def test_plan_finds_a_page_that_doesnt_fit(corpus_dir: Path) -> None:
    cfg = records_config()
    job = StampJob.from_profile(cfg, cfg.default_profile,
                                {"identifier": "99-2026-999901", "doc_type": "Search warrant return"})
    with pytest.raises(StampDoesNotFit) as e:
        H.plan(corpus_dir / "mixed_sizes.pdf", job)
    assert e.value.page == 4  # the receipt, not the page being previewed
    assert "Page 4" in str(e.value)


def test_plan_refuses_xfa(corpus_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(UnsupportedDocument, match="XFA"):
        H.plan(make_xfa_pdf(corpus_dir, tmp_path / "x.pdf"), default_job())


def test_plan_can_be_cancelled(corpus_dir: Path) -> None:
    calls = []

    def cancel() -> bool:
        calls.append(1)
        return len(calls) > 3

    with pytest.raises(Cancelled):
        H.plan(corpus_dir / "large_520_pages.pdf", default_job(), cancel=cancel)
    assert len(calls) == 4


def test_plan_is_quick_on_520_pages(corpus_dir: Path) -> None:
    start = time.perf_counter()
    plan = H.plan(corpus_dir / "large_520_pages.pdf", default_job())
    elapsed = time.perf_counter() - start
    assert plan.page_count == make_corpus.LARGE_PAGE_COUNT
    assert elapsed < 2.0, f"plan took {elapsed:.2f} s"


# ------------------------------------------------------------ the white box covers existing text (UX-004)


def _note(pages: str) -> str:
    return f"On {pages}{COVERS_TEXT_NOTE_END}"


def _covers(warnings: tuple[str, ...]) -> list[str]:
    return [w for w in warnings if w.endswith(COVERS_TEXT_NOTE_END)]


def _letter(rotation: int, crop: bool) -> tuple[pymupdf.Document, pymupdf.Page]:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    if crop:
        page.set_cropbox(pymupdf.Rect(36, 54, 540, 720))  # smaller than, and offset from, the MediaBox
    page.set_rotation(rotation)
    return doc, page


def _stamp_box(rotation: int, crop: bool, tmp_path: Path) -> pymupdf.Rect:
    """Where the default stamp's box goes, in visible coordinates, on a blank page like this."""
    doc, _ = _letter(rotation, crop)
    blank = tmp_path / f"blank_{rotation}_{crop}.pdf"
    doc.save(blank)
    return pymupdf.Rect(H.plan(blank, default_job()).placements[0].box)


def _with_word(tmp_path: Path, rotation: int, crop: bool, where: str) -> Path:
    """A one-page file with the word XX ``inside`` the stamp's box, just ``left`` of it or just ``below`` it.

    The word is placed in visible coordinates through PyMuPDF's own derotation matrix, and its
    position is checked the same way, so this doesn't lean on core/geometry.py.
    """
    box = _stamp_box(rotation, crop, tmp_path)
    target = {
        "inside": pymupdf.Rect(box.x0 + 4, box.y0 + 2, box.x1 - 4, box.y1 - 2),
        "left": pymupdf.Rect(box.x0 - 40, box.y0, box.x0 - 1, box.y1),
        "below": pymupdf.Rect(box.x0, box.y1 + 1, box.x1, box.y1 + 16),
    }[where]
    doc, page = _letter(rotation, crop)
    assert page.insert_textbox(target * page.derotation_matrix, "XX", fontsize=6, rotate=rotation) >= 0
    (word,) = page.get_text("words")
    seen = pymupdf.Rect(word[:4]) * page.rotation_matrix
    assert seen.intersects(box) == (where == "inside"), (seen, box)
    out = tmp_path / f"{where}_{rotation}_{crop}.pdf"
    doc.save(out)
    return out


@pytest.mark.parametrize("crop", [False, True], ids=["mediabox", "cropbox_offset"])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_text_under_the_box_warns_on_every_rotation(rotation: int, crop: bool, tmp_path: Path) -> None:
    plan = H.plan(_with_word(tmp_path, rotation, crop, "inside"), default_job())
    assert _covers(plan.warnings) == [_note("page 1")]
    assert "XX" not in " ".join(plan.warnings)  # page numbers only, never the covered text


@pytest.mark.parametrize("crop", [False, True], ids=["mediabox", "cropbox_offset"])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_text_just_outside_the_box_doesnt_warn(rotation: int, crop: bool, tmp_path: Path) -> None:
    for where in ("left", "below"):
        assert _covers(H.plan(_with_word(tmp_path, rotation, crop, where), default_job()).warnings) == []
    assert _covers(H.plan(_with_word(tmp_path, rotation, crop, "inside"), default_job()).warnings)  # the edge


def test_text_top_right_warns_and_names_the_page(corpus_dir: Path, tmp_path: Path) -> None:
    src = corpus_dir / "text_top_right.pdf"
    plan = H.plan(src, default_job())
    result = H.apply(src, default_job(), tmp_path / "out.pdf")
    assert _covers(plan.warnings) == _covers(result.warnings) == [_note("page 1")]
    assert pdf.needs_check(_note("page 1"))


def test_box_off_or_stamp_moved_doesnt_warn(corpus_dir: Path, tmp_path: Path) -> None:
    src = corpus_dir / "text_top_right.pdf"
    assert _covers(H.plan(src, default_job()).warnings)
    assert _covers(H.plan(src, default_job(background_box=False)).warnings) == []
    job = default_job()
    moved = dataclasses.replace(job, stamps=tuple(s.model_copy(update={"anchor": "bottom-left"}) for s in job.stamps))
    assert _covers(H.plan(src, moved).warnings) == []
    assert _covers(H.apply(src, moved, tmp_path / "moved.pdf").warnings) == []


def test_one_note_lists_every_page(tmp_path: Path) -> None:
    doc = pymupdf.open()
    for i in range(9):
        page = doc.new_page(width=612, height=792)
        if i in (2, 6):  # pages 3 and 7: words in the top-right corner
            page.insert_textbox(pymupdf.Rect(400, 10, 600, 50), "covered words here " * 4, fontsize=8)
    src = tmp_path / "nine.pdf"
    doc.save(src)
    plan = H.plan(src, default_job())
    assert _covers(plan.warnings) == [
        "On pages 3, 7 the white box covers text that was already on the page. "
        "The covered text is still in the file: the box is not redaction. "
        "Choose another position under Placement, or turn off the white box."]
    assert _covers(H.apply(src, default_job(), tmp_path / "out.pdf").warnings) == _covers(plan.warnings)


def test_whitespace_only_doesnt_count(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(pymupdf.Rect(300, 10, 600, 50), "       ", fontsize=8)
    src = tmp_path / "spaces.pdf"
    doc.save(src)
    assert _covers(H.plan(src, default_job()).warnings) == []


def test_scanned_page_isnt_detected(corpus_dir: Path) -> None:
    # No text layer, so nothing to find: the documented limit (help and Decisions).
    assert _covers(H.plan(corpus_dir / "scanned_image_only.pdf", default_job()).warnings) == []


@pytest.mark.parametrize("name", ["text_top_right.pdf", "rotate_mixed.pdf", "cropbox_offset.pdf",
                                  "mixed_sizes.pdf", "acroform_filled.pdf"])
def test_what_is_drawn_is_unchanged(name: str, corpus_dir: Path, tmp_path: Path) -> None:
    """The check only reads: each page's content equals drawing the plan without it."""
    src = corpus_dir / name
    job = default_job()
    out = tmp_path / name
    H.apply(src, job, out, accept_restrictions=True)
    ref = pymupdf.open(src)
    for page in ref:
        values = H._page_values(job, page.number + 1, ref.page_count, src.stem, job.today or date.today())
        H._draw_page(page, H._plan_page(page, job, values, []))
    with pymupdf.open(out) as copy:
        assert [p.read_contents() for p in copy] == [p.read_contents() for p in ref]
    ref.close()


# Where the note shows: it comes from the handler, so every caller has it with no code of its own.


def test_note_reaches_the_command_line(corpus_dir: Path, tmp_path: Path) -> None:
    out, err = io.StringIO(), io.StringIO()
    code = main([str(corpus_dir / "text_top_right.pdf"), "--set", f"identifier={FAKE_ID}",
                 "--out", str(tmp_path / "out.pdf")], stdout=out, stderr=err, stdin=io.StringIO(""))
    assert code == ExitCode.OK, err.getvalue()
    assert _note("page 1") in out.getvalue()


def test_note_reaches_combine_with_combined_page_numbers(corpus_dir: Path, tmp_path: Path) -> None:
    inputs = [CombineInput(copy_to(corpus_dir / n, tmp_path)) for n in ("letter_portrait.pdf", "text_top_right.pdf")]
    combined = combine_pdfs(inputs, tmp_path)
    try:
        plan = H.plan(combined.path, default_job())
        result = H.apply(combined.path, default_job(), tmp_path / "combined.pdf")
    finally:
        combined.cleanup()
    assert _covers(plan.warnings) == _covers(result.warnings) == [_note("page 4")]


def test_note_reaches_batch_check_and_rows(corpus_dir: Path, tmp_path: Path) -> None:
    items = [BatchItem(corpus_dir / n, tmp_path / n) for n in ("letter_portrait.pdf", "text_top_right.pdf")]
    checks = check_batch(H, items, default_job())
    assert [_covers(c.plan.warnings) for c in checks] == [[], [_note("page 1")]]
    outcomes = run_batch(H, checks, default_job())
    assert _covers(outcomes[1].result.warnings) == [_note("page 1")]


def test_sort_notes_puts_it_under_check_the_copy() -> None:
    from stamper.gui.stamp_dialog import sort_notes  # the saved screen's and batch rows' sorting
    check, other, _ = sort_notes([_note("pages 3, 7"), "Page 2, stamp 1: font reduced from 9 pt to 8 pt to fit."])
    assert check == [_note("pages 3, 7")] and len(other) == 1
