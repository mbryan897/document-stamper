"""The window fits the screen, and every screen's buttons stay inside it (D-093)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QSize
from PySide6.QtWidgets import QLabel, QPushButton, QWidget
from pytestqt.qtbot import QtBot

from stamper.gui.main_window import (
    FRAME_ALLOWANCE,
    MINIMUM_SIZE,
    PREFERRED_SIZE,
    MainWindow,
    fit_to_screen,
)
from tests.gui_support import (  # noqa: F401 - fixtures
    FakeServices,
    copy_to,
    fake,
    open_dialog,
    window,
)
from tests.stamping import make_signed_pdf

# ------------------------------------------------------------ fit_to_screen

def test_preferred_size_on_a_big_screen() -> None:
    fit = fit_to_screen(QSize(1920, 1032))  # 1080p at 100 %, taskbar excluded
    assert fit.size == PREFERRED_SIZE and fit.minimum == MINIMUM_SIZE and not fit.maximized


@pytest.mark.parametrize("available", [QSize(1280, 672), QSize(1536, 816), QSize(1366, 728)])
def test_maximized_when_the_preferred_size_does_not_fit(available: QSize) -> None:
    # 1080p at 150 %, 1080p at 125 %, a 1366 x 768 laptop: all too short for 780 + title bar.
    fit = fit_to_screen(available)
    assert fit.maximized
    room = available - FRAME_ALLOWANCE
    assert fit.size.width() <= room.width() and fit.size.height() <= room.height()
    assert fit.minimum == MINIMUM_SIZE


def test_minimum_never_larger_than_the_screen() -> None:
    fit = fit_to_screen(QSize(800, 520))
    assert fit.maximized
    assert fit.minimum.width() <= 800 - FRAME_ALLOWANCE.width()
    assert fit.minimum.height() <= 520 - FRAME_ALLOWANCE.height()


def test_place_on_centres_the_window(window: MainWindow) -> None:
    from PySide6.QtCore import QRect

    fit = window.place_on(QRect(0, 0, 1920, 1032))
    assert not fit.maximized and window.size() == PREFERRED_SIZE
    assert window.minimumSize() == MINIMUM_SIZE


# ------------------------------------------------------------ buttons inside the window

def _inside(window: MainWindow, button: QPushButton) -> bool:
    top = button.mapTo(window, QPoint(0, 0)).y()
    return button.isVisible() and top >= 0 and top + button.height() <= window.height()


def _clipped(widget: QWidget) -> bool:
    """True when a widget is shorter than it can be drawn (its contents would be cut off)."""
    return widget.height() < widget.minimumSizeHint().height()


@pytest.fixture
def small(window: MainWindow, qtbot: QtBot) -> MainWindow:
    window.resize(MINIMUM_SIZE)
    qtbot.waitUntil(lambda: window.size() == MINIMUM_SIZE)
    return window


def test_drop_screen_fits_at_minimum(small: MainWindow) -> None:
    assert _inside(small, small.drop.choose_button)
    assert not _clipped(small.drop)


def test_stamp_dialog_buttons_at_minimum(qtbot: QtBot, small: MainWindow, corpus_dir: Path,
                                         tmp_path: Path) -> None:
    # A signed file adds the tallest confirmation notice to the panel.
    src = make_signed_pdf(corpus_dir, tmp_path / "signed_return.pdf")
    d = open_dialog(qtbot, small, src)
    assert _inside(small, d.save_button) and _inside(small, d.cancel_button)
    assert not _clipped(d)


@pytest.mark.parametrize("section", range(4))
def test_settings_buttons_at_minimum(qtbot: QtBot, small: MainWindow, section: int) -> None:
    s = small.open_settings()
    assert s is not None
    s.show_section(section)
    qtbot.wait(50)
    assert _inside(small, s.save_button) and _inside(small, s.cancel_button)
    assert not _clipped(s)


def test_long_messages_do_not_push_settings_buttons_off(qtbot: QtBot, small: MainWindow) -> None:
    s = small.open_settings()
    assert s is not None
    s.say("A long message. " * 200)
    s.problems = [f"problem {i}" for i in range(8)]
    s.problem_label.setText("Fix these before saving:\n" + "\n".join(f"• problem {i}" for i in range(8)))
    s.problem_box.show()
    s.problem_box.refresh()
    qtbot.wait(50)
    assert _inside(small, s.save_button)
    assert not _clipped(s)
    assert s.problem_box.height() < s.problem_label.heightForWidth(s.problem_box.viewport().width())


def test_saved_screen_groups_many_notes(small: MainWindow) -> None:
    from stamper.handlers.base import ApplyResult

    warnings = tuple(f"Page {n}, stamp 1: the text was made smaller to fit." for n in range(1, 41))
    result = ApplyResult(output_path=Path("out.pdf"), pages_stamped=40, warnings=warnings, placements=(),
                         metadata_written={}, metadata_replaced={}, encryption_kept=False)
    small.saved.show_result(result, label="Case number", identifier="X", signed=False)
    small.stack.setCurrentWidget(small.saved)
    notes = small.saved.notes
    assert sum(1 for label in notes.findChildren(QLabel) if label.isVisible() and label.text()) <= 6 + 1  # + "34 more"
    assert notes.more_button.isVisible() and not notes.more_box.isVisible()
    assert _inside(small, small.saved.open_button)
