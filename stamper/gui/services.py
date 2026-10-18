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
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit, QWidget

__all__ = ["Services", "native_services"]


@dataclass
class Services:
    """Hooks the window calls; each returns None when the user cancels.

    ``ask_password(parent, file_name, problem)`` shows ``problem`` (empty the
    first time) above the prompt. ``ask_save_path(parent, suggested)`` starts
    in the suggested file's folder with its name filled in.
    """

    ask_open_file: Callable[[QWidget, Path | None], Path | None]
    ask_config_file: Callable[[QWidget], Path | None]
    ask_save_path: Callable[[QWidget, Path], Path | None]
    ask_password: Callable[[QWidget, str, str, bool], str | None]
    open_file: Callable[[Path], None]
    show_in_folder: Callable[[Path], None]


def _ask_open_file(parent: QWidget, start: Path | None) -> Path | None:
    name, _ = QFileDialog.getOpenFileName(
        parent, "Choose a PDF to stamp", str(start or ""), "PDF files (*.pdf)")
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
    )
