"""Leaving a stamp screen with values entered (UX-006) and the window title (UX-008).

Synthetic files and values only.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox
from pytestqt.qtbot import QtBot

from stamper.gui.main_window import MAX_TITLE_NAME, MainWindow, _title_name
from stamper.gui.multi import BatchDialog, CombineDialog
from stamper.gui.stamp_dialog import StampDialog
from tests.gui_support import (  # noqa: F401 - fixtures
    WAIT_MS,
    FakeServices,
    choose,
    copy_to,
    fake,
    open_dialog,
    type_into,
    wait_ready,
    window,
)

CASE = "99-2026-999901"
DISCARD = "Discard what you've entered?"
APP = "Document Stamper"


def _shown_title(w: MainWindow) -> str:
    """The title as the title bar shows it (after Qt's own "[*]" handling)."""
    handle = w.windowHandle()
    assert handle is not None
    return handle.title()


def _asked(fake: FakeServices) -> list[str]:
    return [title for title, _ in fake.questions]


def _several(qtbot: QtBot, w: MainWindow, corpus_dir: Path, folder: Path, mode: str) -> StampDialog:
    files = [copy_to(corpus_dir / n, folder) for n in ("letter_portrait.pdf", "rotate_90.pdf", "mixed_sizes.pdf")]
    w.open_files(files)
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.files_screen, timeout=WAIT_MS)
    (w.files_screen.batch_button if mode == "batch" else w.files_screen.combine_button).click()
    kind = BatchDialog if mode == "batch" else CombineDialog
    qtbot.waitUntil(lambda: isinstance(w.dialog, kind) and w.stack.currentWidget() is w.dialog, timeout=WAIT_MS)
    assert w.dialog is not None
    return wait_ready(qtbot, w.dialog)


# ------------------------------------------------------------ UX-006: Esc and Cancel


def test_escape_with_no_edits_leaves_at_once(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                             corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    assert not d.is_dirty()
    qtbot.keyClick(d.editor("identifier"), Qt.Key.Key_Escape)
    assert window.stack.currentWidget() is window.drop
    assert fake.questions == []


def test_prefilled_values_are_not_edits(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                        corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path, f"return_{CASE}.pdf")
    d = open_dialog(qtbot, window, src)
    assert d.values()["identifier"] == CASE  # taken from the file name
    assert not d.is_dirty()
    d.cancel_button.click()
    assert window.stack.currentWidget() is window.drop and fake.questions == []


def test_escape_after_typing_asks_and_declining_keeps_values(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                                             corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    type_into(qtbot, d, "identifier", CASE)
    assert d.is_dirty()
    fake.confirms.append(False)  # keep (the default answer)
    qtbot.keyClick(d.editor("identifier"), Qt.Key.Key_Escape)
    assert _asked(fake) == [DISCARD]
    assert window.dialog is d and window.stack.currentWidget() is d
    assert d.editor("identifier").text() == CASE  # type: ignore[attr-defined]


def test_escape_after_typing_can_discard(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                         corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    type_into(qtbot, d, "identifier", CASE)
    fake.confirms.append(True)
    qtbot.keyClick(d.editor("identifier"), Qt.Key.Key_Escape)
    assert _asked(fake) == [DISCARD]
    assert window.stack.currentWidget() is window.drop and window.dialog is None


def test_cancel_button_asks_after_placement_change(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                                   corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    other = next(a for a in d.anchor_buttons if a != d.anchor)
    d.anchor_buttons[other].click()
    assert d.is_dirty()
    d.cancel_button.click()  # no answer queued: keep
    assert _asked(fake) == [DISCARD] and window.stack.currentWidget() is d


@pytest.mark.parametrize("change", ["box", "tick", "choice"])
def test_other_settings_count_as_edits(qtbot: QtBot, window: MainWindow, corpus_dir: Path, change: str) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    if change == "box":
        d.box_check.toggle()
    elif change == "tick":
        next(iter(d._ticks.values())).toggle()
    else:
        choose(d, "doc_type", "Court order")
    assert d.is_dirty()


def test_undoing_an_edit_is_clean_again(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                        corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    type_into(qtbot, d, "identifier", CASE)
    type_into(qtbot, d, "identifier", "")
    assert not d.is_dirty()
    qtbot.keyClick(d.editor("identifier"), Qt.Key.Key_Escape)
    assert fake.questions == [] and window.stack.currentWidget() is window.drop


def test_escape_in_an_open_list_only_closes_the_list(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                                     corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    type_into(qtbot, d, "identifier", CASE)
    combo = d.editor("doc_type")
    assert isinstance(combo, QComboBox)
    combo.showPopup()
    qtbot.waitUntil(lambda: combo.view().isVisible(), timeout=WAIT_MS)
    popup = QApplication.activePopupWidget()
    assert popup is not None
    qtbot.keyClick(combo.view(), Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: not combo.view().isVisible(), timeout=WAIT_MS)
    assert fake.questions == []
    assert window.stack.currentWidget() is d


# ------------------------------------------------------------ UX-006: closing the window


def test_closing_the_window_while_dirty_asks(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                             corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    type_into(qtbot, d, "identifier", CASE)
    fake.confirms.append(False)
    assert not window.close()
    assert _asked(fake) == [DISCARD]
    assert window.isVisible() and window.dialog is d
    assert d.editor("identifier").text() == CASE  # type: ignore[attr-defined]
    fake.confirms.append(True)
    assert window.close()
    assert window.dialog is None


def test_closing_the_window_with_nothing_entered_doesnt_ask(qtbot: QtBot, window: MainWindow,
                                                            fake: FakeServices, corpus_dir: Path) -> None:
    open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    assert window.close()
    assert fake.questions == []


def test_closing_while_saving_doesnt_ask(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                        corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "large_520_pages.pdf", tmp_path)
    d = open_dialog(qtbot, window, src)
    type_into(qtbot, d, "identifier", CASE)
    choose(d, "doc_type", "Court order")
    wait_ready(qtbot, d)
    out = tmp_path / "big_copy.pdf"
    d.start_save(out)
    assert d.saving and d.is_dirty()
    assert window.close()  # the save is cancelled, as before (D-070)
    assert fake.questions == []
    assert not out.exists()


@pytest.mark.parametrize("mode", ["batch", "combine"])
def test_several_file_dialogs_ask_too(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                      corpus_dir: Path, tmp_path: Path, mode: str) -> None:
    d = _several(qtbot, window, corpus_dir, tmp_path, mode)
    d.cancel_button.click()  # nothing entered: back to the files screen at once
    assert fake.questions == [] and window.stack.currentWidget() is window.files_screen
    d = _several_again(qtbot, window, mode)
    type_into(qtbot, d, "identifier", CASE)
    qtbot.keyClick(d.editor("identifier"), Qt.Key.Key_Escape)  # no answer queued: keep
    assert _asked(fake) == [DISCARD] and window.stack.currentWidget() is d
    fake.confirms.append(True)
    d.cancel_button.click()
    assert window.stack.currentWidget() is window.files_screen


def _several_again(qtbot: QtBot, w: MainWindow, mode: str) -> StampDialog:
    (w.files_screen.batch_button if mode == "batch" else w.files_screen.combine_button).click()
    kind = BatchDialog if mode == "batch" else CombineDialog
    qtbot.waitUntil(lambda: isinstance(w.dialog, kind) and w.stack.currentWidget() is w.dialog, timeout=WAIT_MS)
    assert w.dialog is not None
    return wait_ready(qtbot, w.dialog)


# ------------------------------------------------------------ UX-008: the window title


def test_title_follows_the_screen(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                  corpus_dir: Path, tmp_path: Path) -> None:
    assert _shown_title(window) == APP
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    d = open_dialog(qtbot, window, src)
    assert _shown_title(window) == f"letter_portrait.pdf – {APP}"
    type_into(qtbot, d, "identifier", CASE)
    assert _shown_title(window) == f"letter_portrait.pdf – {APP} (not saved)"
    choose(d, "doc_type", "Court order")
    wait_ready(qtbot, d)
    out = tmp_path / d.suggested_name()
    fake.save_paths.append(out)
    d.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.saved, timeout=WAIT_MS)
    assert _shown_title(window) == f"Saved: {out.name} – {APP}"
    window.saved.another_button.click()
    assert _shown_title(window) == APP


@pytest.mark.parametrize("mode", ["batch", "combine"])
def test_title_counts_files(qtbot: QtBot, window: MainWindow, corpus_dir: Path, tmp_path: Path, mode: str) -> None:
    d = _several(qtbot, window, corpus_dir, tmp_path, mode)
    assert _shown_title(window) == f"3 files – {APP}"
    type_into(qtbot, d, "identifier", CASE)
    assert _shown_title(window) == f"3 files – {APP} (not saved)"


def test_title_name_keeps_brackets_and_is_capped() -> None:
    assert _title_name("a[*]b.pdf") == "a[*][*]b.pdf"
    long = _title_name("x" * 300 + ".pdf")
    assert len(long) == MAX_TITLE_NAME and long.endswith("…")
    assert _title_name("evil‮fdp.exe") == "evil⟨U+202E⟩fdp.exe"


@pytest.mark.skipif(sys.platform == "win32", reason="'*' can't be in a Windows file name")
def test_file_named_with_brackets_shows_in_full(qtbot: QtBot, window: MainWindow, corpus_dir: Path,
                                                tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path, "a[*]b.pdf")
    open_dialog(qtbot, window, src)
    assert _shown_title(window) == f"a[*]b.pdf – {APP}"
