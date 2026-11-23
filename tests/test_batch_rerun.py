"""Re-running a batch, the results screen's Show in folder (UX-034, UX-031).

Synthetic data only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

from stamper.cli import ExitCode
from stamper.gui.main_window import MainWindow
from stamper.gui.multi import BatchDialog
from stamper.handlers.base import ApplyResult
from stamper.handlers.batch import BatchCheck, BatchItem, BatchOutcome, Status, check_batch
from stamper.handlers.pdf import PdfHandler
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
from tests.test_batch import BASE, Run
from tests.test_gui_multi import CASE, open_several, wait_dialog

# ------------------------------------------------------------ the check knows "already there" (UX-034)


def test_check_marks_only_an_existing_output(corpus_dir: Path, tmp_path: Path) -> None:
    a = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    b = copy_to(corpus_dir / "rotate_90.pdf", tmp_path)
    tiny = make_tiny(tmp_path / "tiny.pdf")
    (tmp_path / "a_out.pdf").write_bytes(b"already here")
    clash = tmp_path / "same.pdf"
    checks = check_batch(PdfHandler(), [
        BatchItem(a, tmp_path / "a_out.pdf"),  # exists
        BatchItem(b, clash),  # passes
        BatchItem(tiny, clash),  # same name as b in this batch: can't be stamped
        BatchItem(tiny, tmp_path / "missing" / "x.pdf"),  # no such folder
    ], default_job())
    assert [c.status for c in checks] == [Status.SKIPPED, Status.OK, Status.SKIPPED, Status.FAILED]
    assert [c.output_exists for c in checks] == [True, False, False, False]


def test_cli_lines_and_exit_codes_unchanged(corpus_dir: Path, tmp_path: Path) -> None:
    a = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    b = copy_to(corpus_dir / "rotate_90.pdf", tmp_path)
    outdir = tmp_path / "out"
    outdir.mkdir()
    assert Run(["--batch", str(a), str(b), "--out-dir", str(outdir), *BASE]).code == ExitCode.OK
    first = sorted(outdir.iterdir())
    (first[0]).unlink()  # one copy gone: re-run saves it and skips the other
    partial = Run(["--batch", str(a), str(b), "--out-dir", str(outdir), *BASE])
    assert partial.code == ExitCode.PARTIAL
    skipped = [line for line in partial.stderr.splitlines() if line.startswith("Skipped: ")]
    assert len(skipped) == 1 and skipped[0].endswith("already exists, so it wasn't replaced.")
    again = Run(["--batch", str(a), str(b), "--out-dir", str(outdir), *BASE])
    assert again.code == ExitCode.OUTPUT_REFUSED
    assert again.stderr.count("Skipped: ") == 2 and "0 of 2 files saved." in again.stdout


# ------------------------------------------------------------ the pre-run question (UX-034)


@pytest.fixture
def dialog(qtbot: QtBot, window: MainWindow, corpus_dir: Path, tmp_path: Path,
           monkeypatch: pytest.MonkeyPatch) -> BatchDialog:
    """A batch dialog for four files whose run is replaced, so only the question is exercised."""
    files = [copy_to(corpus_dir / "letter_portrait.pdf", tmp_path, f"f{n}.pdf") for n in range(4)]
    open_several(qtbot, window, files)
    window.files_screen.batch_button.click()
    d = wait_dialog(qtbot, window, BatchDialog)
    assert isinstance(d, BatchDialog)
    choose(d, "doc_type", "Court order")
    type_into(qtbot, d, "identifier", CASE)
    started: list[list[BatchCheck]] = []
    monkeypatch.setattr(d, "_start_run", started.append)
    d.started = started  # type: ignore[attr-defined]
    return d


def _checks(d: BatchDialog, kinds: str) -> list[BatchCheck]:
    """One check per file: o = ok, e = output exists, c = in-batch name clash, f = failed."""
    out: list[BatchCheck] = []
    for i, k in enumerate(kinds):
        item = BatchItem(Path(f"/x/f{i}.pdf"), Path(f"/out/f{i}_stamped.pdf"))
        if k == "o":
            out.append(BatchCheck(item, Status.OK))
        elif k == "e":
            out.append(BatchCheck(item, Status.SKIPPED, f"f{i}_stamped.pdf already exists, so it wasn't replaced.",
                                  output_exists=True))
        elif k == "c":
            out.append(BatchCheck(item, Status.SKIPPED, "f0.pdf in this batch is saved to the same name, x.pdf."))
        else:
            out.append(BatchCheck(item, Status.FAILED, "The stamp doesn't fit."))
    return out


def test_all_not_saved_are_already_there(dialog: BatchDialog, fake: FakeServices) -> None:
    fake.confirms.append(True)
    dialog._after_check(_checks(dialog, "eoeo"))
    title, text = fake.questions[-1]
    assert title == "Some files are already stamped"
    assert text.startswith("2 already have a stamped copy in the output folder and will be left as they are:")
    assert "• f0.pdf\n• f2.pdf" in text and "Can't be stamped" not in text
    assert text.endswith("Stamp the remaining 2?")
    assert len(dialog.started) == 1  # type: ignore[attr-defined]


def test_declining_already_there_saves_nothing(dialog: BatchDialog, fake: FakeServices) -> None:
    fake.confirms.append(False)
    dialog._after_check(_checks(dialog, "eooo"))
    assert fake.questions[-1][1].startswith("1 already has a stamped copy")
    assert dialog.started == [] and dialog.save_message() == "Nothing was saved."  # type: ignore[attr-defined]


def test_mixed_shows_two_groups(dialog: BatchDialog, fake: FakeServices) -> None:
    fake.confirms.append(True)
    dialog._after_check(_checks(dialog, "efco"))
    title, text = fake.questions[-1]
    assert title == "Some files can't be stamped"
    assert text.startswith("3 of 4 files won't be saved:\nAlready there:\n• f0.pdf\n\nCan't be stamped:\n"
                           "• f1.pdf: The stamp doesn't fit.\n• f2.pdf: f0.pdf in this batch is saved to the same name")
    assert text.endswith("Stamp the other 1?")


def test_an_in_batch_clash_stays_cant_be_stamped(dialog: BatchDialog, fake: FakeServices) -> None:
    fake.confirms.append(True)
    dialog._after_check(_checks(dialog, "oocc"))
    title, text = fake.questions[-1]
    assert title == "Some files can't be stamped" and "Already there" not in text
    assert dialog.file_list.item(2).text().startswith("! ")


def test_none_not_saved_asks_nothing(dialog: BatchDialog, fake: FakeServices) -> None:
    dialog._after_check(_checks(dialog, "oooo"))
    assert fake.questions == [] and len(dialog.started) == 1  # type: ignore[attr-defined]


def test_every_file_already_there(dialog: BatchDialog, fake: FakeServices) -> None:
    dialog._after_check(_checks(dialog, "eeee"))
    assert fake.questions == [] and dialog.started == []  # type: ignore[attr-defined]
    assert dialog.save_message() == "All 4 already have a stamped copy in the output folder, so nothing was saved."
    assert "None of the files" not in dialog.save_message()


def test_refusal_shows_names_and_messages_through_visible(dialog: BatchDialog, fake: FakeServices) -> None:
    checks = _checks(dialog, "fefe")
    checks[0] = BatchCheck(BatchItem(Path("/x/a\u202ecod.pdf"), Path("/o/x.pdf")), Status.FAILED,
                           "Bad <b>name</b>\u202e here.")
    dialog._after_check(checks)
    message = dialog.save_message()
    assert "a⟨U+202E⟩cod.pdf: Bad <b>name</b>⟨U+202E⟩ here." in message
    assert "Already there:" in message and "Can't be stamped:" in message
    from PySide6.QtCore import Qt
    assert dialog.save_error.textFormat() == Qt.TextFormat.PlainText


# ------------------------------------------------------------ results screen: Show in folder (UX-031)


def _saved(name: str, out: Path) -> BatchOutcome:
    result = ApplyResult(output_path=out, pages_stamped=1, warnings=(), placements=(),
                         metadata_written={}, metadata_replaced={}, encryption_kept=False)
    return BatchOutcome(BatchItem(Path(f"/x/{name}"), out), Status.SAVED, result=result)


def _row_buttons(window: MainWindow, text: str) -> list[QPushButton]:
    return [b for b in window.batch_saved.rows_widget.findChildren(QPushButton) if b.text() == text]


def test_one_folder_keeps_one_button(qtbot: QtBot, window: MainWindow, fake: FakeServices, tmp_path: Path) -> None:
    screen = window.batch_saved
    screen.show_outcomes([_saved("a.pdf", tmp_path / "a_s.pdf"), _saved("b.pdf", tmp_path / "b_s.pdf"),
                          BatchOutcome(BatchItem(Path("/elsewhere/c.pdf"), Path("/elsewhere/c_s.pdf")),
                                       Status.FAILED, "No.")])
    assert screen.folder_button.text() == "Show in folder"
    assert screen.folder_button.isEnabled() and screen.folder_button.toolTip() == ""
    assert _row_buttons(window, "Show in folder") == []
    screen.folder_button.click()
    assert fake.shown == [tmp_path / "a_s.pdf"]


def test_several_folders_give_each_row_a_button(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                                tmp_path: Path) -> None:
    screen = window.batch_saved
    screen.show_outcomes([_saved("a.pdf", tmp_path / "one" / "a_s.pdf"),
                          _saved("b&\u202ex.pdf", tmp_path / "two" / "b_s.pdf"),
                          BatchOutcome(BatchItem(Path("/x/c.pdf"), Path("/x/c_s.pdf")), Status.SKIPPED, "There.")])
    assert screen.folder_button.text() == "Show in folder"
    assert not screen.folder_button.isEnabled()
    assert "several folders" in screen.folder_button.toolTip()
    buttons = screen.folder_buttons
    assert [b.accessibleName() for b in buttons] == [
        "Show the copy of a.pdf in its folder", "Show the copy of b&⟨U+202E⟩x.pdf in its folder"]
    buttons[1].click()
    assert fake.shown == [tmp_path / "two" / "b_s.pdf"]
    # beside Open, on the same row; the notes span still covers every column
    grid = screen.grid
    open_pos = grid.getItemPosition(grid.indexOf(screen.open_buttons[1]))
    show_pos = grid.getItemPosition(grid.indexOf(buttons[1]))
    assert show_pos[0] == open_pos[0] and show_pos[1] == open_pos[1] + 1


def test_several_folders_with_notes_keep_the_full_span(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    from tests.test_saved_notes import SHRINK

    screen = window.batch_saved
    noted = _saved("a.pdf", tmp_path / "one" / "a_s.pdf")
    assert noted.result is not None
    noted = BatchOutcome(noted.item, noted.status, result=ApplyResult(
        output_path=noted.result.output_path, pages_stamped=1, warnings=(SHRINK[0],), placements=(),
        metadata_written={}, metadata_replaced={}, encryption_kept=False))
    screen.show_outcomes([noted, _saved("b.pdf", tmp_path / "two" / "b_s.pdf")])
    grid = screen.grid
    view = screen.notes_views[0]
    assert view is not None
    _r, col, _rs, span = grid.getItemPosition(grid.indexOf(view))
    assert col == 0 and span == grid.columnCount()
    toggle = grid.getItemPosition(grid.indexOf(screen.notes_buttons[0]))
    assert toggle[1] == grid.columnCount() - 1


def test_real_batch_into_two_folders(qtbot: QtBot, window: MainWindow, fake: FakeServices, corpus_dir: Path,
                                     tmp_path: Path) -> None:
    """Next to each original, from two folders: the batch button is disabled and each row has its own."""
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    files = [copy_to(corpus_dir / "letter_portrait.pdf", tmp_path / "one"),
             copy_to(corpus_dir / "rotate_90.pdf", tmp_path / "two")]
    open_several(qtbot, window, files)
    window.files_screen.batch_button.click()
    d = wait_dialog(qtbot, window, BatchDialog)
    choose(d, "doc_type", "Court order")
    type_into(qtbot, d, "identifier", CASE)
    qtbot.waitUntil(d.ready, timeout=WAIT_MS)
    d.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.batch_saved, timeout=WAIT_MS)
    screen = window.batch_saved
    qtbot.waitUntil(lambda: all(b.isVisible() for b in screen.folder_buttons) and len(screen.folder_buttons) == 2,
                    timeout=WAIT_MS)
    assert not screen.folder_button.isEnabled()
