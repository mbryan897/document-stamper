"""The Explorer "Send to" shortcut (D-086).

``Document Stamper.lnk`` in the user's SendTo folder
(``%APPDATA%\\Microsoft\\Windows\\SendTo``). Explorer passes the chosen
files as bare paths, which ``__main__.route`` sends to the window (D-063).

The shortcut is written and read through the Windows shell's own
``IShellLinkW`` / ``IPersistFile`` COM interfaces, called with ``ctypes``,
so there is no new dependency and nothing runs through PowerShell (which
Constrained Language Mode may block on agency machines).

Target: the exe in a packaged build; from source, ``pythonw.exe -m stamper``
with the repo as the working folder. A copy of the app that moves (a USB
stick with a new drive letter) leaves the shortcut pointing at the old
place; ``status()`` says so and the window offers to re-create it.

Nothing here imports Qt. Only ``status`` and ``shortcut_path`` work off
Windows (for tests); the rest raise ``SendToError``.
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .core.locations import app_dir, is_frozen

__all__ = [
    "SHORTCUT_NAME",
    "SendToError",
    "SendToState",
    "SendToStatus",
    "Target",
    "create",
    "read_shortcut",
    "remove",
    "sendto_dir",
    "shortcut_path",
    "status",
    "target",
]

SHORTCUT_NAME = "Document Stamper.lnk"
_DESCRIPTION = "Stamp a PDF with Document Stamper"


class SendToError(Exception):
    """The shortcut couldn't be read or written. ``str()`` is a sentence for the user."""


@dataclass(frozen=True, slots=True)
class Target:
    """What the shortcut runs."""

    path: Path
    arguments: str
    working_dir: Path


class SendToState(Enum):
    """Whether the shortcut is there and points at this copy of the app."""

    MISSING = "missing"
    CURRENT = "current"  # points at this copy
    ELSEWHERE = "elsewhere"  # points at another (or moved) copy
    UNREADABLE = "unreadable"


@dataclass(frozen=True, slots=True)
class SendToStatus:
    """``state`` plus where the shortcut points, when it could be read."""

    state: SendToState
    shortcut: Path
    points_to: Path | None = None

    @property
    def message(self) -> str:
        """Plain words for the General page."""
        match self.state:
            case SendToState.MISSING:
                return "Document Stamper isn't in the Send to menu."
            case SendToState.CURRENT:
                return "Document Stamper is in the Send to menu."
            case SendToState.ELSEWHERE:
                where = f" ({self.points_to})" if self.points_to else ""
                return ("The Send to shortcut points at another copy of the app"
                        f"{where}. Re-create it to use this copy.")
            case _:
                return "The Send to shortcut is there but can't be read. Re-create it."


def sendto_dir() -> Path:
    """The user's SendTo folder: ``%APPDATA%\\Microsoft\\Windows\\SendTo``."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "SendTo"


def shortcut_path() -> Path:
    """Where the shortcut goes."""
    return sendto_dir() / SHORTCUT_NAME


def target() -> Target:
    """What the shortcut should run for this copy of the app."""
    if is_frozen():
        exe = Path(sys.executable).resolve()
        return Target(exe, "", exe.parent)
    python = Path(sys.executable).resolve()
    pythonw = python.with_name("pythonw.exe")
    return Target(pythonw if pythonw.is_file() else python, "-m stamper", app_dir())


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def status(shortcut: Path | None = None, want: Target | None = None) -> SendToStatus:
    """Is the shortcut there, and does it point at this copy?"""
    lnk = shortcut or shortcut_path()
    if not lnk.is_file():
        return SendToStatus(SendToState.MISSING, lnk)
    try:
        have = read_shortcut(lnk)
    except SendToError:
        return SendToStatus(SendToState.UNREADABLE, lnk)
    want = want or target()
    same = _same(have.path, want.path) and have.arguments.strip() == want.arguments.strip()
    return SendToStatus(SendToState.CURRENT if same else SendToState.ELSEWHERE, lnk, have.path)


def create(shortcut: Path | None = None, what: Target | None = None) -> Path:
    """Write (or replace) the shortcut; returns its path."""
    lnk = shortcut or shortcut_path()
    what = what or target()
    try:
        lnk.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise SendToError(f"Can't create the Send to folder {lnk.parent}: {e.strerror or e}.") from None
    with _ShellLink() as link:
        link.set_path(str(what.path))
        link.set_arguments(what.arguments)
        link.set_working_directory(str(what.working_dir))
        link.set_description(_DESCRIPTION)
        link.set_icon(str(what.path), 0)
        link.save(str(lnk))
    return lnk


def remove(shortcut: Path | None = None) -> bool:
    """Delete the shortcut; True if one was removed."""
    lnk = shortcut or shortcut_path()
    try:
        lnk.unlink()
    except FileNotFoundError:
        return False
    except OSError as e:
        raise SendToError(f"Can't remove {lnk}: {e.strerror or e}.") from None
    return True


def read_shortcut(lnk: Path) -> Target:
    """Read a shortcut's target, arguments and working folder."""
    with _ShellLink() as link:
        link.load(str(lnk))
        return Target(Path(link.get_path()), link.get_arguments(), Path(link.get_working_directory()))


# ------------------------------------------------------------------ COM by hand


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, text: str) -> _GUID:
        h = text.strip("{}").replace("-", "")
        g = cls()
        g.Data1 = int(h[0:8], 16)
        g.Data2 = int(h[8:12], 16)
        g.Data3 = int(h[12:16], 16)
        for i in range(8):
            g.Data4[i] = int(h[16 + 2 * i : 18 + 2 * i], 16)
        return g


_CLSID_SHELLLINK = "{00021401-0000-0000-C000-000000000046}"
_IID_ISHELLLINKW = "{000214F9-0000-0000-C000-000000000046}"
_IID_IPERSISTFILE = "{0000010B-0000-0000-C000-000000000046}"
_CLSCTX_INPROC_SERVER = 0x1
_COINIT_APARTMENTTHREADED = 0x2
_RPC_E_CHANGED_MODE = -2147417850  # 0x80010106: COM already set up another way on this thread
_STGM_READ = 0x0
_MAX = 32768

# Vtable slots (IUnknown takes 0-2).
_SL_GETPATH, _SL_GETWORKINGDIR, _SL_SETWORKINGDIR = 3, 8, 9
_SL_GETARGS, _SL_SETARGS, _SL_SETDESC, _SL_SETICON, _SL_SETPATH = 10, 11, 7, 17, 20
_PF_LOAD, _PF_SAVE = 5, 6
_QUERYINTERFACE, _RELEASE = 0, 2


class _ShellLink:
    """A ShellLink COM object: just the calls this module needs."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise SendToError("The Send to menu is only available on Windows.")
        self._ole = ctypes.WinDLL("ole32")  # type: ignore[attr-defined]
        self._link = ctypes.c_void_p()
        self._file = ctypes.c_void_p()
        self._uninit = False

    def __enter__(self) -> _ShellLink:
        hr = self._ole.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
        # S_OK and S_FALSE both need a matching CoUninitialize; RPC_E_CHANGED_MODE means
        # COM is already running on this thread (Qt sets it up), which is fine to use.
        if hr in (0, 1):
            self._uninit = True
        elif ctypes.c_long(hr).value != _RPC_E_CHANGED_MODE:
            raise SendToError(f"Windows couldn't start the shell (error 0x{hr & 0xFFFFFFFF:08X}).")
        clsid, iid = _GUID.parse(_CLSID_SHELLLINK), _GUID.parse(_IID_ISHELLLINKW)
        hr = self._ole.CoCreateInstance(ctypes.byref(clsid), None, _CLSCTX_INPROC_SERVER,
                                        ctypes.byref(iid), ctypes.byref(self._link))
        self._check(hr, "create a shortcut")
        pf = _GUID.parse(_IID_IPERSISTFILE)
        self._call(self._link, _QUERYINTERFACE, [ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)],
                   ctypes.byref(pf), ctypes.byref(self._file), what="open the shortcut file")
        return self

    def __exit__(self, *_exc: object) -> None:
        for ptr in (self._file, self._link):
            if ptr:
                self._call(ptr, _RELEASE, [], what="", check=False)
        if self._uninit:
            self._ole.CoUninitialize()

    @staticmethod
    def _check(hr: int, what: str) -> None:
        hr = ctypes.c_long(hr).value
        if hr < 0:
            raise SendToError(f"Windows couldn't {what} (error 0x{hr & 0xFFFFFFFF:08X}).")

    def _call(self, obj: ctypes.c_void_p, slot: int, argtypes: list[object], *args: object,
              what: str, check: bool = True) -> int:
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)  # type: ignore[attr-defined]
        hr = proto(vtable[slot])(obj, *args)
        if check:
            self._check(hr, what)
        return hr

    def _set(self, slot: int, value: str, what: str) -> None:
        self._call(self._link, slot, [ctypes.c_wchar_p], value, what=what)

    def _get(self, slot: int, what: str, *, with_find_data: bool = False) -> str:
        buf = ctypes.create_unicode_buffer(_MAX)
        if with_find_data:
            self._call(self._link, slot, [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32],
                       buf, _MAX, None, 0, what=what)
        else:
            self._call(self._link, slot, [ctypes.c_wchar_p, ctypes.c_int], buf, _MAX, what=what)
        return buf.value

    def set_path(self, path: str) -> None:
        self._set(_SL_SETPATH, path, "set the shortcut's target")

    def set_arguments(self, args: str) -> None:
        self._set(_SL_SETARGS, args, "set the shortcut's arguments")

    def set_working_directory(self, folder: str) -> None:
        self._set(_SL_SETWORKINGDIR, folder, "set the shortcut's folder")

    def set_description(self, text: str) -> None:
        self._set(_SL_SETDESC, text, "set the shortcut's description")

    def set_icon(self, path: str, index: int) -> None:
        self._call(self._link, _SL_SETICON, [ctypes.c_wchar_p, ctypes.c_int], path, index,
                   what="set the shortcut's icon")

    def get_path(self) -> str:
        return self._get(_SL_GETPATH, "read the shortcut's target", with_find_data=True)

    def get_arguments(self) -> str:
        return self._get(_SL_GETARGS, "read the shortcut's arguments")

    def get_working_directory(self) -> str:
        return self._get(_SL_GETWORKINGDIR, "read the shortcut's folder")

    def save(self, path: str) -> None:
        self._call(self._file, _PF_SAVE, [ctypes.c_wchar_p, ctypes.c_int], path, 1,
                   what=f"save the shortcut {path}")

    def load(self, path: str) -> None:
        self._call(self._file, _PF_LOAD, [ctypes.c_wchar_p, ctypes.c_uint32], path, _STGM_READ,
                   what=f"read the shortcut {path}")
