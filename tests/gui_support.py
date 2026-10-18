"""Helpers for the window tests: fake dialogs, a window fixture, waiting.

Native dialogs never open in tests: ``FakeServices`` answers instead and
records what it was asked. Synthetic files only.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from stamper.gui.app import make_app
from stamper.gui.main_window import MainWindow
from stamper.gui.services import Services
from stamper.gui.stamp_dialog import StampDialog
from tests.stamping import DEFAULT_CONFIG, RECORDS_CONFIG

TODAY = date(2026, 10, 18)
WAIT_MS = 15000


@dataclass
class FakeServices:
    """Scripted answers for the window's questions, and a record of them."""

    open_files: list[Path | None] = field(default_factory=list)
    config_files: list[Path | None] = field(default_factory=list)
    save_paths: list[Path | None] = field(default_factory=list)
    passwords: list[str | None] = field(default_factory=list)
    password_prompts: list[tuple[str, str, bool]] = field(default_factory=list)
    save_suggestions: list[Path] = field(default_factory=list)
    opened: list[Path] = field(default_factory=list)
    shown: list[Path] = field(default_factory=list)

    def services(self) -> Services:
        """A Services object backed by these lists."""
        return Services(
            ask_open_file=lambda _p, _s: self.open_files.pop(0) if self.open_files else None,
            ask_config_file=lambda _p: self.config_files.pop(0) if self.config_files else None,
            ask_save_path=self._save,
            ask_password=self._password,
            open_file=self.opened.append,
            show_in_folder=self.shown.append,
        )

    def _save(self, _parent: object, suggested: Path) -> Path | None:
        self.save_suggestions.append(suggested)
        return self.save_paths.pop(0) if self.save_paths else None

    def _password(self, _parent: object, name: str, problem: str, owner: bool) -> str | None:
        self.password_prompts.append((name, problem, owner))
        return self.passwords.pop(0) if self.passwords else None


@pytest.fixture
def fake() -> FakeServices:
    """Scripted dialog answers."""
    return FakeServices()


@pytest.fixture
def window(qtbot: QtBot, fake: FakeServices) -> Iterator[MainWindow]:
    """The main window on the synthetic Records config, closed after the test."""
    make_app()
    w = MainWindow(config_path=RECORDS_CONFIG, services=fake.services(), today=TODAY)
    qtbot.addWidget(w)
    w.resize(1200, 780)
    w.show()
    yield w
    w.close()


@pytest.fixture
def default_window(qtbot: QtBot, fake: FakeServices) -> Iterator[MainWindow]:
    """The main window on the shipped generic config."""
    make_app()
    w = MainWindow(config_path=DEFAULT_CONFIG, services=fake.services(), today=TODAY)
    qtbot.addWidget(w)
    w.resize(1200, 780)
    w.show()
    yield w
    w.close()


def copy_to(src: Path, folder: Path, name: str | None = None) -> Path:
    """Copy a corpus file into a test folder (saves land next to it)."""
    dst = folder / (name or src.name)
    shutil.copyfile(src, dst)
    return dst


def wait_ready(qtbot: QtBot, dialog: StampDialog) -> StampDialog:
    """Wait until the preview and the whole-document check are current."""
    qtbot.waitUntil(dialog.ready, timeout=WAIT_MS)
    return dialog


def open_dialog(qtbot: QtBot, window: MainWindow, path: Path) -> StampDialog:
    """Open a file in the window and wait for its stamp dialog to settle."""
    window.open_file(path)
    qtbot.waitUntil(lambda: window.dialog is not None, timeout=WAIT_MS)
    assert window.dialog is not None
    return wait_ready(qtbot, window.dialog)


def choose(dialog: StampDialog, field_name: str, value: str) -> None:
    """Pick a value in a choice field, as a click would."""
    combo = dialog.editor(field_name)
    index = combo.findData(value)  # type: ignore[attr-defined]
    assert index >= 0, value
    combo.setCurrentIndex(index)  # type: ignore[attr-defined]


def type_into(qtbot: QtBot, dialog: StampDialog, field_name: str, text: str) -> None:
    """Replace a text field's contents by typing, as a user would."""
    editor = dialog.editor(field_name)
    if text.isascii():
        editor.selectAll()  # type: ignore[attr-defined]
        if text:
            qtbot.keyClicks(editor, text)
        else:
            qtbot.keyClick(editor, Qt.Key.Key_Delete)
    else:
        # QTest can only type Latin-1; paste the rest as a user edit.
        editor.setText(text)  # type: ignore[attr-defined]
        editor.textEdited.emit(text)  # type: ignore[attr-defined]
