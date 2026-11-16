"""The unexpected-error dialog (step 13).

One handler for errors nothing else caught: ``sys.excepthook`` (a slot on
the window's thread, or on the engine thread, D-070, where PySide calls the
hook too) and ``threading.excepthook``. Instead of a vanished window it says
one plain sentence, then the program closes.

Before the dialog shows, the window's own clean-up runs, the same as when it
is closed: a save in progress is cancelled and its temporary file removed
(D-055), the engine thread stopped, the combine work folder and the Profile
settings sample page removed.

"Copy details" gives the error's type and where it happened as module name
and line number only. Never the error's message, values or full file paths:
any of them can hold a document name or a case number. Nothing is written
to disk (D-007).

Errors the program expects, with their own words for the user (a wrong
password, a stamp that doesn't fit, a refused output, and the engine's
"Something went wrong (…)" for a failed file), never come here.
"""

from __future__ import annotations

import sys
import threading
import traceback
from collections.abc import Callable
from types import TracebackType

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

__all__ = ["MESSAGE", "CrashDialog", "CrashReporter", "crash_details", "install"]

MESSAGE = "Something went wrong and Document Stamper needs to close. Your original files have not been changed."
MAX_FRAMES = 40  # innermost frames kept per error
MAX_CHAIN = 5  # errors followed through "caused by" and "while handling"


def _type_name(exc_type: type[BaseException]) -> str:
    module = getattr(exc_type, "__module__", "") or ""
    name = getattr(exc_type, "__qualname__", "") or getattr(exc_type, "__name__", "Exception")
    return name if module in ("builtins", "__main__", "") else f"{module}.{name}"


def _frames(tb: TracebackType | None) -> list[str]:
    lines: list[str] = []
    for frame, line in traceback.walk_tb(tb):
        module = frame.f_globals.get("__name__")
        lines.append(f"  {module if isinstance(module, str) else '?'} line {line}")
    return lines[-MAX_FRAMES:]


def crash_details(exc_type: type[BaseException], exc: BaseException | None, tb: TracebackType | None) -> str:
    """The text "Copy details" gives: types and module:line frames, nothing else."""
    from ..cli import get_version
    from ..core.locations import is_frozen

    out = [f"Document Stamper {get_version()} ({'packaged' if is_frozen() else 'from source'})",
           f"Thread: {'engine' if QThread.currentThread().objectName() == 'stamper-engine' else 'window'}",
           _type_name(exc_type), *_frames(tb)]
    seen = {id(exc)}
    current = exc
    for _ in range(MAX_CHAIN):
        if current is None:
            break
        nxt = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
        if nxt is None or id(nxt) in seen:
            break
        seen.add(id(nxt))
        out += ["Raised while handling:", _type_name(type(nxt)), *_frames(nxt.__traceback__)]
        current = nxt
    return "\n".join(out) + "\n"


class CrashDialog(QDialog):
    """The sentence, Copy details and Close."""

    def __init__(self, details: str) -> None:
        super().__init__()
        self.details = details
        self.setWindowTitle("Document Stamper")
        self.setObjectName("ground")
        lay = QVBoxLayout(self)
        text = QLabel(MESSAGE)
        text.setTextFormat(Qt.TextFormat.PlainText)
        text.setWordWrap(True)
        text.setMinimumWidth(420)
        lay.addWidget(text)
        row = QHBoxLayout()
        self.copy_button = QPushButton("Copy details")
        self.copy_button.setObjectName("outline")
        self.copy_button.setToolTip("The kind of error and where in the program it happened; "
                                    "no file names or values")
        self.copy_button.clicked.connect(self.copy)
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(self.copy_button)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)

    def copy(self) -> None:
        """Put the details on the clipboard."""
        QGuiApplication.clipboard().setText(self.details)


def _show(details: str) -> None:
    CrashDialog(details).exec()


class CrashReporter(QObject):
    """Takes an unexpected error from any thread to the window's thread, cleans up and says so.

    ``cleanup`` is the window's own clean-up; ``show(details)`` the dialog;
    ``quit()`` ends the program. Tests pass their own ``show`` and ``quit``.
    Only the first error is reported; the program is closing after it.
    """

    _relay = Signal(str)

    def __init__(self, cleanup: Callable[[], None] | None = None, *,
                 show: Callable[[str], None] = _show, quit: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.cleanup = cleanup
        self._show = show
        self._quit = quit or (lambda: QApplication.exit(1))
        self.reported: list[str] = []
        self._relay.connect(self._report, Qt.ConnectionType.QueuedConnection)

    def excepthook(self, exc_type: type[BaseException], exc: BaseException | None,
                   tb: TracebackType | None) -> None:
        """``sys.excepthook``: works out the details here, reports them on the window's thread."""
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)  # type: ignore[arg-type]
            return
        try:
            details = crash_details(exc_type, exc, tb)
        except Exception:  # noqa: BLE001 - the report must still be made
            details = "Document Stamper\n(details unavailable)\n"
        finally:
            if exc is not None:
                exc.__traceback__ = None  # its frames may hold PDF objects; free them here
            del tb
        app = QApplication.instance()
        if app is not None and QThread.currentThread() is app.thread() and app.thread().loopLevel() == 0:
            self._report(details)  # no event loop yet (or any more): report now
        else:
            self._relay.emit(details)  # queued: runs on the window's thread once the hook returns

    def thread_hook(self, args: threading.ExceptHookArgs) -> None:
        """``threading.excepthook``: a Python thread's error goes the same way."""
        self.excepthook(args.exc_type, args.exc_value, args.exc_traceback)

    def _report(self, details: str) -> None:
        if self.reported:
            return
        self.reported.append(details)
        if self.cleanup is not None:
            try:
                self.cleanup()
            except Exception:  # noqa: BLE001 - closing anyway; the dialog still shows
                pass
        try:
            self._show(details)
        finally:
            self._quit()


def install(cleanup: Callable[[], None] | None = None) -> CrashReporter:
    """Make a CrashReporter the handler for ``sys`` and ``threading``; returns it."""
    reporter = CrashReporter(cleanup)
    sys.excepthook = reporter.excepthook
    threading.excepthook = reporter.thread_hook
    return reporter
