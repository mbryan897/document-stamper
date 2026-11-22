"""The window's choice for a protected original's stamped copy: keep, no password, or a new one (amends D-050).

Native dialogs never open: ``FakeServices`` answers. Synthetic files and a
made-up, distinctive password only.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from stamper.core import pending
from stamper.gui.main_window import MainWindow
from stamper.gui.multi import BatchDialog
from stamper.gui.stamp_dialog import StampDialog
from stamper.handlers.batch import Status
from stamper.handlers.pdf import PASSWORD_SET_NOTE, UNPROTECTED_NOTE
from tests.gui_support import (  # noqa: F401 - fixtures
    WAIT_MS,
    FakeServices,
    choose,
    copy_to,
    default_window,
    fake,
    open_dialog,
    type_into,
    wait_ready,
)
from tests.test_gui_multi import give_passwords, open_several, prepare, wait_dialog
from tools import make_corpus

PW = make_corpus.USER_PASSWORD
NEW_PW = "Qk4#planted-copy-pw"
CASE = "99-2026-999901"


def _open_locked(qtbot: QtBot, w: MainWindow, fake: FakeServices, corpus_dir: Path, tmp_path: Path) -> StampDialog:
    fake.passwords.append(PW)
    return open_dialog(qtbot, w, copy_to(corpus_dir / "encrypted_user_password.pdf", tmp_path))


def _fill(qtbot: QtBot, d: StampDialog) -> None:
    choose(d, "doc_type", "Report")
    type_into(qtbot, d, "identifier", CASE)
    wait_ready(qtbot, d)


def _save(qtbot: QtBot, w: MainWindow, fake: FakeServices, d: StampDialog, out: Path) -> None:
    assert d.save_button.isEnabled(), d.blockers()
    fake.save_paths.append(out)
    d.save_button.click()
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.saved, timeout=WAIT_MS)


def _check_the_copy(w: MainWindow) -> list[str]:
    return [label.text() for label in w.saved.notes.check_frame.findChildren(QLabel)]


def _page_texts(path: Path, password: str | None) -> list[str]:
    with pymupdf.open(path) as doc:
        if password:
            assert doc.authenticate(password)
        return [p.get_text() for p in doc]


# ------------------------------------------------------------ one file


def test_locked_file_asks_with_nothing_chosen(qtbot: QtBot, default_window: MainWindow, fake: FakeServices,
                                              corpus_dir: Path, tmp_path: Path) -> None:
    d = _open_locked(qtbot, default_window, fake, corpus_dir, tmp_path)
    box = d.password_box
    assert box is not None and box.isVisible() and box.keep_radio is not None
    assert [r.text() for r in (box.keep_radio, box.none_radio, box.set_radio)] == [
        "Keep the original's password and permissions", "No password", "Set a password"]
    assert not any(r.isChecked() for r in (box.keep_radio, box.none_radio, box.set_radio))
    assert not box.password_edit.text()  # nothing prefilled, not the original's password either
    _fill(qtbot, d)
    assert not d.save_button.isEnabled()
    assert ('Choose "Keep the original\'s password and permissions" or "No password" or "Set a password" '
            "for the stamped copy.") in d.blockers()


def test_no_password_saves_an_open_copy_with_the_warning(qtbot: QtBot, default_window: MainWindow,
                                                         fake: FakeServices, corpus_dir: Path, tmp_path: Path) -> None:
    w = default_window
    d = _open_locked(qtbot, w, fake, corpus_dir, tmp_path)
    assert d.password_box is not None
    d.password_box.none_radio.setChecked(True)
    _fill(qtbot, d)
    out = tmp_path / "copy.pdf"
    _save(qtbot, w, fake, d, out)
    assert UNPROTECTED_NOTE in _check_the_copy(w)
    with pymupdf.open(out) as doc:
        assert not doc.needs_pass and not doc.metadata["encryption"]
        assert all(CASE in p.get_text() for p in doc)


def test_set_a_password_replaces_the_original_one(qtbot: QtBot, default_window: MainWindow, fake: FakeServices,
                                                  corpus_dir: Path, tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    w = default_window
    journal: list[str] = []
    real_record = pending.record

    def spy(tmp: Path, **kw: object) -> Path | None:
        entry = real_record(tmp, **kw)  # type: ignore[arg-type]
        if entry is not None:
            journal.append(entry.read_text(encoding="utf-8"))
        return entry

    monkeypatch.setattr(pending, "record", spy)
    d = _open_locked(qtbot, w, fake, corpus_dir, tmp_path)
    box = d.password_box
    assert box is not None
    box.set_radio.setChecked(True)
    _fill(qtbot, d)
    box.password_edit.setText(NEW_PW)
    box.repeat_edit.setText(NEW_PW + "x")
    assert "The two passwords for the stamped copy don't match." in d.blockers()
    box.repeat_edit.setText(NEW_PW)
    out = tmp_path / "copy.pdf"
    _save(qtbot, w, fake, d, out)
    assert PASSWORD_SET_NOTE in w.saved.notes.texts()

    with pymupdf.open(out) as doc:
        assert doc.needs_pass and not doc.authenticate(PW)  # the original's password no longer opens it
    original = _page_texts(corpus_dir / "encrypted_user_password.pdf", PW)
    copy = _page_texts(out, NEW_PW)
    with pymupdf.open(out) as doc:
        doc.authenticate(NEW_PW)
        assert doc.metadata["encryption"] == "Standard V5 R6 256-bit AES"
    for before, after in zip(original, copy, strict=True):  # D-056: readable after an encrypted save
        assert all(line in after for line in before.strip().splitlines()) and CASE in after
    assert journal and all(NEW_PW not in t for t in journal)
    assert NEW_PW not in w.locs.state_file.read_text(encoding="utf-8")
    assert NEW_PW not in repr(w._last_start) and w.dialog is None


def test_restricted_only_offers_with_keep_chosen(qtbot: QtBot, default_window: MainWindow, fake: FakeServices,
                                                 corpus_dir: Path, tmp_path: Path) -> None:
    w = default_window
    d = open_dialog(qtbot, w, copy_to(corpus_dir / "owner_restrictions.pdf", tmp_path))
    box = d.password_box
    assert box is not None and box.keep_radio is not None and box.keep_radio.isChecked()
    assert d.restriction_tick is not None
    d.restriction_tick.setChecked(True)
    _fill(qtbot, d)
    assert d.save_button.isEnabled(), d.blockers()  # keeping stands; not forced to choose
    box.none_radio.setChecked(True)  # "No password" also drops the restrictions
    out = tmp_path / "copy.pdf"
    _save(qtbot, w, fake, d, out)
    with pymupdf.open(out) as doc:
        assert not doc.metadata["encryption"]
        assert doc.permissions & pymupdf.PDF_PERM_MODIFY and doc.permissions & pymupdf.PDF_PERM_PRINT


def test_unprotected_file_is_not_offered(qtbot: QtBot, default_window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, default_window, corpus_dir / "letter_portrait.pdf")
    assert d.password_box is None


# ------------------------------------------------------------ batch


def _batch(qtbot: QtBot, w: MainWindow, corpus_dir: Path, tmp_path: Path) -> tuple[BatchDialog, list[Path]]:
    files = [copy_to(corpus_dir / "encrypted_user_password.pdf", tmp_path),
             copy_to(corpus_dir / "owner_restrictions.pdf", tmp_path),
             copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)]
    open_several(qtbot, w, files)
    w.files_screen.batch_button.click()
    screen = prepare(qtbot, w)
    give_passwords(qtbot, screen)
    assert screen.restriction_tick is not None
    screen.restriction_tick.setChecked(True)
    screen.continue_button.click()
    d = wait_dialog(qtbot, w, BatchDialog)
    assert isinstance(d, BatchDialog)
    _fill(qtbot, d)
    return d, files


def _run(qtbot: QtBot, w: MainWindow, d: BatchDialog) -> dict[str, Path]:
    assert d.save_button.isEnabled(), d.blockers()
    d.save_button.click()
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.batch_saved, timeout=WAIT_MS)
    assert all(o.status == Status.SAVED for o in w.batch_saved.outcomes)
    return {o.item.path.name: o.item.out_path for o in w.batch_saved.outcomes}


def test_batch_asks_once_and_a_new_password_covers_the_protected_copies(
        qtbot: QtBot, default_window: MainWindow, corpus_dir: Path, tmp_path: Path) -> None:
    w = default_window
    d, _ = _batch(qtbot, w, corpus_dir, tmp_path)
    box = d.password_box
    assert box is not None and box.keep_radio is not None
    assert box.keep_radio.text() == "Keep each original's password and permissions"
    assert not any(r.isChecked() for r in (box.keep_radio, box.none_radio, box.set_radio))
    assert not d.save_button.isEnabled()
    assert any("applies to the copies of protected files" in lab.text() for lab in d.findChildren(QLabel))
    box.set_radio.setChecked(True)
    box.password_edit.setText(NEW_PW)
    box.repeat_edit.setText(NEW_PW)
    out = _run(qtbot, w, d)
    for name in ("encrypted_user_password.pdf", "owner_restrictions.pdf"):
        with pymupdf.open(out[name]) as doc:
            assert doc.needs_pass and not doc.authenticate(PW)
        assert all(CASE in t for t in _page_texts(out[name], NEW_PW))
    with pymupdf.open(out["letter_portrait.pdf"]) as doc:
        assert not doc.needs_pass and not doc.metadata["encryption"]  # wasn't protected; still isn't


def test_batch_no_password_removes_every_copys_protection(qtbot: QtBot, default_window: MainWindow,
                                                          corpus_dir: Path, tmp_path: Path) -> None:
    w = default_window
    d, _ = _batch(qtbot, w, corpus_dir, tmp_path)
    assert d.password_box is not None
    d.password_box.none_radio.setChecked(True)
    out = _run(qtbot, w, d)
    for path in out.values():
        with pymupdf.open(path) as doc:
            assert not doc.needs_pass and not doc.metadata["encryption"]
