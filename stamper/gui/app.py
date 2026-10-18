"""Start the window: QApplication, fonts, style, main window."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .fonts import load_fonts
from .main_window import MainWindow
from .theme import STYLE_SHEET

__all__ = ["make_app", "run_window"]


def make_app() -> QApplication:
    """The QApplication, set up once: name, Fusion style, fonts and style sheet."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv[:1])
    app.setApplicationName("Document Stamper")
    app.setStyle("Fusion")  # the same look on every Windows version
    load_fonts()
    app.setStyleSheet(STYLE_SHEET)
    return app


def run_window(files: Sequence[Path] = ()) -> int:
    """Show the window and run until it's closed; returns the exit code.

    No files: the drop screen. One file: its stamp dialog. Several: the drop
    screen with a message, since batch stamping is out of scope.
    """
    app = make_app()
    window = MainWindow()
    window.show()
    if files:
        window.open_files([Path(f) for f in files])
    return app.exec()
