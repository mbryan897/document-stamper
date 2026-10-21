"""Files on disk: do two paths name the same file, is this a link, how big is it (in words).

The same-file checks are used everywhere a copy must never land on an
original, or a file mustn't be listed twice. ``same_file`` compares two
paths; ``FileIndex`` answers the same question for one path against many,
with one look at the disk per path instead of one per pair, so checking a
few hundred files on a network share stays quick.

Nothing here imports Qt or PyMuPDF.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Generic, TypeVar

__all__ = ["FileIndex", "is_link", "same_file", "size_text"]

T = TypeVar("T")


def _strip_long_prefix(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _canonical(path: Path) -> str:
    raw = _strip_long_prefix(os.fspath(path))
    return os.path.normcase(os.path.realpath(os.path.abspath(raw)))


def _identity(path: Path) -> tuple[int, int] | None:
    """The file's device and file ID, or None when it doesn't exist or the file system gives no ID."""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return (st.st_dev, st.st_ino) if st.st_ino else None


def same_file(a: Path, b: Path) -> bool:
    """True if two paths name the same file.

    Handles relative paths, letter case (on Windows), ``\\\\?\\`` prefixes,
    8.3 short names and links: canonical paths are compared first, then the
    files' IDs when both exist.
    """
    if _canonical(a) == _canonical(b):
        return True
    ident = _identity(a)
    return ident is not None and ident == _identity(b)


def is_link(st: os.stat_result) -> bool:
    """True for a symbolic link, or on Windows any reparse point (a junction, a mount point)."""
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def size_text(n: int) -> str:
    """A file size for a message: "12 KB", "3.4 MB" or "1.2 GB"."""
    mb = n / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb:.1f} MB" if mb >= 0.1 else f"{max(1, round(n / 1024))} KB"


class FileIndex(Generic[T]):
    """Paths added so far, each with a value; ``find`` looks one up by file, as ``same_file`` would."""

    def __init__(self, items: Iterable[tuple[Path, T]] = ()) -> None:
        self._by_path: dict[str, T] = {}
        self._by_id: dict[tuple[int, int], T] = {}
        for path, value in items:
            self.add(path, value)

    def add(self, path: Path, value: T) -> None:
        """Remember ``path``; the first value added for a file is kept."""
        self._by_path.setdefault(_canonical(path), value)
        ident = _identity(path)
        if ident is not None:
            self._by_id.setdefault(ident, value)

    def find(self, path: Path) -> T | None:
        """The value of an added path naming the same file as ``path``, or None."""
        hit = self._by_path.get(_canonical(path))
        if hit is not None:
            return hit
        ident = _identity(path)
        return self._by_id.get(ident) if ident is not None else None
