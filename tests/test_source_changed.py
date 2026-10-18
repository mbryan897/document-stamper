"""A source that changes after it was opened is refused, without hashing (D-125)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from stamper.cli import _ERROR_CODES, ExitCode
from stamper.gui.main_window import MainWindow
from stamper.handlers.base import SourceChanged, SourceFingerprint, check_unchanged
from stamper.handlers.batch import BatchItem, Status, check_batch, run_batch
from stamper.handlers.combine import CombineInput, combine_pdfs
from stamper.handlers.pdf import PdfHandler
from tests.gui_support import (  # noqa: F401 - fixtures
    FakeServices,
    choose,
    copy_to,
    fake,
    open_dialog,
    type_into,
    wait_ready,
    window,
)
from tests.stamping import default_job, sha256
from tools import make_corpus

MESSAGE = "changed after you opened it"


def _touch(path: Path) -> None:
    """Change only the modification time (a re-save that kept the same bytes)."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def _replace_keeping_size_and_time(path: Path, other: Path) -> None:
    """Swap in a different file with the same size and modification time; only the file ID differs."""
    st = path.stat()
    data = other.read_bytes()[: st.st_size].ljust(st.st_size, b" ")
    swap = path.with_name("swap.tmp")
    swap.write_bytes(data)
    os.utime(swap, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(swap, path)


def _left(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name.startswith("~"))


def test_inspect_records_the_fingerprint(corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    info = PdfHandler().inspect(src)
    st = src.stat()
    assert info.fingerprint == SourceFingerprint(st.st_size, st.st_mtime_ns, st.st_dev, st.st_ino)
    check_unchanged(src, info.fingerprint)  # no change: no error
    check_unchanged(src, None)  # nothing to compare: no error


@pytest.mark.parametrize("change", ["touch", "append", "swap"])
def test_apply_refuses_a_changed_source(corpus_dir: Path, tmp_path: Path, change: str) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    handler = PdfHandler()
    info = handler.inspect(src)
    if change == "touch":
        _touch(src)
    elif change == "append":
        with src.open("ab") as f:
            f.write(b"\n% appended\n")
    else:
        _replace_keeping_size_and_time(src, corpus_dir / "rotate_90.pdf")
    before = sha256(src)
    out = tmp_path / "copy.pdf"
    with pytest.raises(SourceChanged, match=MESSAGE):
        handler.plan(src, default_job(), expect=info.fingerprint)
    with pytest.raises(SourceChanged, match=MESSAGE):
        handler.render_preview(src, 1, default_job(), expect=info.fingerprint)
    with pytest.raises(SourceChanged, match=MESSAGE):
        handler.apply(src, default_job(), out, expect=info.fingerprint)
    assert not out.exists() and _left(tmp_path) == []
    assert sha256(src) == before


def test_apply_checks_again_just_before_writing(corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    handler = PdfHandler()
    info = handler.inspect(src)
    out = tmp_path / "copy.pdf"
    calls = []

    def progress(done: int, total: int) -> None:
        if done == total and not calls:
            calls.append(done)
            _touch(src)  # changes while the pages are being stamped

    with pytest.raises(SourceChanged):
        handler.apply(src, default_job(), out, expect=info.fingerprint, progress=progress)
    assert not out.exists() and _left(tmp_path) == []


def test_missing_source_is_reported(corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    info = PdfHandler().inspect(src)
    src.unlink()
    with pytest.raises(SourceChanged, match="can't be found any more"):
        check_unchanged(src, info.fingerprint)


def test_unchanged_source_stamps_normally(corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    handler = PdfHandler()
    info = handler.inspect(src)
    result = handler.apply(src, default_job(), tmp_path / "copy.pdf", expect=info.fingerprint)
    assert result.output_path.is_file()


def test_fingerprint_without_file_ids_compares_size_and_time() -> None:
    a = SourceFingerprint(100, 5, 0, 0)
    assert a.matches(SourceFingerprint(100, 5, 7, 9))
    assert not a.matches(SourceFingerprint(101, 5, 0, 0))
    assert not a.matches(SourceFingerprint(100, 6, 0, 0))
    assert not SourceFingerprint(100, 5, 1, 2).matches(SourceFingerprint(100, 5, 1, 3))


def test_combine_refuses_a_changed_source(corpus_dir: Path, tmp_path: Path) -> None:
    a = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    b = copy_to(corpus_dir / "rotate_90.pdf", tmp_path)
    handler = PdfHandler()
    seen = {p: handler.inspect(p).fingerprint for p in (a, b)}
    _touch(b)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(SourceChanged, match="rotate_90.pdf"):
        combine_pdfs([CombineInput(a, None, seen[a]), CombineInput(b, None, seen[b])], work)
    assert list(work.iterdir()) == []


def test_batch_reports_the_changed_file_and_saves_the_rest(corpus_dir: Path, tmp_path: Path) -> None:
    a = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    b = copy_to(corpus_dir / "rotate_90.pdf", tmp_path)
    handler = PdfHandler()
    items = [BatchItem(p, tmp_path / f"{p.stem}_stamped.pdf", None, handler.inspect(p).fingerprint) for p in (a, b)]
    checks = check_batch(handler, items, default_job())
    assert all(c.ok for c in checks)
    _touch(b)  # changes between the check and the save
    outcomes = run_batch(handler, checks, default_job())
    assert [o.status for o in outcomes] == [Status.SAVED, Status.FAILED]
    assert MESSAGE in outcomes[1].message
    assert not (tmp_path / "rotate_90_stamped.pdf").exists()


def test_cli_maps_source_changed_to_cant_be_stamped() -> None:
    assert (SourceChanged, ExitCode.UNSUPPORTED) in _ERROR_CODES


def test_window_refuses_to_save_a_changed_original(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                                   corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    d = open_dialog(qtbot, window, src)
    type_into(qtbot, d, "identifier", make_corpus.FAKE_CASE_SO)
    choose(d, "doc_type", "Court order")
    wait_ready(qtbot, d)
    assert d.save_button.isEnabled(), d.blockers()
    shutil.copyfile(corpus_dir / "rotate_90.pdf", src)  # re-exported over the original while the window is open
    out = tmp_path / "copy.pdf"
    fake.save_paths.append(out)
    d.save_button.click()
    qtbot.waitUntil(lambda: MESSAGE in d.save_message(), timeout=15000)
    assert window.stack.currentWidget() is d
    assert not out.exists() and _left(tmp_path) == []
