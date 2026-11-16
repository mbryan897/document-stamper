"""About Document Stamper, and the plain text files shown from the Help menu (step 13).

A module of its own because ``help.py`` is the Help window; this is a
dialog that reuses its viewer. The descriptive text (license, source
address, what it's built with, fonts, privacy, no warranty) is the About
help topic, ``assets/help/about.md``, so the help and this dialog can't say
different things. The version numbers are read at run time, never typed in.

Nothing here opens a web address: the help viewer refuses them, and the
source address is only selectable text. "Copy details" puts plain text on
the clipboard: versions, Windows, packaged or not, and where the settings
file came from. Never a document, a folder of documents, a profile, a
field or anything from ``state.json``.
"""

from __future__ import annotations

import platform
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.filenames import visible
from ..core.locations import ConfigSource, is_frozen, resource_dir
from .help import HelpBrowser
from .helpbook import HelpBook, help_dir

__all__ = [
    "CHANGELOG_NAME",
    "LICENSE_NAME",
    "NOTICES_NAME",
    "AboutDialog",
    "TextDialog",
    "copy_details",
    "library_versions",
    "read_bundled",
    "whats_new",
]

LICENSE_NAME = "LICENSE"
NOTICES_NAME = "THIRD-PARTY-NOTICES"
CHANGELOG_NAME = "CHANGELOG.md"
MAX_PATH_SHOWN = 400  # characters of the settings file's path in Copy details


def read_bundled(name: str) -> str:
    """A text file bundled at the top of the resources (``LICENSE``, notices, changelog)."""
    path = resource_dir() / name
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return f"{name} isn't with this copy of the program. It's with the source code."


def program_version() -> str:
    """The installed package version (D-022), as ``--version`` prints it."""
    from ..cli import get_version

    return get_version()


def library_versions() -> list[tuple[str, str]]:
    """``(name, version)`` for Python, PySide6/Qt and PyMuPDF/MuPDF, read now."""
    import pymupdf
    import PySide6
    from PySide6.QtCore import qVersion

    return [
        ("Python", platform.python_version()),
        ("PySide6", f"{PySide6.__version__} (Qt {qVersion()})"),
        ("PyMuPDF", f"{pymupdf.VersionBind} (MuPDF {pymupdf.VersionFitz})"),
    ]


def _windows() -> str:
    """The Windows version from the running system.

    Not ``platform.system()``/``release()``/``version()``: on Windows they go
    through ``platform.uname()``, which also reads the computer's name with
    ``socket.gethostname()``. Nothing here needs it.
    """
    get = getattr(sys, "getwindowsversion", None)
    if get is None:
        return sys.platform
    v = get()
    name = "Windows 11" if v.major == 10 and v.build >= 22000 else f"Windows {v.major}"
    return f"{name} (version {v.major}.{v.minor}.{v.build})"


def copy_details(source: ConfigSource | None) -> str:
    """The text "Copy details" puts on the clipboard.

    Only versions, the system, packaged or not, and the settings file in use:
    where it came from, its path and whether it's read-only.
    """
    lines = [f"Document Stamper {program_version()}"]
    lines += [f"{name} {version}" for name, version in library_versions()]
    lines.append(f"System: {_windows()}")
    lines.append(f"Running: {'packaged' if is_frozen() else 'from source'}")
    if source is None:
        lines.append("Settings file: none loaded")
    else:
        path = visible(str(source.path), keep="")
        if len(path) > MAX_PATH_SHOWN:
            path = path[:MAX_PATH_SHOWN] + "…"
        lines.append(f"Settings file: {source.kind.description}")
        lines.append(f"Settings file path: {path}")
        lines.append(f"Read-only: {'no' if source.writable else 'yes'}")
    return "\n".join(lines) + "\n"


class TextDialog(QDialog):
    """A bundled text in a scrolling, read-only box: plain (license, notices) or Markdown (What's new)."""

    def __init__(self, title: str, text: str, *, markdown: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setObjectName("ground")
        self.resize(760, 640)
        lay = QVBoxLayout(self)
        if markdown:
            self.view: QWidget = HelpBrowser(help_dir())  # the help viewer: refuses web addresses
            self.view.show_markdown(text)
        else:
            box = QPlainTextEdit(text)
            box.setReadOnly(True)
            box.setObjectName("code")
            box.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            self.view = box
        self.view.setAccessibleName(title)
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)


def whats_new(parent: QWidget | None = None) -> TextDialog:
    """The changelog, in the help viewer."""
    return TextDialog("What's new", read_bundled(CHANGELOG_NAME), markdown=True, parent=parent)


class AboutDialog(QDialog):
    """Name and version, the libraries' versions, the About topic, the license and notices."""

    def __init__(self, source: ConfigSource | None, *, book: HelpBook | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.source = source
        self.setWindowTitle("About Document Stamper")
        self.setObjectName("ground")
        self.resize(760, 680)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        self.title = QLabel(f"Document Stamper {program_version()}")
        self.title.setObjectName("hero")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.title)
        self.versions = QLabel(", ".join(f"{n} {v}" for n, v in library_versions()))
        self.versions.setObjectName("muted")
        self.versions.setTextFormat(Qt.TextFormat.PlainText)
        self.versions.setWordWrap(True)
        self.versions.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.versions)

        if book is None:
            from .help import _replacements

            book = HelpBook.load(replacements=_replacements())
        self.text = HelpBrowser(book.folder)
        self.text.setAccessibleName("About Document Stamper")
        self.text.show_markdown(book.topics["about"].markdown)
        lay.addWidget(self.text, 1)

        row = QHBoxLayout()
        self.license_button = QPushButton("License…")
        self.notices_button = QPushButton("Third-party notices…")
        self.copy_button = QPushButton("Copy details")
        self.copy_button.setToolTip("Copy the version numbers and where the settings file is, for a support request")
        close = QPushButton("Close")
        close.setDefault(True)
        for b in (self.license_button, self.notices_button, self.copy_button):
            b.setObjectName("outline")
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self.copied = QLabel("")
        self.copied.setObjectName("muted")
        lay.addWidget(self.copied)

        self.license_button.clicked.connect(self.show_license)
        self.notices_button.clicked.connect(self.show_notices)
        self.copy_button.clicked.connect(self.copy)
        close.clicked.connect(self.accept)
        self.shown: TextDialog | None = None

    def show_license(self) -> TextDialog:
        """The bundled LICENSE in a scrolling read-only box."""
        return self._show(TextDialog("License", read_bundled(LICENSE_NAME), parent=self))

    def show_notices(self) -> TextDialog:
        """The bundled third-party notices in a scrolling read-only box."""
        return self._show(TextDialog("Third-party notices", read_bundled(NOTICES_NAME), parent=self))

    def _show(self, dialog: TextDialog) -> TextDialog:
        self.shown = dialog
        dialog.open()
        return dialog

    def copy(self) -> str:
        """Put the details on the clipboard; returns them."""
        text = copy_details(self.source)
        QGuiApplication.clipboard().setText(text)
        self.copied.setText("Copied.")
        return text
