"""Show a saved file in Explorer without starting a program by name (D-116).

``select_in_explorer`` asks the Windows shell itself to open the folder and
select the file (``SHOpenFolderAndSelectItems``), called with ``ctypes`` like
``sendto.py``. No process is started from here, so nothing depends on the
search path: a planted ``explorer.exe`` next to a portable copy of the app is
never run, and paths with commas, quotes or spaces need no quoting.

Nothing here imports Qt. Off Windows every call raises ``OSError``.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

__all__ = ["select_in_explorer"]

_COINIT_APARTMENTTHREADED = 0x2
_RPC_E_CHANGED_MODE = ctypes.c_long(0x80010106).value


def select_in_explorer(path: Path) -> None:
    """Open ``path``'s folder in Explorer with the file selected.

    Raises ``OSError`` when the shell can't do it (not Windows, the file is
    gone, or the call fails); the caller then falls back to opening the folder.
    """
    if os.name != "nt":
        raise OSError("Explorer is only available on Windows.")
    full = os.path.abspath(path)
    if not os.path.exists(full):
        raise OSError(f"{path} doesn't exist.")

    ole32 = ctypes.WinDLL("ole32")  # type: ignore[attr-defined]
    shell32 = ctypes.WinDLL("shell32")  # type: ignore[attr-defined]
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    shell32.ILCreateFromPathW.argtypes = [ctypes.c_wchar_p]
    shell32.ILCreateFromPathW.restype = ctypes.c_void_p
    shell32.ILFree.argtypes = [ctypes.c_void_p]
    shell32.ILFree.restype = None
    shell32.SHOpenFolderAndSelectItems.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong]
    shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long

    hr = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    # S_OK and S_FALSE need a matching CoUninitialize; RPC_E_CHANGED_MODE means
    # COM already runs on this thread (Qt sets it up), which is fine to use.
    uninit = hr in (0, 1)
    if not uninit and hr != _RPC_E_CHANGED_MODE:
        raise OSError(f"Windows couldn't start the shell (error 0x{hr & 0xFFFFFFFF:08X}).")
    try:
        pidl = shell32.ILCreateFromPathW(full)
        if not pidl:
            raise OSError(f"Windows couldn't find {path}.")
        try:
            # cidl = 0: pidl names the single item to select (documented behaviour).
            hr = shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0)
        finally:
            shell32.ILFree(pidl)
        if hr < 0:
            raise OSError(f"Windows couldn't show the file (error 0x{hr & 0xFFFFFFFF:08X}).")
    finally:
        if uninit:
            ole32.CoUninitialize()
