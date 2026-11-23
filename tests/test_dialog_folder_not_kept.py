"""Qt's file dialog saves the folder it was last in; the program puts back what was there (F-03)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog
from pytestqt.qtbot import QtBot

from stamper.gui import services

PLANTED = "PlantedCaseFolderQQ-99-2026-999901"
ASKS = ("_ask_open_file", "_ask_open_files", "_ask_folder", "_ask_config_file", "_ask_save_path",
        "_ask_import_file", "_ask_export_path")


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand-in for Qt's settings: an ini file, so no test touches the real registry."""
    ini = tmp_path / "QtProject.ini"

    def settings() -> QSettings:
        s = QSettings(str(ini), QSettings.Format.IniFormat)
        s.beginGroup("FileDialog")
        return s

    monkeypatch.setattr(services, "_dialog_settings", settings)

    def qt_saves(*_a: object, **_k: object) -> tuple[str, str]:  # what Qt does when a dialog closes
        s = settings()
        s.setValue("lastVisited", f"file:///C:/{PLANTED}")
        s.setValue("history", [f"file:///C:/{PLANTED}"])
        s.setValue("qtVersion", "6.12.0")
        s.sync()
        return "", ""

    for name in ("getOpenFileName", "getOpenFileNames", "getSaveFileName"):
        monkeypatch.setattr(QFileDialog, name, qt_saves)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: qt_saves()[0])
    return ini


def _call(name: str, tmp_path: Path) -> object:
    ask = getattr(services, name)
    if name in ("_ask_save_path", "_ask_export_path", "_ask_folder"):
        return ask(None, tmp_path / "x.pdf")
    if name == "_ask_config_file":
        return ask(None)
    return ask(None, tmp_path)


@pytest.mark.parametrize("name", ASKS)
def test_no_dialog_leaves_its_folder_in_qts_settings(name: str, store: Path, tmp_path: Path) -> None:
    assert not _call(name, tmp_path)  # cancelled
    text = store.read_text(encoding="utf-8") if store.exists() else ""
    assert PLANTED not in text and "lastVisited" not in text and "history" not in text


@pytest.mark.parametrize("name", ASKS)
def test_what_another_qt_program_left_is_put_back(name: str, store: Path, tmp_path: Path) -> None:
    s = services._dialog_settings()
    s.setValue("lastVisited", "file:///C:/SomeoneElses")
    s.setValue("viewMode", "List")
    s.sync()
    _call(name, tmp_path)
    s = services._dialog_settings()
    assert s.value("lastVisited") == "file:///C:/SomeoneElses" and s.value("viewMode") == "List"
    assert sorted(s.allKeys()) == ["lastVisited", "viewMode"]
    assert PLANTED not in store.read_text(encoding="utf-8")


def test_settings_that_cant_be_read_never_stop_a_dialog(store: Path, tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> QSettings:
        raise OSError("no settings")

    monkeypatch.setattr(services, "_dialog_settings", broken)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(tmp_path / "a.pdf"), ""))
    assert services._ask_open_file(None, tmp_path) == tmp_path / "a.pdf"


@pytest.mark.skipif(os.name == "nt", reason="Qt's real settings are the registry on Windows; not touched by tests")
def test_a_real_qt_dialog_leaves_nothing(qtbot: QtBot, tmp_path: Path) -> None:
    """Not a stand-in: Qt's own dialog, closed by a timer, with Qt's settings pointed at a temp folder."""
    conf = tmp_path / "conf"
    QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, str(conf))
    folder = tmp_path / PLANTED
    folder.mkdir()

    def close() -> None:
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QFileDialog):
                w.reject()

    QTimer.singleShot(300, close)
    assert services._ask_open_file(None, folder) is None
    kept = "".join(p.read_text(encoding="utf-8") for p in conf.rglob("*") if p.is_file())
    assert PLANTED not in kept
