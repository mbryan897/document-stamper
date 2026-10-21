"""PDF and Qt objects are freed on the thread that made them (MuPDF isn't thread-safe)."""

from __future__ import annotations

import gc
import weakref
from pathlib import Path

from pytestqt.qtbot import QtBot

from stamper.gui.app import make_app
from stamper.gui.engine import EngineClient
from stamper.handlers.base import PasswordRequired
from stamper.handlers.batch import BatchItem, check_batch
from stamper.handlers.pdf import PdfHandler
from tests.gui_support import WAIT_MS
from tests.multi_support import make_tiny
from tests.stamping import default_job


def test_engine_is_freed_without_the_garbage_collector() -> None:
    # In a reference cycle, the collector would free its QThread later, on whichever thread it ran on.
    make_app()
    engine = EngineClient(PdfHandler())
    engine.shutdown()
    ref = weakref.ref(engine)
    gc.collect()
    gc.disable()
    try:
        del engine
        assert ref() is None
    finally:
        gc.enable()


def test_engine_failures_carry_no_traceback(qtbot: QtBot, corpus_dir: Path) -> None:
    # A traceback keeps the failed call's frames, and their Document, alive on the window's thread.
    make_app()
    engine = EngineClient(PdfHandler())
    try:
        with qtbot.waitSignal(engine.inspect_failed, timeout=WAIT_MS) as sig:
            engine.inspect(corpus_dir / "encrypted_user_password.pdf")
        failure = sig.args[1]
        assert isinstance(failure.error, PasswordRequired)
        assert failure.error.__traceback__ is None
    finally:
        engine.shutdown()


def test_batch_errors_carry_no_traceback(tmp_path: Path) -> None:
    tiny = make_tiny(tmp_path / "tiny.pdf")
    (check,) = check_batch(PdfHandler(), [BatchItem(tiny, tmp_path / "out.pdf")], default_job())
    assert check.error is not None and check.error.__traceback__ is None
