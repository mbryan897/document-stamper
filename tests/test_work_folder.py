"""Combining works in a private folder (D-115); cleanup deletes only what the app made (D-120)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

import stamper.handlers.combine as combine
from stamper.core.locations import work_dir
from stamper.gui.main_window import MainWindow
from stamper.handlers.base import StampError
from stamper.handlers.combine import MARKER_NAME, TEMP_PREFIX, CombineInput, combine_pdfs, remove_stale_temp
from stamper.handlers.pdf import PdfHandler
from tests.gui_support import FakeServices, copy_to, fake  # noqa: F401 - fixtures
from tests.stamping import DEFAULT_CONFIG, default_job, sha256
from tools import make_corpus

PW = make_corpus.USER_PASSWORD
REPO = Path(__file__).resolve().parent.parent


def _two_protected(corpus_dir: Path, folder: Path) -> list[CombineInput]:
    a = copy_to(corpus_dir / "encrypted_user_password.pdf", folder, "locked_a.pdf")
    b = copy_to(corpus_dir / "encrypted_user_password.pdf", folder, "locked_b.pdf")
    return [CombineInput(a, PW), CombineInput(b, PW)]


def _listing(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


@pytest.fixture
def evidence(tmp_path: Path) -> Path:
    folder = tmp_path / "evidence"
    folder.mkdir()
    return folder


# ------------------------------------------------------------ the private folder (D-115)


def test_combine_works_in_the_private_folder(corpus_dir: Path, evidence: Path) -> None:
    inputs = _two_protected(corpus_dir, evidence)
    before = _listing(evidence)
    digests = {i.path: sha256(i.path) for i in inputs}
    with combine_pdfs(inputs, fallback=evidence) as combined:
        assert combined.fallback_reason == "" and combined.location_warning() == ""
        assert combined.folder.parent == work_dir()
        assert (combined.folder / MARKER_NAME).is_file()
        marker = json.loads((combined.folder / MARKER_NAME).read_text(encoding="utf-8"))
        assert marker["pid"] == os.getpid()
        # The decrypted working copy never touches the evidence folder.
        assert _listing(evidence) == before
        out = evidence / "exhibit.pdf"
        PdfHandler().apply(combined.path, default_job(), out)
        assert _listing(evidence) == sorted([*before, "exhibit.pdf"])
    assert list(work_dir().iterdir()) == []
    for path, digest in digests.items():
        assert sha256(path) == digest


@pytest.mark.parametrize("why", ["unwritable", "network", "space"])
def test_fallback_to_the_output_folder_says_why(corpus_dir: Path, evidence: Path, tmp_path: Path, why: str,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    if why == "unwritable":
        blocker = tmp_path / "not-a-folder"
        blocker.write_text("x")
        monkeypatch.setenv("STAMPER_LOCAL_DIR", str(blocker))
        expect = "can't be created"
    elif why == "network":
        monkeypatch.setattr(combine, "_on_network_drive", lambda _p: True)
        expect = "network drive"
    else:
        class Usage:
            free = 1024 * 1024

        monkeypatch.setattr(combine.shutil, "disk_usage", lambda _p: Usage)
        expect = "free there"
    inputs = _two_protected(corpus_dir, evidence)
    combined = combine_pdfs(inputs, fallback=evidence)
    try:
        assert expect in combined.fallback_reason
        assert combined.folder.parent == evidence
        assert combined.folder.name.startswith(TEMP_PREFIX)
        assert (combined.folder / MARKER_NAME).is_file()
        warning = combined.location_warning()
        assert expect in warning and str(evidence) in warning and "unprotected" in warning
    finally:
        combined.cleanup()
    assert [p for p in evidence.iterdir() if p.name.startswith("~")] == []


def test_no_fallback_refuses(corpus_dir: Path, evidence: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(combine, "_on_network_drive", lambda _p: True)
    with pytest.raises(StampError, match="private work folder can't be used"):
        combine_pdfs(_two_protected(corpus_dir, evidence))
    assert [p for p in evidence.iterdir() if p.name.startswith("~")] == []


@pytest.mark.parametrize("path", [r"\\server\share\work", r"\\?\UNC\server\share\work"])
def test_unc_paths_count_as_network(path: str) -> None:
    assert combine._on_network_drive(Path(path))


def test_local_path_is_not_network(tmp_path: Path) -> None:
    assert not combine._on_network_drive(tmp_path)


def test_cancel_in_fallback_leaves_nothing(corpus_dir: Path, evidence: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    from stamper.handlers.base import Cancelled

    monkeypatch.setattr(combine, "_on_network_drive", lambda _p: True)
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] >= 2

    with pytest.raises(Cancelled):
        combine_pdfs(_two_protected(corpus_dir, evidence), fallback=evidence, cancel=cancel)
    assert [p for p in evidence.iterdir() if p.name.startswith("~")] == []


_KILLED = """
import os, sys
from pathlib import Path
from stamper.handlers.combine import CombineInput, combine_pdfs
a, b, pw = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
combined = combine_pdfs([CombineInput(a, pw), CombineInput(b, pw)], fallback=a.parent)
print(combined.folder)
sys.stdout.flush()
os._exit(1)  # killed while the dialog was open: no cleanup
"""


def test_a_killed_combine_is_swept_next_time(corpus_dir: Path, evidence: Path) -> None:
    inputs = _two_protected(corpus_dir, evidence)
    run = subprocess.run([sys.executable, "-c", _KILLED, str(inputs[0].path), str(inputs[1].path), PW],
                         cwd=REPO, env=os.environ.copy(), capture_output=True, text=True, check=False)
    left = Path(run.stdout.strip())
    assert left.parent == work_dir() and left.is_dir(), run.stderr
    assert remove_stale_temp(work_dir()) == [left]
    assert not left.exists()


def test_window_start_sweeps_the_work_folder(qtbot: QtBot, fake: FakeServices, corpus_dir: Path,
                                             evidence: Path) -> None:
    inputs = _two_protected(corpus_dir, evidence)
    run = subprocess.run([sys.executable, "-c", _KILLED, str(inputs[0].path), str(inputs[1].path), PW],
                         cwd=REPO, env=os.environ.copy(), capture_output=True, text=True, check=False)
    left = Path(run.stdout.strip())
    assert left.is_dir(), run.stderr
    w = MainWindow(config_path=DEFAULT_CONFIG, services=fake.services())
    qtbot.addWidget(w)
    assert not left.exists()


# ------------------------------------------------------------ the sweep (D-120)


@pytest.fixture
def parent(tmp_path: Path) -> Iterator[Path]:
    folder = tmp_path / "sweep"
    folder.mkdir()
    yield folder


def _work(parent: Path, name: str, *, pid: int = 999_999_999, started: float | None = None,
          marker: bool = True) -> Path:
    folder = parent / f"{TEMP_PREFIX}{name}"
    folder.mkdir()
    (folder / "copy.pdf").write_bytes(b"%PDF-1.7 working copy")
    if marker:
        data = {"pid": pid, "started": time.time() if started is None else started, "version": "test"}
        (folder / MARKER_NAME).write_text(json.dumps(data), encoding="utf-8")
    return folder


def _dead(_pid: int) -> bool:
    return False


def _alive(_pid: int) -> bool:
    return True


def test_sweep_removes_a_stopped_runs_folder(parent: Path) -> None:
    folder = _work(parent, "dead")
    assert remove_stale_temp(parent, alive=_dead) == [folder]
    assert not folder.exists()


def test_sweep_keeps_a_folder_without_a_marker(parent: Path) -> None:
    folder = _work(parent, "nomarker", marker=False)
    old = time.time() - 3 * 24 * 3600
    os.utime(folder, (old, old))
    assert remove_stale_temp(parent, alive=_dead) == []
    assert (folder / "copy.pdf").is_file()


def test_sweep_keeps_a_folder_with_a_bad_marker(parent: Path) -> None:
    folder = _work(parent, "badmarker", marker=False)
    (folder / MARKER_NAME).write_text("{not json", encoding="utf-8")
    assert remove_stale_temp(parent, alive=_dead) == []
    assert folder.is_dir()


def test_sweep_keeps_a_running_combine(parent: Path) -> None:
    folder = _work(parent, "live")
    assert remove_stale_temp(parent, alive=_alive) == []
    assert folder.is_dir()


def test_sweep_removes_a_day_old_folder_even_if_the_pid_was_reused(parent: Path) -> None:
    folder = _work(parent, "reused", started=time.time() - 2 * 24 * 3600)
    assert remove_stale_temp(parent, alive=_alive) == [folder]


def test_sweep_keeps_a_folder_with_a_subfolder(parent: Path) -> None:
    folder = _work(parent, "subfolder")
    (folder / "inner").mkdir()
    (folder / "inner" / "evidence.pdf").write_bytes(b"keep me")
    assert remove_stale_temp(parent, alive=_dead) == []
    assert (folder / "inner" / "evidence.pdf").read_bytes() == b"keep me"
    assert (folder / "copy.pdf").is_file()


def _link_dir(link: Path, target: Path) -> None:
    if os.name == "nt":
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if made.returncode != 0:
            pytest.skip("couldn't make a junction")
    else:
        link.symlink_to(target, target_is_directory=True)


def test_sweep_never_follows_a_link_inside(parent: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evidence.pdf").write_bytes(b"keep me")
    folder = _work(parent, "linkinside")
    _link_dir(folder / "planted", outside)
    assert remove_stale_temp(parent, alive=_dead) == []
    assert (outside / "evidence.pdf").read_bytes() == b"keep me"


def test_sweep_never_follows_a_linked_folder(parent: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evidence.pdf").write_bytes(b"keep me")
    (outside / MARKER_NAME).write_text(json.dumps({"pid": 999_999_999, "started": 0}), encoding="utf-8")
    _link_dir(parent / f"{TEMP_PREFIX}linked", outside)
    assert remove_stale_temp(parent, alive=_dead) == []
    assert (outside / "evidence.pdf").read_bytes() == b"keep me"


def test_sweep_ignores_other_names(parent: Path) -> None:
    other = parent / "case files"
    other.mkdir()
    (other / MARKER_NAME).write_text(json.dumps({"pid": 999_999_999, "started": 0}), encoding="utf-8")
    assert remove_stale_temp(parent, alive=_dead) == []
    assert other.is_dir()


def test_own_cleanup_removes_a_link_but_not_its_target(corpus_dir: Path, evidence: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evidence.pdf").write_bytes(b"keep me")
    combined = combine_pdfs(_two_protected(corpus_dir, evidence), fallback=evidence)
    _link_dir(combined.folder / "planted", outside)
    combined.cleanup()
    assert not combined.folder.exists()
    assert (outside / "evidence.pdf").read_bytes() == b"keep me"


def test_combine_dialog_shows_the_fallback(qtbot: QtBot, fake: FakeServices, corpus_dir: Path, evidence: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    from stamper.gui.multi import CombineDialog
    from tests.gui_support import WAIT_MS, wait_ready

    monkeypatch.setattr(combine, "_on_network_drive", lambda _p: True)
    files = [copy_to(corpus_dir / n, evidence) for n in ("letter_portrait.pdf", "rotate_90.pdf")]
    w = MainWindow(config_path=DEFAULT_CONFIG, services=fake.services())
    qtbot.addWidget(w)
    w.open_files(files)
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.files_screen, timeout=WAIT_MS)
    w.files_screen.combine_button.click()
    qtbot.waitUntil(lambda: isinstance(w.dialog, CombineDialog), timeout=WAIT_MS)
    d = wait_ready(qtbot, w.dialog)
    from PySide6.QtWidgets import QLabel

    texts = [lbl.text() for lbl in d.findChildren(QLabel)]
    assert any("private work folder couldn't be used (it's on a network drive" in t for t in texts), texts
    assert any("private work folder" in n for n in d.saved_notes())
    d.cancel_button.click()
    qtbot.waitUntil(lambda: not [p for p in evidence.iterdir() if p.name.startswith("~")], timeout=WAIT_MS)
