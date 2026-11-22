"""The stamp dialog: fields, validation, pre-fill, the button, file names, warnings."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLabel, QLineEdit, QWidget
from pytestqt.qtbot import QtBot

from stamper.gui import profiles
from stamper.gui.main_window import MainWindow
from stamper.gui.page_numbers import WITH_IDENTIFIER
from stamper.handlers.pdf import COVERS_TEXT_NOTE_END
from tests.gui_support import (  # noqa: F401 - fixtures
    TODAY,
    FakeServices,
    choose,
    close_window,
    copy_to,
    default_window,
    fake,
    open_dialog,
    type_into,
    wait_ready,
    window,
)
from tests.stamping import RECORDS_CONFIG, make_signed_pdf
from tools import make_corpus

SO = make_corpus.FAKE_CASE_SO
DASHED = make_corpus.FAKE_CASE_DASHED


# ------------------------------------------------------------ fields from the config


def test_fields_built_from_records_sample(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    ident, doc_type, title, received = (d.editor(n) for n in ("identifier", "doc_type", "doc_title", "received"))
    assert isinstance(ident, QLineEdit) and ident.objectName() == "identifier"  # Mono font
    assert isinstance(doc_type, QComboBox)
    assert [doc_type.itemText(i) for i in range(doc_type.count())] == [
        "Choose…", "Search warrant return", "Subpoena return", "Court order",
        "Supplemental report", "Forensic report"]
    assert isinstance(title, QLineEdit) and title.objectName() != "identifier"
    assert isinstance(received, QLineEdit) and received.placeholderText() == "MM-DD-YYYY"
    labels = {n: d._rows[n].label.text() for n in d._rows}
    assert labels["identifier"] == "Case number"
    assert labels["doc_title"] == "Document title  (optional, not stamped)"
    assert labels["received"] == "Date received  (optional, not stamped)"


def test_identifier_font_is_mono(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    assert d.editor("identifier").fontInfo().family() == "Atkinson Hyperlegible Mono"
    assert d.editor("doc_title").fontInfo().family() == "Atkinson Hyperlegible Next"


# ------------------------------------------------------------ validation


def test_live_validation_messages(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    assert d.message("identifier") == "Case number is required."
    type_into(qtbot, d, "identifier", "so261332-999902")
    assert d.message("identifier") == (
        "Case number must match one of: a value like 12-2026-345678, a value like SO261018-123456. "
        "Closest is a value like SO261018-123456: not a real date: month 13 isn't a real month (01-12).")
    assert d._rows["identifier"].message.objectName() == "msgBad"
    type_into(qtbot, d, "identifier", "so261017–999902")  # lower case, en dash
    assert d.message("identifier") == "Matches SOYYMMDD-######, dated October 17, 2026."
    assert d.values()["identifier"] == SO
    type_into(qtbot, d, "identifier", DASHED)
    assert d.message("identifier") == "Matches ##-YYYY-######."
    type_into(qtbot, d, "received", "02-30-2026")
    assert d.message("received") == "Enter date received as a real date in the form MM-DD-YYYY."


def test_future_date_note(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    type_into(qtbot, d, "identifier", "SO621017-999902")
    assert d.note("identifier") == (
        "The date in this case number, October 17, 2062, is in the future. Check it before saving.")
    choose(d, "doc_type", "Court order")
    wait_ready(qtbot, d)
    assert d.save_button.isEnabled()  # a note, not a block (D-069)
    type_into(qtbot, d, "identifier", "SO261018-999902")  # TODAY itself isn't the future
    assert d.note("identifier") == ""


@pytest.mark.parametrize(
    ("name", "expected"),
    [(f"SO261017-999902_provider_return.pdf", SO), (f"provider_return_99-2026-999901.pdf", DASHED)],
)
def test_identifier_prefilled_from_filename(
    qtbot: QtBot, window: MainWindow, corpus_dir: Path, name: str, expected: str
) -> None:
    d = open_dialog(qtbot, window, corpus_dir / name)
    assert d.editor("identifier").text() == expected
    assert d.message("identifier").startswith("Taken from the file name. Matches ")
    type_into(qtbot, d, "identifier", "SO261017-999903")
    assert "Taken from the file name" not in d.message("identifier")


def test_button_disabled_until_valid(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    assert not d.save_button.isEnabled()
    assert d.blockers() == ["Fill in or fix: Case number, Document type."]
    type_into(qtbot, d, "identifier", SO)
    wait_ready(qtbot, d)
    assert not d.save_button.isEnabled()
    choose(d, "doc_type", "Court order")
    wait_ready(qtbot, d)
    assert d.save_button.isEnabled() and d.blockers() == []
    type_into(qtbot, d, "received", "13-01-2026")  # an optional field that's wrong also blocks
    wait_ready(qtbot, d)
    assert not d.save_button.isEnabled()
    type_into(qtbot, d, "received", "")
    wait_ready(qtbot, d)
    assert d.save_button.isEnabled()


# ------------------------------------------------------------ file name selector


def test_file_name_selector(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "SO261017-999902_provider_return.pdf")
    choose(d, "doc_type", "Search warrant return")
    assert d.ticked() == ["identifier", "doc_type"]
    assert d.name_label.text() == f"{SO}_Search-warrant-return_provider_return.pdf"  # O-008
    d._ticks["doc_type"].setChecked(False)
    assert d.name_label.text() == f"{SO}_provider_return.pdf"
    d._ticks["date"].setChecked(True)
    assert d.name_label.text() == f"{SO}_10-18-2026_provider_return.pdf"
    d._ticks["identifier"].setChecked(False)
    assert d.name_label.text() == f"10-18-2026_{SO}_provider_return.pdf"
    type_into(qtbot, d, "doc_title", "Provider: return / v2")
    d._ticks["doc_title"].setChecked(True)
    assert d.name_label.text() == f"Provider-return-v2_10-18-2026_{SO}_provider_return.pdf"


# ------------------------------------------------------------ keyboard


def test_enter_saves_only_when_enabled(
    qtbot: QtBot, window: MainWindow, fake: FakeServices, corpus_dir: Path, tmp_path: Path
) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    d = open_dialog(qtbot, window, src)
    fake.save_paths.append(tmp_path / "out.pdf")
    d.editor("identifier").setFocus()
    qtbot.keyClick(d.editor("identifier"), Qt.Key.Key_Return)
    assert fake.save_suggestions == []  # disabled: Enter does nothing
    type_into(qtbot, d, "identifier", SO)
    choose(d, "doc_type", "Court order")
    wait_ready(qtbot, d)
    qtbot.keyClick(d.editor("identifier"), Qt.Key.Key_Return)
    assert fake.save_suggestions == [tmp_path / f"{SO}_Court-order_letter_portrait.pdf"]
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.saved, timeout=15000)


def test_escape_cancels(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    qtbot.keyClick(d.editor("identifier"), Qt.Key.Key_Escape)
    assert window.stack.currentWidget() is window.drop
    assert window.dialog is None


def test_every_control_reachable_by_tab(qtbot: QtBot, window: MainWindow, corpus_dir: Path, tmp_path: Path) -> None:
    src = make_signed_pdf(corpus_dir, tmp_path / "signed.pdf")
    d = open_dialog(qtbot, window, src)
    type_into(qtbot, d, "identifier", SO)
    choose(d, "doc_type", "Court order")
    wait_ready(qtbot, d)
    d.signature_tick.setChecked(True)  # type: ignore[union-attr]
    wait_ready(qtbot, d)
    controls = [w for w in d.findChildren(QWidget)
                if w.focusPolicy() & Qt.FocusPolicy.TabFocus and w.isVisibleTo(d) and w.isEnabled()
                and not isinstance(w.parentWidget(), QComboBox)
                and w.metaObject().className() not in ("QScrollArea", "QScrollBar")]
    # Walk the focus chain Tab follows, starting from the first field.
    chain: list[QWidget] = []
    w = d.editor("identifier")
    for _ in range(500):
        chain.append(w)
        w = w.nextInFocusChain()
        if w is d.editor("identifier"):
            break
    missing = [c.accessibleName() or c.text() if hasattr(c, "text") else type(c).__name__
               for c in controls if c not in chain]
    assert not missing, missing
    names = [c.accessibleName() or getattr(c, "text", lambda: "")().replace("&", "") for c in chain if c in controls]
    for expected in ("Case number", "Document type", "Save a copy without a valid signature",
                     "Top right", "Along right edge", "Page numbers", "White box behind stamp",
                     "Cancel", "Stamp and save…"):
        assert expected in names, expected


# ------------------------------------------------------------ signed, restricted, password


def test_signed_needs_tick(qtbot: QtBot, window: MainWindow, corpus_dir: Path, tmp_path: Path) -> None:
    d = open_dialog(qtbot, window, make_signed_pdf(corpus_dir, tmp_path / "signed.pdf"))
    assert d.signature_tick is not None and d.signature_tick.text() == "Save a copy without a valid signature"
    type_into(qtbot, d, "identifier", SO)
    choose(d, "doc_type", "Court order")
    wait_ready(qtbot, d)
    assert not d.save_button.isEnabled()
    assert 'Tick "Save a copy without a valid signature" to continue.' in d.blockers()
    d.signature_tick.setChecked(True)
    wait_ready(qtbot, d)
    assert d.save_button.isEnabled()


def test_restricted_needs_tick(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "owner_restrictions.pdf")
    assert d.restriction_tick is not None and d.restriction_tick.text() == "Stamp a copy"
    notice = d.restriction_tick.parentWidget().findChildren(QLabel)[0]
    assert notice.textFormat() == Qt.TextFormat.PlainText
    assert notice.text() == ("This PDF is set not to allow changes. The original isn't touched. "
                             "Choose below whether the stamped copy keeps the setting.")
    assert d.owner_button is not None and d.owner_button.text() == "I have the permissions password (optional)…"
    type_into(qtbot, d, "identifier", SO)
    choose(d, "doc_type", "Court order")
    wait_ready(qtbot, d)
    assert not d.save_button.isEnabled()
    assert 'Tick "Stamp a copy" to continue.' in d.blockers()
    shown = "\n".join(lab.text() for lab in d.findChildren(QLabel)) + d.restriction_tick.text()
    assert "owner restriction" not in shown.lower() and "anyway" not in shown
    d.restriction_tick.setChecked(True)
    wait_ready(qtbot, d)
    assert d.save_button.isEnabled()


def test_owner_password_lifts_restrictions(
    qtbot: QtBot, window: MainWindow, fake: FakeServices, corpus_dir: Path
) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "owner_restrictions.pdf")
    fake.passwords.extend(["wrong-owner-pw", make_corpus.OWNER_PASSWORD])
    d._ask_owner_password()
    assert [p[1] for p in fake.password_prompts] == [
        "", "That password isn't the owner password for owner_restrictions.pdf."]
    assert all(p[2] for p in fake.password_prompts)  # asked for the owner password
    assert d.password == make_corpus.OWNER_PASSWORD
    assert d.restriction_tick is not None and d.restriction_tick.isChecked()
    assert not d.restriction_tick.isEnabled()
    assert d.restriction_tick.text() == "Permissions password given; changes allowed"


def test_password_prompt_retries_then_opens(
    qtbot: QtBot, window: MainWindow, fake: FakeServices, corpus_dir: Path
) -> None:
    fake.passwords.extend(["nope", make_corpus.USER_PASSWORD])
    d = open_dialog(qtbot, window, corpus_dir / "encrypted_user_password.pdf")
    assert fake.password_prompts == [
        ("encrypted_user_password.pdf", "", False),
        ("encrypted_user_password.pdf", "That password doesn't open encrypted_user_password.pdf.", False),
    ]
    assert d.password == make_corpus.USER_PASSWORD
    assert d.preview_png() is not None


def test_password_cancelled_stays_on_drop_screen(
    qtbot: QtBot, window: MainWindow, fake: FakeServices, corpus_dir: Path
) -> None:
    fake.passwords.append(None)
    window.open_file(corpus_dir / "encrypted_user_password.pdf")
    qtbot.waitUntil(lambda: bool(fake.password_prompts), timeout=15000)
    assert window.stack.currentWidget() is window.drop
    assert window.drop.message.text() == "encrypted_user_password.pdf wasn't opened: it needs a password."


# ------------------------------------------------------------ engine problems, next to what to fix


def test_receipt_page_that_doesnt_fit(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "mixed_sizes.pdf")
    type_into(qtbot, d, "identifier", DASHED)
    choose(d, "doc_type", "Search warrant return")
    wait_ready(qtbot, d)
    assert d.placement_problem().startswith("Page 4 (3.1 × 8.0 in), stamp 1")
    assert not d.save_button.isEnabled()
    assert d.preview_png() is not None  # page 1 still previews
    d._go(3)
    wait_ready(qtbot, d)
    assert d.preview_text().startswith("Page 4 (3.1 × 8.0 in)")
    d._set_direction("along-right")
    wait_ready(qtbot, d)
    assert d.placement_problem() == "" and d.save_button.isEnabled()


def test_missing_glyph_shown_under_its_field(qtbot: QtBot, default_window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, default_window, corpus_dir / "letter_portrait.pdf")
    type_into(qtbot, d, "identifier", "CASE \u2603 1")  # a snowman: not in the stamp font
    choose(d, "doc_type", "Report")
    wait_ready(qtbot, d)
    assert d.note("identifier") == (
        "The stamp font has no character for '\u2603' (U+2603) in Identifier. "
        "Remove or replace that character.")
    assert not d.save_button.isEnabled()
    type_into(qtbot, d, "identifier", "CASE 1")
    wait_ready(qtbot, d)
    assert d.note("identifier") == "" and d.save_button.isEnabled()


def test_form_field_warning(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "acroform_filled.pdf")
    assert d.plan_warnings() == [
        "On page 1 a form field covers part of the stamp and is drawn on top of it. "
        "Choose another position under Placement to avoid it."]
    d._set_anchor("bottom-left")
    wait_ready(qtbot, d)
    assert d.plan_warnings() == []


def test_shrink_warning_shown_before_saving(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "mixed_sizes.pdf")
    type_into(qtbot, d, "identifier", DASHED)
    choose(d, "doc_type", "Court order")
    wait_ready(qtbot, d)
    assert d.plan_warnings() == ["Page 4, stamp 1: font reduced from 9 pt to 6 pt to fit.",
                                 "On page 4" + COVERS_TEXT_NOTE_END]  # the receipt's title (UX-004)
    assert d.save_button.isEnabled()


# ------------------------------------------------------------ page numbers in the dialog


def test_page_numbers_option_in_dialog(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "rotate_mixed.pdf")
    type_into(qtbot, d, "identifier", SO)
    choose(d, "doc_type", "Court order")
    combo = d.page_numbers_combo
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "In the main stamp", "Bottom left", "Bottom center", "Bottom right"]  # the field is "Case number" (UX-028)
    assert d.page_numbers == WITH_IDENTIFIER
    assert [s.text for s in d.job().stamps] == ["{identifier}  |  {doc_type}  |  Page {page} of {total}"]
    combo.setCurrentIndex(3)
    combo.activated.emit(3)
    wait_ready(qtbot, d)
    job = d.job()
    assert [(s.text, s.anchor, s.direction) for s in job.stamps] == [
        ("{identifier}  |  {doc_type}", "top-right", "along-top"),
        ("Page {page} of {total}", "bottom-right", "along-top"),
    ]
    d._set_anchor("bottom-right")  # now bottom right is taken by the main stamp
    model = combo.model()
    assert not model.item(3).isEnabled()  # type: ignore[attr-defined]
    wait_ready(qtbot, d)
    assert "overlap" in d.placement_problem()


def test_box_switch_on_by_default(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    assert isinstance(d.box_check, QCheckBox) and d.box_check.isChecked()
    assert d.job().background_box is True
    d.box_check.setChecked(False)
    assert d.job().background_box is False


# ------------------------------------------------------------ the mouse wheel (UX-003)


def _wheel(widget: QWidget) -> QWheelEvent:
    """One notch of the mouse wheel down, as if the pointer were over the widget."""
    pos = QPointF(widget.width() / 2, widget.height() / 2)
    event = QWheelEvent(pos, QPointF(widget.mapToGlobal(pos.toPoint())), QPoint(0, 0), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase,
                        False)
    QApplication.sendEvent(widget, event)
    return event


def test_scroll_moving_the_panel_passes_over_its_drop_downs(qtbot: QtBot, window: MainWindow,
                                                            corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "rotate_mixed.pdf")
    doc_type, page_numbers = d.editor("doc_type"), d.page_numbers_combo
    for combo in (doc_type, page_numbers):
        assert combo.focusPolicy() == Qt.FocusPolicy.StrongFocus  # type: ignore[attr-defined]
        before = combo.currentIndex()  # type: ignore[attr-defined]
        _wheel(d.scroll.viewport())  # the panel was scrolling, then the drop-down slid under the pointer
        event = _wheel(combo)
        assert not event.isAccepted() and combo.currentIndex() == before  # type: ignore[attr-defined]


def test_scroll_that_starts_on_a_drop_down_changes_it(qtbot: QtBot, window: MainWindow, corpus_dir: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "rotate_mixed.pdf")
    monkeypatch.setattr(profiles, "_page_scrolled_at", 0.0)  # the panel has been still
    doc_type = d.editor("doc_type")
    _wheel(doc_type)
    assert doc_type.currentIndex() == 1  # type: ignore[attr-defined]


# ------------------------------------------------------------ what a field accepts (UX-017, UX-018, UX-043)
REGEX = r"[A-Z]{2}\d{6}"


@pytest.fixture
def edited_window(qtbot: QtBot, fake: FakeServices, tmp_path: Path) -> Iterator[Callable[..., MainWindow]]:
    """A window on the Records config after ``edit(config_dict)``."""
    opened: list[MainWindow] = []

    def make(edit: Callable[[dict], None]) -> MainWindow:
        data = json.loads(RECORDS_CONFIG.read_text(encoding="utf-8"))
        edit(data)
        path = tmp_path / f"config_{len(opened)}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        w = MainWindow(config_path=path, services=fake.services(), today=TODAY)
        qtbot.addWidget(w)
        w.resize(1200, 780)
        w.show()
        opened.append(w)
        return w

    yield make
    for w in opened:
        close_window(w)


@pytest.fixture
def rules_window(edited_window: Callable[..., MainWindow]) -> Callable[[list[dict[str, str]]], MainWindow]:
    """A window on the Records config with the identifier's rules replaced."""
    def make(formats: list[dict[str, str]]) -> MainWindow:
        return edited_window(lambda c: c["fields"]["identifier"].__setitem__("formats", formats))
    return make


def _all_text(d: QWidget) -> str:
    """Every label, placeholder and accessible description in the dialog."""
    parts = [lab.text() for lab in d.findChildren(QLabel)]
    for edit in d.findChildren(QLineEdit):
        parts += [edit.placeholderText(), edit.accessibleDescription()]
    return "\n".join(parts)


def test_one_rule_is_the_placeholder(qtbot: QtBot, rules_window, corpus_dir: Path) -> None:  # noqa: ANN001
    w = rules_window([{"name": "Agency case number", "pattern": "##-YYYY-######"}])
    d = open_dialog(qtbot, w, corpus_dir / "letter_portrait.pdf")
    assert d.editor("identifier").placeholderText() == "Agency case number"
    assert d._rows["identifier"].accepts.isHidden()
    w2 = rules_window([{"pattern": "##-YYYY-######"}])
    d2 = open_dialog(qtbot, w2, corpus_dir / "letter_portrait.pdf")
    assert d2.editor("identifier").placeholderText() == "like 12-2026-345678"


def test_several_rules_show_a_line_until_typing(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    accepts = d._rows["identifier"].accepts
    assert d.editor("identifier").placeholderText() == ""
    assert accepts.isVisible() and accepts.textFormat() == Qt.TextFormat.PlainText
    assert accepts.text() == "Accepted: like 12-2026-345678; like SO261018-123456."
    type_into(qtbot, d, "identifier", "SO")
    assert not accepts.isVisible()
    type_into(qtbot, d, "identifier", "")
    assert accepts.isVisible()
    assert d._rows["doc_title"].accepts.isHidden()  # no rules, nothing to say


def test_wildcard_field_has_no_hint_and_no_matches(qtbot: QtBot, default_window: MainWindow,
                                                   corpus_dir: Path) -> None:
    d = open_dialog(qtbot, default_window, corpus_dir / "letter_portrait.pdf")
    assert d.editor("identifier").placeholderText() == ""
    assert d._rows["identifier"].accepts.isHidden()
    type_into(qtbot, d, "identifier", "ABC-123")
    assert "Matches" not in d.message("identifier")
    assert d._rows["identifier"].label.text() == "Identifier"  # D-031


def test_regex_never_appears_on_screen(qtbot: QtBot, rules_window, corpus_dir: Path) -> None:  # noqa: ANN001
    w = rules_window([{"regex": REGEX}])
    d = open_dialog(qtbot, w, corpus_dir / "letter_portrait.pdf")
    assert d.editor("identifier").placeholderText() == "the required format"
    type_into(qtbot, d, "identifier", "nope")
    assert d.message("identifier") == "Case number must match the required format."
    assert REGEX not in _all_text(d)
    type_into(qtbot, d, "identifier", "AB123456")
    assert d.message("identifier") == "Matches the required format."
    w2 = rules_window([{"regex": REGEX}, {"name": "Agency case number", "pattern": "##-YYYY-######"}])
    d2 = open_dialog(qtbot, w2, corpus_dir / "letter_portrait.pdf")
    assert d2._rows["identifier"].accepts.text() == "Accepted: the required format; Agency case number."
    type_into(qtbot, d2, "identifier", "99-20")
    for text in (_all_text(d), _all_text(d2)):
        assert REGEX not in text and "\\d" not in text


# ------------------------------------------------------------ Page numbers wording (UX-028)


def test_page_numbers_already_separate_names_its_place(qtbot: QtBot, edited_window, corpus_dir: Path) -> None:  # noqa: ANN001
    def edit(c: dict) -> None:
        c["profiles"][0]["stamps"] = [{"text": "{identifier}  |  {doc_type}"},
                                      {"text": "Page {page} of {total}", "anchor": "middle-left"}]
    d = open_dialog(qtbot, edited_window(edit), corpus_dir / "rotate_mixed.pdf")
    combo = d.page_numbers_combo
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "Middle left, as set up", "Bottom left", "Bottom center", "Bottom right"]
    labels = " ".join(combo.itemText(i) for i in range(combo.count())) + d.page_numbers_note.text()
    assert "identifier" not in labels.lower() and "Case number" not in labels
