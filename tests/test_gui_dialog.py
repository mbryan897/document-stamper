"""The stamp dialog: fields, validation, pre-fill, the button, file names, warnings."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLineEdit, QWidget
from pytestqt.qtbot import QtBot

from stamper.gui import profiles
from stamper.gui.main_window import MainWindow
from stamper.gui.page_numbers import WITH_IDENTIFIER
from stamper.handlers.pdf import COVERS_TEXT_NOTE_END
from tests.gui_support import (  # noqa: F401 - fixtures
    FakeServices,
    choose,
    copy_to,
    default_window,
    fake,
    open_dialog,
    type_into,
    wait_ready,
    window,
)
from tests.stamping import make_signed_pdf
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
        "Case number must match one of: ##-YYYY-######, SOYYMMDD-######. "
        "Closest is SOYYMMDD-######: not a real date: month 13 isn't a real month (01-12).")
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
    names = [c.accessibleName() or getattr(c, "text", lambda: "")() for c in chain if c in controls]
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
    assert d.restriction_tick is not None and d.restriction_tick.text() == "Stamp it anyway"
    type_into(qtbot, d, "identifier", SO)
    choose(d, "doc_type", "Court order")
    wait_ready(qtbot, d)
    assert not d.save_button.isEnabled()
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
        "With the identifier", "Bottom left", "Bottom center", "Bottom right"]
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
