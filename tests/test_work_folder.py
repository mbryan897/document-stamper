"""Combining works only in a private folder (D-115); cleanup deletes only what the app made (D-120)."""

from __future__ import annotations

import io
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
from stamper.cli import ExitCode
from stamper.cli import main as cli_main
from stamper.core.locations import work_dir
from stamper.gui.main_window import MainWindow
from stamper.handlers.base import StampError
from stamper.handlers.combine import MARKER_NAME, TEMP_PREFIX, CombineInput, combine_pdfs, remove_stale_temp
from stamper.handlers.pdf import PdfHandler
from tests.gui_support import FakeServices, copy_to, fake  # noqa: F401 - fixtures
from tests.stamping import DEFAULT_CONFIG, FAKE_ID, default_job, sha256
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
    with combine_pdfs(inputs, sweep=evidence) as combined:
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


def _refusal() -> str:
    return (f"Document Stamper can't use its working folder ({work_dir()}). "
            "Nothing was combined and your files are unchanged.")


def _break_work_folder(why: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the private work folder unusable in one of the ways D-115 checks."""
    if why == "not-a-folder":
        work_dir().parent.mkdir(parents=True, exist_ok=True)
        work_dir().write_text("x")  # a file where the folder should be: it can't be created
    elif why == "unwritable":
        def refuse(parent: Path, *, hide: bool = True) -> Path:
            raise StampError(f"Can't write to the folder {parent} (Access is denied).")

        monkeypatch.setattr(combine, "_make_temp_folder", refuse)
    elif why == "network":
        monkeypatch.setattr(combine, "_on_network_drive", lambda _p: True)
    else:
        class Usage:
            free = 1024 * 1024

        monkeypatch.setattr(combine.shutil, "disk_usage", lambda _p: Usage)


WHYS = ["not-a-folder", "unwritable", "network", "space"]


@pytest.mark.parametrize("why", WHYS)
def test_unusable_work_folder_refuses_and_writes_nothing(corpus_dir: Path, evidence: Path, why: str,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = _two_protected(corpus_dir, evidence)
    before = _listing(evidence)
    digests = {i.path: sha256(i.path) for i in inputs}
    _break_work_folder(why, monkeypatch)
    with pytest.raises(StampError) as caught:
        combine_pdfs(inputs, sweep=evidence)
    assert str(caught.value) == _refusal()
    assert _listing(evidence) == before  # nothing in the output folder, hidden or not
    for path, digest in digests.items():
        assert sha256(path) == digest
    assert not combine.in_progress()


@pytest.mark.parametrize("why", WHYS)
def test_refusal_comes_before_any_source_is_opened(corpus_dir: Path, evidence: Path, why: str,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    def opened(*_a: object, **_k: object) -> None:
        raise AssertionError("a source was opened")

    monkeypatch.setattr(PdfHandler, "inspect", opened)
    monkeypatch.setattr(PdfHandler, "_open", opened)
    _break_work_folder(why, monkeypatch)
    progress: list[str] = []
    with pytest.raises(StampError, match="can't use its working folder"):
        combine_pdfs(_two_protected(corpus_dir, evidence), sweep=evidence,
                     progress=lambda _d, _t, name: progress.append(name))
    assert progress == []


def test_cli_refuses_with_the_sentence_and_the_cant_stamp_code(corpus_dir: Path, evidence: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    # The first file needs a password and none is given: the refusal must come first, not a password error.
    a = copy_to(corpus_dir / "encrypted_user_password.pdf", evidence, "locked_a.pdf")
    b = copy_to(corpus_dir / "letter_portrait.pdf", evidence, "plain_b.pdf")
    before = _listing(evidence)
    _break_work_folder("network", monkeypatch)
    out, err = io.StringIO(), io.StringIO()
    code = cli_main(["--combine", str(a), str(b), "--out", str(evidence / "exhibit.pdf"),
                     "--config", str(DEFAULT_CONFIG), "--set", f"identifier={FAKE_ID}", "--set", "doc_type=Report"],
                    stdout=out, stderr=err, stdin=io.StringIO(""))
    assert code == ExitCode.ERROR
    assert err.getvalue().strip() == _refusal()
    assert _listing(evidence) == before


@pytest.mark.parametrize("path", [r"\\server\share\work", r"\\?\UNC\server\share\work"])
def test_unc_paths_count_as_network(path: str) -> None:
    assert combine._on_network_drive(Path(path))


def test_local_path_is_not_network(tmp_path: Path) -> None:
    assert not combine._on_network_drive(tmp_path)


def test_cancel_leaves_nothing(corpus_dir: Path, evidence: Path) -> None:
    from stamper.handlers.base import Cancelled

    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] >= 2

    inputs = _two_protected(corpus_dir, evidence)
    before = _listing(evidence)
    with pytest.raises(Cancelled):
        combine_pdfs(inputs, sweep=evidence, cancel=cancel)
    assert _listing(evidence) == before
    assert list(work_dir().iterdir()) == []


def test_a_failed_combine_leaves_nothing(corpus_dir: Path, evidence: Path) -> None:
    from stamper.handlers.base import WrongPassword

    inputs = [CombineInput(i.path, "wrong") for i in _two_protected(corpus_dir, evidence)]
    before = _listing(evidence)
    with pytest.raises(WrongPassword):
        combine_pdfs(inputs, sweep=evidence)
    assert _listing(evidence) == before
    assert list(work_dir().iterdir()) == []


def test_old_leftover_in_the_output_folder_is_swept_when_a_combine_starts(corpus_dir: Path,
                                                                          evidence: Path) -> None:
    # Releases before this one could leave a working folder in the output folder.
    old = _work(evidence, "oldrelease", pid=os.getpid(), started=time.time() - 2 * 24 * 3600)
    recent = _work(evidence, "otherrun", pid=os.getpid())  # still in use by a running process: kept
    with combine_pdfs(_two_protected(corpus_dir, evidence), sweep=evidence) as combined:
        assert combined.folder.parent == work_dir()
    assert not old.exists()
    assert recent.is_dir()


def test_cli_combine_sweeps_an_old_leftover_in_the_output_folder(corpus_dir: Path, evidence: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    old = _work(evidence, "oldrelease", started=time.time() - 2 * 24 * 3600)
    a = copy_to(corpus_dir / "letter_portrait.pdf", evidence, "a.pdf")
    b = copy_to(corpus_dir / "rotate_90.pdf", evidence, "b.pdf")
    err = io.StringIO()
    code = cli_main(["--combine", str(a), str(b), "--out", str(evidence / "exhibit.pdf"),
                     "--config", str(DEFAULT_CONFIG), "--set", f"identifier={FAKE_ID}", "--set", "doc_type=Report"],
                    stdout=io.StringIO(), stderr=err, stdin=io.StringIO(""))
    assert code == ExitCode.OK, err.getvalue()
    assert not old.exists()
    assert sorted(p.name for p in evidence.iterdir()) == ["a.pdf", "b.pdf", "exhibit.pdf"]


_KILLED = """
import os, sys
from pathlib import Path
from stamper.handlers.combine import CombineInput, combine_pdfs
a, b, pw = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
combined = combine_pdfs([CombineInput(a, pw), CombineInput(b, pw)])
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


_EXITED = """
import sys
from pathlib import Path
from stamper.handlers.combine import CombineInput, combine_pdfs
a, b, pw = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
combined = combine_pdfs([CombineInput(a, pw), CombineInput(b, pw)])
print(combined.folder)
sys.exit(0)  # exits without calling cleanup(): the atexit fallback removes the folder
"""


def test_atexit_removes_the_working_copy(corpus_dir: Path, evidence: Path) -> None:
    inputs = _two_protected(corpus_dir, evidence)
    before = _listing(evidence)
    run = subprocess.run([sys.executable, "-c", _EXITED, str(inputs[0].path), str(inputs[1].path), PW],
                         cwd=REPO, env=os.environ.copy(), capture_output=True, text=True, check=False)
    left = Path(run.stdout.strip())
    assert run.returncode == 0 and left.parent == work_dir(), run.stderr
    assert not left.exists()
    assert _listing(evidence) == before


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


def _cli_start() -> None:
    """Start the command line (it stops at a missing file, after the start-up sweep)."""
    cli_main(["no-such-file.pdf"], stdout=io.StringIO(), stderr=io.StringIO(), stdin=io.StringIO(""))


def test_cli_start_sweeps_the_work_folder() -> None:
    work_dir().mkdir(parents=True)
    left = _work(work_dir(), "crashed")  # its process (pid 999999999) has stopped
    _cli_start()
    assert not left.exists()


def test_start_sweep_leaves_similar_names_alone() -> None:
    work_dir().mkdir(parents=True)
    names = ["~stamper-combine", "~stamper-combinex", "stamper-combine-1", "x~stamper-combine-1", "~Stamper-Combine_1"]
    kept = []
    for name in names:
        folder = work_dir() / name
        folder.mkdir()
        (folder / MARKER_NAME).write_text(json.dumps({"pid": 999_999_999, "started": 0}), encoding="utf-8")
        kept.append(folder)
    _cli_start()
    assert all(f.is_dir() and (f / MARKER_NAME).is_file() for f in kept)


_HELD = """
import sys, time
from pathlib import Path
from stamper.handlers.combine import CombineInput, combine_pdfs
a, b, pw = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
combined = combine_pdfs([CombineInput(a, pw), CombineInput(b, pw)])
print(combined.folder)
sys.stdout.flush()
time.sleep(120)  # a second copy of the program, in the middle of a combine
"""


def test_start_sweep_leaves_a_running_combine_alone(qtbot: QtBot, fake: FakeServices, corpus_dir: Path,
                                                    evidence: Path) -> None:
    inputs = _two_protected(corpus_dir, evidence)
    other = subprocess.Popen([sys.executable, "-c", _HELD, str(inputs[0].path), str(inputs[1].path), PW],
                             cwd=REPO, env=os.environ.copy(), stdout=subprocess.PIPE, text=True)
    try:
        assert other.stdout is not None
        held = Path(other.stdout.readline().strip())
        assert held.parent == work_dir() and held.is_dir()
        _cli_start()
        w = MainWindow(config_path=DEFAULT_CONFIG, services=fake.services())
        qtbot.addWidget(w)
        assert held.is_dir() and any(held.glob("*.pdf"))
    finally:
        other.kill()
        other.wait()
    _cli_start()  # killed: next start removes it
    assert not held.exists()


def test_a_failing_start_sweep_is_silent(qtbot: QtBot, fake: FakeServices,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_a: object, **_k: object) -> list[Path]:
        raise RuntimeError("sweep failed")

    from stamper.gui import main_window

    monkeypatch.setattr(combine, "remove_stale_temp", broken)
    monkeypatch.setattr(main_window, "remove_stale_temp", broken, raising=False)  # releases that imported it
    err = io.StringIO()
    code = cli_main(["no-such-file.pdf"], stdout=io.StringIO(), stderr=err, stdin=io.StringIO(""))
    assert code != ExitCode.OK and "sweep" not in err.getvalue()
    w = MainWindow(config_path=DEFAULT_CONFIG, services=fake.services())
    qtbot.addWidget(w)


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
    combined = combine_pdfs(_two_protected(corpus_dir, evidence))
    _link_dir(combined.folder / "planted", outside)
    combined.cleanup()
    assert not combined.folder.exists()
    assert (outside / "evidence.pdf").read_bytes() == b"keep me"


def test_window_refuses_before_password_or_progress(qtbot: QtBot, fake: FakeServices, corpus_dir: Path,
                                                   evidence: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.gui_support import WAIT_MS

    # Both files need a password: without the early check, the Before stamping screen would ask for it.
    files = [i.path for i in _two_protected(corpus_dir, evidence)]
    before = _listing(evidence)
    w = MainWindow(config_path=DEFAULT_CONFIG, services=fake.services())
    qtbot.addWidget(w)
    w.open_files(files)
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.files_screen, timeout=WAIT_MS)
    _break_work_folder("network", monkeypatch)
    w.files_screen.combine_button.click()
    assert w.stack.currentWidget() is w.files_screen  # no password screen, no progress bar
    assert w.prepare is None and w.dialog is None and w._task is None
    assert w.files_screen.message.text() == _refusal()
    assert w.files_screen.message_frame.isVisibleTo(w.files_screen)
    assert _listing(evidence) == before
