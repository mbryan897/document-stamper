"""Re-running a batch (UX-034).

Synthetic data only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from stamper.cli import ExitCode
from stamper.gui.main_window import MainWindow
from stamper.gui.multi import BatchDialog
from stamper.handlers.batch import BatchCheck, BatchItem, Status, check_batch
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
    checks[0] = BatchCheck(BatchItem(Path("/x/a‮cod.pdf"), Path("/o/x.pdf")), Status.FAILED,
                           "Bad <b>name</b>‮ here.")
    dialog._after_check(checks)
    message = dialog.save_message()
    assert "a⟨U+202E⟩cod.pdf: Bad <b>name</b>⟨U+202E⟩ here." in message
    assert "Already there:" in message and "Can't be stamped:" in message
    from PySide6.QtCore import Qt
    assert dialog.save_error.textFormat() == Qt.TextFormat.PlainText
