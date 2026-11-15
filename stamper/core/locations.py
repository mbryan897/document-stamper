"""Where the app's files live: config lookup, safe saving, last-used values.

Config lookup (D-017, D-080)
----------------------------
1. ``config.json`` next to the app (the exe folder; the repo root when run
   from source). This is the portable copy: it travels with the app folder.
2. ``config.json`` in the user folder (``%APPDATA%\\DocumentStamper``).
3. The shipped ``config/default.json``, which is never written to.

"Open another config…" in the window and ``--config`` on the command line
override the lookup for that session or run; they are not part of it.

Profile settings saves back to the file it loaded. A file that can't be
written (a locked shared copy) is read-only in the window, which offers
Export instead. Saving the shipped default goes to the first place that can
take it: next to the app, else the user folder.

Last-used values (D-081) go in ``state.json`` in the user folder, never next
to the app, so a read-only shared copy still remembers them. Only the last
profile and the last folder a file was opened from are kept. Field values
are never remembered.

Packaged builds (O-010, D-087)
------------------------------
``app_dir()`` is where ``config.json`` is looked for; ``resource_dir()`` is
where the bundled fonts and the shipped config are. From source both are the
repo root. In a PyInstaller onedir build the exe folder and its ``_internal``
folder.

Two environment variables override the folders, for tests and for IT:
``STAMPER_APP_DIR`` and ``STAMPER_USER_DIR``.

Nothing here imports Qt.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from .config import Config, parse_config

__all__ = [
    "APP_FOLDER_NAME",
    "CONFIG_NAME",
    "STATE_NAME",
    "AppState",
    "ConfigChanged",
    "ConfigSource",
    "Locations",
    "SourceKind",
    "app_dir",
    "backup_path",
    "builtin_config_path",
    "dated_backups",
    "describe",
    "find_config",
    "folder_is_writable",
    "is_frozen",
    "load_state",
    "local_dir",
    "locations",
    "resource_dir",
    "save_config",
    "save_state",
    "save_target",
    "state_folder",
    "user_dir",
    "work_dir",
]

APP_FOLDER_NAME = "DocumentStamper"
CONFIG_NAME = "config.json"
STATE_NAME = "state.json"
BACKUP_SUFFIX = ".bak"
DATED_BACKUPS_KEPT = 5  # config.json.YYYYMMDD-HHMMSS.bak files kept beside config.json.bak (UX-023)

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ folders


def is_frozen() -> bool:
    """True when running from a packaged (PyInstaller) build."""
    return bool(getattr(sys, "frozen", False))


def _override(name: str) -> str | None:
    """A folder override from the environment, for source runs and tests only.

    The packaged app ignores them (D-121): a variable planted in a user's
    environment can't point the app at another config or work folder.
    """
    if is_frozen():
        return None
    return os.environ.get(name) or None


def resource_dir() -> Path:
    """Where ``assets/`` and ``config/default.json`` are.

    From source: the repo root. Packaged: PyInstaller's ``_internal`` folder
    (``sys._MEIPASS``).
    """
    if is_frozen():
        base = getattr(sys, "_MEIPASS", None)
        return Path(base) if base else Path(sys.executable).resolve().parent
    return _REPO_ROOT


def app_dir() -> Path:
    """The folder whose ``config.json`` comes first: the exe's folder, or the repo root."""
    override = _override("STAMPER_APP_DIR")
    if override:
        return Path(override)
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return _REPO_ROOT


def user_dir() -> Path:
    """``%APPDATA%\\DocumentStamper``; elsewhere ``~/.config/DocumentStamper``."""
    override = _override("STAMPER_USER_DIR")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_FOLDER_NAME
    return Path.home() / ".config" / APP_FOLDER_NAME


def local_dir() -> Path:
    """``%LOCALAPPDATA%\\DocumentStamper``: this machine only, never synced by roaming profiles.

    Holds the temp-file journal (D-124) and, from H-1, the combine work folder.
    Elsewhere ``~/.local/share/DocumentStamper``. ``STAMPER_LOCAL_DIR`` overrides
    it for tests.
    """
    override = _override("STAMPER_LOCAL_DIR")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / APP_FOLDER_NAME
    return Path.home() / ".local" / "share" / APP_FOLDER_NAME


def work_dir() -> Path:
    """Where combining builds its working copy (D-115): ``local_dir()\\work``."""
    return local_dir() / "work"


def builtin_config_path() -> Path:
    """The shipped generic config. Read, never written."""
    return resource_dir() / "config" / "default.json"


@dataclass(frozen=True, slots=True)
class Locations:
    """The three places a config can come from, and where state goes."""

    app: Path
    user: Path
    builtin: Path

    @property
    def app_config(self) -> Path:
        """``config.json`` next to the app."""
        return self.app / CONFIG_NAME

    @property
    def user_config(self) -> Path:
        """``config.json`` in the user folder."""
        return self.user / CONFIG_NAME

    @property
    def state_file(self) -> Path:
        """``state.json`` in the user folder."""
        return self.user / STATE_NAME


def locations() -> Locations:
    """The folders for this run (environment overrides applied)."""
    return Locations(app=app_dir(), user=user_dir(), builtin=builtin_config_path())


# ------------------------------------------------------------------ lookup


class SourceKind(Enum):
    """Where a config came from."""

    APP = "app"  # next to the exe
    USER = "user"  # %APPDATA%
    BUILTIN = "builtin"  # shipped default, read-only
    OTHER = "other"  # "Open another config…" or --config

    @property
    def description(self) -> str:
        """Plain words for the interface."""
        return {
            SourceKind.APP: "next to the app",
            SourceKind.USER: "in your user folder",
            SourceKind.BUILTIN: "the built-in default",
            SourceKind.OTHER: "opened for this session",
        }[self]


@dataclass(frozen=True, slots=True)
class ConfigSource:
    """A config file and what may be done with it.

    ``writable`` is False for the built-in default and for files the user
    can't change; Profile settings then offers Export instead of Save, except
    for the built-in default, whose first save goes to ``save_target()``.
    """

    path: Path
    kind: SourceKind
    writable: bool
    digest: str | None = field(default=None, compare=False)  # the file's contents when found; None: no file


def _config_digest(path: Path) -> str | None:
    """SHA-256 of a config file's bytes, or None when there's no file."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None


def folder_is_writable(folder: Path) -> bool:
    """True if a file can be created in ``folder`` (tries it; ACLs make ``os.access`` unreliable)."""
    try:
        fd, name = tempfile.mkstemp(prefix="~stamper-check-", suffix=".tmp", dir=folder)
    except OSError:
        return False
    os.close(fd)
    try:
        os.remove(name)
    except OSError:
        pass
    return True


def _file_is_writable(path: Path) -> bool:
    # The read-only attribute (os.access) and the folder (replace needs a temp file there).
    return os.access(path, os.W_OK) and folder_is_writable(path.parent)


def describe(path: Path, kind: SourceKind) -> ConfigSource:
    """A ConfigSource for a path, checking whether it can be written."""
    if kind is SourceKind.BUILTIN:
        return ConfigSource(path, kind, False)
    return ConfigSource(path, kind, path.is_file() and _file_is_writable(path), _config_digest(path))


def find_config(locs: Locations | None = None) -> ConfigSource:
    """The config to use when none was chosen: next to the app, then the user folder, then the default.

    A file that exists counts, even if it turns out to be invalid; the caller
    reports its problems rather than silently falling back to another config.
    """
    locs = locs or locations()
    if locs.app_config.is_file():
        return describe(locs.app_config, SourceKind.APP)
    if locs.user_config.is_file():
        return describe(locs.user_config, SourceKind.USER)
    return describe(locs.builtin, SourceKind.BUILTIN)


def save_target(source: ConfigSource, locs: Locations | None = None) -> ConfigSource | None:
    """Where Profile settings saves: the loaded file, or for the default the first writable place.

    None when nothing can be written (a read-only file, or no writable folder).
    """
    locs = locs or locations()
    if source.kind is not SourceKind.BUILTIN:
        return source if source.writable else None
    if locs.app.is_dir() and folder_is_writable(locs.app):
        return ConfigSource(locs.app_config, SourceKind.APP, True, _config_digest(locs.app_config))
    try:
        locs.user.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    if folder_is_writable(locs.user):
        return ConfigSource(locs.user_config, SourceKind.USER, True, _config_digest(locs.user_config))
    return None


# ------------------------------------------------------------------ saving


def backup_path(path: Path) -> Path:
    """``config.json`` -> ``config.json.bak``."""
    return path.with_name(path.name + BACKUP_SUFFIX)


def _dated_pattern(path: Path) -> re.Pattern[str]:
    return re.compile(re.escape(path.name) + r"\.[0-9]{8}-[0-9]{6}" + re.escape(BACKUP_SUFFIX))


def dated_backups(path: Path) -> list[Path]:
    """``config.json.YYYYMMDD-HHMMSS.bak`` files beside ``path``, oldest first.

    Only names matching exactly that pattern; nothing else in the folder.
    """
    rx = _dated_pattern(path)
    try:
        names = os.listdir(path.parent)
    except OSError:
        return []
    return [path.with_name(n) for n in sorted(n for n in names if rx.fullmatch(n))]


def _keep_dated_backup(path: Path) -> None:
    """Copy ``path`` to a dated backup, then delete all but the newest few.

    A failed copy raises, so the save stops as it does when ``.bak`` can't be
    written. An existing backup with the same second is left as it is (two
    saves in one second keep the older copy; the newer is in ``.bak``). A
    failed delete of an old backup is ignored.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dated = path.with_name(f"{path.name}.{stamp}{BACKUP_SUFFIX}")
    if not dated.exists():
        shutil.copy2(path, dated)
    for old in dated_backups(path)[:-DATED_BACKUPS_KEPT]:
        try:
            old.unlink()
        except OSError:
            pass


def _write_atomic(path: Path, text: str) -> None:
    """Write UTF-8 text to a temp file in the same folder, then replace ``path``."""
    fd, tmp = tempfile.mkstemp(prefix="~stamper-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


class ConfigChanged(OSError):
    """The config file changed on disk after it was loaded, so saving would overwrite someone else's save."""


_UNCHECKED = "not checked"  # never a SHA-256 digest


def save_config(config: Config, path: Path, *, backup: bool = True, expect: str | None = _UNCHECKED) -> Path:
    """Write a validated config to ``path`` safely; returns ``path``.

    The text is re-read through the validator before anything on disk
    changes, so a file that wouldn't load is never written. With ``backup``,
    an existing file is copied to ``config.json.bak`` first, and to a dated
    ``config.json.YYYYMMDD-HHMMSS.bak`` (the newest five are kept).
    ``expect`` is the file's ``_config_digest`` when it was loaded (None: there
    was no file); if the file is different now, nothing is written and
    ConfigChanged is raised. Raises OSError if the folder can't be written,
    ConfigError if the text doesn't validate (a programming error).
    """
    text = config.to_json()
    parse_config(json.loads(text), path.name)
    if expect != _UNCHECKED and _config_digest(path) != expect:
        raise ConfigChanged(
            f"{path.name} was changed by someone else after it was loaded, so it wasn't overwritten. "
            "Export your changes, then restart the app to load the new version")
    if backup and path.is_file():
        shutil.copy2(path, backup_path(path))
        _keep_dated_backup(path)
    _write_atomic(path, text)
    return path


# ------------------------------------------------------------------ state


@dataclass(slots=True)
class AppState:
    """Last-used values (D-081). Never field values."""

    last_profile: str | None = None
    last_folder: str | None = None
    last_config: str | None = None  # the config found at the last start-up (D-121)
    last_save_folder: str | None = None  # where the last single or combined copy was saved (UX-037)
    batch_folder: str | None = None  # the last batch's "In one folder" folder (UX-039)
    batch_in_one_folder: bool = False  # the last batch saved in one folder (UX-039)
    extra: dict[str, object] = field(default_factory=dict)  # keys from newer versions, kept as-is

    def to_json(self) -> str:
        """Serialize, keeping unknown keys."""
        data: dict[str, object] = dict(self.extra)
        data["last_profile"] = self.last_profile
        data["last_folder"] = self.last_folder
        data["last_config"] = self.last_config
        data["last_save_folder"] = self.last_save_folder
        data["batch_folder"] = self.batch_folder
        data["batch_in_one_folder"] = self.batch_in_one_folder
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def load_state(locs: Locations | None = None) -> AppState:
    """Read ``state.json``; a missing or damaged file gives empty state (it's only a convenience)."""
    locs = locs or locations()
    try:
        data = json.loads(locs.state_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return AppState()
    if not isinstance(data, dict):
        return AppState()
    profile = data.pop("last_profile", None)
    folder = data.pop("last_folder", None)
    config = data.pop("last_config", None)
    save_folder = data.pop("last_save_folder", None)
    batch_folder = data.pop("batch_folder", None)
    in_one = data.pop("batch_in_one_folder", None)
    return AppState(
        last_profile=profile if isinstance(profile, str) else None,
        last_folder=folder if isinstance(folder, str) else None,
        last_config=config if isinstance(config, str) else None,
        last_save_folder=save_folder if isinstance(save_folder, str) else None,
        batch_folder=batch_folder if isinstance(batch_folder, str) else None,
        batch_in_one_folder=in_one is True,  # a real bool only, not 1 or "true"
        extra=data,
    )


_MAX_PATH16 = 32767  # the longest path Windows accepts, in UTF-16 units


def state_folder(value: object) -> Path | None:
    """A folder read back from ``state.json``, or None unless it's usable now.

    Usable means a string, an absolute path and an existing folder. The
    file is only a convenience and may have been edited, so anything else
    is ignored. Checked when the folder is used, since it may have been
    deleted or renamed after start-up.
    """
    if not isinstance(value, str) or not value or len(value) > _MAX_PATH16:
        return None
    try:
        path = Path(value)
        return path if path.is_absolute() and path.is_dir() else None
    except (OSError, ValueError):
        return None


def save_state(state: AppState, locs: Locations | None = None) -> bool:
    """Write ``state.json``; returns False (and changes nothing) if it can't."""
    locs = locs or locations()
    try:
        locs.user.mkdir(parents=True, exist_ok=True)
        _write_atomic(locs.state_file, state.to_json())
    except OSError:
        return False
    return True
