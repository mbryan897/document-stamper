"""Keyboard reach, target size and contrast (UX-012, UX-014, UX-015, UX-029, UX-041, UX-042).

Synthetic files and configs only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QLabel, QPushButton
from pytestqt.qtbot import QtBot

from stamper.gui import theme
from stamper.gui.main_window import MainWindow
from tests.gui_support import (  # noqa: F401 - fixtures
    FakeServices,
    copy_to,
    fake,
    open_dialog,
    window,
)

# ------------------------------------------------------------ contrast and size (UX-041, UX-012)


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    channels = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _rule(selector: str) -> str:
    m = re.search(re.escape(selector) + r" \{(.*?)\}", theme.STYLE_SHEET, re.S)
    assert m, selector
    return m.group(1)


@pytest.mark.parametrize("selector", ["QLineEdit, QComboBox", "QPushButton", "QToolButton#anchor"])
def test_resting_borders_reach_3_to_1(selector: str) -> None:
    border = re.search(r"border: 1\.5px solid (#[0-9A-Fa-f]{6})", _rule(selector))
    assert border, selector
    assert _contrast(border.group(1), theme.COLORS["surface"]) >= 3.0


def test_anchor_buttons_are_32_px(qtbot: QtBot, window: MainWindow, corpus_dir: Path, tmp_path: Path) -> None:
    d = open_dialog(qtbot, window, copy_to(corpus_dir / "letter_portrait.pdf", tmp_path))
    for b in d.anchor_buttons.values():
        assert b.width() >= 32 and b.height() >= 32
    tl, tc = d.anchor_buttons["top-left"].geometry(), d.anchor_buttons["top-center"].geometry()
    ml = d.anchor_buttons["middle-left"].geometry()
    assert tc.left() - tl.right() - 1 >= 4 and ml.top() - tl.bottom() - 1 >= 4


# ------------------------------------------------------------ the stamp screen (UX-015)


def _focused(qtbot: QtBot, window: MainWindow):  # noqa: ANN202
    window.activateWindow()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is not None, timeout=5000)
    return QApplication.focusWidget()


def test_tab_enters_the_grid_on_the_chosen_square_once(qtbot: QtBot, window: MainWindow,
                                                       corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    assert d.anchor != "top-left"
    start = d.editor("identifier")
    start.setFocus()
    qtbot.waitUntil(lambda: _focused(qtbot, window) is start, timeout=5000)
    visited = []
    for _ in range(80):  # once round the whole Tab cycle
        qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
        if QApplication.focusWidget() in d.anchor_buttons.values():
            visited.append(QApplication.focusWidget())
        if QApplication.focusWidget() is start:
            break
    assert visited == [d.anchor_buttons[d.anchor]]
    qtbot.keyClick(visited[0], Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
    assert QApplication.focusWidget() not in d.anchor_buttons.values()


def test_arrow_keys_move_the_position(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    # Qt's own exclusive-group handling; this guards it, it passed before this change too.
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    d.anchor_buttons["top-right"].click()
    d.anchor_buttons["top-right"].setFocus()
    qtbot.waitUntil(lambda: _focused(qtbot, window) is d.anchor_buttons["top-right"], timeout=5000)
    steps = [(Qt.Key.Key_Right, "top-right"), (Qt.Key.Key_Up, "top-right"),  # edges: no wrap
             (Qt.Key.Key_Left, "top-center"), (Qt.Key.Key_Down, "middle-center"),
             (Qt.Key.Key_Down, "bottom-center"), (Qt.Key.Key_Down, "bottom-center"),
             (Qt.Key.Key_Left, "bottom-left"), (Qt.Key.Key_Left, "bottom-left")]
    for key, expected in steps:
        qtbot.keyClick(QApplication.focusWidget(), key)
        assert d.anchor == expected
        assert d.anchor_buttons[expected].isChecked() and QApplication.focusWidget() is d.anchor_buttons[expected]
    tab_stops = [a for a, b in d.anchor_buttons.items() if b.focusPolicy() & Qt.FocusPolicy.TabFocus]
    assert tab_stops == ["bottom-left"]


def test_access_keys_on_the_stamp_screen(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    d.editor("identifier").setFocus()
    qtbot.waitUntil(lambda: _focused(qtbot, window) is d.editor("identifier"), timeout=5000)
    qtbot.keyClick(d.editor("identifier"), Qt.Key.Key_N, Qt.KeyboardModifier.AltModifier)
    assert QApplication.focusWidget() is d.page_numbers_combo
    d.anchor_buttons["bottom-left"].click()
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_P, Qt.KeyboardModifier.AltModifier)
    assert QApplication.focusWidget() is d.anchor_buttons["bottom-left"]
    widgets = [w for kind in (QLabel, QPushButton, QCheckBox) for w in d.findChildren(kind)]
    keys = [w.text() for w in widgets if w.isVisible() and (not isinstance(w, QLabel) or w.buddy())]
    marked = [re.search(r"&([^&])", t.replace("&&", "")) for t in keys]
    letters = [m.group(1).lower() for m in marked if m]
    assert sorted(letters) == sorted(set(letters)) and {"p", "d", "n", "s", "c"} <= set(letters)


def test_ampersand_in_a_configured_label_is_shown(qtbot: QtBot, fake: FakeServices, tmp_path: Path,
                                                  corpus_dir: Path) -> None:
    from stamper.gui.app import make_app
    from tests.gui_support import TODAY, close_window
    from tests.stamping import RECORDS_CONFIG

    config = tmp_path / "config.json"
    config.write_text(RECORDS_CONFIG.read_text(encoding="utf-8").replace("Document title", "R&D number"),
                      encoding="utf-8")
    make_app()
    w = MainWindow(config_path=config, services=fake.services(), today=TODAY)
    qtbot.addWidget(w)
    w.resize(1200, 780)
    w.show()
    try:
        d = open_dialog(qtbot, w, corpus_dir / "letter_portrait.pdf")
        label = d._rows["doc_title"].label
        tick = d._ticks["doc_title"]
        same_label = QLabel()  # no buddy, so nothing is read as an access key
        same_label.setObjectName(label.objectName())
        same_label.setStyleSheet(w.styleSheet())
        same_label.setFont(label.font())
        same_label.setText("R&D number  (optional, not stamped)")
        assert label.sizeHint().width() == same_label.sizeHint().width()
        assert label.text().replace("&&", "&") == same_label.text()
        assert tick.text() == "R&&D number"
    finally:
        close_window(w)


# ------------------------------------------------------------ Ctrl+O (UX-014)


def test_ctrl_o_opens_the_chooser_only_where_files_can_be_dropped(
        qtbot: QtBot, window: MainWindow, fake: FakeServices, corpus_dir: Path, tmp_path: Path) -> None:
    from tests.gui_support import choose, type_into, wait_ready
    from tools import make_corpus

    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    d = open_dialog(qtbot, window, src)
    fake.open_files.append(src)
    qtbot.keyClick(d, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)
    assert fake.open_files == [src] and window.dialog is d  # the stamp screen ignores it

    type_into(qtbot, d, "identifier", make_corpus.FAKE_CASE_SO)
    choose(d, "doc_type", "Court order")
    wait_ready(qtbot, d)
    fake.save_paths.append(tmp_path / "stamped.pdf")
    d.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.saved, timeout=15000)
    qtbot.keyClick(window.saved, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)
    assert fake.open_files == []  # asked, and the file opened
    qtbot.waitUntil(lambda: window.dialog is not None and window.dialog is not d, timeout=15000)
