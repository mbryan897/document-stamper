"""Start the window: QApplication, fonts, style, icon, main window."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .fonts import load_fonts
from .icon import app_icon, use_own_taskbar_id
from .main_window import MainWindow
from .theme import STYLE_SHEET

__all__ = ["make_app", "run_window"]


def make_app() -> QApplication:
    """The QApplication, set up once: name, icon, Fusion style, fonts and style sheet."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        use_own_taskbar_id()  # from source only; before any window exists
        app = QApplication(sys.argv[:1])
    app.setApplicationName("Document Stamper")
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)  # every window and dialog, the taskbar and Alt+Tab
    app.setStyle("Fusion")  # the same look on every Windows version
    load_fonts()
    app.setStyleSheet(STYLE_SHEET)
    return app


def run_window(files: Sequence[Path] = ()) -> int:
    """Show the window and run until it's closed; returns the exit code.

    No files: the drop screen. One file: its stamp dialog. Several: the files
    screen, to order them and choose Combine or Batch (D-100).
    """
    app = make_app()
    window = MainWindow()
    window.show_fitted()  # normal and centred when it fits the screen, else maximized (D-093)
    if files:
        window.open_files([Path(f) for f in files])
    return app.exec()
