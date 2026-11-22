"""Entry point routing (D-063) and the drop screen's file checks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

import stamper.__main__ as entry
from stamper.gui.main_window import MainWindow
from tests.gui_support import FakeServices, fake, open_dialog, window  # noqa: F401 - fixtures
from tests.stamping import RECORDS_CONFIG, make_xfa_pdf

REPO = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------ route()


def test_no_arguments_opens_the_window() -> None:
    assert entry.route([]) == entry.Route("window")


def test_one_existing_file_opens_its_dialog(corpus_dir: Path) -> None:
    f = corpus_dir / "letter_portrait.pdf"
    assert entry.route([str(f)]) == entry.Route("window", (f,))


def test_several_files_open_the_window(corpus_dir: Path) -> None:
    a, b = corpus_dir / "letter_portrait.pdf", corpus_dir / "rotate_90.pdf"
    assert entry.route([str(a), str(b)]) == entry.Route("window", (a, b))


def test_any_file_type_goes_to_the_window(tmp_path: Path) -> None:
    # A non-PDF dropped on the exe still opens the window, which says why it can't stamp it.
    notes = tmp_path / "notes.txt"
    notes.write_text("x", encoding="utf-8")
    assert entry.route([str(notes)]).kind == "window"


@pytest.mark.parametrize(
    "argv",
    [
        ["--version"],
        ["--help"],
        ["--", "letter_portrait.pdf"],
        ["letter_portrait.pdf", "--set", "identifier=99-2026-999901"],
        ["missing.pdf"],
        ["-x"],
    ],
)
def test_options_or_missing_files_go_to_the_cli(argv: list[str], corpus_dir: Path) -> None:
    argv = [str(corpus_dir / a) if a.endswith(".pdf") and a != "missing.pdf" else a for a in argv]
    assert entry.route(argv).kind == "cli"


def test_main_dispatches(monkeypatch: pytest.MonkeyPatch, corpus_dir: Path) -> None:
    calls: list[object] = []
    import stamper.gui.app as app

    monkeypatch.setattr(app, "run_window", lambda files: calls.append(("window", tuple(files))) or 0)
    monkeypatch.setattr(entry, "cli_main", lambda args: calls.append(("cli", tuple(args))) or 7)
    f = corpus_dir / "letter_portrait.pdf"
    assert entry.main([]) == 0
    assert entry.main([str(f)]) == 0
    assert entry.main(["--version"]) == 7
    assert calls == [("window", ()), ("window", (f,)), ("cli", ("--version",))]


def test_windowed_exe_opens_the_window_for_a_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # DocumentStamper.exe has no console (sys.stdout is None): Send To on a folder must show something.
    calls: list[object] = []
    import stamper.gui.app as app

    monkeypatch.setattr(app, "run_window", lambda files: calls.append(("window", tuple(files))) or 0)
    monkeypatch.setattr(entry, "cli_main", lambda args: calls.append(("cli", tuple(args))) or 5)
    monkeypatch.setattr(sys, "stdout", None)
    assert entry.main([str(tmp_path)]) == 0
    assert entry.main(["--version"]) == 5  # an option still means the command line
    assert calls == [("window", (tmp_path,)), ("cli", ("--version",))]


def test_window_says_a_folder_isnt_a_file(window: MainWindow, tmp_path: Path) -> None:
    window.open_file(tmp_path)
    assert "is a folder" in window.drop.message.text()


def test_stamper_cli_entry_point_always_runs_the_cli(corpus_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "o.pdf"
    p = subprocess.run(
        [sys.executable, "-c", "import sys; from stamper.cli import main; sys.exit(main())",
         str(corpus_dir / "letter_portrait.pdf"), "--set", "identifier=X1", "--out", str(out)],
        cwd=REPO, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    assert out.is_file()


# ------------------------------------------------------------ the drop screen


def test_several_files_open_the_files_screen(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    # Replaces the D-063 refusal (Phase 4.5, D-100): several files go to the files screen.
    window.open_files([corpus_dir / "letter_portrait.pdf", corpus_dir / "rotate_90.pdf"])
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.files_screen, timeout=15000)
    assert [p.name for p in window.files_screen.paths()] == ["letter_portrait.pdf", "rotate_90.pdf"]


def test_not_a_pdf(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    notes = tmp_path / "notes.pdf"
    notes.write_text("not a pdf", encoding="utf-8")
    window.open_file(notes)
    assert window.stack.currentWidget() is window.drop
    assert window.drop.message.text() == "notes.pdf isn't a PDF file. Choose a PDF to stamp."


def test_missing_file(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    window.open_file(tmp_path / "gone.pdf")
    assert window.drop.message.text() == f"Can't find the file {tmp_path / 'gone.pdf'}."


def test_xfa_refused(qtbot: QtBot, window: MainWindow, corpus_dir: Path, tmp_path: Path) -> None:
    window.open_file(make_xfa_pdf(corpus_dir, tmp_path / "form.pdf"))
    qtbot.waitUntil(lambda: "XFA" in window.drop.message.text(), timeout=15000)
    assert window.stack.currentWidget() is window.drop
    assert window.drop.message.text() == (
        "form.pdf is an XFA form, which this tool can't stamp. "
        "Print it to PDF first, then stamp the printed copy.")


def test_choose_file_uses_the_dialog(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                     corpus_dir: Path) -> None:
    fake.open_files.append(corpus_dir / "letter_portrait.pdf")
    window.choose_file()
    qtbot.waitUntil(lambda: window.dialog is not None, timeout=15000)


def test_drop_event_opens_one_file(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(corpus_dir / "letter_portrait.pdf"))])
    event = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    window.dropEvent(event)
    qtbot.waitUntil(lambda: window.dialog is not None, timeout=15000)


def test_profiles_and_another_config(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                     tmp_path: Path) -> None:
    assert window.drop.profile_combo.currentText() == "Records submission"
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    fake.config_files.append(bad)
    window.choose_config()
    assert window.drop.message.text().startswith("bad.json can't be used:\n• line 1, column 2")
    assert window.config_path == RECORDS_CONFIG  # the working config stays loaded
    two = tmp_path / "two.json"
    text = RECORDS_CONFIG.read_text(encoding="utf-8").replace(
        '"profiles": [', '"profiles": [{"name": "Short", "fields": ["identifier"], '
        '"stamps": [{"text": "{identifier}"}]},', 1)
    two.write_text(text, encoding="utf-8")
    fake.config_files.append(two)
    window.choose_config()
    combo = window.drop.profile_combo
    assert [combo.itemText(i) for i in range(combo.count())] == ["Short", "Records submission"]
    assert combo.currentText() == "Records submission" and combo.isEnabled()
    combo.setCurrentIndex(0)
    combo.activated.emit(0)
    assert window.profile is not None and window.profile.name == "Short"


def test_cli_without_a_console_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    # The windowed exe has no console: sys.stdout and sys.stderr are None (D-087).
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    with pytest.raises(SystemExit) as stop:  # argparse's own exit for a bad option
        entry.main(["--no-such-option"])
    assert stop.value.code == 2
    assert entry.main(["missing.pdf", "--set", "identifier=X"]) == 5  # an error sentence, no crash
