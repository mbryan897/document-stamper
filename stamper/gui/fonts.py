"""Interface fonts (D-006, D-071).

Atkinson Hyperlegible Next for the interface, Atkinson Hyperlegible Mono for
identifier fields, so 0/O and 1/l are never confused. The files are bundled
in ``assets/fonts`` and registered with Qt at start-up. A missing interface
font isn't fatal: Qt falls back to the system font. (The stamp font is a
different matter; the engine refuses to stamp without it.)
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase

__all__ = ["MONO_FAMILY", "UI_FAMILY", "UI_FONT_FILES", "load_fonts", "mono_font", "ui_font_dir"]

UI_FAMILY = "Atkinson Hyperlegible Next"
MONO_FAMILY = "Atkinson Hyperlegible Mono"
UI_FONT_FILES = (
    "AtkinsonHyperlegibleNext-Regular.ttf",
    "AtkinsonHyperlegibleNext-Medium.ttf",
    "AtkinsonHyperlegibleNext-Bold.ttf",
    "AtkinsonHyperlegibleMono-Regular.ttf",
    "AtkinsonHyperlegibleMono-SemiBold.ttf",
)

_loaded: list[str] | None = None


def ui_font_dir() -> Path:
    """Where the interface fonts live.

    One function so packaging (Phase 5, O-010) can change the lookup in one place.
    """
    return Path(__file__).resolve().parents[2] / "assets" / "fonts"


def load_fonts() -> list[str]:
    """Register the bundled interface fonts with Qt; returns the families loaded.

    Needs a QGuiApplication. Safe to call more than once.
    """
    global _loaded
    if _loaded is not None:
        return _loaded
    families: list[str] = []
    for name in UI_FONT_FILES:
        path = ui_font_dir() / name
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id >= 0:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))
    _loaded = sorted(set(families))
    return _loaded


def mono_font(pixel_size: int = 17, weight: QFont.Weight = QFont.Weight.DemiBold) -> QFont:
    """The identifier font: Atkinson Hyperlegible Mono, SemiBold by default."""
    font = QFont(MONO_FAMILY)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font
