"""Profile settings (Phase 4): editing, checks, saving, import/export, lookup and state.

Every test works on copies in temp folders; the app and user folders are
temp folders too (conftest). Synthetic configs only.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QPushButton, QWidget
from pytestqt.qtbot import QtBot

from stamper.core.config import load_config
from stamper.core.locations import AppState, Locations, SourceKind, backup_path, load_state, save_state
from stamper.gui.app import make_app
from stamper.gui.main_window import MainWindow
from stamper.gui import profiles
from stamper.gui.profiles import ProfileSettings
from stamper.gui.theme import COLORS, STYLE_SHEET
from tests.gui_support import (  # noqa: F401
    TODAY,
    WAIT_MS,
    FakeServices,
    close_window,
    copy_to,
    fake,
    open_dialog,
)
from tests.stamping import DEFAULT_CONFIG, RECORDS_CONFIG


@pytest.fixture
def locs(tmp_path: Path) -> Locations:
    app, user = tmp_path / "app", tmp_path / "user"
    app.mkdir()
    user.mkdir()
    return Locations(app=app, user=user, builtin=DEFAULT_CONFIG)


def _window(qtbot: QtBot, fake: FakeServices, locs: Locations, **kw: object) -> MainWindow:
    make_app()
    w = MainWindow(services=fake.services(), today=TODAY, locs=locs,
                   sendto_shortcut=locs.user / "SendTo" / "Document Stamper.lnk", **kw)  # type: ignore[arg-type]
    qtbot.addWidget(w)
    w.resize(1280, 860)
    w.show()
    return w


@pytest.fixture
def records_window(qtbot: QtBot, fake: FakeServices, locs: Locations) -> Iterator[MainWindow]:
    """The window with the Records fixture as config.json next to the app."""
    shutil.copyfile(RECORDS_CONFIG, locs.app_config)
    w = _window(qtbot, fake, locs)
    yield w
    close_window(w)


def _settings(w: MainWindow) -> ProfileSettings:
    s = w.open_settings()
    assert s is not None and w.stack.currentWidget() is s
    return s


def _type(qtbot: QtBot, widget: object, text: str) -> None:
    widget.selectAll()  # type: ignore[attr-defined]
    qtbot.keyClicks(widget, text)
    qtbot.wait(200)  # past the debounce


# ------------------------------------------------------------ lookup in the window


def test_window_uses_config_next_to_the_app(records_window: MainWindow, locs: Locations) -> None:
    assert records_window.source is not None
    assert records_window.source.kind is SourceKind.APP
    assert records_window.config_path == locs.app_config
    assert "next to the app" in records_window.drop.config_label.text()


def test_window_falls_back_to_the_builtin(qtbot: QtBot, fake: FakeServices, locs: Locations) -> None:
    w = _window(qtbot, fake, locs)
    assert w.source is not None and w.source.kind is SourceKind.BUILTIN
    assert w.profile is not None and w.profile.name == "Standard"
    close_window(w)


def test_invalid_config_next_to_the_app_is_reported(qtbot: QtBot, fake: FakeServices, locs: Locations) -> None:
    locs.app_config.write_text('{"schema_version": 1}', encoding="utf-8")
    w = _window(qtbot, fake, locs)
    assert "config.json can't be used" in w.drop.message.text()
    assert not w.drop.settings_action.isEnabled()
    close_window(w)


# ------------------------------------------------------------ last-used values (D-081)


def test_last_profile_and_folder_are_remembered(qtbot: QtBot, fake: FakeServices, locs: Locations,
                                                corpus_dir: Path, tmp_path: Path) -> None:
    cfg = json.loads(RECORDS_CONFIG.read_text(encoding="utf-8"))
    second = json.loads(json.dumps(cfg["profiles"][0]))
    second.update(name="Second", default=False)
    cfg["profiles"].append(second)
    locs.app_config.write_text(json.dumps(cfg), encoding="utf-8")
    w = _window(qtbot, fake, locs)
    w.drop.profile_combo.setCurrentIndex(1)
    w.drop.profile_combo.activated.emit(1)
    work = tmp_path / "work"
    work.mkdir()
    pdf = copy_to(corpus_dir / "letter_portrait.pdf", work)
    open_dialog(qtbot, w, pdf)
    close_window(w)
    assert load_state(locs) == AppState("Second", str(work), str(locs.app_config))  # last_config (D-121)
    assert "99-2026" not in locs.state_file.read_text(encoding="utf-8")  # no field values

    w2 = _window(qtbot, fake, locs)
    assert w2.profile is not None and w2.profile.name == "Second"
    assert w2._last_folder == work
    close_window(w2)


def test_remembered_profile_that_no_longer_exists_is_ignored(qtbot: QtBot, fake: FakeServices,
                                                             locs: Locations) -> None:
    save_state(AppState("Gone"), locs)
    w = _window(qtbot, fake, locs)
    assert w.profile is not None and w.profile.name == "Standard"
    close_window(w)


# ------------------------------------------------------------ opening and leaving


def test_settings_open_on_profiles(records_window: MainWindow) -> None:
    s = _settings(records_window)
    assert s.nav.currentRow() == 0
    assert s.profiles_page.name.text() == "Records submission"
    assert not s.can_save()  # nothing changed yet
    assert not s.banner.isVisible()


def test_cancel_without_changes_goes_back(records_window: MainWindow, fake: FakeServices) -> None:
    s = _settings(records_window)
    s.cancel()
    assert records_window.stack.currentWidget() is records_window.drop
    assert fake.questions == []


def test_cancel_with_changes_asks_first(qtbot: QtBot, records_window: MainWindow, fake: FakeServices,
                                        locs: Locations) -> None:
    s = _settings(records_window)
    _type(qtbot, s.profiles_page.name, "Renamed")
    before = locs.app_config.read_bytes()
    fake.confirms = [False]
    s.cancel()
    assert records_window.stack.currentWidget() is s  # stayed
    fake.confirms = [True]
    s.cancel()
    assert records_window.stack.currentWidget() is records_window.drop
    assert locs.app_config.read_bytes() == before
    assert [q[0] for q in fake.questions] == ["Discard changes?", "Discard changes?"]


def test_escape_cancels(qtbot: QtBot, records_window: MainWindow) -> None:
    s = _settings(records_window)
    s.nav.setFocus()
    qtbot.keyClick(s.nav, Qt.Key.Key_Escape)
    assert records_window.stack.currentWidget() is records_window.drop


def test_drop_is_ignored_while_settings_are_open(records_window: MainWindow) -> None:
    _settings(records_window)
    assert records_window.stack.currentWidget() is records_window.settings


# ------------------------------------------------------------ saving


def test_rename_profile_and_save(fake: FakeServices, qtbot: QtBot, records_window: MainWindow, locs: Locations) -> None:
    s = _settings(records_window)
    _type(qtbot, s.profiles_page.name, "Records")
    assert s.can_save()
    fake.confirms.append(True)
    s.save()
    assert load_config(locs.app_config).default_profile.name == "Records"
    assert load_config(backup_path(locs.app_config)).default_profile.name == "Records submission"
    w = records_window
    assert w.stack.currentWidget() is w.drop
    assert "Profile settings saved" in w.drop.message.text()
    assert w.profile is not None and w.profile.name == "Records"
    assert w.drop.profile_combo.currentText() == "Records"


def test_save_refuses_a_config_someone_else_saved(fake: FakeServices, qtbot: QtBot, records_window: MainWindow,
                                                  locs: Locations) -> None:
    s = _settings(records_window)
    _type(qtbot, s.profiles_page.name, "Mine")
    theirs = load_config(RECORDS_CONFIG).to_json().replace("Records submission", "Theirs")
    locs.app_config.write_text(theirs, encoding="utf-8")  # a colleague saved meanwhile
    fake.confirms.append(True)
    s.save()
    assert locs.app_config.read_text(encoding="utf-8") == theirs
    assert records_window.stack.currentWidget() is s and "changed by someone else" in s.status.text()


def test_problem_blocks_save_and_is_listed(qtbot: QtBot, records_window: MainWindow) -> None:
    s = _settings(records_window)
    ed = s.profiles_page.stamp_editors[0]
    _type(qtbot, ed.text, "{identifier} {nope}")
    assert not s.can_save()
    assert "uses {nope}" in s.problem_label.text()
    _type(qtbot, ed.text, "{identifier}  |  Page {page} of {total}")
    assert s.can_save()
    assert not s.problem_label.isVisible()


def test_ctrl_s_saves(fake: FakeServices, qtbot: QtBot, records_window: MainWindow, locs: Locations) -> None:
    s = _settings(records_window)
    _type(qtbot, s.profiles_page.name, "Ctrl S")
    fake.confirms.append(True)
    qtbot.keyClick(s.profiles_page.name, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert fake.questions[-1][0] == "Save changes?"  # Ctrl+S asks like the button
    assert load_config(locs.app_config).default_profile.name == "Ctrl S"


def test_builtin_saves_next_to_the_app(qtbot: QtBot, fake: FakeServices, locs: Locations) -> None:
    w = _window(qtbot, fake, locs)
    s = _settings(w)
    assert s.banner.isVisible() and "Saving creates" in s.banner_text.text()
    assert s.can_save()  # saving the built-in as your own needs no edit
    fake.confirms.append(True)
    s.save()
    assert locs.app_config.is_file()
    assert w.source is not None and w.source.kind is SourceKind.APP
    assert DEFAULT_CONFIG.read_bytes() == (Path(DEFAULT_CONFIG)).read_bytes()  # untouched
    close_window(w)


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="POSIX permissions, not as root")
def test_read_only_config_offers_export_only(qtbot: QtBot, fake: FakeServices, locs: Locations,
                                             tmp_path: Path) -> None:
    shutil.copyfile(RECORDS_CONFIG, locs.app_config)
    locs.app_config.chmod(stat.S_IREAD)
    w = _window(qtbot, fake, locs)
    s = _settings(w)
    assert s.banner.isVisible() and "can't be changed here" in s.banner_text.text()
    _type(qtbot, s.profiles_page.name, "Mine")
    assert not s.can_save()
    out = tmp_path / "mine.json"
    fake.export_paths = [out]
    s.export_config()
    assert load_config(out).default_profile.name == "Mine"
    close_window(w)


# ------------------------------------------------------------ profiles page


def test_add_profile_and_make_it_default(fake: FakeServices, records_window: MainWindow, locs: Locations) -> None:
    s = _settings(records_window)
    page = s.profiles_page
    page.master.buttons["Add"].click()
    assert page.name.text() == "New profile"
    page.master.buttons["Make default"].click()
    assert "(default)" in page.master.list.item(1).text()
    fake.confirms.append(True)
    s.save()
    cfg = load_config(locs.app_config)
    assert [p.name for p in cfg.profiles] == ["Records submission", "New profile"]
    assert cfg.default_profile.name == "New profile"


def test_delete_last_profile_is_refused(records_window: MainWindow) -> None:
    s = _settings(records_window)
    s.profiles_page.master.buttons["Delete"].click()
    assert "at least one profile" in s.status.text()
    assert len(s.draft.profiles) == 1


def test_untick_a_field_drops_it_from_the_file_name(records_window: MainWindow, locs: Locations) -> None:
    s = _settings(records_window)
    page = s.profiles_page
    page.fields.set_ticked("doc_title", False)
    assert s.draft.profiles[0]["fields"] == ["identifier", "doc_type", "received"]
    page.fields.set_ticked("doc_type", False)
    # doc_type is in the stamp text, so that's now a problem; the file name lost it already.
    assert s.draft.profiles[0]["output_name"]["fields"] == ["identifier"]
    assert not s.can_save() and "uses {doc_type}" in s.problem_label.text()


def test_reorder_fields(records_window: MainWindow) -> None:
    s = _settings(records_window)
    lst = s.profiles_page.fields
    lst.list.setCurrentRow(1)
    lst.down.click()
    assert s.draft.profiles[0]["fields"] == ["identifier", "doc_title", "doc_type", "received"]


def test_stamp_example_line(qtbot: QtBot, records_window: MainWindow) -> None:
    s = _settings(records_window)
    ed = s.profiles_page.stamp_editors[0]
    assert ed.example.text() == "Example: 12-2026-345678  |  Search warrant return  |  Page 1 of 3"
    _type(qtbot, ed.text, "{identifier}  |  [Title: {doc_title}]")
    assert ed.example.text() == "Example: 12-2026-345678  |  Title: Document title"


def test_insert_menu_adds_tokens(records_window: MainWindow) -> None:
    s = _settings(records_window)
    ed = s.profiles_page.stamp_editors[0]
    ed.text.setCursorPosition(len(ed.text.text()))
    actions = {a.text(): a for a in ed.insert.menu().actions() if a.text()}
    actions["Date received  {received}"].trigger()
    assert s.draft.profiles[0]["stamps"][0]["text"].endswith("{received}")
    assert "Today's date  {date}" in actions and "Optional part  [ ]" in actions


def test_add_a_bottom_stamp(fake: FakeServices, qtbot: QtBot, records_window: MainWindow, locs: Locations) -> None:
    s = _settings(records_window)
    s.profiles_page.form.itemAt(0)  # page is built
    add = next(b for b in s.profiles_page.detail.findChildren(type(s.save_button)) if b.text() == "Add a stamp")
    add.click()
    assert len(s.profiles_page.stamp_editors) == 2
    assert s.profiles_page.stamp_editors[1].anchor.currentData() == "bottom-right"
    fake.confirms.append(True)
    s.save()
    assert len(load_config(locs.app_config).default_profile.stamps) == 2


def test_file_name_example(records_window: MainWindow) -> None:
    s = _settings(records_window)
    assert s.profiles_page.name_example.text() == (
        "Example: 12-2026-345678_Search-warrant-return_provider_return.pdf")


def test_duplicate_metadata_name_blocks_save(qtbot: QtBot, records_window: MainWindow) -> None:
    s = _settings(records_window)
    page = s.profiles_page
    page._add_meta_row("", "")
    k, v = page.meta[-1]
    k.setText("CaseNumber")
    k.textEdited.emit("CaseNumber")
    qtbot.wait(200)
    assert not s.can_save() and "listed twice" in s.problem_label.text()


def test_preview_renders(qtbot: QtBot, records_window: MainWindow) -> None:
    s = _settings(records_window)
    qtbot.waitUntil(s.preview_ready, timeout=WAIT_MS)


def test_preview_says_why_it_is_missing(qtbot: QtBot, records_window: MainWindow) -> None:
    s = _settings(records_window)
    _type(qtbot, s.profiles_page.stamp_editors[0].text, "{nope}")
    qtbot.wait(400)
    assert "problems below are fixed" in s.profiles_page.preview.text()


# ------------------------------------------------------------ fields page


def test_relabel_identifier_keeps_its_key(fake: FakeServices, qtbot: QtBot, records_window: MainWindow,
                                          locs: Locations) -> None:
    s = _settings(records_window)
    s.show_section(1)
    page = s.fields_page
    page.select_key("identifier")
    assert not page.type_combo.isEnabled()
    _type(qtbot, page.label_edit, "Report number")
    fake.confirms.append(True)
    s.save()
    cfg = load_config(locs.app_config)
    assert cfg.fields["identifier"].label == "Report number"


def test_rule_explanation_and_bad_rule(qtbot: QtBot, records_window: MainWindow) -> None:
    s = _settings(records_window)
    s.show_section(1)
    page = s.fields_page
    page.select_key("identifier")
    ed = page.format_editors[1]
    assert ed.explanation.text().startswith('"SO", then 2-digit year')
    _type(qtbot, ed.rule, "SOYYYMMDD")
    assert ed.explanation.objectName() == "msgBad"
    assert not s.can_save()


def test_try_a_value_and_a_file_name(qtbot: QtBot, records_window: MainWindow) -> None:
    s = _settings(records_window)
    s.show_section(1)
    page = s.fields_page
    page.select_key("identifier")
    assert page.try_value is not None and page.try_file is not None
    _type(qtbot, page.try_value, "so261017-000412")
    assert page.try_value_result.text() == (
        "Matches SOYYMMDD-######, dated October 17, 2026. Saved as SO261017-000412.")
    _type(qtbot, page.try_value, "SO261317-000412")
    assert page.try_value_result.objectName() == "msgBad"
    _type(qtbot, page.try_file, "SO261017-000412_return.pdf")
    assert page.try_file_result.text() == "Would fill in SO261017-000412."


def test_test_box_follows_unsaved_rule_edits(qtbot: QtBot, records_window: MainWindow) -> None:
    s = _settings(records_window)
    s.show_section(1)
    page = s.fields_page
    page.select_key("identifier")
    page._add_format()
    _type(qtbot, page.format_editors[-1].rule, "@@-####")
    _type(qtbot, page.try_value, "ab-1234")
    assert page.try_value_result.text().startswith("Matches @@-####")


def test_advanced_regex_rule(fake: FakeServices, qtbot: QtBot, records_window: MainWindow, locs: Locations) -> None:
    s = _settings(records_window)
    s.show_section(1)
    page = s.fields_page
    page.select_key("identifier")
    page._add_format()
    ed = page.format_editors[-1]
    ed.regex_box.setChecked(True)
    _type(qtbot, ed.rule, r"CASE-\d{4}")
    assert "Regular expression" in ed.explanation.text()
    fake.confirms.append(True)
    s.save()
    fmt = load_config(locs.app_config).fields["identifier"].formats[-1]
    assert fmt.regex == r"CASE-\d{4}" and fmt.pattern is None


def test_add_field_then_use_it(fake: FakeServices, qtbot: QtBot, records_window: MainWindow, locs: Locations) -> None:
    s = _settings(records_window)
    s.show_section(1)
    page = s.fields_page
    page.master.buttons["Add field"].click()
    _type(qtbot, page.label_edit, "Reviewer")
    assert "{new_field}" in page.form.itemAt(2).widget().text()  # the key was made from "New field"
    s.show_section(0)
    s.profiles_page.fields.set_ticked("new_field", True)
    fake.confirms.append(True)
    s.save()
    cfg = load_config(locs.app_config)
    assert cfg.fields["new_field"].label == "Reviewer"
    assert "new_field" in cfg.default_profile.fields


def test_delete_field_in_use_is_refused(records_window: MainWindow) -> None:
    s = _settings(records_window)
    s.show_section(1)
    s.fields_page.select_key("doc_title")
    s.fields_page.master.buttons["Delete"].click()
    assert 'is used by "Records submission"' in s.status.text()
    assert "doc_title" in s.draft.fields


def test_change_type_to_choice(records_window: MainWindow) -> None:
    s = _settings(records_window)
    s.show_section(1)
    page = s.fields_page
    page.select_key("doc_title")
    page.type_combo.setCurrentIndex(page.type_combo.findData("choice"))
    assert s.draft.fields["doc_title"]["taxonomy"] == "doc_types"
    assert page.taxonomy.currentData() == "doc_types"
    assert s.can_save()


# ------------------------------------------------------------ lists page


def test_rename_list_and_edit_choices(fake: FakeServices, records_window: MainWindow, locs: Locations) -> None:
    s = _settings(records_window)
    s.show_section(2)
    page = s.lists_page
    page.name_edit.setText("Document types")
    page.name_edit.editingFinished.emit()
    page._add_item()
    page.items.item(page.items.count() - 1).setText("Evidence log")
    fake.confirms.append(True)
    s.save()
    cfg = load_config(locs.app_config)
    assert cfg.taxonomies["Document types"][-1] == "Evidence log"
    assert cfg.fields["doc_type"].taxonomy == "Document types"


def test_emptied_list_is_a_problem(records_window: MainWindow) -> None:
    s = _settings(records_window)
    s.show_section(2)
    page = s.lists_page
    while page.items.count():
        page.items.setCurrentRow(0)
        page._remove_item()
    assert not s.can_save() and "the list is empty" in s.problem_label.text()


# ------------------------------------------------------------ general page


def test_date_format(fake: FakeServices, qtbot: QtBot, records_window: MainWindow, locs: Locations) -> None:
    s = _settings(records_window)
    s.show_section(3)
    page = s.general_page
    assert page.date_example.text() == "Today is written 10-18-2026."
    _type(qtbot, page.date_format, "YYYY-MM-DD")
    assert page.date_example.text() == "Today is written 2026-10-18."
    fake.confirms.append(True)
    s.save()
    assert load_config(locs.app_config).settings.date_format == "YYYY-MM-DD"


def test_bad_date_format(qtbot: QtBot, records_window: MainWindow) -> None:
    s = _settings(records_window)
    s.show_section(3)
    _type(qtbot, s.general_page.date_format, "MM-DD")
    assert s.general_page.date_example.objectName() == "msgBad"
    assert not s.can_save()


def test_sendto_status_shown(records_window: MainWindow) -> None:
    s = _settings(records_window)
    s.show_section(3)
    assert "isn't in the Send to menu" in s.general_page.sendto_status.text()
    assert s.general_page.sendto_add.isEnabled() == (os.name == "nt")


# ------------------------------------------------------------ import and export


def test_import_replaces_after_confirm_and_save(qtbot: QtBot, fake: FakeServices, locs: Locations) -> None:
    w = _window(qtbot, fake, locs)  # built-in default
    s = _settings(w)
    s.show_section(3)
    fake.import_files = [RECORDS_CONFIG]
    fake.confirms = [True]
    s.general_page.import_button.click()
    title, text = fake.questions[-1]
    assert title == "Import this config?"
    assert "Profiles added: Records submission" in text and "Nothing changes on disk" in text
    assert not locs.app_config.exists()  # not until Save
    fake.confirms.append(True)
    s.save()
    assert load_config(locs.app_config) == load_config(RECORDS_CONFIG)
    assert w.profile is not None and w.profile.name == "Records submission"
    close_window(w)


def test_import_declined_changes_nothing(records_window: MainWindow, fake: FakeServices) -> None:
    s = _settings(records_window)
    fake.import_files = [DEFAULT_CONFIG]
    fake.confirms = [False]
    s.import_config()
    assert not s.draft.dirty


def test_import_of_a_bad_file_says_why(records_window: MainWindow, fake: FakeServices, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    s = _settings(records_window)
    fake.import_files = [bad]
    s.import_config()
    assert "bad.json can't be imported" in s.status.text()
    assert fake.questions == []


def test_export_writes_a_copy(qtbot: QtBot, records_window: MainWindow, fake: FakeServices,
                              locs: Locations, tmp_path: Path) -> None:
    s = _settings(records_window)
    _type(qtbot, s.profiles_page.name, "Exported")
    out = tmp_path / "copy.json"
    fake.export_paths = [out]
    s.export_config()
    assert load_config(out).default_profile.name == "Exported"
    assert load_config(locs.app_config).default_profile.name == "Records submission"  # not saved
    assert "Exported to" in s.status.text()


def test_export_onto_itself_is_refused(records_window: MainWindow, fake: FakeServices, locs: Locations) -> None:
    s = _settings(records_window)
    fake.export_paths = [locs.app_config]
    s.export_config()
    assert "use Save changes" in s.status.text()


# ------------------------------------------------------------ portability (exit check, automated half)


def test_app_folder_copied_elsewhere_keeps_its_profiles(qtbot: QtBot, fake: FakeServices,
                                                         tmp_path: Path) -> None:
    """Save in one app folder, copy the folder, open it with a different user folder."""
    usb = tmp_path / "usb" / "DocumentStamper"
    usb.mkdir(parents=True)
    first = Locations(app=usb, user=tmp_path / "pc1", builtin=DEFAULT_CONFIG)
    w = _window(qtbot, fake, first)
    s = _settings(w)
    fake.import_files = [RECORDS_CONFIG]
    fake.confirms = [True]
    s.import_config()
    fake.confirms.append(True)
    s.save()
    close_window(w)
    moved = tmp_path / "other-pc" / "E" / "DocumentStamper"
    shutil.copytree(usb, moved)
    second = Locations(app=moved, user=tmp_path / "pc2", builtin=DEFAULT_CONFIG)
    w2 = _window(qtbot, fake, second)
    assert w2.source is not None and w2.source.kind is SourceKind.APP
    assert w2.config == load_config(RECORDS_CONFIG)
    close_window(w2)


@pytest.mark.parametrize(
    ("raw", "plain"),
    [
        ('profiles[0] "R": stamps[0] text uses {bad}, which', 'Profile "R": stamp 1 text uses {bad}, which'),
        ('profiles[0] "R": stamps[1].margin_in: is 0.1 inches', 'Profile "R": stamp 2 margin: is 0.1 inches'),
        ('profiles[1] "X": output_name.fields lists foo', 'Profile "X": file name lists foo'),
        ("taxonomies.doc_types: the list is empty", 'List "doc_types": the list is empty'),
        ('settings.date_format: "MM-DD" isn\'t usable', 'Date format: "MM-DD" isn\'t usable'),
    ],
)
def test_plain_problem(raw: str, plain: str) -> None:
    from stamper.gui.profiles import plain_problem

    assert plain_problem(raw) == plain


def test_closing_the_window_with_unsaved_settings_asks(qtbot: QtBot, records_window: MainWindow,
                                                       fake: FakeServices) -> None:
    s = _settings(records_window)
    _type(qtbot, s.profiles_page.name, "Renamed")
    fake.confirms = [False]
    records_window.close()
    assert records_window.isVisible() and records_window.settings is s  # kept editing
    assert [q[0] for q in fake.questions] == ["Discard changes?"]
    fake.confirms = [True]
    records_window.close()
    assert not records_window.isVisible()


def test_a_file_still_opening_doesnt_replace_settings(qtbot: QtBot, records_window: MainWindow,
                                                      corpus_dir: Path, tmp_path: Path) -> None:
    w = records_window
    w.open_file(copy_to(corpus_dir / "letter_portrait.pdf", tmp_path))
    s = _settings(w)  # before the inspect result arrives
    qtbot.wait(1500)  # long enough for the inspect to finish
    assert w.stack.currentWidget() is s and w.dialog is None


# ------------------------------------------------------------ undo, wheel, Delete (UX-002, UX-003, UX-013)


def _wheel(widget: QWidget) -> QWheelEvent:
    """Send one notch of the mouse wheel to a widget, as if the pointer were over it."""
    pos = QPointF(widget.width() / 2, widget.height() / 2)
    event = QWheelEvent(pos, QPointF(widget.mapToGlobal(pos.toPoint())), QPoint(0, 0), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase,
                        False)
    QApplication.sendEvent(widget, event)
    return event


def test_delete_field_then_undo_restores_it(records_window: MainWindow) -> None:
    s = _settings(records_window)
    s.show_section(1)
    page = s.fields_page
    page.master.buttons["Add field"].click()
    before = copy.deepcopy(s.draft.data)
    page.master.buttons["Delete"].click()
    assert "new_field" not in s.draft.fields
    # The message outlives the changed() the delete makes, with Undo beside it.
    assert s.status.text() == 'Deleted the field "New field".'
    assert s.status_box.isVisible() and s.undo_button.isVisible()
    s.undo_button.click()
    assert s.draft.data == before
    assert "New field" in [page.master.list.item(i).text() for i in range(page.master.list.count())]
    assert s.status.text() == 'Restored "New field".'
    assert not s.undo_button.isVisible() and not s.draft.can_undo


def test_undo_is_offered_only_until_the_next_edit(records_window: MainWindow) -> None:
    """After a delete and another edit, Undo is gone from the screen. The draft's snapshot
    would put back the whole draft as it was before the delete, the later edit included,
    which is why the screen doesn't offer it any more."""
    s = _settings(records_window)
    s.show_section(1)
    page = s.fields_page
    page.master.buttons["Add field"].click()
    before = copy.deepcopy(s.draft.data)
    page.master.buttons["Delete"].click()
    page.select_key("doc_title")
    page.required.toggle()  # another edit
    assert s.status.text() == "" and not s.undo_button.isVisible()
    assert s.draft.fields["doc_title"]["required"] != before["fields"]["doc_title"]["required"]
    s.undo()  # still possible in code: back to the snapshot, the later edit with it
    assert s.draft.data == before


def test_delete_profile_message_and_undo(records_window: MainWindow) -> None:
    s = _settings(records_window)
    page = s.profiles_page
    page.master.buttons["Add"].click()
    s.draft.profiles[1]["name"] = "\u202e<b>x</b>" + "n" * 200  # as a hand-edited config.json could hold
    page.rebuild()
    before = copy.deepcopy(s.draft.data)
    page.master.buttons["Delete"].click()
    msg = s.status.text()
    assert s.status.textFormat() == Qt.TextFormat.PlainText
    assert msg.startswith('Deleted the profile "⟨U+202E⟩<b>x</b>nnn') and msg.endswith('…".') and len(msg) < 120
    assert s.undo_button.isVisible()
    s.undo_button.click()
    assert s.draft.data == before
    assert len(s.draft.profiles) == 2


def test_refused_delete_offers_no_undo(records_window: MainWindow) -> None:
    s = _settings(records_window)
    s.profiles_page.master.buttons["Delete"].click()
    assert "at least one profile" in s.status.text()
    assert not s.undo_button.isVisible()


def test_each_remove_button_can_be_undone(qtbot: QtBot, records_window: MainWindow) -> None:
    s = _settings(records_window)
    page = s.profiles_page
    # A stamp
    page._add_stamp()
    before = copy.deepcopy(s.draft.data)
    page.stamp_editors[1].remove.click()
    assert s.status.text() == "Removed stamp 2." and s.undo_button.isVisible()
    s.undo_button.click()
    assert s.draft.data == before and s.status.text() == "Restored stamp 2."
    # A document property
    page._add_meta_row("case", "{identifier}")
    page._meta_changed()
    before = copy.deepcopy(s.draft.data)
    rm = page.meta_rows.itemAt(page.meta_rows.count() - 1).layout().itemAt(2).widget()
    rm.click()
    assert s.status.text() == 'Removed the property "case".' and s.undo_button.isVisible()
    s.undo_button.click()
    assert s.draft.data == before and [k.text() for k, _ in page.meta] == list(before["profiles"][0]["metadata"])
    # A rule
    s.show_section(1)
    fields = s.fields_page
    fields.select_key("identifier")
    before = copy.deepcopy(s.draft.data)
    fields.format_editors[0]._remove()
    assert s.status.text().startswith("Removed rule 1 from ") and s.undo_button.isVisible()
    s.undo_button.click()
    assert s.draft.data == before and len(fields.format_editors) == len(before["fields"]["identifier"]["formats"])
    # A choice
    s.show_section(2)
    lists = s.lists_page
    lists.items.setCurrentRow(0)
    first = lists.items.item(0).text()
    before = copy.deepcopy(s.draft.data)
    lists._remove_item()
    assert s.status.text() == f'Removed the choice "{first}".' and s.undo_button.isVisible()
    s.undo_button.click()
    assert s.draft.data == before and lists.items.item(0).text() == first


def test_type_change_that_drops_rules_says_so_and_can_be_undone(records_window: MainWindow) -> None:
    s = _settings(records_window)
    s.show_section(1)
    page = s.fields_page
    page.master.buttons["Add field"].click()
    spec = s.draft.fields["new_field"]
    spec["formats"] = [{"name": None, "pattern": "##", "regex": None},
                       {"name": None, "pattern": "###", "regex": None}]
    spec["from_filename"] = True
    page.rebuild_detail()
    before = copy.deepcopy(s.draft.data)
    page.type_combo.setCurrentIndex(page.type_combo.findData("date"))
    assert s.draft.fields["new_field"]["formats"] == [] and not s.draft.fields["new_field"]["from_filename"]
    assert s.status.text() == ('Changed "New field" to Date. 2 rules were removed. '
                               '"Fill in from the file name" was turned off.')
    assert s.undo_button.isVisible()
    s.undo_button.click()
    assert s.draft.data == before
    assert page.type_combo.currentData() == "text" and len(page.format_editors) == 2
    assert page.from_filename.isChecked()
    assert s.status.text() == 'Restored "New field".'


def test_type_change_that_drops_nothing_says_nothing(records_window: MainWindow) -> None:
    s = _settings(records_window)
    s.show_section(1)
    page = s.fields_page
    page.select_key("doc_title")
    page.type_combo.setCurrentIndex(page.type_combo.findData("date"))
    assert s.status.text() == "" and not s.undo_button.isVisible() and not s.draft.can_undo


def _page_scrolls(page: QWidget) -> None:
    """A wheel notch on the page itself, as when it's scrolling."""
    area = page.detail.parentWidget().parentWidget()  # type: ignore[attr-defined]
    _wheel(area.viewport())


def _page_still(monkeypatch: pytest.MonkeyPatch) -> None:
    """The page hasn't scrolled for longer than the latch."""
    monkeypatch.setattr(profiles, "_page_scrolled_at", 0.0)


def test_scroll_already_moving_the_page_passes_over_a_combo(records_window: MainWindow) -> None:
    s = _settings(records_window)
    s.show_section(1)
    page = s.fields_page
    page.select_key("doc_title")
    combo = page.type_combo
    assert combo.focusPolicy() == Qt.FocusPolicy.StrongFocus
    combo.setFocus()  # focus doesn't matter: it kept focus after use and still changed (Windows touchpad)
    for _ in range(3):  # the page was scrolling, then the combo slid under the pointer
        _page_scrolls(page)
        event = _wheel(combo)
        assert not event.isAccepted()  # passed on, so the page goes on scrolling
    assert combo.currentData() == "text" and s.draft.fields["doc_title"]["type"] == "text"


def test_scroll_that_starts_on_a_combo_changes_it(records_window: MainWindow,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(records_window)
    s.show_section(1)
    page = s.fields_page
    page.select_key("doc_title")
    _page_still(monkeypatch)
    event = _wheel(page.type_combo)
    assert event.isAccepted() and s.draft.fields["doc_title"]["type"] != "text"
    _wheel(page.type_combo)  # the same scroll goes on changing it: the page didn't get it
    assert s.draft.fields["doc_title"]["type"] == "date"


def test_scroll_passes_over_a_number_box_at_its_limit(records_window: MainWindow,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(records_window)
    page = s.profiles_page
    margin = page.stamp_editors[0].margin
    assert margin.focusPolicy() == Qt.FocusPolicy.StrongFocus
    _page_scrolls(page)
    event = _wheel(margin)  # 0.25 in is the minimum; a plain box took this and the page stopped
    assert not event.isAccepted()
    _page_still(monkeypatch)
    size = page.stamp_editors[0].size
    before = size.value()
    _wheel(size)  # one notch down, started on the box
    assert size.value() < before


def test_delete_is_a_danger_button_on_its_own_row(records_window: MainWindow) -> None:
    s = _settings(records_window)
    for section, page in ((0, s.profiles_page), (1, s.fields_page), (2, s.lists_page)):
        s.show_section(section)
        QApplication.processEvents()
        delete = page.master.buttons["Delete"]
        others = [b for t, b in page.master.buttons.items() if t != "Delete"]
        assert delete.objectName() == "danger"
        assert all(b.objectName() == "small" for b in others)
        assert delete.y() - max(b.geometry().bottom() + 1 for b in others) >= 16
        assert delete.height() == others[0].height()


def test_danger_color_contrast() -> None:
    def lum(hex_color: str) -> float:
        rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    assert (1.05) / (lum(COLORS["danger"]) + 0.05) >= 4.5
    assert "QPushButton#danger" in STYLE_SHEET


# ------------------------------------------------------------ save asks, backups, restore, paste (UX-022/023/027/033)


def test_save_asks_and_lists_a_stamp_text_change(qtbot: QtBot, records_window: MainWindow, fake: FakeServices,
                                                 locs: Locations) -> None:
    s = _settings(records_window)
    _type(qtbot, s.profiles_page.stamp_editors[0].text, "{identifier}  |  Pg {page}")
    fake.confirms.append(True)
    s.save()
    title, text = fake.questions[-1]
    assert title == "Save changes?"
    assert "• Stamp text changed: Records submission" in text
    assert profiles.SHARED_COPY_NOTE in text  # config.json next to the app
    assert load_config(locs.app_config).default_profile.stamps[0].text == "{identifier}  |  Pg {page}"


def test_declining_the_save_writes_nothing(qtbot: QtBot, records_window: MainWindow, fake: FakeServices,
                                           locs: Locations) -> None:
    before = locs.app_config.read_bytes()
    s = _settings(records_window)
    _type(qtbot, s.profiles_page.name, "Not saved")
    s.save()  # fake.confirms is empty: Cancel
    assert fake.questions and fake.questions[-1][0] == "Save changes?"
    assert locs.app_config.read_bytes() == before
    assert sorted(p.name for p in locs.app.iterdir()) == ["config.json"]  # no backups either
    assert records_window.stack.currentWidget() is s and s.draft.dirty


def test_user_folder_config_has_no_shared_folder_line(qtbot: QtBot, fake: FakeServices, locs: Locations) -> None:
    shutil.copyfile(RECORDS_CONFIG, locs.user_config)
    w = _window(qtbot, fake, locs)
    s = _settings(w)
    assert s.target is not None and s.target.kind is SourceKind.USER
    _type(qtbot, s.profiles_page.name, "Mine")
    fake.confirms.append(True)
    s.save()
    assert "Profiles added: Mine" in fake.questions[-1][1]
    assert profiles.SHARED_COPY_NOTE not in fake.questions[-1][1]
    close_window(w)


def test_names_in_the_save_summary_are_shown_as_text(records_window: MainWindow, fake: FakeServices) -> None:
    s = _settings(records_window)
    name = "<b>x</b>\u202e"
    s.profiles_page.name.setText(name)
    s.profiles_page.name.textEdited.emit(name)
    s.changed()
    s.save()
    text = fake.questions[-1][1]
    assert "Profiles added: <b>x</b>⟨U+202E⟩" in text  # literal, hidden character shown


def test_confirm_box_is_plain_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtWidgets import QMessageBox
    from stamper.gui import services
    make_app()
    seen: list[QMessageBox] = []
    monkeypatch.setattr(QMessageBox, "exec", lambda box: seen.append(box) or 0)
    assert services._confirm(None, "Save changes?", "• Profiles added: <b>x</b>", "Save changes") is False
    box = seen[0]
    assert box.textFormat() == Qt.TextFormat.PlainText
    assert box.text() == "• Profiles added: <b>x</b>"
    assert box.defaultButton().text() == "Cancel"


def test_summary_is_capped() -> None:
    out = profiles._summary([f"line {i}" for i in range(25)])
    assert out.count("• ") == profiles.MAX_SUMMARY_LINES and out.endswith("…and 5 more.")
    assert len(profiles._summary(["y" * 1000])) == len("• ") + profiles.MAX_SUMMARY_LINE


def test_restore_fills_the_draft_and_writes_nothing(records_window: MainWindow, fake: FakeServices,
                                                    locs: Locations) -> None:
    old = locs.app.joinpath("config.json.20261101-101010.bak")
    shutil.copyfile(DEFAULT_CONFIG, old)
    listing = sorted(p.name for p in locs.app.iterdir())
    before = locs.app_config.read_bytes()
    s = _settings(records_window)
    s.show_section(3)
    fake.import_files = [old]
    fake.confirms = [True]
    s.general_page.restore_button.click()
    start, name_filter = fake.import_prompts[-1]
    assert start == locs.app
    assert name_filter == "Backups of config.json (config.json.bak config.json.*.bak)"
    assert "Profiles added: Standard" in fake.questions[-1][1]  # shown as a diff and confirmed
    assert [p["name"] for p in s.draft.profiles] == ["Standard"] and s.draft.dirty
    assert locs.app_config.read_bytes() == before
    assert sorted(p.name for p in locs.app.iterdir()) == listing


def test_restore_refuses_an_invalid_backup(records_window: MainWindow, fake: FakeServices, locs: Locations) -> None:
    bad = locs.app.joinpath("config.json.bak")
    bad.write_text("{nope", encoding="utf-8")
    s = _settings(records_window)
    fake.import_files = [bad]
    s.restore_backup()
    assert "config.json.bak can't be imported" in s.status.text()
    assert "this isn't valid JSON" in s.status.text()
    assert fake.questions == [] and not s.draft.dirty


def test_fill_in_warning_follows_the_rules(qtbot: QtBot, records_window: MainWindow) -> None:
    s = _settings(records_window)
    s.show_section(1)
    page = s.fields_page
    page.select_key("identifier")
    assert page.no_usable_rule is not None and page.no_usable_rule.isHidden()  # simple patterns
    for only in ({"name": None, "pattern": "*", "regex": None},
                 {"name": None, "pattern": None, "regex": "[A-Z]{2}[0-9]+"}):
        s.draft.fields["identifier"]["formats"] = [dict(only)]
        page.rebuild_detail()
        s.changed()
        assert not page.no_usable_rule.isHidden(), only
        assert "Add a rule without *" in page.no_usable_rule.text()
    page._add_format()
    _type(qtbot, page.format_editors[-1].rule, "##-YYYY-######")
    assert s.valid_config is not None and page.no_usable_rule.isHidden()
    page.from_filename.setChecked(False)  # unticked: nothing to warn about
    s.draft.fields["identifier"]["formats"] = [{"name": None, "pattern": "*", "regex": None}]
    page.rebuild_detail()
    s.changed()
    assert page.no_usable_rule.isHidden()


def _paste(s: ProfileSettings, mime: QMimeData) -> None:
    """Put ``mime`` on the clipboard and click "Paste choices" in the Lists section."""
    s.show_section(2)
    QApplication.clipboard().setMimeData(mime)
    next(b for b in s.lists_page.findChildren(QPushButton) if b.text() == "Paste choices").click()


def _text(text: str) -> QMimeData:
    mime = QMimeData()
    mime.setText(text)
    return mime


def test_paste_choices_adds_new_lines_and_can_be_undone(records_window: MainWindow) -> None:
    s = _settings(records_window)
    before = list(s.draft.taxonomies["doc_types"])
    head = ["court ORDER", "", "   ", "  Alpha  ", "alpha", "y" * profiles.MAX_PASTED_CHOICE]
    lines = head + [f"Item {i}" for i in range(600 - len(head))]
    _paste(s, _text("\r\n".join(lines)))
    added = s.draft.taxonomies["doc_types"][len(before):]
    assert added == ["Alpha", "y" * profiles.MAX_PASTED_CHOICE] + [f"Item {i}" for i in range(500 - len(head))]
    assert s.status.text() == "Added 496 choices; 2 already in the list. Only the first 500 lines were read."
    assert s.undo_button.isVisible() and s.valid_config is not None
    s.undo_button.click()
    assert s.draft.taxonomies["doc_types"] == before
    assert s.lists_page.items.count() == len(before)


@pytest.mark.parametrize(("line", "why"), [
    ("Has\ttab", "line 2 has a tab or another hidden character"),
    ("zero\u200bwidth", "line 2 has a tab or another hidden character"),
    ("x" * (profiles.MAX_PASTED_CHOICE + 1), f"line 2 is longer than {profiles.MAX_PASTED_CHOICE} characters"),
    ("\U0001F4C4" * (profiles.MAX_PASTED_CHOICE // 2 + 1), "line 2 is longer than"),  # UTF-16, as file names count
])
def test_paste_with_a_line_over_the_limits_adds_nothing(records_window: MainWindow, line: str, why: str) -> None:
    s = _settings(records_window)
    before = copy.deepcopy(s.draft.data)
    _paste(s, _text(f"Fine\n{line}\nAlso fine"))
    assert s.status.text().startswith(f"Nothing was pasted: {why}")
    assert s.draft.data == before and not s.draft.can_undo and not s.undo_button.isVisible()


@pytest.mark.parametrize("extra", ["text/html", "text/csv", 'application/x-qt-windows-mime;value="Biff12"',
                                   'application/x-qt-windows-mime;value="XML Spreadsheet"', "text/uri-list"])
def test_paste_that_isnt_plain_text_adds_nothing(records_window: MainWindow, extra: str) -> None:
    s = _settings(records_window)
    before = copy.deepcopy(s.draft.data)
    mime = _text("One\nTwo")
    mime.setData(extra, b"<table><tr><td>One</td></tr></table>")
    _paste(s, mime)
    assert s.status.text().startswith("Nothing was pasted: the clipboard holds more than plain text")
    assert s.draft.data == before and not s.draft.can_undo


def test_paste_of_no_text_adds_nothing(records_window: MainWindow) -> None:
    s = _settings(records_window)
    mime = QMimeData()
    mime.setData("image/png", b"\x89PNG")
    _paste(s, mime)
    assert s.status.text().startswith("Nothing was pasted")
