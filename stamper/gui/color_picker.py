"""The stamp color button in Profile settings (D-094).

A button showing the current color as a swatch and a name. Clicking it opens
a panel like Word's font color menu: a row of dark colors suited to stamps,
Word's ten standard colors, and "More colors…", which opens the color wheel
(Qt's color dialog, through ``Services.ask_color`` so tests never open it).

The config keeps the color as ``#RRGGBB`` (D-045); nothing here changes the
schema. A color too light to read on the white box gets a note, not a block.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

__all__ = [
    "MIN_CONTRAST",
    "STAMP_COLORS",
    "ColorButton",
    "color_name",
    "contrast_on_white",
    "light_color_note",
]

# (name, hex). Row one: dark colors that read well at 9 pt; the default stamp
# blue first. Row two: Word's ten standard font colors, in Word's order.
STAMP_COLORS: tuple[tuple[str, str], ...] = (
    ("Stamp blue", "#2B3A8F"),
    ("Black", "#000000"),
    ("Dark gray", "#404040"),
    ("Gray", "#7F7F7F"),
    ("Navy", "#1F3864"),
    ("Dark teal", "#0E5A62"),
    ("Dark green", "#375623"),
    ("Brown", "#843C0C"),
    ("Maroon", "#7B1B1B"),
    ("Dark purple", "#4B2366"),
    ("Dark red", "#C00000"),
    ("Red", "#FF0000"),
    ("Orange", "#FFC000"),
    ("Yellow", "#FFFF00"),
    ("Light green", "#92D050"),
    ("Green", "#00B050"),
    ("Light blue", "#00B0F0"),
    ("Blue", "#0070C0"),
    ("Dark blue", "#002060"),
    ("Purple", "#7030A0"),
)
COLUMNS = 10
SWATCH = 22
MIN_CONTRAST = 3.0  # against white; below this the stamp is hard to read

_NAMES = {h.upper(): n for n, h in STAMP_COLORS}


def color_name(hex_color: str) -> str:
    """The palette name for a color, or its code for any other color."""
    return _NAMES.get(hex_color.upper(), hex_color.upper())


def _luminance(hex_color: str) -> float:
    c = QColor(hex_color)

    def channel(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(c.red()) + 0.7152 * channel(c.green()) + 0.0722 * channel(c.blue())


def contrast_on_white(hex_color: str) -> float:
    """WCAG contrast ratio of a color against white (1 to 21)."""
    return 1.05 / (_luminance(hex_color) + 0.05)


def light_color_note(hex_color: str) -> str:
    """A note for colors too light to read on the white box; empty otherwise."""
    if not QColor.isValidColorName(hex_color) or contrast_on_white(hex_color) >= MIN_CONTRAST:
        return ""
    return (f"{color_name(hex_color)} is light and may be hard to read on the page, "
            "especially once printed or scanned. A darker color is safer.")


def _swatch_icon(hex_color: str, size: int = 18) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor("#8B96A8"))
    p.setBrush(QColor(hex_color))
    p.drawRoundedRect(0.5, 0.5, size - 1, size - 1, 3, 3)
    p.end()
    return QIcon(pix)


class _Panel(QFrame):
    """The grid of swatches and "More colors…" shown in the button's menu."""

    picked = Signal(str)
    more = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("colorPanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        for title, start in (("Dark colors", 0), ("Standard colors", COLUMNS)):
            heading = QLabel(title)
            heading.setObjectName("muted")
            lay.addWidget(heading)
            grid = QGridLayout()
            grid.setSpacing(4)
            for i, (name, hex_color) in enumerate(STAMP_COLORS[start:start + COLUMNS]):
                b = QToolButton()
                b.setObjectName("swatch")
                b.setIcon(_swatch_icon(hex_color, SWATCH))
                b.setIconSize(QSize(SWATCH, SWATCH))
                b.setToolTip(f"{name} ({hex_color})")
                b.setAccessibleName(name)
                b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                b.setAutoRaise(True)
                b.clicked.connect(lambda _c=False, h=hex_color: self.picked.emit(h))
                grid.addWidget(b, 0, i)
            lay.addLayout(grid)
        more = QPushButton("More colors…")
        more.setObjectName("link")
        more.setCursor(Qt.CursorShape.PointingHandCursor)
        more.clicked.connect(self.more.emit)
        lay.addWidget(more, 0, Qt.AlignmentFlag.AlignLeft)
        self.more_button = more

    def swatch(self, name: str) -> QToolButton:
        """The swatch button for a palette color (tests and keyboard)."""
        for b in self.findChildren(QToolButton, "swatch"):
            if b.accessibleName() == name:
                return b
        raise KeyError(name)


class ColorButton(QToolButton):
    """Shows the stamp color; its menu offers the palette and the color wheel.

    ``ask_color(parent, current)`` returns a ``#RRGGBB`` string or None; it is
    the window's ``Services.ask_color``. Emits ``changed(hex)``.
    """

    changed = Signal(str)

    def __init__(self, color: str, ask_color, parent: QWidget | None = None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setObjectName("insert")  # same look as the stamp editor's Insert… button
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(18, 18))
        self._ask_color = ask_color
        self.panel = _Panel()
        self.panel.picked.connect(self._pick)
        self.panel.more.connect(self._more)
        menu = QMenu(self)
        action = QWidgetAction(menu)
        action.setDefaultWidget(self.panel)
        menu.addAction(action)
        self.setMenu(menu)
        self._color = ""
        self.set_color(color)

    def color(self) -> str:
        """The color shown, as ``#RRGGBB``."""
        return self._color

    def set_color(self, hex_color: str) -> None:
        """Show a color without emitting ``changed``."""
        self._color = hex_color.upper() if QColor.isValidColorName(hex_color) else hex_color
        valid = QColor.isValidColorName(hex_color)
        self.setIcon(_swatch_icon(hex_color) if valid else QIcon())
        self.setText(color_name(hex_color) if valid else hex_color)
        self.setToolTip(self._color)

    def _pick(self, hex_color: str) -> None:
        self.menu().hide()
        if hex_color.upper() != self._color:
            self.set_color(hex_color)
            self.changed.emit(self._color)

    def _more(self) -> None:
        self.menu().hide()
        chosen = self._ask_color(self, self._color)
        if chosen:
            self._pick(chosen)
