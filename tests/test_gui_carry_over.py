"""Repeated work on the stamp screen (UX-032, UX-035, UX-036, UX-038).

"Stamp another with the same values", switching the profile on the stamp
screen, and the "Stamped this session" list. All of it lives in memory only:
nothing here may reach state.json (D-081). Synthetic files and values only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QLabel, QPushButton
from pytestqt.qtbot import QtBot

from stamper.core.filenames import visible
from stamper.gui.app import make_app
from stamper.gui.main_window import MINIMUM_SIZE, SESSION_SHOWN, MainWindow, SessionEntry
from stamper.gui.multi import BatchDialog, CombineDialog
from stamper.gui.stamp_dialog import CARRIED_NOTE, StampDialog
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
from tests.stamping import RECORDS_CONFIG, make_signed_pdf
from tools import make_corpus

CASE = "99-2026-999901"
OTHER_CASE = "99-2026-999902"
TITLE = "Synthetic title 7"
EVIDENCE = "Evidence log"


# ------------------------------------------------------------ helpers


def _fill(qtbot: QtBot, d: StampDialog) -> None:
    """Values and placement that all differ from the profile's starting point."""
    type_into(qtbot, d, "identifier", CASE)
    choose(d, "doc_type", "Court order")
    type_into(qtbot, d, "doc_title", TITLE)
    d.anchor_buttons["bottom-left"].click()
    d.direction_buttons["along-right"].click()
    d.box_check.setChecked(False)
    d._ticks["date"].setChecked(True)
    d._ticks["doc_type"].setChecked(False)
    wait_ready(qtbot, d)


def _save(qtbot: QtBot, w: MainWindow, fake: FakeServices, d: StampDialog, folder: Path) -> Path:
    out = folder / f"stamped_{len(list(folder.iterdir()))}.pdf"
    fake.save_paths.append(out)
    d.save_button.click()
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.saved, timeout=WAIT_MS)
    return out


def _saved_once(qtbot: QtBot, w: MainWindow, fake: FakeServices, corpus_dir: Path, tmp_path: Path) -> Path:
    """Open a file, fill everything in, save it; returns the output."""
    d = open_dialog(qtbot, w, copy_to(corpus_dir / "letter_portrait.pdf", tmp_path, "first.pdf"))
    _fill(qtbot, d)
    return _save(qtbot, w, fake, d, tmp_path)


def _wait_dialog(qtbot: QtBot, w: MainWindow) -> StampDialog:
    qtbot.waitUntil(lambda: w.dialog is not None and w.stack.currentWidget() is w.dialog, timeout=WAIT_MS)
    assert w.dialog is not None
    return wait_ready(qtbot, w.dialog)


def _is_blank(d: StampDialog) -> bool:
    return (d.values() == {"identifier": "", "doc_type": "", "doc_title": "", "received": ""}
            and d.anchor == "top-right" and d.direction == "along-top" and d.box_check.isChecked()
            and d.ticked() == ["identifier", "doc_type"] and not any(d.note(n) for n in d.values()))


def _drop(w: MainWindow, path: Path) -> None:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    w.dropEvent(QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                           Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


def _two_profiles(folder: Path) -> Path:
    """The Records fixture plus a second profile that shares only some fields."""
    data = json.loads(RECORDS_CONFIG.read_text(encoding="utf-8"))
    data["profiles"].append({
        "name": EVIDENCE,
        "fields": ["identifier", "doc_title"],
        "stamps": [{"text": "{identifier}  |  Page {page} of {total}"}],
        "output_name": {"fields": ["identifier"], "separator": "_", "include_original_name": True},
    })
    path = folder / "two_profiles.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def two(qtbot: QtBot, fake: FakeServices, tmp_path: Path) -> Iterator[MainWindow]:
    """A window on a config with two profiles."""
    make_app()
    w = MainWindow(config_path=_two_profiles(tmp_path), services=fake.services(), today=TODAY)
    qtbot.addWidget(w)
    w.resize(1200, 780)
    w.show()
    yield w
    close_window(w)


# ------------------------------------------------------------ UX-032 / UX-038: carrying values over


def test_stamp_another_file_starts_blank(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                         corpus_dir: Path, tmp_path: Path) -> None:
    """Guards what already happened before this change: the plain button never carries anything."""
    _saved_once(qtbot, window, fake, corpus_dir, tmp_path)
    window.saved.another_button.click()
    assert window.stack.currentWidget() is window.drop
    d = open_dialog(qtbot, window, corpus_dir / "rotate_90.pdf")
    assert _is_blank(d)


def test_dropping_on_the_saved_screen_starts_blank(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                                   corpus_dir: Path, tmp_path: Path) -> None:
    """Guards what already happened before this change, and that a pending carry is dropped too."""
    _saved_once(qtbot, window, fake, corpus_dir, tmp_path)
    window._carry = window._last_start  # as if the values were waiting: a drop here still starts blank
    _drop(window, corpus_dir / "rotate_90.pdf")
    assert _is_blank(_wait_dialog(qtbot, window))


def test_ctrl_o_on_the_saved_screen_starts_blank(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                                 corpus_dir: Path, tmp_path: Path) -> None:
    _saved_once(qtbot, window, fake, corpus_dir, tmp_path)
    window._carry = window._last_start
    fake.open_files.append(corpus_dir / "rotate_90.pdf")
    window._open_shortcut()
    assert _is_blank(_wait_dialog(qtbot, window))


def test_same_values_carry_values_and_placement(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                                corpus_dir: Path, tmp_path: Path) -> None:
    _saved_once(qtbot, window, fake, corpus_dir, tmp_path)
    fake.open_files.append(copy_to(corpus_dir / "rotate_90.pdf", tmp_path, "second.pdf"))
    window.saved.same_button.click()  # opens the file picker at once
    d = _wait_dialog(qtbot, window)
    assert d.values() == {"identifier": CASE, "doc_type": "Court order", "doc_title": TITLE, "received": ""}
    assert (d.anchor, d.direction) == ("bottom-left", "along-right")
    assert d.anchor_buttons["bottom-left"].isChecked() and d.direction_buttons["along-right"].isChecked()
    assert not d.box_check.isChecked()
    assert d.ticked() == ["identifier", "date"]
    for name in ("identifier", "doc_type", "doc_title"):
        assert d.note(name) == CARRIED_NOTE, name
    assert d.note("received") == ""  # nothing was carried into it
    assert d.save_button.isEnabled(), d.blockers()  # carried values went through validation
    assert "Matches ##-YYYY-######." in d.message("identifier")


def test_carried_values_are_the_starting_point(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                               corpus_dir: Path, tmp_path: Path) -> None:
    """Esc right after a carried start doesn't ask, and the title doesn't say "(not saved)"."""
    _saved_once(qtbot, window, fake, corpus_dir, tmp_path)
    fake.open_files.append(corpus_dir / "rotate_90.pdf")
    window.saved.same_button.click()
    d = _wait_dialog(qtbot, window)
    assert not d.is_dirty() and "(not saved)" not in window.windowTitle()
    qtbot.keyClick(d.editor("identifier"), Qt.Key.Key_Escape)
    assert window.stack.currentWidget() is window.drop and fake.questions == []
    # Used once: the next file starts blank.
    assert _is_blank(open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf"))


def test_carried_note_clears_on_edit(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                     corpus_dir: Path, tmp_path: Path) -> None:
    _saved_once(qtbot, window, fake, corpus_dir, tmp_path)
    fake.open_files.append(corpus_dir / "rotate_90.pdf")
    window.saved.same_button.click()
    d = _wait_dialog(qtbot, window)
    type_into(qtbot, d, "doc_title", "Another synthetic title")
    choose(d, "doc_type", "Search warrant return")
    assert d.note("doc_title") == "" and d.note("doc_type") == ""
    assert d.note("identifier") == CARRIED_NOTE  # not edited: still asks to be checked


def test_file_name_value_wins_and_the_note_names_both(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                                      corpus_dir: Path, tmp_path: Path) -> None:
    _saved_once(qtbot, window, fake, corpus_dir, tmp_path)
    fake.open_files.append(copy_to(corpus_dir / "rotate_90.pdf", tmp_path, f"return_{OTHER_CASE}.pdf"))
    window.saved.same_button.click()
    d = _wait_dialog(qtbot, window)
    assert d.values()["identifier"] == OTHER_CASE
    note = d.note("identifier")
    assert OTHER_CASE in note and CASE in note and CARRIED_NOTE not in note
    assert "Taken from the file name." in d.message("identifier")
    assert d.note("doc_title") == CARRIED_NOTE


def test_file_name_with_the_same_value_needs_no_note(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                                     corpus_dir: Path, tmp_path: Path) -> None:
    _saved_once(qtbot, window, fake, corpus_dir, tmp_path)
    fake.open_files.append(copy_to(corpus_dir / "rotate_90.pdf", tmp_path, f"return_{CASE}.pdf"))
    window.saved.same_button.click()
    d = _wait_dialog(qtbot, window)
    assert d.values()["identifier"] == CASE and d.note("identifier") == ""


def test_the_drop_screen_says_values_are_waiting(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                                 corpus_dir: Path, tmp_path: Path) -> None:
    _saved_once(qtbot, window, fake, corpus_dir, tmp_path)
    window.saved.same_button.click()  # the picker is cancelled: no file given
    assert window.stack.currentWidget() is window.drop
    assert "starts with the last file's values" in window.drop.message.text()
    d = open_dialog(qtbot, window, corpus_dir / "rotate_90.pdf")  # a later drop or pick still uses them
    assert d.values()["identifier"] == CASE


def test_carried_values_reach_a_combined_file(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                              corpus_dir: Path, tmp_path: Path) -> None:
    _saved_once(qtbot, window, fake, corpus_dir, tmp_path)
    window.saved.same_button.click()
    window.open_files([copy_to(corpus_dir / n, tmp_path) for n in ("rotate_90.pdf", "mixed_sizes.pdf")])
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.files_screen, timeout=WAIT_MS)
    window.files_screen.combine_button.click()
    d = _wait_dialog(qtbot, window)
    assert isinstance(d, CombineDialog)
    assert d.values()["identifier"] == CASE and d.note("identifier") == CARRIED_NOTE


# ------------------------------------------------------------ UX-036: switching the profile


def test_one_profile_shows_a_label(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    assert d.profile_combo is None
    label = next(w for w in d.form.findChildren(QLabel) if w.text() == "Profile: Records submission")
    assert label.textFormat() == Qt.TextFormat.PlainText


def test_switch_keeps_shared_fields_and_names_the_rest(qtbot: QtBot, two: MainWindow, fake: FakeServices,
                                                       corpus_dir: Path) -> None:
    d = open_dialog(qtbot, two, corpus_dir / "letter_portrait.pdf")
    assert d.profile_combo is not None and d.profile_combo.currentText() == "Records submission"
    type_into(qtbot, d, "identifier", CASE)
    type_into(qtbot, d, "doc_title", TITLE)
    choose(d, "doc_type", "Court order")
    d.anchor_buttons["bottom-left"].click()
    wait_ready(qtbot, d)
    d.profile_combo.setCurrentIndex(d.profile_combo.findData(EVIDENCE))
    d.profile_combo.activated.emit(d.profile_combo.currentIndex())
    new = _wait_dialog(qtbot, two)
    assert new is not d and new.profile.name == EVIDENCE
    assert new.values() == {"identifier": CASE, "doc_title": TITLE}
    assert new.anchor == "bottom-left"
    assert "Document type" in new.start_label.text() and not new.start_frame.isHidden()
    assert all(new.note(n) == "" for n in new.values())  # same file: no "from the last file" note
    assert new.is_dirty() and "(not saved)" in two.windowTitle()  # what was typed is still unsaved
    # The drop screen's list and the remembered profile follow (D-081 keeps the name, never values).
    assert two.drop.profile_combo.currentText() == EVIDENCE
    assert two.profile is not None and two.profile.name == EVIDENCE
    assert two.state.last_profile == EVIDENCE
    assert d.path.name in two.windowTitle()


def test_switch_with_nothing_lost_has_no_notice(qtbot: QtBot, two: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, two, corpus_dir / "letter_portrait.pdf")
    assert d.profile_combo is not None
    d.profile_combo.activated.emit(d.profile_combo.findData(EVIDENCE))
    new = _wait_dialog(qtbot, two)
    assert new.start_frame.isHidden() and not new.is_dirty()
    qtbot.keyClick(new.editor("identifier"), Qt.Key.Key_Escape)
    assert two.stack.currentWidget() is two.drop


def test_switch_keeps_the_signature_tick(qtbot: QtBot, two: MainWindow, corpus_dir: Path, tmp_path: Path) -> None:
    d = open_dialog(qtbot, two, make_signed_pdf(corpus_dir, tmp_path / "signed.pdf"))
    assert d.signature_tick is not None
    d.signature_tick.setChecked(True)
    assert d.profile_combo is not None
    d.profile_combo.activated.emit(d.profile_combo.findData(EVIDENCE))
    new = _wait_dialog(qtbot, two)
    assert new.signature_tick is not None and new.signature_tick.isChecked()
    assert "signature confirmation" in new.start_label.text()


def test_switch_keeps_the_restriction_tick(qtbot: QtBot, two: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, two, corpus_dir / "owner_restrictions.pdf")
    assert d.restriction_tick is not None
    d.restriction_tick.setChecked(True)
    assert d.profile_combo is not None
    d.profile_combo.activated.emit(d.profile_combo.findData(EVIDENCE))
    new = _wait_dialog(qtbot, two)
    assert new.restriction_tick is not None and new.restriction_tick.isChecked()
    assert "protected file" in new.start_label.text()


def test_switch_keeps_the_permissions_password(qtbot: QtBot, two: MainWindow, fake: FakeServices,
                                               corpus_dir: Path) -> None:
    d = open_dialog(qtbot, two, corpus_dir / "owner_restrictions.pdf")
    fake.passwords.append(make_corpus.OWNER_PASSWORD)
    d._ask_owner_password()
    asked = len(fake.password_prompts)
    assert d.profile_combo is not None
    d.profile_combo.activated.emit(d.profile_combo.findData(EVIDENCE))
    new = _wait_dialog(qtbot, two)
    assert len(fake.password_prompts) == asked  # not asked again
    assert new.password == make_corpus.OWNER_PASSWORD and not new.info.restricted
    assert new.restriction_tick is None
    assert "permissions password" in new.start_label.text()


def test_switch_in_a_combined_file_keeps_it(qtbot: QtBot, two: MainWindow, corpus_dir: Path,
                                            tmp_path: Path) -> None:
    two.open_files([copy_to(corpus_dir / n, tmp_path) for n in ("rotate_90.pdf", "mixed_sizes.pdf")])
    qtbot.waitUntil(lambda: two.stack.currentWidget() is two.files_screen, timeout=WAIT_MS)
    two.files_screen.combine_button.click()
    d = _wait_dialog(qtbot, two)
    assert isinstance(d, CombineDialog) and d.profile_combo is not None
    type_into(qtbot, d, "identifier", CASE)
    combined = d.combined.path
    d.profile_combo.activated.emit(d.profile_combo.findData(EVIDENCE))
    new = _wait_dialog(qtbot, two)
    assert isinstance(new, CombineDialog) and new.combined.path == combined and combined.is_file()
    assert new.values()["identifier"] == CASE


def test_switch_in_a_batch_keeps_the_folder(qtbot: QtBot, two: MainWindow, fake: FakeServices,
                                            corpus_dir: Path, tmp_path: Path) -> None:
    two.open_files([copy_to(corpus_dir / n, tmp_path) for n in ("rotate_90.pdf", "mixed_sizes.pdf")])
    qtbot.waitUntil(lambda: two.stack.currentWidget() is two.files_screen, timeout=WAIT_MS)
    two.files_screen.batch_button.click()
    d = _wait_dialog(qtbot, two)
    assert isinstance(d, BatchDialog) and d.profile_combo is not None
    out = tmp_path / "out"
    out.mkdir()
    d._set_folder(out)
    d.profile_combo.activated.emit(d.profile_combo.findData(EVIDENCE))
    new = _wait_dialog(qtbot, two)
    assert isinstance(new, BatchDialog) and new.out_folder == out and len(new.files) == 2


# ------------------------------------------------------------ UX-035: stamped this session


def test_session_list_after_a_save(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                   corpus_dir: Path, tmp_path: Path) -> None:
    assert window.drop.session_frame.isHidden()  # empty: hidden
    out = _saved_once(qtbot, window, fake, corpus_dir, tmp_path)
    window.saved.another_button.click()
    assert not window.drop.session_frame.isHidden()
    assert [lb.text().split("  ", 1)[1] for lb in window.drop.session_labels] == ["first.pdf"]
    button = window.drop.session_buttons[0]
    assert "first.pdf" in button.accessibleName()
    button.click()
    assert fake.shown == [out]


def test_session_list_after_a_batch(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                    corpus_dir: Path, tmp_path: Path) -> None:
    files = [copy_to(corpus_dir / n, tmp_path) for n in ("rotate_90.pdf", "mixed_sizes.pdf")]
    window.open_files(files)
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.files_screen, timeout=WAIT_MS)
    window.files_screen.batch_button.click()
    d = _wait_dialog(qtbot, window)
    type_into(qtbot, d, "identifier", CASE)
    choose(d, "doc_type", "Court order")
    wait_ready(qtbot, d)
    d.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.batch_saved, timeout=WAIT_MS)
    window.batch_saved.another_button.click()
    names = [lb.text().split("  ", 1)[1] for lb in window.drop.session_labels]
    assert names == ["mixed_sizes.pdf", "rotate_90.pdf"]  # newest first


def test_session_list_caps_at_five_and_shows_hidden_characters(window: MainWindow, fake: FakeServices,
                                                               tmp_path: Path) -> None:
    for i in range(SESSION_SHOWN + 2):
        window._add_to_session(f"case_{i}​.pdf", tmp_path / f"out_{i}.pdf")
    window.show_drop()
    labels = window.drop.session_labels
    assert len(labels) == SESSION_SHOWN == len(window.session)
    assert labels[0].text().endswith(visible("case_6\u200b.pdf"))  # newest first, the hidden character shown
    assert "U+200B" in labels[0].text() and "\u200b" not in labels[0].text()
    assert labels[0].textFormat() == Qt.TextFormat.PlainText
    assert "U+200B" in window.drop.session_buttons[0].accessibleName()
    window.drop.session_buttons[-1].click()
    assert fake.shown == [tmp_path / "out_2.pdf"]


def test_session_names_are_never_read_as_markup(window: MainWindow, tmp_path: Path) -> None:
    window._add_to_session("<b>bold</b>&amp.pdf", tmp_path / "x.pdf")
    window.show_drop()
    assert window.drop.session_labels[0].text().endswith("<b>bold</b>&amp.pdf")
    assert window.drop.session_labels[0].textFormat() == Qt.TextFormat.PlainText


# ------------------------------------------------------------ D-081: nothing on disk


def test_state_json_never_holds_values_or_the_session(qtbot: QtBot, two: MainWindow, fake: FakeServices,
                                                      corpus_dir: Path, tmp_path: Path) -> None:
    d = open_dialog(qtbot, two, copy_to(corpus_dir / "letter_portrait.pdf", tmp_path, "first.pdf"))
    _fill(qtbot, d)
    _save(qtbot, two, fake, d, tmp_path)
    fake.open_files.append(copy_to(corpus_dir / "rotate_90.pdf", tmp_path, "second.pdf"))
    two.saved.same_button.click()
    d = _wait_dialog(qtbot, two)
    assert d.profile_combo is not None
    d.profile_combo.activated.emit(d.profile_combo.findData(EVIDENCE))
    d = _wait_dialog(qtbot, two)
    _save(qtbot, two, fake, d, tmp_path)
    two.saved.another_button.click()
    assert len(two.session) == 2
    text = two.locs.state_file.read_text(encoding="utf-8")
    for secret in (CASE, TITLE, "Court order", "stamped_", "first.pdf", "second.pdf", "session"):
        assert secret not in text, secret
    assert json.loads(text)["last_profile"] == EVIDENCE


# ------------------------------------------------------------ D-093: everything fits at 880 x 560


def test_saved_screen_buttons_fit_at_minimum(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                             corpus_dir: Path, tmp_path: Path) -> None:
    _saved_once(qtbot, window, fake, corpus_dir, tmp_path)
    window.resize(MINIMUM_SIZE)
    qtbot.waitUntil(lambda: window.size() == MINIMUM_SIZE)
    s = window.saved
    for b in (s.open_button, s.folder_button, s.another_button, s.same_button, s.help_button):
        top_left = b.mapTo(window, QPoint(0, 0))
        assert b.isVisible() and top_left.y() + b.height() <= window.height(), b.text()
        assert top_left.x() >= 0 and top_left.x() + b.width() <= window.width(), b.text()
        assert b.width() >= b.minimumSizeHint().width(), b.text()  # not squeezed: the whole text shows


def test_session_entry_holds_no_identifier() -> None:
    """The list keeps the name, the output and the time, and nothing else (UX-035)."""
    assert set(SessionEntry.__dataclass_fields__) == {"original", "output", "when"}
    SessionEntry("a.pdf", Path("b.pdf"), datetime(2026, 11, 15))


def test_session_buttons_are_push_buttons(window: MainWindow, tmp_path: Path) -> None:
    window._add_to_session("a.pdf", tmp_path / "b.pdf")
    window.show_drop()
    assert all(isinstance(b, QPushButton) and b.text() == "Show in folder" for b in window.drop.session_buttons)
