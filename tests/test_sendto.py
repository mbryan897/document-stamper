"""The Send to shortcut (D-086). Writing real shortcuts is tested on Windows only."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from stamper import sendto
from stamper.sendto import SendToError, SendToState, Target

windows = pytest.mark.skipif(os.name != "nt", reason="shell shortcuts are Windows-only")
REPO = Path(__file__).resolve().parents[1]


def test_shortcut_goes_in_the_users_sendto_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert sendto.shortcut_path() == tmp_path / "Microsoft" / "Windows" / "SendTo" / "Document Stamper.lnk"


def test_source_target_runs_the_module_from_the_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAMPER_APP_DIR")
    t = sendto.target()
    assert t.arguments == "-m stamper"
    assert t.working_dir == REPO
    assert t.path.name.lower().startswith("python")


def test_frozen_target_is_the_exe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exe = tmp_path / "DocumentStamper.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert sendto.target() == Target(exe.resolve(), "", tmp_path.resolve())


def test_missing_shortcut(tmp_path: Path) -> None:
    st = sendto.status(tmp_path / "none.lnk")
    assert st.state is SendToState.MISSING
    assert "isn't in the Send to menu" in st.message


def test_remove_missing_is_false(tmp_path: Path) -> None:
    assert sendto.remove(tmp_path / "none.lnk") is False


@pytest.mark.skipif(os.name == "nt", reason="checks the non-Windows message")
def test_off_windows_writing_says_so(tmp_path: Path) -> None:
    with pytest.raises(SendToError, match="only available on Windows"):
        sendto.create(tmp_path / "x.lnk")


@pytest.mark.skipif(os.name == "nt", reason="checks the non-Windows status")
def test_off_windows_an_existing_file_is_unreadable(tmp_path: Path) -> None:
    lnk = tmp_path / "x.lnk"
    lnk.write_bytes(b"not a shortcut")
    assert sendto.status(lnk).state is SendToState.UNREADABLE


def test_messages_name_the_other_copy(tmp_path: Path) -> None:
    st = sendto.SendToStatus(SendToState.ELSEWHERE, tmp_path / "x.lnk", Path(r"E:\Stamper\DocumentStamper.exe"))
    assert "another copy" in st.message and "DocumentStamper.exe" in st.message


@windows
def test_create_read_status_remove(tmp_path: Path) -> None:
    lnk = tmp_path / "SendTo" / "Document Stamper.lnk"
    want = sendto.target()
    assert sendto.create(lnk, want) == lnk and lnk.is_file()
    got = sendto.read_shortcut(lnk)
    assert os.path.normcase(got.path) == os.path.normcase(want.path)
    assert got.arguments == want.arguments
    assert os.path.normcase(got.working_dir) == os.path.normcase(want.working_dir)
    assert sendto.status(lnk, want).state is SendToState.CURRENT
    assert sendto.remove(lnk) is True and not lnk.exists()


@windows
def test_status_spots_a_moved_copy(tmp_path: Path) -> None:
    lnk = tmp_path / "Document Stamper.lnk"
    old = Target(tmp_path / "old" / "DocumentStamper.exe", "", tmp_path / "old")
    sendto.create(lnk, old)
    st = sendto.status(lnk, sendto.target())
    assert st.state is SendToState.ELSEWHERE
    assert st.points_to is not None and st.points_to.name == "DocumentStamper.exe"


@windows
def test_create_replaces_an_old_shortcut(tmp_path: Path) -> None:
    lnk = tmp_path / "Document Stamper.lnk"
    sendto.create(lnk, Target(tmp_path / "old.exe", "", tmp_path))
    sendto.create(lnk, sendto.target())
    assert sendto.status(lnk).state is SendToState.CURRENT
