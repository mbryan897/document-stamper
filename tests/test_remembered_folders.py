"""Save As and batches start where the last save went (UX-037, UX-039).

state.json may hold the folders and the batch choice, never a field value
(D-081, amended 2026-11-14). Every value read back is untrusted. Synthetic
data only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from stamper.core.filenames import visible
from stamper.core.locations import load_state, state_folder
from stamper.gui.app import make_app
from stamper.gui.main_window import MainWindow
from stamper.gui.multi import BatchDialog, CombineDialog
from tests.gui_support import (  # noqa: F401 - fixtures
    TODAY,
    WAIT_MS,
    FakeServices,
    choose,
    close_window,
    copy_to,
    fake,
    open_dialog,
    type_into,
    wait_ready,
    window,
)
from tests.multi_support import make_tiny
from tests.stamping import RECORDS_CONFIG
from tests.test_gui_multi import CASE, open_several, wait_dialog

DOC_TYPE = "Court order"


def _fill(qtbot: QtBot, d) -> None:  # noqa: ANN001
    choose(d, "doc_type", DOC_TYPE)
    type_into(qtbot, d, "identifier", CASE)
    wait_ready(qtbot, d)
    assert d.save_button.isEnabled(), d.blockers()


def _state_text(window: MainWindow) -> str:
    return window.locs.state_file.read_text(encoding="utf-8")


def _assert_no_field_values(window: MainWindow) -> None:
    text = _state_text(window)
    assert CASE not in text and DOC_TYPE not in text and "Court-order" not in text


def _write_state(window: MainWindow, **values: object) -> None:
    """Replace state.json as an edit outside the app would, and read it back."""
    window.locs.user.mkdir(parents=True, exist_ok=True)
    window.locs.state_file.write_text(json.dumps(values), encoding="utf-8")
    window.state = load_state(window.locs)


def _combine(qtbot: QtBot, window: MainWindow, files: list[Path]) -> CombineDialog:
    open_several(qtbot, window, files)
    window.files_screen.combine_button.click()
    d = wait_dialog(qtbot, window, CombineDialog)
    assert isinstance(d, CombineDialog)
    return d


def _batch(qtbot: QtBot, window: MainWindow, files: list[Path]) -> BatchDialog:
    open_several(qtbot, window, files)
    window.files_screen.batch_button.click()
    d = wait_dialog(qtbot, window, BatchDialog)
    assert isinstance(d, BatchDialog)
    return d


# ------------------------------------------------------------ the rule for a folder read back


def test_state_folder_accepts_only_an_existing_absolute_folder(tmp_path: Path) -> None:
    a_file = tmp_path / "f.pdf"
    a_file.write_bytes(b"x")
    assert state_folder(str(tmp_path)) == tmp_path
    for bad in (None, 5, True, [str(tmp_path)], "", "relative/folder", str(tmp_path / "gone"), str(a_file),
                "x" * 40000, f"{tmp_path}\0x"):
        assert state_folder(bad) is None, bad


def test_last_folder_uses_the_same_rule(qtbot: QtBot, fake: FakeServices, tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "relative").mkdir()  # exists, but only relative to wherever the app was started
    make_app()
    probe = MainWindow(config_path=RECORDS_CONFIG, services=fake.services(), today=TODAY)
    locs = probe.locs
    close_window(probe)
    locs.user.mkdir(parents=True, exist_ok=True)
    for value, expected in ((str(tmp_path), tmp_path), ("relative", None), (7, None)):
        locs.state_file.write_text(json.dumps({"last_folder": value}), encoding="utf-8")
        w = MainWindow(config_path=RECORDS_CONFIG, services=fake.services(), today=TODAY)
        qtbot.addWidget(w)
        assert w._last_folder == expected
        close_window(w)


def test_unknown_keys_survive_and_a_damaged_file_is_empty(window: MainWindow, tmp_path: Path) -> None:
    _write_state(window, future=[1], last_save_folder=str(tmp_path), batch_in_one_folder=True)
    assert window.state.extra == {"future": [1]}
    assert window.state.last_save_folder == str(tmp_path) and window.state.batch_in_one_folder is True
    window.locs.state_file.write_text("{not json", encoding="utf-8")
    state = load_state(window.locs)
    assert (state.last_save_folder, state.batch_folder, state.batch_in_one_folder) == (None, None, False)


# ------------------------------------------------------------ Save As (UX-037)


def test_save_as_starts_in_the_last_save_folder(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                               corpus_dir: Path, tmp_path: Path) -> None:
    src_dir, out_dir = tmp_path / "originals", tmp_path / "copies"
    src_dir.mkdir()
    out_dir.mkdir()
    src = copy_to(corpus_dir / "letter_portrait.pdf", src_dir)
    d = open_dialog(qtbot, window, src)
    _fill(qtbot, d)
    d.save_button.click()  # cancelled: nothing saved yet, so it starts next to the original
    assert fake.save_suggestions[-1].parent == src_dir
    fake.save_paths.append(out_dir / "first.pdf")
    d.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.saved, timeout=WAIT_MS)
    assert json.loads(_state_text(window))["last_save_folder"] == str(out_dir)
    _assert_no_field_values(window)

    # The next single file starts there.
    other = copy_to(corpus_dir / "rotate_90.pdf", src_dir)
    d = open_dialog(qtbot, window, other)
    _fill(qtbot, d)
    d.save_button.click()
    assert fake.save_suggestions[-1].parent == out_dir

    # So does a combine, and saving it keeps the rule.
    window.show_drop()
    files = [copy_to(corpus_dir / n, src_dir, f"c_{n}") for n in ("letter_portrait.pdf", "rotate_90.pdf")]
    c = _combine(qtbot, window, files)
    _fill(qtbot, c)
    c.save_button.click()
    assert fake.save_suggestions[-1].parent == out_dir
    out2 = tmp_path / "combined"
    out2.mkdir()
    fake.save_paths.append(out2 / "both.pdf")
    c.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.saved, timeout=WAIT_MS)
    assert json.loads(_state_text(window))["last_save_folder"] == str(out2)
    _assert_no_field_values(window)


def _bad_values(tmp_path: Path) -> dict[str, object]:
    gone = tmp_path / "deleted"
    gone.mkdir()
    gone.rmdir()
    a_file = tmp_path / "a_file.pdf"
    a_file.write_bytes(b"x")
    return {"deleted": str(gone), "relative": "copies", "number": 12, "file": str(a_file)}


@pytest.mark.parametrize("kind", ["deleted", "relative", "number", "file"])
def test_unusable_last_save_folder_falls_back(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                             corpus_dir: Path, tmp_path: Path, kind: str) -> None:
    src_dir = tmp_path / "originals"
    src_dir.mkdir()
    _write_state(window, last_save_folder=_bad_values(tmp_path)[kind])
    src = copy_to(corpus_dir / "letter_portrait.pdf", src_dir)
    d = open_dialog(qtbot, window, src)
    _fill(qtbot, d)
    d.save_button.click()
    assert fake.save_suggestions[-1].parent == src_dir
    window.show_drop()
    files = [copy_to(corpus_dir / n, src_dir, f"c_{n}") for n in ("rotate_90.pdf", "letter_portrait.pdf")]
    c = _combine(qtbot, window, files)
    _fill(qtbot, c)
    c.save_button.click()
    assert fake.save_suggestions[-1].parent == src_dir  # the first original's folder


def test_folder_deleted_after_start_up_falls_back(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                                 corpus_dir: Path, tmp_path: Path) -> None:
    gone = tmp_path / "gone"
    gone.mkdir()
    _write_state(window, last_save_folder=str(gone))
    gone.rmdir()
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    d = open_dialog(qtbot, window, src)
    _fill(qtbot, d)
    d.save_button.click()
    assert fake.save_suggestions[-1].parent == tmp_path


# ------------------------------------------------------------ batch (UX-039)


def test_batch_choice_is_remembered_and_preselected(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                                   corpus_dir: Path, tmp_path: Path) -> None:
    files = [copy_to(corpus_dir / n, tmp_path) for n in ("letter_portrait.pdf", "rotate_90.pdf")]
    tiny = make_tiny(tmp_path / "tiny.pdf")  # can't be stamped: makes the pre-run question show both groups
    out = tmp_path / "out\u202efdp.exe"  # a hidden character in the folder name
    out.mkdir()
    d = _batch(qtbot, window, [*files, tiny])
    assert d.next_to_radio.isChecked() and d.out_folder is None  # nothing remembered yet
    fake.folders.append(out)
    d.folder_radio.click()
    _fill(qtbot, d)
    fake.confirms.append(True)
    d.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.batch_saved, timeout=WAIT_MS)
    assert len(list(out.iterdir())) == 2
    saved = json.loads(_state_text(window))
    assert saved["batch_folder"] == str(out) and saved["batch_in_one_folder"] is True
    _assert_no_field_values(window)

    # The next batch starts in that folder; a re-run finds the copies already there.
    d = _batch(qtbot, window, [*files, tiny])
    assert d.folder_radio.isChecked() and d.out_folder == out
    assert d.folder_label.textFormat() == Qt.TextFormat.PlainText
    assert d.folder_label.text() == visible(str(out)) and "⟨U+202E⟩" in d.folder_label.text()
    _fill(qtbot, d)
    d.save_button.click()
    qtbot.waitUntil(lambda: not d.saving and d.save_message() != "", timeout=WAIT_MS)
    message = d.save_message()
    assert message.startswith("None of the files can be saved as set up:\nAlready there:\n")
    there = message.split("Already there:\n", 1)[1].split("\n\n", 1)[0]
    assert there.splitlines() == [f"• {p.name}" for p in files]
    assert "Can't be stamped:\n• tiny.pdf" in message
    assert sorted(p.name for p in out.iterdir()) == sorted(d.name_for(p) for p in files)  # nothing replaced


def test_next_to_each_original_is_remembered(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                            corpus_dir: Path, tmp_path: Path) -> None:
    keep = tmp_path / "kept"
    keep.mkdir()
    _write_state(window, batch_folder=str(keep), batch_in_one_folder=True)
    files = [copy_to(corpus_dir / n, tmp_path) for n in ("letter_portrait.pdf", "rotate_90.pdf")]
    d = _batch(qtbot, window, files)
    assert d.out_folder == keep
    d.next_to_radio.click()
    _fill(qtbot, d)
    d.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.batch_saved, timeout=WAIT_MS)
    saved = json.loads(_state_text(window))
    assert saved["batch_in_one_folder"] is False and saved["batch_folder"] == str(keep)
    _assert_no_field_values(window)
    d = _batch(qtbot, window, files)
    assert d.next_to_radio.isChecked() and d.out_folder is None and d.folder_label.text() == ""


@pytest.mark.parametrize("flag", [1, "true", None, 0.0])
def test_non_bool_batch_choice_is_ignored(qtbot: QtBot, window: MainWindow, corpus_dir: Path, tmp_path: Path,
                                         flag: object) -> None:
    _write_state(window, batch_folder=str(tmp_path), batch_in_one_folder=flag)
    files = [copy_to(corpus_dir / n, tmp_path) for n in ("letter_portrait.pdf", "rotate_90.pdf")]
    d = _batch(qtbot, window, files)
    assert d.next_to_radio.isChecked() and d.out_folder is None


@pytest.mark.parametrize("kind", ["deleted", "relative", "number", "file"])
def test_unusable_batch_folder_falls_back(qtbot: QtBot, window: MainWindow, corpus_dir: Path, tmp_path: Path,
                                         kind: str) -> None:
    _write_state(window, batch_folder=_bad_values(tmp_path)[kind], batch_in_one_folder=True)
    files = [copy_to(corpus_dir / n, tmp_path) for n in ("letter_portrait.pdf", "rotate_90.pdf")]
    d = _batch(qtbot, window, files)
    assert d.next_to_radio.isChecked() and d.out_folder is None
