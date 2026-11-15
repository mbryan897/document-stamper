"""Keyboard reach, target size and contrast (UX-012, UX-014, UX-015, UX-029, UX-041, UX-042).

Synthetic files and configs only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton
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
