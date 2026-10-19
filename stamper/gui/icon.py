"""The app icon for the window, the taskbar and Alt+Tab (Phase 5).

Qt doesn't take the exe's icon for its windows, so the window icon is set
here from ``assets/icon/png``: one PNG per size, so Qt picks a sharp one at
any display scaling. A missing file isn't fatal; the window just shows Qt's
default icon.

Run from source the process is ``python.exe``/``pythonw.exe``, and the
taskbar would group the window under Python's icon. ``use_own_taskbar_id``
gives the process its own AppUserModelID so the window's icon is used. The
packaged exe doesn't need it (Windows uses the exe's own icon) and doesn't
set it, so pinning the exe to the taskbar behaves as usual.

The exe's own icon (Explorer, Send to) is ``assets/icon/stamper.ico``, put
in by PyInstaller (``packaging/spec_common.py``). All the files are made by
``tools/make_icon.py``.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon

from ..core.locations import is_frozen, resource_dir

__all__ = ["ICON_SIZES", "TASKBAR_ID", "app_icon", "icon_dir", "ico_path", "use_own_taskbar_id"]

ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256, 512)
TASKBAR_ID = "DocumentStamper.App"


def icon_dir() -> Path:
    """``assets/icon`` in the repo, or in a packaged build's resources."""
    return resource_dir() / "assets" / "icon"


def ico_path() -> Path:
    """The Windows ``.ico`` (used by the Send to shortcut when run from source)."""
    return icon_dir() / "stamper.ico"


def app_icon() -> QIcon:
    """The app icon, every size that's there. Empty (``isNull()``) if none are."""
    icon = QIcon()
    for size in ICON_SIZES:
        path = icon_dir() / "png" / f"stamper-{size}.png"
        if path.is_file():
            icon.addFile(str(path), QSize(size, size))
    return icon


def use_own_taskbar_id() -> bool:
    """From source on Windows, group the window under its own taskbar icon.

    Must run before the first window is shown. Returns True if the ID was set.
    Does nothing in the packaged exe or off Windows.
    """
    if os.name != "nt" or is_frozen():
        return False
    try:
        shell32 = ctypes.WinDLL("shell32")  # type: ignore[attr-defined]
        set_id = shell32.SetCurrentProcessExplicitAppUserModelID
        set_id.argtypes = [ctypes.c_wchar_p]
        set_id.restype = ctypes.c_long
        return set_id(TASKBAR_ID) >= 0
    except (OSError, AttributeError):
        return False
