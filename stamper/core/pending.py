"""The temp-file journal: partial copies left in an output folder by a crash (D-124).

Saving writes ``~stamper-<random>.tmp`` next to the output and renames it into
place (D-055). If the app is killed in between, that partial copy stays in
the evidence folder. Each temp file is recorded here before it is written and
forgotten once it is gone, so after a crash the window can name exactly what
was left and offer to delete it.

No folder is ever scanned and nothing is deleted without the user choosing
to. Entries live in ``local_dir()\\pending\\``, one small JSON file each, so
two copies of the app never overwrite each other's entries. A journal that
can't be written never stops a save.

Nothing here imports Qt.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .filenames import visible
from .locations import local_dir

__all__ = [
    "TEMP_NAME_PREFIX",
    "TEMP_NAME_SUFFIX",
    "Leftover",
    "delete",
    "describe",
    "forget",
    "journal_dir",
    "keep",
    "leftovers",
    "pid_alive",
    "record",
]

TEMP_NAME_PREFIX = "~stamper-"
TEMP_NAME_SUFFIX = ".tmp"
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def journal_dir(base: Path | None = None) -> Path:
    """The folder holding the entries."""
    return (base or local_dir()) / "pending"


@dataclass(frozen=True, slots=True)
class Leftover:
    """A partial copy a stopped run left behind."""

    entry: Path  # the journal entry
    path: Path  # the partial copy
    size: int
    modified: datetime


def record(tmp: Path, *, base: Path | None = None) -> Path | None:
    """Note ``tmp`` before it's written; returns the entry to ``forget``, or None if it can't."""
    folder = journal_dir(base)
    entry = folder / f"{uuid.uuid4().hex}.json"
    data = {"path": str(Path(tmp).resolve()), "pid": os.getpid(),
            "created": datetime.now().isoformat(timespec="seconds")}
    try:
        folder.mkdir(parents=True, exist_ok=True)
        entry.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        return None  # never stops a save
    return entry


def forget(entry: Path | None) -> None:
    """Drop an entry (the temp file is gone, or the user chose to keep it)."""
    if entry is None:
        return
    try:
        entry.unlink(missing_ok=True)
    except OSError:
        pass


def pid_alive(pid: int) -> bool:
    """True if a process with this ID is running (this process counts)."""
    if pid == os.getpid():
        return True
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            # Access denied means it exists (another user's process); anything else, it doesn't.
            return ctypes.get_last_error() == 5
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_our_file(path: Path) -> os.stat_result | None:
    """The file's lstat if it's still a plain file with a temp-file name; None otherwise."""
    if not (path.name.startswith(TEMP_NAME_PREFIX) and path.name.endswith(TEMP_NAME_SUFFIX)):
        return None
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None  # a link, junction or folder in its place: not ours
    if getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT:
        return None
    return st


def leftovers(*, base: Path | None = None, alive: Callable[[int], bool] | None = None) -> list[Leftover]:
    """Partial copies whose run has stopped, oldest first.

    Entries whose file is gone, or is no longer a plain file with the name
    that was recorded, are dropped without touching anything. Entries of a
    run that's still going are left alone.
    """
    is_alive = alive if alive is not None else pid_alive
    folder = journal_dir(base)
    try:
        entries = sorted(folder.glob("*.json"))
    except OSError:
        return []
    found: list[Leftover] = []
    for entry in entries:
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
            path = Path(data["path"])
            pid = int(data["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            forget(entry)  # damaged entry: nothing to act on
            continue
        if is_alive(pid):
            continue
        st = _is_our_file(path)
        if st is None:
            forget(entry)
            continue
        found.append(Leftover(entry, path, st.st_size, datetime.fromtimestamp(st.st_mtime)))
    found.sort(key=lambda lo: lo.modified)
    return found


def delete(leftover: Leftover) -> None:
    """Delete one partial copy the user chose to remove, then its entry.

    Checks again that it's still a plain file with the recorded temp-file
    name. Raises OSError (with a sentence for the user) if it can't.
    """
    if _is_our_file(leftover.path) is None:
        forget(leftover.entry)
        raise OSError(f"{leftover.path.name} is no longer the partial copy that was left behind, "
                      "so it wasn't deleted.")
    try:
        os.unlink(leftover.path)
    except FileNotFoundError:
        pass
    except OSError as e:
        raise OSError(f"{leftover.path.name} couldn't be deleted ({e.strerror or e}).") from None
    forget(leftover.entry)


def keep(leftover: Leftover) -> None:
    """Leave the file where it is and stop asking about it."""
    forget(leftover.entry)


def _size(n: int) -> str:
    mb = n / (1024 * 1024)
    return f"{mb:.1f} MB" if mb >= 0.1 else f"{max(1, round(n / 1024))} KB"


def describe(items: Sequence[Leftover], problem: str = "") -> str:
    """The notice for partial copies left behind (D-124); empty when there are none."""
    if not items:
        return ""

    def line(lo: Leftover) -> str:
        when = lo.modified.strftime("%b %d %I:%M %p").replace(" 0", " ")
        return f"{visible(str(lo.path))} ({_size(lo.size)}, {when})"

    if len(items) == 1:
        text = ("A partial copy was left behind when Document Stamper stopped unexpectedly: "
                f"{line(items[0])}. It's an unfinished stamped copy, not the original.")
    else:
        shown = "\n".join(f"• {line(lo)}" for lo in items[:8])
        more = f"\n…and {len(items) - 8} more." if len(items) > 8 else ""
        text = ("Partial copies were left behind when Document Stamper stopped unexpectedly. "
                f"They're unfinished stamped copies, not originals:\n{shown}{more}")
    return f"{problem}\n\n{text}" if problem else text
