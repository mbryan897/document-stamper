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
from pathlib import Path, PureWindowsPath

from .filenames import visible
from .locations import local_dir
from .paths import is_link, size_text

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
COMBINE_FOLDER_PREFIX = "~stamper-combine-"  # combine.TEMP_PREFIX; core can't import handlers
MAX_SOURCE_NAME = 80  # characters of the source's name shown in the notice


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
    source: str | None = None  # the file being stamped, as recorded; untrusted, only ever shown


def record(tmp: Path, *, source: Path | None = None, base: Path | None = None) -> Path | None:
    """Note ``tmp`` before it's written; returns the entry to ``forget``, or None if it can't.

    ``source`` is the file being stamped, so the notice can name it (UX-024).
    The output name is never recorded: it is built from field values (D-081).
    """
    folder = journal_dir(base)
    entry = folder / f"{uuid.uuid4().hex}.json"
    data: dict[str, object] = {"path": str(Path(tmp).resolve()), "pid": os.getpid(),
                               "created": datetime.now().isoformat(timespec="seconds")}
    if source is not None:
        data["source"] = str(source)
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
    if not stat.S_ISREG(st.st_mode) or is_link(st):
        return None  # a link, junction or folder in its place: not ours
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
            source = data.get("source")
        except (OSError, ValueError, KeyError, TypeError):
            forget(entry)  # damaged entry: nothing to act on
            continue
        if is_alive(pid):
            continue
        st = _is_our_file(path)
        if st is None:
            forget(entry)
            continue
        found.append(Leftover(entry, path, st.st_size, datetime.fromtimestamp(st.st_mtime),
                              source if isinstance(source, str) and source else None))
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


def _source_text(lo: Leftover) -> str:
    """"the stamped copy of <name>" (or of a combined file), or "" when unknown (UX-024).

    The recorded path is only shown, as a name: never opened or acted on.
    """
    if not lo.source:
        return ""
    path = PureWindowsPath(lo.source)  # splits on both \\ and /
    name = visible(path.name, keep="")
    if not name:
        return ""
    if len(name) > MAX_SOURCE_NAME:
        name = name[: MAX_SOURCE_NAME - 1] + "…"
    if path.parent.name.startswith(COMBINE_FOLDER_PREFIX):
        # combine stamps a temporary file named after the first source (D-104)
        return f"the stamped copy of a combined file starting with {name}"
    return f"the stamped copy of {name}"


def describe(items: Sequence[Leftover], problem: str = "") -> str:
    """The notice for partial copies left behind (D-124); empty when there are none."""
    if not items:
        return ""

    def line(lo: Leftover) -> str:
        when = lo.modified.strftime("%b %d %I:%M %p").replace(" 0", " ")
        return f"{visible(str(lo.path))} ({size_text(lo.size)}, {when})"

    if len(items) == 1:
        text = ("A partial copy was left behind when Document Stamper stopped unexpectedly: "
                f"{line(items[0])}. It's an unfinished stamped copy, not the original.")
        source = _source_text(items[0])
        if source:
            text += f" {source[0].upper()}{source[1:]} wasn't finished. Stamp it again."
    else:
        def entry(lo: Leftover) -> str:
            source = _source_text(lo)
            return f"• {line(lo)}: {source}" if source else f"• {line(lo)}"

        shown = "\n".join(entry(lo) for lo in items[:8])
        more = f"\n…and {len(items) - 8} more." if len(items) > 8 else ""
        text = ("Partial copies were left behind when Document Stamper stopped unexpectedly. "
                f"They're unfinished stamped copies, not originals:\n{shown}{more}")
        if any(_source_text(lo) for lo in items[:8]):
            text += "\nStamp those files again."
    return f"{problem}\n\n{text}" if problem else text
