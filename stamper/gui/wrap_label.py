"""A label for long unbroken text, such as a file name, that wraps anywhere (D-137).

A word-wrapped ``QLabel`` can only break at spaces. A suggested file name
like ``99-2026-999901_Report_99-2026-999901_return.pdf`` has none, so the
label asked for its full width, which widened the whole stamp-dialog panel
past its scroll area and cut off every field on the right. This label breaks
the text at any character instead, so it never asks for more than one
character's width. ``text()`` is still the whole text.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QPainter, QPaintEvent
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

__all__ = ["WrapAnywhereLabel"]

_FLAGS = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWrapAnywhere


class WrapAnywhereLabel(QLabel):
    """Plain text that wraps at any character; the full text is also the tooltip."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt name
        """Show ``text``; the tooltip shows it too, for when it's long."""
        super().setText(text)
        self.setToolTip(text)
        self.updateGeometry()

    def _box(self, width: int) -> QRect:
        m = self.contentsMargins()
        inner = max(1, width - m.left() - m.right())
        return self.fontMetrics().boundingRect(QRect(0, 0, inner, 100_000), int(_FLAGS), self.text())

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt name
        """The height depends on the width."""
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt name
        """As tall as the text wrapped at this width."""
        m = self.contentsMargins()
        return self._box(width).height() + m.top() + m.bottom() if self.text() else 0

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt name
        """One character wide, so it never widens its layout."""
        return QSize(self.fontMetrics().horizontalAdvance("M"), self.fontMetrics().height())

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt name
        """The text on one line, or as wrapped at the current width if that's narrower."""
        m = self.contentsMargins()
        one_line = self.fontMetrics().horizontalAdvance(self.text()) + m.left() + m.right()
        w = min(one_line, self.width()) if self.width() > 1 else one_line
        return QSize(w, self.heightForWidth(max(w, 1)))

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt name
        """Draw the text wrapped at any character."""
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        p.setFont(self.font())
        p.drawText(self.contentsRect(), int(_FLAGS), self.text())
        p.end()
