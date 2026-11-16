"""The unexpected-error dialog (step 13).

An error nothing else caught, in a slot on the window's thread or on the
engine thread, reaches one handler: the window cleans up as it does on
close, the dialog shows one sentence, Copy details gives types and
module:line only, and the program ends. The real dialog and the real exit
are replaced; synthetic files only (99-2026-999901).
"""

from __future__ import annotations

import re
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from pytestqt.qtbot import QtBot

from stamper.core.locations import work_dir
from stamper.gui import crash
from stamper.gui.app import make_app
from stamper.gui.crash import MESSAGE, CrashDialog, CrashReporter, crash_details
from stamper.gui.main_window import MainWindow
from tests.gui_support import WAIT_MS, FakeServices, close_window, copy_to, fake  # noqa: F401
from tests.stamping import DEFAULT_CONFIG

pytestmark = pytest.mark.qt_no_exception_capture  # these tests install their own sys.excepthook

SECRET = "99-2026-999901"
_DETAIL_LINE = re.compile(r"(  [A-Za-z_][\w.]* line \d+|  \? line \d+|Raised while handling:|[A-Za-z_][\w.]*)")
SECRET_NAME = f"Sealed-{SECRET}.pdf"


class Planted(RuntimeError):
    """An error whose message carries a case number and a path, as a real one might."""


class BadMessage(Exception):
    """An error whose message itself fails: _failure() can't word it, so it escapes the engine's slot."""

    def __str__(self) -> str:
        raise ValueError(f"no words for C:\\Cases\\{SECRET_NAME}")


@pytest.fixture
def reporter() -> Iterator[tuple[CrashReporter, list[str], list[bool]]]:
    """A CrashReporter installed as the hooks, with the dialog and the exit recorded."""
    make_app()
    shown: list[str] = []
    quit_called: list[bool] = []
    r = CrashReporter(show=shown.append, quit=lambda: quit_called.append(True))
    old_sys, old_thread = sys.excepthook, threading.excepthook
    sys.excepthook = r.excepthook
    threading.excepthook = r.thread_hook
    yield r, shown, quit_called
    sys.excepthook, threading.excepthook = old_sys, old_thread


def _raise_planted(where: Path) -> None:
    raise Planted(f"failed on {where} for case {SECRET}")


def _assert_clean(details: str, *secrets: str) -> None:
    for s in (SECRET, SECRET_NAME, "C:\\", "/tmp", *secrets):
        assert s not in details, s
    for line in details.splitlines()[2:]:
        assert _DETAIL_LINE.fullmatch(line), line


def test_the_sentence_is_the_agreed_wording() -> None:
    assert MESSAGE == ("Something went wrong and Document Stamper needs to close. "
                       "Your original files have not been changed.")


def test_details_are_types_and_module_lines_only(tmp_path: Path) -> None:
    try:
        try:
            _raise_planted(tmp_path / SECRET_NAME)
        except Planted as inner:
            raise KeyError(f"{SECRET}") from inner
    except KeyError as e:
        details = crash_details(type(e), e, e.__traceback__)
    _assert_clean(details, str(tmp_path))
    lines = details.splitlines()
    assert lines[0].startswith("Document Stamper ") and lines[1] == "Thread: window"
    assert lines[2] == "KeyError"
    assert "  tests.test_unexpected_error line " in details
    assert "Raised while handling:" in details and "tests.test_unexpected_error.Planted" in details
    assert all(not ln.strip().startswith(("File ", "Traceback")) for ln in lines)


def test_a_slot_error_on_the_window_thread_reaches_the_dialog(qtbot: QtBot, reporter, tmp_path: Path) -> None:
    r, shown, quit_called = reporter
    QTimer.singleShot(0, lambda: _raise_planted(tmp_path / SECRET_NAME))
    qtbot.waitUntil(lambda: bool(shown), timeout=WAIT_MS)
    assert quit_called == [True]
    assert shown[0].splitlines()[1:3] == ["Thread: window", "tests.test_unexpected_error.Planted"]
    _assert_clean(shown[0], str(tmp_path))


def test_only_the_first_error_is_reported(qtbot: QtBot, reporter, tmp_path: Path) -> None:
    r, shown, quit_called = reporter
    QTimer.singleShot(0, lambda: _raise_planted(tmp_path))
    QTimer.singleShot(0, lambda: _raise_planted(tmp_path))
    qtbot.waitUntil(lambda: bool(shown), timeout=WAIT_MS)
    qtbot.wait(100)
    assert len(shown) == 1 and quit_called == [True]


def test_a_python_thread_error_reaches_the_dialog(qtbot: QtBot, reporter, tmp_path: Path) -> None:
    r, shown, _quit = reporter
    t = threading.Thread(target=_raise_planted, args=(tmp_path / SECRET_NAME,))
    t.start()
    t.join()
    qtbot.waitUntil(lambda: bool(shown), timeout=WAIT_MS)
    _assert_clean(shown[0], str(tmp_path))


def test_copy_details_button_copies_the_details_only(qtbot: QtBot) -> None:
    make_app()
    d = CrashDialog("Document Stamper 1.0.0 (from source)\nThread: window\nKeyError\n  stamper.x line 1\n")
    qtbot.addWidget(d)
    d.show()
    d.copy_button.click()
    assert QGuiApplication.clipboard().text() == d.details
    assert d.isVisible()  # copying doesn't close it


def test_engine_thread_error_reaches_the_dialog_and_cleans_up(qtbot: QtBot, fake: FakeServices, reporter,
                                                              corpus_dir: Path, tmp_path: Path) -> None:
    """An error escaping the engine's own handling, during a combine: the working copy is removed."""
    from stamper.gui.multi import CombineDialog
    from tests.gui_support import wait_ready

    r, shown, quit_called = reporter
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    files = [copy_to(corpus_dir / n, evidence) for n in ("letter_portrait.pdf", "rotate_90.pdf")]
    make_app()
    w = MainWindow(config_path=DEFAULT_CONFIG, services=fake.services())
    qtbot.addWidget(w)
    r.cleanup = w.release
    try:
        w.open_files(files)
        qtbot.waitUntil(lambda: w.stack.currentWidget() is w.files_screen, timeout=WAIT_MS)
        w.files_screen.combine_button.click()
        qtbot.waitUntil(lambda: isinstance(w.dialog, CombineDialog), timeout=WAIT_MS)
        wait_ready(qtbot, w.dialog)
        combined = w._combined
        assert combined is not None and combined.path.is_file()
        assert combined.folder.parent == work_dir()

        def fail(_ctx: object) -> None:
            raise BadMessage()

        w.engine.run_task(fail)
        qtbot.waitUntil(lambda: bool(shown), timeout=WAIT_MS)
        assert shown[0].splitlines()[1] == "Thread: engine"
        assert "stamper.gui.engine line " in shown[0]
        _assert_clean(shown[0], str(tmp_path), str(evidence))
        assert quit_called == [True]
        # The window's own clean-up ran before the dialog: the working copy and its folder are gone,
        # nothing is left in the work folder or beside the originals, and the originals are as they were.
        assert not combined.folder.exists()
        left = list(work_dir().iterdir()) if work_dir().exists() else []
        assert not [p for p in left if p.name.startswith("~stamper-")]
        assert sorted(p.name for p in evidence.iterdir()) == ["letter_portrait.pdf", "rotate_90.pdf"]
        assert w.dialog is None and not w.engine._thread.isRunning()
    finally:
        close_window(w)


def test_a_save_in_progress_is_cancelled_and_its_temp_file_removed(qtbot: QtBot, fake: FakeServices, reporter,
                                                                    tmp_path: Path) -> None:
    """The window's clean-up waits for the engine, so a temporary file being written is removed (D-055)."""
    r, shown, _quit = reporter
    make_app()
    w = MainWindow(config_path=DEFAULT_CONFIG, services=fake.services())
    qtbot.addWidget(w)
    r.cleanup = w.release
    tmp = tmp_path / "~stamper-test.tmp"
    started = threading.Event()

    def long_save(ctx: object) -> None:
        tmp.write_bytes(b"partial")
        started.set()
        try:
            while not ctx.cancelled():  # type: ignore[attr-defined]
                threading.Event().wait(0.01)
        finally:
            tmp.unlink()  # what the handler does when a save is cancelled

    try:
        w.engine.run_task(long_save)
        assert started.wait(10)
        QTimer.singleShot(0, lambda: _raise_planted(tmp_path))
        qtbot.waitUntil(lambda: bool(shown), timeout=WAIT_MS)
        assert not tmp.exists()
    finally:
        close_window(w)


def test_expected_errors_keep_their_own_words(qtbot: QtBot, fake: FakeServices, reporter, tmp_path: Path) -> None:
    """A refused output or any other error the engine words for the user never reaches the dialog."""
    from stamper.handlers.base import OutputRefused

    r, shown, _quit = reporter
    make_app()
    w = MainWindow(config_path=DEFAULT_CONFIG, services=fake.services())
    qtbot.addWidget(w)
    failures: list[object] = []
    w.engine.task_failed.connect(lambda _req, f: failures.append(f))
    try:
        def refuse(_ctx: object) -> None:
            raise OutputRefused("That file already exists.")

        w.engine.run_task(refuse)
        qtbot.waitUntil(lambda: bool(failures), timeout=WAIT_MS)
        qtbot.wait(100)
        assert shown == []
        assert failures[0].message == "That file already exists."  # type: ignore[attr-defined]
    finally:
        close_window(w)


def test_run_window_installs_the_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    from stamper.gui import app as app_module

    seen: list[object] = []

    class FakeWindow:
        def __init__(self) -> None:
            seen.append(sys.excepthook)

        def release(self) -> None:
            pass

        def show_fitted(self) -> None:
            pass

    monkeypatch.setattr(app_module, "MainWindow", FakeWindow)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    qapp = make_app()
    monkeypatch.setattr(qapp, "exec", lambda: 0)
    assert app_module.run_window() == 0
    hook = seen[0]
    assert getattr(hook, "__self__", None).__class__ is crash.CrashReporter  # installed before the window
    assert hook.__self__.cleanup is not None  # type: ignore[union-attr]
