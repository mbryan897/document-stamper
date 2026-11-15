"""What the sender is told after saving (UX-005, UX-019, UX-045, UX-046).

Notes on the saved screen and the batch results, the properties note and the original's path.

Synthetic data only.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from stamper.gui.main_window import MINIMUM_SIZE, MainWindow
from stamper.gui.multi import BatchDialog
from stamper.handlers.base import ApplyResult
from stamper.handlers.batch import BatchItem, BatchOutcome, Status
from stamper.handlers import pdf
from stamper.handlers.pdf import PROPERTIES_NOTE_PREFIX, PdfHandler
from tests.gui_support import (  # noqa: F401 - fixtures
    WAIT_MS,
    FakeServices,
    choose,
    copy_to,
    fake,
    type_into,
    window,
)
from tests.multi_support import make_tiny
from tests.stamping import default_job
from tests.test_gui_multi import CASE, _clipped, _inside, open_several, wait_dialog

# Literal, not imported, so each test runs (and fails on its own assertion) against older code.
SIGNATURE_LOST_NOTE = "The original is digitally signed; the stamped copy's signature won't validate."
UNPROTECTED_NOTE = "The copy isn't password-protected, unlike the original."
COMBINED_UNPROTECTED_NOTE = "The combined file is saved without a password or restrictions."
MAX_PROPERTY_CHARS = 60
SHRINK = [f"Page {n}, stamp 1: font reduced from 9 pt to 7 pt to fit." for n in range(1, 9)]
REPAIRED = "receipts.pdf was damaged and was repaired when opened. Check the stamped copy carefully."
PROPS = f'{PROPERTIES_NOTE_PREFIX} Title: "Lab report", Last changed: "2026-11-14 14:02:07 -05:00". XMP metadata: none.'


def _result(warnings: list[str], out: str = "out.pdf") -> ApplyResult:
    return ApplyResult(output_path=Path(out), pages_stamped=8, warnings=tuple(warnings), placements=(),
                       metadata_written={}, metadata_replaced={}, encryption_kept=False)


def _shown(widget: QLabel | object) -> list[str]:
    """Texts of the visible, non-empty labels under ``widget``, top to bottom."""
    labels = [w for w in widget.findChildren(QLabel) if w.isVisible() and w.text()]  # type: ignore[attr-defined]
    return [w.text() for w in sorted(labels, key=lambda w: w.mapTo(widget, w.rect().topLeft()).y())]


# ------------------------------------------------------------ handler: which notes are "check the copy"


@pytest.mark.parametrize("note", [
    REPAIRED,
    SIGNATURE_LOST_NOTE,
    UNPROTECTED_NOTE,
    f"{COMBINED_UNPROTECTED_NOTE} Protected files in it: a.pdf.",
    'Document property Title was "Old" in the original; the copy has "New".',
])
def test_check_the_copy_notes(note: str) -> None:
    assert pdf.needs_check(note)


@pytest.mark.parametrize("note", [
    SHRINK[0], PROPS, "This file has 2,500 pages; checking and saving take longer.",
    "The original has XMP metadata, which wasn't updated; only the document properties were.",
])
def test_other_notes(note: str) -> None:  # passes only once needs_check exists
    assert not pdf.needs_check(note)


# ------------------------------------------------------------ handler: the properties note (UX-046)


def test_properties_note_names_values() -> None:
    note = pdf.properties_note({"Title": "Lab report", "Creator": "Writer", "CreationDate": "D:20260102100005+01'00'",
                            "ModDate": None, "Identifier": "SO26-0001"}, "", False)
    assert note.startswith(PROPERTIES_NOTE_PREFIX)
    assert 'Title: "Lab report"' in note and 'Made with: "Writer"' in note
    assert 'Created: "2026-01-02 10:00:05 +01:00"' in note and "Last changed: when saved" in note
    assert 'Custom properties: Identifier: "SO26-0001".' in note


def test_properties_note_value_is_text_and_cut() -> None:
    values = {"Title": "A" * 500, "Author": "<b>x</b>", "Subject": "line\nbreak\u202eevil"}
    note = pdf.properties_note(values, "", False)
    title = note.split('Title: "', 1)[1].split('"', 1)[0]
    assert len(title) <= MAX_PROPERTY_CHARS and title.endswith("…")
    assert 'Author: "<b>x</b>"' in note
    assert "\n" not in note and "⟨U+202E⟩" in note


def test_properties_note_caps_custom_properties() -> None:
    note = pdf.properties_note({f"Custom{i:02}": "v" for i in range(20)}, "", False)
    assert "Custom07" in note and "Custom08" not in note and "and 12 more." in note


def test_apply_reports_the_values_the_copy_carries(corpus_dir: Path, tmp_path: Path) -> None:
    src = tmp_path / "with_props.pdf"
    with pymupdf.open(corpus_dir / "letter_portrait.pdf") as doc:
        doc.set_metadata({"title": "T" * 500, "author": "<b>x</b>"})
        doc.save(src)
    result = PdfHandler().apply(src, default_job(), tmp_path / "out.pdf")
    note = next(w for w in result.warnings if w.startswith(PROPERTIES_NOTE_PREFIX))
    assert 'Author: "<b>x</b>"' in note
    assert "T" * (MAX_PROPERTY_CHARS - 1) + "…" in note and "T" * MAX_PROPERTY_CHARS not in note
    assert "Last changed: \"2" in note  # the date the copy was saved with


# ------------------------------------------------------------ saved screen (UX-019, UX-045)


@pytest.fixture
def small(window: MainWindow, qtbot: QtBot) -> MainWindow:
    window.resize(MINIMUM_SIZE)
    qtbot.waitUntil(lambda: window.size() == MINIMUM_SIZE)
    return window


def test_saved_screen_puts_check_the_copy_first(qtbot: QtBot, small: MainWindow) -> None:
    saved = small.saved
    saved.show_result(_result([*SHRINK, REPAIRED, PROPS]), label="Case number", identifier="X", signed=False,
                      source=Path("C:/cases/receipts.pdf"))
    small.stack.setCurrentWidget(saved)
    notes = _shown(saved.notes)
    assert notes[:2] == ["Check the copy", REPAIRED]
    assert notes[-1] == PROPS  # in full, and never counted against the limit
    assert notes.count(SHRINK[0]) == 1 and SHRINK[-1] not in notes
    assert "3 more notes." in notes
    assert _inside(small, saved.open_button) and not _clipped(saved)
    saved.notes.more_button.click()
    qtbot.wait(50)
    assert all(n in _shown(saved.notes) for n in SHRINK)
    assert _inside(small, saved.open_button) and not _clipped(saved)


def test_saved_screen_many_notes_still_fit(qtbot: QtBot, small: MainWindow) -> None:
    saved = small.saved
    long_props = pdf.properties_note({k: "v" * 300 for k in ("Title", "Author", "Subject", "Keywords", "Creator",
                                                          "Producer")} | {"ModDate": "D:20261114140207Z"}, "", True)
    many = [f"Page {n}, stamp 1: font reduced from 9 pt to 7 pt to fit. " * 3 for n in range(1, 60)]
    saved.show_result(_result([*many, REPAIRED, SIGNATURE_LOST_NOTE, long_props]), label="Case number",
                      identifier="X", signed=True, source=Path("C:/cases/" + "long_name_" * 12 + ".pdf"))
    small.stack.setCurrentWidget(saved)
    qtbot.wait(50)
    assert _inside(small, saved.open_button) and not _clipped(saved)
    saved.notes.more_button.click()
    qtbot.wait(50)
    assert _inside(small, saved.open_button) and not _clipped(saved)


def test_saved_screen_note_is_plain_text(qtbot: QtBot, window: MainWindow) -> None:
    note = 'Document property Title was "<b>x</b>" in the original; the copy has "New".'
    window.saved.show_result(_result([note, PROPS]), label="Case number", identifier="X", signed=False)
    labels = [w for w in window.saved.notes.findChildren(QLabel) if w.text()]
    assert any(note == w.text() for w in labels)
    assert all(w.textFormat() == Qt.TextFormat.PlainText for w in labels if "<" in w.text())


def test_saved_screen_names_the_original(qtbot: QtBot, window: MainWindow) -> None:
    src = Path("C:/cases/return<b>x</b>\u202e.pdf")
    saved = window.saved
    saved.show_result(_result([PROPS]), label="Case number", identifier="X", signed=True, source=src)
    label = saved.original_label
    shown = str(src).replace("‮", "⟨U+202E⟩")
    assert label.text() == f"{shown} – unchanged, still digitally signed"
    assert "<b>x</b>" in label.text()
    assert label.textFormat() == Qt.TextFormat.PlainText
    assert label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    assert saved.path_label.textFormat() == Qt.TextFormat.PlainText
    saved.show_result(_result([PROPS]), label="Case number", identifier="X", signed=False, source=src,
                      original_text="All 3 files unchanged")
    assert label.text() == "All 3 files unchanged"


# ------------------------------------------------------------ batch results (UX-005)


def test_batch_row_with_notes(qtbot: QtBot, window: MainWindow) -> None:
    screen = window.batch_saved
    outcomes = [
        BatchOutcome(BatchItem(Path("a.pdf"), Path("a_out.pdf")), Status.SAVED, result=_result([SHRINK[0], PROPS])),
        BatchOutcome(BatchItem(Path("b.pdf"), Path("b_out.pdf")), Status.SAVED, result=_result([PROPS])),
    ]
    screen.show_outcomes(outcomes)
    window.stack.setCurrentWidget(screen)
    texts = _shown(screen.rows_widget)
    assert "Saved, with notes (1)" in texts and texts.count("Saved") == 1
    assert SHRINK[0] not in texts
    assert len(screen.notes_buttons) == 1 and screen.notes_views[1] is None
    screen.notes_buttons[0].click()
    assert SHRINK[0] in _shown(screen.rows_widget) and PROPS in _shown(screen.rows_widget)
    assert screen.notes_buttons[0].text() == "Hide notes"
    screen.notes_buttons[0].click()
    assert SHRINK[0] not in _shown(screen.rows_widget)


def test_batch_check_lists_a_passing_files_note(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                                corpus_dir: Path, tmp_path: Path) -> None:
    files = [copy_to(corpus_dir / "mixed_sizes.pdf", tmp_path), copy_to(corpus_dir / "letter_portrait.pdf", tmp_path),
             make_tiny(tmp_path / "tiny.pdf")]
    open_several(qtbot, window, files)
    window.files_screen.batch_button.click()
    d = wait_dialog(qtbot, window, BatchDialog)
    choose(d, "doc_type", "Court order")
    type_into(qtbot, d, "identifier", CASE)
    d.show_file(0)
    qtbot.waitUntil(d.ready, timeout=WAIT_MS)
    fake.confirms.append(True)
    d.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.batch_saved, timeout=WAIT_MS)
    _title, text = fake.questions[-1]
    assert "tiny.pdf" in text
    assert "Notes for the files that will be saved:" in text
    assert "• mixed_sizes.pdf: Page 4, stamp 1: font reduced" in text
    assert PROPERTIES_NOTE_PREFIX not in text
    texts = _shown(window.batch_saved.rows_widget)
    assert "Saved, with notes (1)" in texts and "Saved" in texts
