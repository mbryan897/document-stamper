"""Things the window asks the operating system or the user to do.

The native file dialogs, the password prompt and opening Explorer are
grouped here so the windows stay testable: tests pass a ``Services`` with
plain functions instead, and nothing modal ever blocks a test run.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QColorDialog,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QWidget,
)

__all__ = ["Services", "native_services"]


@dataclass
class Services:
    """Hooks the window calls; each returns None when the user cancels.

    ``ask_password(parent, file_name, problem)`` shows ``problem`` (empty the
    first time) above the prompt. ``ask_save_path(parent, suggested)`` starts
    in the suggested file's folder with its name filled in.
    ``confirm(parent, title, text, yes_label)`` returns True for the yes button.
    ``ask_import_file`` and ``ask_export_path`` pick config files for
    Profile settings (Phase 4). ``ask_color(parent, current)`` opens the color
    wheel and returns ``#RRGGBB`` (D-094). ``ask_open_files(parent, start)``
    picks one or more PDFs for "Choose files…" (D-109); None in a test's
    Services falls back to ``ask_open_file``. ``ask_folder(parent, start)``
    picks the output folder for a batch (D-110).
    """

    ask_open_file: Callable[[QWidget, Path | None], Path | None]
    ask_config_file: Callable[[QWidget], Path | None]
    ask_save_path: Callable[[QWidget, Path], Path | None]
    ask_password: Callable[[QWidget, str, str, bool], str | None]
    open_file: Callable[[Path], None]
    show_in_folder: Callable[[Path], None]
    confirm: Callable[[QWidget, str, str, str], bool] = lambda *_a: False
    ask_import_file: Callable[[QWidget], Path | None] = lambda _p: None
    ask_export_path: Callable[[QWidget, Path], Path | None] = lambda _p, _s: None
    ask_color: Callable[[QWidget, str], str | None] = lambda _p, _c: None
    ask_open_files: Callable[[QWidget, Path | None], list[Path] | None] | None = None
    ask_folder: Callable[[QWidget, Path], Path | None] = lambda _p, _s: None


def _ask_open_file(parent: QWidget, start: Path | None) -> Path | None:
    name, _ = QFileDialog.getOpenFileName(
        parent, "Choose a PDF to stamp", str(start or ""), "PDF files (*.pdf)")
    return Path(name) if name else None


def _ask_open_files(parent: QWidget, start: Path | None) -> list[Path] | None:
    names, _ = QFileDialog.getOpenFileNames(
        parent, "Choose PDFs to stamp", str(start or ""), "PDF files (*.pdf)")
    return [Path(n) for n in names] if names else None


def _ask_folder(parent: QWidget, start: Path) -> Path | None:
    name = QFileDialog.getExistingDirectory(parent, "Save the stamped copies in", str(start))
    return Path(name) if name else None


def _ask_config_file(parent: QWidget) -> Path | None:
    name, _ = QFileDialog.getOpenFileName(
        parent, "Open a config file", "", "Config files (*.json)")
    return Path(name) if name else None


def _ask_save_path(parent: QWidget, suggested: Path) -> Path | None:
    # The window refuses existing files itself (D-075), so the dialog's own
    # "Replace?" question is turned off rather than offering an overwrite.
    name, _ = QFileDialog.getSaveFileName(
        parent, "Save the stamped copy", str(suggested), "PDF files (*.pdf)",
        options=QFileDialog.Option.DontConfirmOverwrite)
    if not name:
        return None
    path = Path(name)
    return path if path.suffix.lower() == ".pdf" else path.with_name(path.name + ".pdf")


def _ask_password(parent: QWidget, file_name: str, problem: str, owner: bool) -> str | None:
    what = "the owner password" if owner else "the password"
    lead = f"{problem}\n\n" if problem else ""
    text, ok = QInputDialog.getText(
        parent, "Password needed" if not owner else "Owner password",
        f"{lead}Enter {what} for {file_name}:", QLineEdit.EchoMode.Password)
    return text if ok else None


def _confirm(parent: QWidget, title: str, text: str, yes_label: str) -> bool:
    box = QMessageBox(QMessageBox.Icon.Question, title, text, QMessageBox.StandardButton.NoButton, parent)
    yes = box.addButton(yes_label, QMessageBox.ButtonRole.AcceptRole)
    no = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(no)  # destructive questions default to the safe answer
    box.setEscapeButton(no)
    box.exec()
    return box.clickedButton() is yes


def _ask_import_file(parent: QWidget) -> Path | None:
    name, _ = QFileDialog.getOpenFileName(
        parent, "Import a config file", "", "Config files (*.json)")
    return Path(name) if name else None


def _ask_export_path(parent: QWidget, suggested: Path) -> Path | None:
    name, _ = QFileDialog.getSaveFileName(
        parent, "Export the config", str(suggested), "Config files (*.json)")
    if not name:
        return None
    path = Path(name)
    return path if path.suffix.lower() == ".json" else path.with_name(path.name + ".json")


def _ask_color(parent: QWidget, current: str) -> str | None:
    color = QColorDialog.getColor(QColor(current), parent, "Choose a stamp color")
    return color.name().upper() if color.isValid() else None


def _open_file(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]  # the user's PDF viewer
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def _show_in_folder(path: Path) -> None:
    if sys.platform == "win32":
        # explorer wants /select,"path" as one argument; it returns 1 even on success.
        subprocess.Popen(f'explorer /select,"{path}"')  # noqa: S602
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


def native_services() -> Services:
    """The real dialogs and Explorer."""
    return Services(
        ask_open_file=_ask_open_file,
        ask_config_file=_ask_config_file,
        ask_save_path=_ask_save_path,
        ask_password=_ask_password,
        open_file=_open_file,
        show_in_folder=_show_in_folder,
        confirm=_confirm,
        ask_import_file=_ask_import_file,
        ask_export_path=_ask_export_path,
        ask_color=_ask_color,
        ask_open_files=_ask_open_files,
        ask_folder=_ask_folder,
    )
