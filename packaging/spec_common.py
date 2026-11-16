"""Shared settings for the two PyInstaller specs (D-087, D-092).

``stamper-onefile.spec`` builds the single ``DocumentStamper.exe``;
``stamper.spec`` builds the ``DocumentStamper`` folder (fallback).
Both bundle the same files and leave out the same unused Qt parts.
"""

from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

ROOT = Path(__file__).resolve().parent.parent

# The exes' own icon: Explorer, the Send to menu, the taskbar when pinned.
# Made by tools/make_icon.py; the window's icon comes from assets/icon/png.
ICON = str(ROOT / "assets" / "icon" / "stamper.ico")

# The exe's version resource (Explorer > Properties > Details), written by
# packaging/version_info.py; build.ps1 passes its path. None when PyInstaller
# is run by hand, and packaging/check_exe.py then fails the build.
VERSION_FILE = os.environ.get("STAMPER_VERSION_FILE") or None

DATAS = [
    (str(ROOT / "assets" / "fonts" / "*.ttf"), "assets/fonts"),
    (str(ROOT / "assets" / "fonts" / "*.txt"), "assets/fonts"),
    (str(ROOT / "config" / "default.json"), "config"),
    (str(ROOT / "assets" / "help"), "assets/help"),  # the Help window's topics and pictures (D-133)
    (str(ROOT / "assets" / "icon" / "png" / "*.png"), "assets/icon/png"),  # window and taskbar icon
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "THIRD-PARTY-NOTICES"), "."),  # About › Third-party notices…
    (str(ROOT / "CHANGELOG.md"), "."),  # Help › What's new
    *copy_metadata("document-stamper"),  # so --version reports the real version
]

EXCLUDES = ["tkinter", "pytest", "pytestqt", "PyInstaller", "setuptools", "pip"]

# Qt parts the app never uses. The one-file exe unpacks everything on every
# launch, so each one left out makes start-up faster. Matched against the
# bundled path, lowercase, on Windows and Linux names alike.
UNUSED = (
    "pyside6/translations/",  # the interface is English only
    "opengl32sw.dll",  # software OpenGL fallback (20 MB); widgets don't need it
    "qt6quick", "qt6qml", "qt6virtualkeyboard",  # QML engine and on-screen keyboard
    "platforminputcontexts/",  # the virtual keyboard's plugin
    "qt6pdf", "imageformats/qpdf",  # Qt's own PDF module; PyMuPDF does all PDF work
)


def trim(toc):
    """Drop unused Qt files from a PyInstaller TOC (list of (dest, src, kind))."""
    def keep(entry):
        dest = str(entry[0]).replace("\\", "/").lower()
        return not any(part in dest for part in UNUSED)

    return [e for e in toc if keep(e)]


def analysis(script):
    """Analyse one entry script with the shared settings, unused Qt parts removed."""
    a = Analysis(  # noqa: F821 - injected into the spec's namespace below
        [str(ROOT / "packaging" / script)],
        pathex=[str(ROOT)],
        datas=DATAS,
        excludes=EXCLUDES,
        noarchive=False,
    )
    a.binaries = trim(a.binaries)
    a.datas = trim(a.datas)
    return a
