"""The window's choice of a password for the combined file (F-01, amends D-096).

Native dialogs never open: ``FakeServices`` answers. Synthetic files and a
made-up, distinctive password only.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from stamper.core import pending
from stamper.gui.app import make_app
from stamper.gui.crash import crash_details
from stamper.gui.main_window import MainWindow
from stamper.gui.multi import CombineDialog, PrepareScreen
from stamper.handlers.pdf import OUTPUT_PASSWORD_RULE, PASSWORD_SET_NOTE
from tests.gui_support import (  # noqa: F401 - fixtures
    TODAY,
    WAIT_MS,
    FakeServices,
    choose,
    close_window,
    copy_to,
    default_window,
    fake,
    type_into,
    wait_ready,
)
from tests.test_gui_carry_over import EVIDENCE, _two_profiles, _wait_dialog
from tests.test_gui_multi import give_passwords, open_several, prepare, wait_dialog
from tools import make_corpus

PW = make_corpus.USER_PASSWORD
OUT_PW = "Zq7!planted-out-pw"
CASE = "99-2026-999901"
DECLINED = "This combined file has no password. 1 of its files was password-protected."


def _locked_and_open(corpus_dir: Path, folder: Path) -> list[Path]:
    return [copy_to(corpus_dir / "encrypted_user_password.pdf", folder),
            copy_to(corpus_dir / "letter_portrait.pdf", folder)]


def _to_prepare(qtbot: QtBot, w: MainWindow, files: list[Path]) -> PrepareScreen:
    open_several(qtbot, w, files)
    w.files_screen.combine_button.click()
    screen = prepare(qtbot, w)
    give_passwords(qtbot, screen)
    return screen


def _fill_and_save(qtbot: QtBot, w: MainWindow, fake: FakeServices, d: CombineDialog, out: Path) -> None:
    choose(d, "doc_type", "Report")
    type_into(qtbot, d, "identifier", CASE)
    wait_ready(qtbot, d)
    assert d.save_button.isEnabled(), d.blockers()
    fake.save_paths.append(out)
    d.save_button.click()
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.saved, timeout=WAIT_MS)


def _check_the_copy(w: MainWindow) -> list[str]:
    return [label.text() for label in w.saved.notes.check_frame.findChildren(QLabel)]


def _assert_protected(out: Path, corpus_dir: Path) -> None:
    with pymupdf.open(out) as doc:
        assert doc.needs_pass and not doc.authenticate("wrong-password")
    sources: list[str] = []
    for name, pw in (("encrypted_user_password.pdf", PW), ("letter_portrait.pdf", None)):
        with pymupdf.open(corpus_dir / name) as src:
            if pw:
                src.authenticate(pw)
            sources.extend(p.get_text().strip() for p in src)
    with pymupdf.open(out) as doc:
        assert doc.authenticate(OUT_PW)
        assert doc.metadata["encryption"] == "Standard V5 R6 256-bit AES"
        for page, original in zip(doc, sources, strict=True):
            text = page.get_text()
            assert all(line in text for line in original.splitlines()) and CASE in text


# ------------------------------------------------------------ a source needed a password: the question


def test_locked_source_asks_and_waits_for_an_answer(qtbot: QtBot, default_window: MainWindow,
                                                    corpus_dir: Path, tmp_path: Path) -> None:
    screen = _to_prepare(qtbot, default_window, _locked_and_open(corpus_dir, tmp_path))
    box = screen.password_box
    assert box is not None and box.isVisible()
    assert not box.none_radio.isChecked() and not box.set_radio.isChecked()  # nothing preselected
    assert not box.password_edit.text() and not box.repeat_edit.text()  # nothing prefilled
    assert not screen.continue_button.isEnabled()
    assert 'Choose "No password" or "Set a password" for the combined file.' in screen.status.text()
    box.none_radio.setChecked(True)
    assert screen.continue_button.isEnabled()
    box.set_radio.setChecked(True)
    box.password_edit.setText(OUT_PW)
    default_window._close_prepare()  # Back, or the combine dialog opening; deleted later
    assert not box.password_edit.text() and screen.output_password() is None


def test_no_password_saves_an_open_file_with_the_counted_warning(qtbot: QtBot, default_window: MainWindow,
                                                                 fake: FakeServices, corpus_dir: Path,
                                                                 tmp_path: Path) -> None:
    w = default_window
    screen = _to_prepare(qtbot, w, _locked_and_open(corpus_dir, tmp_path))
    assert screen.password_box is not None
    screen.password_box.none_radio.setChecked(True)
    screen.continue_button.click()
    d = wait_dialog(qtbot, w, CombineDialog)
    assert isinstance(d, CombineDialog)
    assert d.password_link is None  # already answered; not offered again
    out = tmp_path / "combined.pdf"
    _fill_and_save(qtbot, w, fake, d, out)
    assert any(t.startswith(DECLINED) for t in _check_the_copy(w))
    with pymupdf.open(out) as doc:
        assert not doc.needs_pass


@pytest.mark.parametrize(("typed", "repeat", "sentence"), [
    ("", "", "Type the password for the combined file in both boxes."),
    (OUT_PW, "", "Type the password for the combined file in both boxes."),
    (OUT_PW, OUT_PW + "x", "The two passwords for the combined file don't match."),
    ("pässwort", "pässwort", OUTPUT_PASSWORD_RULE),
    ("x" * 41, "x" * 41, OUTPUT_PASSWORD_RULE),
])
def test_unusable_passwords_are_refused_before_continuing(qtbot: QtBot, default_window: MainWindow,
                                                          corpus_dir: Path, tmp_path: Path,
                                                          typed: str, repeat: str, sentence: str) -> None:
    screen = _to_prepare(qtbot, default_window, _locked_and_open(corpus_dir, tmp_path))
    box = screen.password_box
    assert box is not None
    box.set_radio.setChecked(True)
    assert box.password_edit.isVisible() and box.repeat_edit.isVisible()
    box.password_edit.setText(typed)
    box.repeat_edit.setText(repeat)
    assert not screen.continue_button.isEnabled()
    assert sentence in screen.status.text()
    assert screen.output_password() is None


def test_set_a_password_protects_the_file_and_keeps_it_nowhere(
        qtbot: QtBot, default_window: MainWindow, fake: FakeServices, corpus_dir: Path, tmp_path: Path,
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
    screen = _to_prepare(qtbot, w, _locked_and_open(corpus_dir, tmp_path))
    box = screen.password_box
    assert box is not None
    box.set_radio.setChecked(True)
    box.password_edit.setText(OUT_PW)
    box.repeat_edit.setText(OUT_PW)
    assert screen.continue_button.isEnabled(), screen.blockers()
    screen.continue_button.click()
    d = wait_dialog(qtbot, w, CombineDialog)
    assert isinstance(d, CombineDialog)
    assert w.prepare is None  # the screen holding the boxes is gone
    assert PASSWORD_SET_NOTE in [label.text() for label in d.findChildren(QLabel)]
    out = tmp_path / "combined.pdf"
    _fill_and_save(qtbot, w, fake, d, out)

    _assert_protected(out, corpus_dir)
    notes = w.saved.notes.texts()
    assert PASSWORD_SET_NOTE in notes and not any("has no password" in n for n in notes)
    assert w.dialog is None  # dropped once saved

    # A credential: in memory only, never written or carried.
    assert journal and all(OUT_PW not in text for text in journal)
    state = w.locs.state_file
    assert state.is_file() and OUT_PW not in state.read_text(encoding="utf-8")
    assert OUT_PW not in repr(w._last_start)  # not part of "Stamp another with the same values"
    try:
        raise RuntimeError(f"planted {OUT_PW}")
    except RuntimeError:
        assert OUT_PW not in crash_details(*sys.exc_info())


# ------------------------------------------------------------ no source needed a password: offered, not asked


def test_open_sources_are_not_asked_and_save_as_before(qtbot: QtBot, default_window: MainWindow,
                                                       fake: FakeServices, corpus_dir: Path, tmp_path: Path) -> None:
    w = default_window
    files = [copy_to(corpus_dir / n, tmp_path) for n in ("letter_portrait.pdf", "rotate_90.pdf")]
    open_several(qtbot, w, files)
    w.files_screen.combine_button.click()
    d = wait_dialog(qtbot, w, CombineDialog)  # straight to the dialog: no Before stamping screen
    assert isinstance(d, CombineDialog) and w.prepare is None
    assert d.password_link is not None and d.password_link.isVisible()
    assert d.password_box is not None and not d.password_box.isVisible()
    assert d.password_box.none_radio.isChecked()  # the standing choice
    out = tmp_path / "combined.pdf"
    _fill_and_save(qtbot, w, fake, d, out)
    assert not any("password" in n for n in w.saved.notes.texts())
    with pymupdf.open(out) as doc:
        assert not doc.needs_pass and not doc.metadata["encryption"]


def test_open_sources_can_set_a_password_from_the_link(qtbot: QtBot, default_window: MainWindow,
                                                       fake: FakeServices, corpus_dir: Path, tmp_path: Path) -> None:
    w = default_window
    files = _locked_and_open(corpus_dir, tmp_path)
    plain = [files[1], copy_to(corpus_dir / "rotate_90.pdf", tmp_path)]
    open_several(qtbot, w, plain)
    w.files_screen.combine_button.click()
    d = wait_dialog(qtbot, w, CombineDialog)
    assert isinstance(d, CombineDialog) and d.password_link is not None and d.password_box is not None
    d.password_link.click()
    assert d.password_box.isVisible() and not d.password_link.isVisible()
    d.password_box.set_radio.setChecked(True)
    choose(d, "doc_type", "Report")
    type_into(qtbot, d, "identifier", CASE)
    wait_ready(qtbot, d)
    assert not d.save_button.isEnabled()
    assert "Type the password for the combined file in both boxes." in d.blockers()
    d.password_box.password_edit.setText(OUT_PW)
    d.password_box.repeat_edit.setText(OUT_PW)
    assert d.save_button.isEnabled(), d.blockers()
    out = tmp_path / "combined.pdf"
    fake.save_paths.append(out)
    d.save_button.click()
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.saved, timeout=WAIT_MS)
    assert PASSWORD_SET_NOTE in w.saved.notes.texts()
    with pymupdf.open(out) as doc:
        assert doc.needs_pass and not doc.authenticate("wrong-password")
    with pymupdf.open(out) as doc:
        assert doc.authenticate(OUT_PW) and all(CASE in p.get_text() for p in doc)


def test_owner_restricted_only_does_not_ask(qtbot: QtBot, default_window: MainWindow, corpus_dir: Path,
                                            tmp_path: Path) -> None:
    w = default_window
    files = [copy_to(corpus_dir / "owner_restrictions.pdf", tmp_path),
             copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)]
    open_several(qtbot, w, files)
    w.files_screen.combine_button.click()
    screen = prepare(qtbot, w)  # shown for the restrictions tick, not for a password
    assert screen.password_box is None and screen.locked_count() == 0
    texts = [label.text() for label in screen.findChildren(QLabel)]
    assert any(t.startswith("The combined file is saved without a password or restrictions.") for t in texts)
    assert screen.restriction_tick is not None
    screen.restriction_tick.setChecked(True)
    assert screen.continue_button.isEnabled()
    screen.continue_button.click()
    d = wait_dialog(qtbot, w, CombineDialog)
    assert isinstance(d, CombineDialog)
    assert d.combined.locked == () and d.password_link is not None  # offered on the dialog instead
    assert "has no password" not in d.combined.protection_warning()


def test_leaving_the_dialog_drops_the_password(qtbot: QtBot, default_window: MainWindow, corpus_dir: Path,
                                               tmp_path: Path) -> None:
    w = default_window
    screen = _to_prepare(qtbot, w, _locked_and_open(corpus_dir, tmp_path))
    box = screen.password_box
    assert box is not None
    box.set_radio.setChecked(True)
    box.password_edit.setText(OUT_PW)
    box.repeat_edit.setText(OUT_PW)
    screen.continue_button.click()
    d = wait_dialog(qtbot, w, CombineDialog)
    assert isinstance(d, CombineDialog) and d.output_password() == OUT_PW
    w.services.confirm = lambda *_a: True
    d.cancel_button.click()
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.files_screen, timeout=WAIT_MS)
    assert d.chosen_password is None and w.dialog is None


def test_switching_profile_keeps_the_password_and_says_so(qtbot: QtBot, fake: FakeServices, corpus_dir: Path,
                                                          tmp_path: Path) -> None:
    make_app()
    w = MainWindow(config_path=_two_profiles(tmp_path), services=fake.services(), today=TODAY)
    qtbot.addWidget(w)
    w.show()
    try:
        screen = _to_prepare(qtbot, w, _locked_and_open(corpus_dir, tmp_path))
        assert screen.password_box is not None
        screen.password_box.set_radio.setChecked(True)
        screen.password_box.password_edit.setText(OUT_PW)
        screen.password_box.repeat_edit.setText(OUT_PW)
        screen.continue_button.click()
        d = wait_dialog(qtbot, w, CombineDialog)
        assert d.profile_combo is not None
        d.profile_combo.setCurrentIndex(d.profile_combo.findData(EVIDENCE))
        d.profile_combo.activated.emit(d.profile_combo.currentIndex())
        new = _wait_dialog(qtbot, w)
        assert isinstance(new, CombineDialog) and new is not d
        assert new.output_password() == OUT_PW
        assert "the password for the combined file" in new.start_label.text()
        assert d.chosen_password is None  # the old screen forgot it
    finally:
        close_window(w)
