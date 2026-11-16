"""What "Remove my settings from this computer…" removes (step 13).

Only exact, known paths, decided before anything is deleted so the confirm
can list them: ``state.json``; the pending journal folder when it holds no
entries; the Send to shortcut when it points at this copy of the program;
then the user folder, the combine work folder and ``local_dir()``, each only
if it is empty by then (and the work folder only while no combine runs).
No wildcard and no folder walk: a folder is removed with ``os.rmdir``,
which refuses one that isn't empty, and a link or junction is never
removed or followed.

A settings file (``config.json``) and its backups are never removed; they
are listed as kept, so the confirm can say so. Nothing here imports Qt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .locations import Locations, backup_path, dated_backups, local_dir, work_dir
from .pending import journal_dir

__all__ = ["Removal", "carry_out", "plan_removal"]


@dataclass(frozen=True, slots=True)
class Removal:
    """What will be removed, and what will be left with the reason.

    ``files`` are deleted; ``folders`` are removed in order, each only if
    empty when its turn comes. ``kept`` are ``(path, why)`` pairs.
    """

    files: tuple[Path, ...] = ()
    folders: tuple[Path, ...] = ()
    kept: tuple[tuple[Path, str], ...] = ()


@dataclass(slots=True)
class Outcome:
    """What ``carry_out`` did: removed paths and ``(path, why)`` for those left."""

    removed: list[Path] = field(default_factory=list)
    left: list[tuple[Path, str]] = field(default_factory=list)


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except OSError:
        return True  # can't tell: leave it


def _empty_dir(path: Path) -> bool | None:
    """True if an empty folder, False if a folder with something in it, None if not a plain folder."""
    if _is_link(path) or not path.is_dir():
        return None
    try:
        with os.scandir(path) as it:
            return next(it, None) is None
    except OSError:
        return None


def plan_removal(locs: Locations, *, settings_files: tuple[Path, ...] = (), shortcut: Path | None = None,
                 shortcut_is_ours: bool = False, shortcut_kept: str = "it opens another copy of Document Stamper",
                 combine_running: bool = False) -> Removal:
    """Decide what "Remove my settings" removes; nothing is changed.

    ``settings_files`` are config files to name as kept (the one in use and
    the usual places; missing ones are skipped). ``shortcut`` is the Send to
    shortcut and ``shortcut_is_ours`` whether it points at this copy of the
    program (``sendto.status`` tells); ``shortcut_kept`` says why it stays otherwise.
    """
    files: list[Path] = []
    folders: list[Path] = []
    kept: list[tuple[Path, str]] = []

    state = locs.state_file
    if state.is_file() and not _is_link(state):
        files.append(state)

    journal = journal_dir()
    empty = _empty_dir(journal)
    if empty:
        folders.append(journal)
    elif empty is False:
        kept.append((journal, "it lists copies that weren't finished; deal with them on the first screen first"))

    if shortcut is not None and shortcut.is_file():
        if shortcut_is_ours:
            files.append(shortcut)
        else:
            kept.append((shortcut, shortcut_kept))

    work = work_dir()
    if combine_running and work.exists():
        kept.append((work, "files are being combined"))
    elif work.exists():
        folders.append(work)
    if local_dir().exists():
        folders.append(local_dir())
    if locs.user.exists():
        folders.append(locs.user)

    seen: set[Path] = set()
    for path in settings_files:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        has_backups = backup_path(path).exists() or bool(dated_backups(path))
        kept.append((path, "settings files are never removed" + (", nor their backups" if has_backups else "")))
    return Removal(tuple(files), tuple(folders), tuple(kept))


def carry_out(removal: Removal) -> Outcome:
    """Delete the planned files, then remove each planned folder that is empty by then."""
    out = Outcome(left=list(removal.kept))
    for path in removal.files:
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as e:
            out.left.append((path, f"it couldn't be removed ({e.strerror or e})"))
            continue
        out.removed.append(path)
    for folder in removal.folders:
        empty = _empty_dir(folder)
        if empty is None:
            continue  # gone, or not a plain folder any more
        if not empty:
            out.left.append((folder, "other files are in it"))
            continue
        try:
            os.rmdir(folder)
        except OSError as e:
            out.left.append((folder, f"it couldn't be removed ({e.strerror or e})"))
            continue
        out.removed.append(folder)
    return out
