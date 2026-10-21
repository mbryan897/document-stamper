"""Same-file checks: one pair, and one path against many without comparing every pair."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from stamper.core import paths
from stamper.core.paths import FileIndex, same_file


def _file(path: Path) -> Path:
    path.write_bytes(b"%PDF-1.7")
    return path


def test_same_file_helper(tmp_path: Path) -> None:
    a = _file(tmp_path / "a.pdf")
    assert same_file(a, tmp_path / "." / "a.pdf")
    assert not same_file(a, tmp_path / "b.pdf")


def test_same_file_through_a_hard_link(tmp_path: Path) -> None:
    a = _file(tmp_path / "a.pdf")
    os.link(a, tmp_path / "linked.pdf")
    assert same_file(a, tmp_path / "linked.pdf")


def test_index_finds_the_same_file_by_any_name(tmp_path: Path) -> None:
    a, b = _file(tmp_path / "a.pdf"), _file(tmp_path / "b.pdf")
    os.link(a, tmp_path / "linked.pdf")
    index: FileIndex[str] = FileIndex([(a, "first"), (b, "second")])
    assert index.find(tmp_path / "." / "a.pdf") == "first"
    assert index.find(tmp_path / "linked.pdf") == "first"
    assert index.find(tmp_path / "missing.pdf") is None
    index.add(tmp_path / "linked.pdf", "later")
    assert index.find(a) == "first"  # the first value for a file is kept


def test_index_finds_a_path_that_doesnt_exist_yet(tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    index: FileIndex[int] = FileIndex([(out, 1)])
    assert index.find(tmp_path / "sub" / ".." / "out.pdf") == 1


def test_index_looks_at_each_path_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 300 files on a share: one look per path, not one per pair (45,000 pairs).
    files = [_file(tmp_path / f"{i}.pdf") for i in range(300)]
    calls = 0
    real = paths._canonical

    def counting(path: Path) -> str:
        nonlocal calls
        calls += 1
        return real(path)

    monkeypatch.setattr(paths, "_canonical", counting)
    index: FileIndex[Path] = FileIndex()
    for f in files:
        assert index.find(f) is None
        index.add(f, f)
    assert calls == 2 * len(files)


def test_size_text_is_the_same_everywhere() -> None:
    assert [paths.size_text(n) for n in (0, 50 * 1024, 3_565_158, 5 * 1024**3 // 4)] == [
        "1 KB", "50 KB", "3.4 MB", "1.2 GB"]


def test_is_link(tmp_path: Path) -> None:
    target, link = tmp_path / "target", tmp_path / "link"
    target.mkdir()
    if os.name == "nt":  # a junction: no admin rights needed, unlike a symbolic link
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if made.returncode != 0:
            pytest.skip("couldn't make a junction")
    else:
        link.symlink_to(target, target_is_directory=True)
    assert paths.is_link(os.lstat(link))
    assert not paths.is_link(os.lstat(target))
