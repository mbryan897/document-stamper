"""One background thread for every engine call (D-070).

PyMuPDF must not be used from two threads at once, so the window never calls
the handler itself: inspect, preview, plan and apply all run, one at a time,
on a single long-lived ``QThread``. Results come back as signals on the GUI
thread.

Preview and plan requests carry a number. Only the newest request of each
kind is worked on: an older one still waiting in the queue is skipped, and an
older plan already running is cancelled between pages. A save is never
skipped; Cancel sets an event that the handler's ``cancel()`` callback reads
between pages.

Multi-file work (screening several files, combining, a batch check and a
batch save, D-108) runs as a *task*: a plain function given a ``TaskContext``
for progress and cancellation, run on the same thread, one at a time, like
every other call.
"""

from __future__ import annotations

import threading
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..handlers.base import Cancelled, Handler, SourceFingerprint, StampError, StampJob, without_traceback

__all__ = ["PROGRESS_INTERVAL_S", "ApplyRequest", "EngineClient", "EngineFailure", "TaskContext"]

PROGRESS_INTERVAL_S = 0.05


@dataclass(frozen=True, slots=True)
class EngineFailure:
    """A failed engine call: the handler's own sentence, and the error if it was a StampError."""

    message: str
    error: Exception


@dataclass(frozen=True, slots=True)
class ApplyRequest:
    """Everything ``Handler.apply`` needs from the dialog."""

    path: Path
    job: StampJob
    out_path: Path
    password: str | None
    accept_signature_loss: bool
    accept_restrictions: bool
    expect: SourceFingerprint | None = None  # refuse a source that changed since it was opened (D-125)
    out_password: str | None = None  # a new password for the copy; used for the save, never kept
    remove_encryption: bool = False  # "No password" for a protected original (D-050 as amended)


class TaskContext:
    """What a task function gets: ``progress(done, total, text)`` and ``cancelled()``.

    Progress is throttled like a save's, so a fast task doesn't flood the GUI
    thread; the first and last step always get through.
    """

    def __init__(self, req: int, emit: Callable[[int, int, int, str], None], stop: threading.Event) -> None:
        self.req = req
        self._emit = emit
        self._stop = stop
        self._last = 0.0

    def progress(self, done: int, total: int, text: str = "") -> None:
        """Report progress to the GUI thread."""
        now = time.monotonic()
        if done in (0, 1, total) or now - self._last >= PROGRESS_INTERVAL_S:
            self._last = now
            self._emit(self.req, done, total, text)

    def cancelled(self) -> bool:
        """True once the user has pressed Cancel (or the window is closing)."""
        return self._stop.is_set()


def _failure(error: Exception) -> EngineFailure:
    without_traceback(error)  # it goes to the GUI thread; its frames' PDF objects must be freed here
    if isinstance(error, StampError):
        return EngineFailure(str(error), error)
    return EngineFailure(
        f"Something went wrong ({type(error).__name__}: {error}). Nothing was saved over the original.",
        error,
    )


class _Worker(QObject):
    """Lives on the engine thread; every slot runs there."""

    inspected = Signal(int, object)  # request, DocumentInfo
    inspect_failed = Signal(int, object)  # request, EngineFailure
    previewed = Signal(int, bytes)  # request, PNG
    preview_failed = Signal(int, object)
    planned = Signal(int, object)  # request, PlanResult
    plan_failed = Signal(int, object)
    progress = Signal(int, int)  # done, total
    applied = Signal(object)  # ApplyResult
    apply_failed = Signal(object)
    task_done = Signal(int, object)  # request, result
    task_failed = Signal(int, object)  # request, EngineFailure
    task_progress = Signal(int, int, int, str)  # request, done, total, text

    def __init__(self, handler: Handler, client: EngineClient) -> None:
        super().__init__()
        self._handler = handler
        # Weak: a strong reference back would put the client, its QThread and this worker in a
        # reference cycle, which the garbage collector frees on whichever thread it runs on (a later
        # window's engine thread, say), deleting Qt objects off their thread.
        self._client = weakref.proxy(client)

    @Slot(int, object, object)
    def inspect(self, req: int, path: Path, password: object) -> None:
        try:
            info = self._handler.inspect(path, password)  # type: ignore[arg-type]
        except Exception as e:  # reported, never raised into Qt
            self.inspect_failed.emit(req, _failure(e))
        else:
            self.inspected.emit(req, info)

    @Slot(int, object, int, object, object, int)
    def preview(self, req: int, path: Path, page: int, job: StampJob, password: object, dpi: int) -> None:
        if req != self._client.latest_preview:
            return
        try:
            png = self._handler.render_preview(path, page, job, password=password, dpi=dpi)  # type: ignore[arg-type]
        except Exception as e:
            self.preview_failed.emit(req, _failure(e))
        else:
            self.previewed.emit(req, png)

    @Slot(int, object, object, object, object)
    def plan(self, req: int, path: Path, job: StampJob, password: object, expect: object) -> None:
        if req != self._client.latest_plan:
            return
        try:
            result = self._handler.plan(
                path, job, password=password,  # type: ignore[arg-type]
                cancel=lambda: req != self._client.latest_plan,
                expect=expect,  # type: ignore[arg-type]
            )
        except Cancelled:
            return  # a newer plan is on its way
        except Exception as e:
            self.plan_failed.emit(req, _failure(e))
        else:
            self.planned.emit(req, result)

    @Slot(object)
    def apply(self, request: ApplyRequest) -> None:
        stop = self._client.cancel_event
        last = [0.0]

        def progress(done: int, total: int) -> None:
            # At most one update every PROGRESS_INTERVAL_S, plus the first and last
            # page, so a fast save doesn't flood the GUI thread's event queue.
            now = time.monotonic()
            if done in (1, total) or now - last[0] >= PROGRESS_INTERVAL_S:
                last[0] = now
                self.progress.emit(done, total)

        try:
            result = self._handler.apply(
                request.path, request.job, request.out_path,
                password=request.password,
                accept_signature_loss=request.accept_signature_loss,
                accept_restrictions=request.accept_restrictions,
                overwrite=False,  # never from the window (D-075)
                progress=progress,
                cancel=stop.is_set,
                expect=request.expect,
                out_password=request.out_password,
                remove_encryption=request.remove_encryption,
            )
        except Exception as e:
            self.apply_failed.emit(_failure(e))
        else:
            self.applied.emit(result)

    @Slot(int, object)
    def task(self, req: int, fn: object) -> None:
        ctx = TaskContext(req, self.task_progress.emit, self._client.cancel_event)
        try:
            result = fn(ctx)  # type: ignore[operator]
        except Exception as e:  # Cancelled included: the caller decides what to say
            self.task_failed.emit(req, _failure(e))
        else:
            self.task_done.emit(req, result)


class EngineClient(QObject):
    """The GUI thread's handle on the engine thread."""

    inspected = Signal(int, object)
    inspect_failed = Signal(int, object)
    previewed = Signal(int, bytes)
    preview_failed = Signal(int, object)
    planned = Signal(int, object)
    plan_failed = Signal(int, object)
    progress = Signal(int, int)
    applied = Signal(object)
    apply_failed = Signal(object)
    task_done = Signal(int, object)
    task_failed = Signal(int, object)
    task_progress = Signal(int, int, int, str)

    _do_inspect = Signal(int, object, object)
    _do_preview = Signal(int, object, int, object, object, int)
    _do_plan = Signal(int, object, object, object, object)
    _do_apply = Signal(object)
    _do_task = Signal(int, object)

    def __init__(self, handler: Handler, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.latest_preview = 0
        self.latest_plan = 0
        self._next = 0
        self.cancel_event = threading.Event()
        self.previews_started = 0  # for the debounce tests and timing report
        self.plans_started = 0

        self._thread = QThread()
        self._thread.setObjectName("stamper-engine")
        self._worker = _Worker(handler, self)
        self._worker.moveToThread(self._thread)
        for name in ("inspected", "inspect_failed", "previewed", "preview_failed",
                     "planned", "plan_failed", "progress", "applied", "apply_failed",
                     "task_done", "task_failed", "task_progress"):
            getattr(self._worker, name).connect(getattr(self, name))
        self._do_inspect.connect(self._worker.inspect)
        self._do_preview.connect(self._worker.preview)
        self._do_plan.connect(self._worker.plan)
        self._do_apply.connect(self._worker.apply)
        self._do_task.connect(self._worker.task)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    def _number(self) -> int:
        self._next += 1
        return self._next

    def inspect(self, path: Path, password: str | None = None) -> int:
        """Queue ``Handler.inspect``; returns the request number."""
        req = self._number()
        self._do_inspect.emit(req, Path(path), password)
        return req

    def preview(self, path: Path, page: int, job: StampJob, password: str | None, dpi: int) -> int:
        """Queue a preview; any older preview not yet started is dropped."""
        req = self._number()
        self.latest_preview = req
        self.previews_started += 1
        self._do_preview.emit(req, Path(path), page, job, password, dpi)
        return req

    def plan(self, path: Path, job: StampJob, password: str | None,
             expect: SourceFingerprint | None = None) -> int:
        """Queue a whole-document check; an older one is dropped or cancelled.

        ``expect`` makes the check refuse a source that changed since it was opened (D-125).
        """
        req = self._number()
        self.latest_plan = req
        self.plans_started += 1
        self._do_plan.emit(req, Path(path), job, password, expect)
        return req

    def apply(self, request: ApplyRequest) -> None:
        """Queue the save. Pending previews and plans are dropped first."""
        self.cancel_event.clear()
        self.latest_preview = self._number()
        self.latest_plan = self._number()
        self._do_apply.emit(request)

    def run_task(self, fn: Callable[[TaskContext], object], *, clear_cancel: bool = True) -> int:
        """Queue a task function on the engine thread; returns the request number.

        Pending previews and plans are dropped first. ``clear_cancel`` resets
        Cancel for a new piece of work; clean-up tasks pass False so a Cancel
        already pressed still reaches the task that is running.
        """
        if clear_cancel:
            self.cancel_event.clear()
        self.forget()
        req = self._number()
        self._do_task.emit(req, fn)
        return req

    def cancel(self) -> None:
        """Ask a running save to stop between pages."""
        self.cancel_event.set()

    def forget(self) -> None:
        """Drop pending previews and plans (the document was closed)."""
        self.latest_preview = self._number()
        self.latest_plan = self._number()

    def shutdown(self, timeout_ms: int = 30000) -> None:
        """Cancel any save, then stop the thread, waiting for the current call to end.

        Waiting lets the handler remove its temporary file (D-055).
        """
        self.cancel_event.set()
        self.forget()
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(timeout_ms)
