"""Partial copies left by a stopped run: journaled, offered for deletion, never deleted silently (D-124)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest
from pytestqt.qtbot import QtBot

from stamper.cli import main
from stamper.core import pending
from stamper.gui.main_window import MainWindow
from stamper.handlers.base import OutputRefused
from stamper.handlers.pdf import PdfHandler
from tests.gui_support import FakeServices, copy_to, fake  # noqa: F401 - fixtures
from tests.stamping import DEFAULT_CONFIG, FAKE_ID, default_job

REPO = Path(__file__).resolve().parent.parent

_CRASH = """
import os, sys
from pathlib import Path
from stamper.core import pending
tmp = Path(sys.argv[1])
tmp.write_bytes(b"%PDF-1.7 partial")
pending.record(tmp)
os._exit(1)  # killed before the rename: no cleanup runs
"""


def crash_leaving(tmp: Path) -> None:
    """Run a process that journals ``tmp``, writes it and dies without cleaning up."""
    subprocess.run([sys.executable, "-c", _CRASH, str(tmp)], cwd=REPO, env=os.environ.copy(), check=False)


def test_a_save_leaves_no_entry(corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    PdfHandler().apply(src, default_job(), tmp_path / "copy.pdf")
    assert list(pending.journal_dir().glob("*.json")) == []


def test_a_refused_save_leaves_no_entry_or_temp(corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    taken = tmp_path / "copy.pdf"
    taken.write_bytes(b"existing")
    with pymupdf.open(src) as doc, pytest.raises(OutputRefused):
        PdfHandler._save_atomic(doc, taken, pymupdf.PDF_ENCRYPT_KEEP, overwrite=False)
    assert list(pending.journal_dir().glob("*.json")) == []
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("~")] == []


def test_an_unwritable_journal_doesnt_stop_a_save(corpus_dir: Path, tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    monkeypatch.setenv("STAMPER_LOCAL_DIR", str(blocker))
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    assert PdfHandler().apply(src, default_job(), tmp_path / "copy.pdf").output_path.is_file()


def test_a_crash_is_found_and_deleted_on_request(tmp_path: Path) -> None:
    tmp = tmp_path / "~stamper-abc123.tmp"
    crash_leaving(tmp)
    assert tmp.is_file()
    found = pending.leftovers()
    assert [lo.path for lo in found] == [tmp.resolve()]
    assert found[0].size == len(b"%PDF-1.7 partial")
    pending.delete(found[0])
    assert not tmp.exists()
    assert pending.leftovers() == []


def test_keep_leaves_the_file_and_stops_asking(tmp_path: Path) -> None:
    tmp = tmp_path / "~stamper-keep.tmp"
    crash_leaving(tmp)
    (lo,) = pending.leftovers()
    pending.keep(lo)
    assert tmp.is_file()
    assert pending.leftovers() == []


def test_a_running_save_is_left_alone(tmp_path: Path) -> None:
    tmp = tmp_path / "~stamper-live.tmp"
    tmp.write_bytes(b"in progress")
    entry = pending.record(tmp)  # this process: still running
    assert entry is not None
    assert pending.leftovers() == []
    assert pending.leftovers(alive=lambda _pid: False)[0].path == tmp.resolve()


def test_a_file_that_is_gone_is_dropped_quietly(tmp_path: Path) -> None:
    tmp = tmp_path / "~stamper-gone.tmp"
    crash_leaving(tmp)
    tmp.unlink()
    assert pending.leftovers() == []
    assert list(pending.journal_dir().glob("*.json")) == []


def test_a_folder_in_its_place_is_never_deleted(tmp_path: Path) -> None:
    tmp = tmp_path / "~stamper-dir.tmp"
    crash_leaving(tmp)
    (lo,) = pending.leftovers()
    tmp.unlink()
    tmp.mkdir()
    (tmp / "evidence.pdf").write_bytes(b"keep me")
    with pytest.raises(OSError, match="no longer the partial copy"):
        pending.delete(lo)
    assert (tmp / "evidence.pdf").read_bytes() == b"keep me"
    assert pending.leftovers() == []


def test_a_link_in_its_place_is_never_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "evidence.pdf"
    target.write_bytes(b"keep me")
    tmp = tmp_path / "~stamper-link.tmp"
    crash_leaving(tmp)
    (lo,) = pending.leftovers()
    tmp.unlink()
    if os.name == "nt":
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(tmp), str(outside)], capture_output=True)
        if made.returncode != 0:
            pytest.skip("couldn't make a junction")
    else:
        tmp.symlink_to(target)
    with pytest.raises(OSError):
        pending.delete(lo)
    assert target.read_bytes() == b"keep me"
    assert pending.leftovers() == []


def test_a_name_that_isnt_ours_is_ignored(tmp_path: Path) -> None:
    other = tmp_path / "report.pdf"
    other.write_bytes(b"not ours")
    pending.journal_dir().mkdir(parents=True, exist_ok=True)
    (pending.journal_dir() / "planted.json").write_text(json.dumps({"path": str(other), "pid": 999999999}))
    assert pending.leftovers() == []
    assert other.read_bytes() == b"not ours"


def test_a_damaged_entry_is_dropped(tmp_path: Path) -> None:
    pending.journal_dir().mkdir(parents=True, exist_ok=True)
    bad = pending.journal_dir() / "bad.json"
    bad.write_text("{not json")
    assert pending.leftovers() == []
    assert not bad.exists()


def test_describe_one_and_several(tmp_path: Path) -> None:
    for name in ("~stamper-a.tmp", "~stamper-b.tmp"):
        crash_leaving(tmp_path / name)
    found = pending.leftovers()
    one = pending.describe(found[:1])
    assert one.startswith("A partial copy was left behind") and "not the original" in one
    several = pending.describe(found)
    assert several.count("• ") == 2 and "not originals" in several


def test_cli_notes_but_never_deletes(corpus_dir: Path, tmp_path: Path) -> None:
    tmp = tmp_path / "~stamper-cli.tmp"
    crash_leaving(tmp)
    out, err = io.StringIO(), io.StringIO()
    main([str(corpus_dir / "letter_portrait.pdf"), "--dry-run", "--config", str(DEFAULT_CONFIG),
          "--set", f"identifier={FAKE_ID}", "--set", "doc_type=Report"],
         stdout=out, stderr=err, stdin=io.StringIO(""))
    assert "A partial copy was left behind" in out.getvalue()
    assert "delete or keep it" in out.getvalue()
    assert tmp.is_file() and len(pending.leftovers()) == 1


def _window(qtbot: QtBot, fake: FakeServices) -> MainWindow:
    w = MainWindow(config_path=DEFAULT_CONFIG, services=fake.services())
    qtbot.addWidget(w)
    return w


def test_window_offers_delete(qtbot: QtBot, fake: FakeServices, tmp_path: Path) -> None:
    tmp = tmp_path / "~stamper-gui.tmp"
    crash_leaving(tmp)
    w = _window(qtbot, fake)
    assert not w.drop.leftover_frame.isHidden()
    assert "A partial copy was left behind" in w.drop.leftover_label.text()
    assert w.drop.leftover_delete.text() == "Delete"
    w.drop.leftover_show.click()
    assert fake.shown == [tmp.resolve()]
    assert tmp.is_file()
    w.drop.leftover_delete.click()
    assert not tmp.exists()
    assert w.drop.leftover_frame.isHidden()


def test_window_keep_all(qtbot: QtBot, fake: FakeServices, tmp_path: Path) -> None:
    for name in ("~stamper-1.tmp", "~stamper-2.tmp"):
        crash_leaving(tmp_path / name)
    w = _window(qtbot, fake)
    assert w.drop.leftover_keep.text() == "Keep all" and w.drop.leftover_show.isHidden()
    w.drop.leftover_keep.click()
    assert w.drop.leftover_frame.isHidden()
    assert (tmp_path / "~stamper-1.tmp").is_file() and (tmp_path / "~stamper-2.tmp").is_file()
    w2 = _window(qtbot, fake)
    assert w2.drop.leftover_frame.isHidden()


def test_window_without_leftovers_shows_nothing(qtbot: QtBot, fake: FakeServices) -> None:
    w = _window(qtbot, fake)
    assert w.drop.leftover_frame.isHidden()
