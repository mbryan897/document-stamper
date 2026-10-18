"""The main window: drop screen, stamp dialog and saved screen.

One window, three screens in a stack. The drop screen takes one PDF (drag
and drop or "Choose a file…") and shows the active profile, with a way to
switch profiles or open another config file for testing. Problems opening a
file (not a PDF, missing, XFA, no password) are plain messages on the drop
screen, which stays put.

The window sizes itself to the screen it opens on (D-093): 1200 x 780,
centred, when that fits; maximized otherwise. No screen in the stack may need
more height than ``MINIMUM_SIZE``, so the buttons at the bottom of each
screen stay inside the window at any size the user can choose.

The config comes from the lookup in ``core/locations.py`` (D-017, D-080):
next to the app, then ``%APPDATA%``, then the built-in default. "Open another
config…" is a per-session override on top of it (D-072). "Profile
settings…" edits whichever config is loaded and saves it back (D-082). The
last profile and the last folder a file came from are remembered in
``state.json`` (D-081); field values never are.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import (
    QCloseEvent,
    QDragEnterEvent,
    QDropEvent,
    QGuiApplication,
    QPixmap,
    QScreen,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.config import Config, ConfigError, Profile, load_config
from ..core.locations import (
    ConfigSource,
    Locations,
    SourceKind,
    describe,
    find_config,
    load_state,
    locations,
    save_state,
)
from ..handlers.base import (
    ApplyResult,
    DocumentInfo,
    Handler,
    PasswordRequired,
    StampJob,
    WrongPassword,
)
from ..handlers.pdf import PdfHandler
from .engine import EngineClient, EngineFailure
from .fonts import MONO_FAMILY
from .profiles import ProfileSettings
from .services import Services, native_services
from .stamp_dialog import StampDialog, group_warnings

__all__ = [
    "FRAME_ALLOWANCE",
    "MINIMUM_SIZE",
    "PREFERRED_SIZE",
    "DropScreen",
    "MainWindow",
    "SavedScreen",
    "WindowFit",
    "fit_to_screen",
    "several_files_message",
]

MAX_CONFIG_PROBLEMS = 6
THUMB_W, THUMB_H = 200, 260

PREFERRED_SIZE = QSize(1200, 780)  # the mockup's working area
MINIMUM_SIZE = QSize(880, 560)  # every screen's buttons fit in this
FRAME_ALLOWANCE = QSize(16, 40)  # Windows title bar and borders, in logical pixels


@dataclass(frozen=True)
class WindowFit:
    """How the window opens on a screen: its size, its minimum, and whether maximized."""

    size: QSize
    minimum: QSize
    maximized: bool


def fit_to_screen(
    available: QSize,
    *,
    preferred: QSize = PREFERRED_SIZE,
    minimum: QSize = MINIMUM_SIZE,
    frame: QSize = FRAME_ALLOWANCE,
) -> WindowFit:
    """Size the window for a screen's available area (taskbar excluded), D-093.

    The preferred size opens as a normal window when it fits with its frame;
    otherwise the window opens maximized, with a restore size that fits. The
    minimum is never larger than the screen, so the window can always be made
    small enough to see all of it.
    """
    room = QSize(max(available.width() - frame.width(), 1), max(available.height() - frame.height(), 1))
    fits = preferred.width() <= room.width() and preferred.height() <= room.height()
    return WindowFit(
        size=QSize(preferred) if fits else preferred.boundedTo(room),
        minimum=minimum.boundedTo(room),
        maximized=not fits,
    )


def several_files_message(count: int) -> str:
    """What the drop screen says when more than one file arrives (batch is out of scope)."""
    return f"Drop one file at a time. {count} files were dropped; none were opened."


def _message_frame() -> tuple[QFrame, QLabel]:
    frame = QFrame()
    frame.setObjectName("notice")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 12, 16, 12)
    label = QLabel()
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lay.addWidget(label)
    frame.hide()
    return frame, label


class DropScreen(QWidget):
    """One large drop target, "Choose a file…", and the active profile."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ground")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 32, 32, 24)
        outer.setSpacing(24)
        outer.addStretch(1)

        self.target = QFrame()
        self.target.setObjectName("dropTarget")
        self.target.setProperty("dragging", False)
        # 320 tall when there's room; shrinks to 220 so a short window still shows everything.
        self.target.setMinimumSize(560, 220)
        self.target.setMaximumSize(720, 320)
        self.target.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        tl = QVBoxLayout(self.target)
        tl.setContentsMargins(10, 10, 10, 10)
        inner = QFrame()
        inner.setObjectName("dropInner")
        il = QVBoxLayout(inner)
        il.setContentsMargins(24, 32, 24, 32)
        il.setSpacing(14)
        il.addStretch(1)
        hero = QLabel("Drop a PDF to stamp")
        hero.setObjectName("hero")
        hero.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lead = QLabel("You'll confirm the label and placement before anything is saved.")
        lead.setObjectName("lead")
        lead.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lead.setWordWrap(True)
        self.choose_button = QPushButton("Choose a file…")
        self.choose_button.setObjectName("outline")
        self.choose_button.setDefault(True)
        il.addWidget(hero)
        il.addWidget(lead)
        il.addSpacing(6)
        il.addWidget(self.choose_button, 0, Qt.AlignmentFlag.AlignCenter)
        il.addStretch(1)
        tl.addWidget(inner)
        outer.addWidget(self.target, 4, Qt.AlignmentFlag.AlignHCenter)

        self.message_frame, self.message = _message_frame()
        self.message_frame.setMaximumWidth(720)
        outer.addWidget(self.message_frame, 0, Qt.AlignmentFlag.AlignHCenter)

        row = QHBoxLayout()
        row.setSpacing(12)
        row.addStretch(1)
        with_label = QLabel("Stamping with")
        with_label.setObjectName("muted")
        self.profile_combo = QComboBox()
        self.profile_combo.setAccessibleName("Profile")
        self.profile_combo.setMinimumWidth(220)
        with_label.setBuddy(self.profile_combo)
        self.config_button = QPushButton("Open another config…")
        self.config_button.setObjectName("link")
        self.config_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button = QPushButton("Profile settings…")
        self.settings_button.setObjectName("link")
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(with_label)
        row.addWidget(self.profile_combo)
        row.addWidget(self.settings_button)
        row.addWidget(self.config_button)
        row.addStretch(1)
        outer.addLayout(row)
        self.config_label = QLabel("")
        self.config_label.setObjectName("muted")
        self.config_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.config_label)
        outer.addStretch(1)

    def show_message(self, text: str) -> None:
        """A plain message under the drop target; empty hides it."""
        self.message.setText(text)
        self.message_frame.setVisible(bool(text))

    def set_dragging(self, on: bool) -> None:
        """Highlight the drop target while a file is dragged over the window."""
        self.target.setProperty("dragging", on)
        self.target.style().unpolish(self.target)
        self.target.style().polish(self.target)


class SavedScreen(QWidget):
    """Where the file went, notes from the result, and what to do next."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ground")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 32, 32, 32)
        outer.addStretch(1)
        card = QFrame()
        card.setObjectName("savedCard")
        card.setMaximumWidth(920)
        cl = QHBoxLayout(card)
        cl.setContentsMargins(44, 40, 44, 40)
        cl.setSpacing(40)

        self.thumbnail = QLabel()
        self.thumbnail.setObjectName("thumbnail")
        self.thumbnail.setFixedSize(THUMB_W + 2, THUMB_H + 2)
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.setAccessibleName("First page of the stamped copy")
        cl.addWidget(self.thumbnail, 0, Qt.AlignmentFlag.AlignTop)

        right = QVBoxLayout()
        right.setSpacing(18)
        title = QLabel("Stamped and saved")
        title.setObjectName("savedTitle")
        self.lead = QLabel()
        self.lead.setObjectName("lead")
        self.lead.setWordWrap(True)
        self.lead.setTextFormat(Qt.TextFormat.RichText)
        right.addWidget(title)
        right.addWidget(self.lead)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        saved_to = QLabel("Saved to")
        saved_to.setObjectName("muted")
        self.path_label = QLabel()
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        original = QLabel("Original")
        original.setObjectName("muted")
        self.original_label = QLabel()
        self.original_label.setWordWrap(True)
        grid.addWidget(saved_to, 0, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.path_label, 0, 1)
        grid.addWidget(original, 1, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.original_label, 1, 1)
        grid.setColumnStretch(1, 1)
        right.addLayout(grid)

        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setObjectName("help")
        self.notes.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right.addWidget(self.notes)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.open_button = QPushButton("Open file")
        self.open_button.setObjectName("primary")
        self.folder_button = QPushButton("Show in folder")
        self.another_button = QPushButton("Stamp another file")
        self.another_button.setObjectName("link")
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.folder_button)
        buttons.addWidget(self.another_button)
        buttons.addStretch(1)
        right.addLayout(buttons)
        cl.addLayout(right, 1)
        outer.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)
        self.result: ApplyResult | None = None

    def show_result(self, result: ApplyResult, *, label: str, identifier: str, signed: bool) -> None:
        """Fill the screen from an ApplyResult."""
        self.result = result
        pages = "The page carries" if result.pages_stamped == 1 else f"All {result.pages_stamped} pages carry"
        if identifier:
            self.lead.setText(
                f"{pages} {label.lower()} "
                f"<span style=\"font-family: '{MONO_FAMILY}'; font-weight: 600; color: #18202E\">"
                f"{_escape(identifier)}</span>.")
        else:
            self.lead.setText(f"{pages} the stamp.")
        self.path_label.setText(str(result.output_path))
        self.original_label.setText("Unchanged, still digitally signed" if signed else "Unchanged")
        # Grouped like the dialog's notes (D-066), and capped: one line per shrunk page
        # on a long receipt bundle would push the buttons off the window (D-093).
        notes = group_warnings(result.warnings)
        self.notes.setText("\n".join(f"Note: {w}" for w in notes))
        self.notes.setVisible(bool(notes))
        self.thumbnail.clear()
        self.open_button.setFocus()

    def show_thumbnail(self, png: bytes) -> None:
        """Show the first page of the stamped copy."""
        pix = QPixmap()
        pix.loadFromData(png, "PNG")
        ratio = self.devicePixelRatioF() or 1.0
        pix = pix.scaled(QSize(int(THUMB_W * ratio), int(THUMB_H * ratio)),
                         Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        pix.setDevicePixelRatio(ratio)
        self.thumbnail.setPixmap(pix)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class MainWindow(QMainWindow):
    """The Document Stamper window."""

    def __init__(
        self,
        *,
        config_path: Path | None = None,
        services: Services | None = None,
        handler: Handler | None = None,
        today: date | None = None,
        locs: Locations | None = None,
        sendto_shortcut: Path | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Document Stamper")
        screen = QGuiApplication.primaryScreen()
        fit = fit_to_screen(screen.availableGeometry().size() if screen else PREFERRED_SIZE
                            + FRAME_ALLOWANCE)
        self.setMinimumSize(fit.minimum)
        self.resize(fit.size)
        self._watching_screen = False
        self.setAcceptDrops(True)
        self.services = services or native_services()
        self.handler = handler or PdfHandler()
        self.engine = EngineClient(self.handler, self)
        self.today = today
        self.locs = locs or locations()
        self.state = load_state(self.locs)
        self._sendto_shortcut = sendto_shortcut
        self.config: Config | None = None
        self.config_path: Path | None = None
        self.source: ConfigSource | None = None
        self.profile: Profile | None = None
        self.dialog: StampDialog | None = None
        self.settings: ProfileSettings | None = None
        self._opening: tuple[int, Path, str | None] | None = None
        self._thumb_req = 0
        self._last_folder: Path | None = (
            Path(self.state.last_folder) if self.state.last_folder and Path(self.state.last_folder).is_dir()
            else None)

        self.stack = QStackedWidget()
        self.drop = DropScreen()
        self.saved = SavedScreen()
        self.stack.addWidget(self.drop)
        self.stack.addWidget(self.saved)
        self.setCentralWidget(self.stack)

        self.drop.choose_button.clicked.connect(self.choose_file)
        self.drop.config_button.clicked.connect(self.choose_config)
        self.drop.settings_button.clicked.connect(self.open_settings)
        self.drop.profile_combo.activated.connect(self._profile_picked)
        self.saved.open_button.clicked.connect(self._open_saved)
        self.saved.folder_button.clicked.connect(self._show_saved)
        self.saved.another_button.clicked.connect(lambda: self.show_drop())
        self.engine.inspected.connect(self._on_inspected)
        self.engine.inspect_failed.connect(self._on_inspect_failed)
        self.engine.previewed.connect(self._on_thumbnail)

        source = describe(Path(config_path), SourceKind.OTHER) if config_path else find_config(self.locs)
        loaded = self.load_config(source.path, source)
        # Keep the reason on screen when the config can't be used.
        self.show_drop("" if loaded else self.drop.message.text())

    # ------------------------------------------------------------ size on screen

    def place_on(self, available: QRect) -> WindowFit:
        """Size and centre the window for a screen's available area; returns the fit."""
        fit = fit_to_screen(available.size())
        self.setMinimumSize(fit.minimum)
        self.resize(fit.size)
        outer_w = fit.size.width() + FRAME_ALLOWANCE.width()
        outer_h = fit.size.height() + FRAME_ALLOWANCE.height()
        self.move(available.x() + max(0, (available.width() - outer_w) // 2),
                  available.y() + max(0, (available.height() - outer_h) // 2))
        return fit

    def show_fitted(self) -> None:
        """Show the window sized to its screen: normal and centred if it fits, else maximized."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            self.show()
            return
        if self.place_on(screen.availableGeometry()).maximized:
            self.showMaximized()
        else:
            self.show()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt name
        """Start following screen changes once the window has a native handle."""
        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None and not self._watching_screen:
            handle.screenChanged.connect(self._screen_changed)
            self._watching_screen = True

    def _screen_changed(self, screen: QScreen | None) -> None:
        # Dragged to a smaller monitor: let the window shrink to fit it.
        if screen is not None:
            self.setMinimumSize(fit_to_screen(screen.availableGeometry().size()).minimum)

    # ------------------------------------------------------------ config and profile

    def load_config(self, path: Path, source: ConfigSource | None = None) -> bool:
        """Load a config file; problems are shown on the drop screen.

        ``source`` says where it came from (the lookup, or "Open another
        config…" when not given).
        """
        path = Path(path)
        try:
            config = load_config(path)
        except ConfigError as e:
            problems = e.problems[:MAX_CONFIG_PROBLEMS]
            more = len(e.problems) - len(problems)
            lines = "\n".join(f"• {p}" for p in problems)
            tail = f"\n…and {more} more." if more > 0 else ""
            self.drop.show_message(f"{path.name} can't be used:\n{lines}{tail}")
            if self.config is None:
                self.drop.profile_combo.clear()
                self.drop.config_label.setText("No config loaded. Open another config to continue.")
                self.drop.settings_button.setEnabled(False)
            return False
        keep = self.profile.name if self.profile and self.config else self.state.last_profile
        self.config, self.config_path = config, path
        self.source = source or describe(path, SourceKind.OTHER)
        try:
            self.profile = config.profile(keep) if keep else config.default_profile
        except KeyError:
            self.profile = config.default_profile
        combo = self.drop.profile_combo
        combo.clear()
        for p in config.profiles:
            combo.addItem(p.name, p.name)
        combo.setCurrentIndex(combo.findData(self.profile.name))
        combo.setEnabled(len(config.profiles) > 1)
        self.drop.config_label.setText(f"Config: {self.config_path} ({self.source.kind.description})")
        self.drop.settings_button.setEnabled(True)
        self.drop.show_message("")
        return True

    def choose_config(self) -> None:
        """"Open another config…": load a config.json for this session only."""
        path = self.services.ask_config_file(self)
        if path is not None:
            self.load_config(path)

    def _profile_picked(self, index: int) -> None:
        if self.config is not None:
            self.profile = self.config.profile(self.drop.profile_combo.itemData(index))
            self.state.last_profile = self.profile.name
            save_state(self.state, self.locs)

    # ------------------------------------------------------------ screens

    def show_drop(self, message: str = "") -> None:
        """Back to the drop screen, optionally with a message."""
        self._close_dialog()
        self._close_settings()
        self.stack.setCurrentWidget(self.drop)
        self.drop.show_message(message)
        self.drop.choose_button.setFocus()

    # ------------------------------------------------------------ profile settings

    def open_settings(self) -> ProfileSettings | None:
        """Show Profile settings for the loaded config."""
        if self.config is None or self.source is None:
            return None
        self._close_dialog()
        self._close_settings()
        settings = ProfileSettings(
            config=self.config, source=self.source, locs=self.locs, engine=self.engine,
            services=self.services, today=self.today, sendto_shortcut=self._sendto_shortcut)
        settings.saved.connect(self._settings_saved)
        settings.closed.connect(lambda: self.show_drop())
        self.settings = settings
        self.stack.addWidget(settings)
        self.stack.setCurrentWidget(settings)
        settings.nav.setFocus()
        return settings

    def _settings_saved(self, source: ConfigSource) -> None:
        if self.load_config(source.path, source):
            self.show_drop(f"Profile settings saved to {source.path}.")
        else:
            self.show_drop()

    def _close_settings(self) -> None:
        if self.settings is not None:
            self.settings.dispose()
            self.stack.removeWidget(self.settings)
            self.settings.deleteLater()
            self.settings = None

    def _close_dialog(self) -> None:
        if self.dialog is not None:
            self.dialog.disconnect_engine()
            self.stack.removeWidget(self.dialog)
            self.dialog.deleteLater()
            self.dialog = None

    # ------------------------------------------------------------ opening a file

    def choose_file(self) -> None:
        """"Choose a file…": the native dialog, PDFs only."""
        path = self.services.ask_open_file(self, self._last_folder)
        if path is not None:
            self.open_file(path)

    def open_files(self, paths: Sequence[Path]) -> None:
        """Open what was dropped or passed on the command line: exactly one file."""
        if len(paths) > 1:
            self.show_drop(several_files_message(len(paths)))
        elif paths:
            self.open_file(paths[0])

    def open_file(self, path: Path) -> None:
        """Check a file and, if it can be stamped, show the stamp dialog for it."""
        path = Path(path)
        if self.config is None or self.profile is None:
            self.show_drop("Open a config file that works before choosing a PDF.")
            return
        if not path.is_file():
            self.show_drop(f"Can't find the file {path}.")
            return
        if not self.handler.can_open(path):
            self.show_drop(f"{path.name} isn't a PDF file. Choose a PDF to stamp.")
            return
        self._last_folder = path.parent
        self.state.last_folder = str(path.parent)
        save_state(self.state, self.locs)
        self.show_drop(f"Opening {path.name}…")
        self._opening = (self.engine.inspect(path), path, None)

    def _on_inspected(self, req: int, info: DocumentInfo) -> None:
        if self._opening is None or self._opening[0] != req:
            return
        _, path, password = self._opening
        self._opening = None
        if info.xfa:
            self.show_drop(
                f"{path.name} is an XFA form, which this tool can't stamp. "
                "Print it to PDF first, then stamp the printed copy.")
            return
        self.show_dialog(path, info, password)

    def _on_inspect_failed(self, req: int, failure: EngineFailure) -> None:
        if self._opening is None or self._opening[0] != req:
            return
        _, path, _ = self._opening
        self._opening = None
        if isinstance(failure.error, (PasswordRequired, WrongPassword)):
            problem = "" if isinstance(failure.error, PasswordRequired) else failure.message
            pw = self.services.ask_password(self, path.name, problem, False)
            if pw is None:
                self.show_drop(f"{path.name} wasn't opened: it needs a password.")
                return
            self._opening = (self.engine.inspect(path, pw), path, pw)
            return
        self.show_drop(failure.message)

    def show_dialog(self, path: Path, info: DocumentInfo, password: str | None) -> StampDialog:
        """Show the stamp dialog for a file that has been inspected."""
        assert self.config is not None and self.profile is not None
        self._close_dialog()
        self.drop.show_message("")
        dialog = StampDialog(
            config=self.config, profile=self.profile, path=path, info=info,
            password=password, engine=self.engine, services=self.services, today=self.today,
        )
        dialog.cancelled.connect(lambda: self.show_drop())
        dialog.saved.connect(self._on_saved)
        self.dialog = dialog
        self.stack.addWidget(dialog)
        self.stack.setCurrentWidget(dialog)
        dialog.focus_first()
        return dialog

    # ------------------------------------------------------------ saved

    def _on_saved(self, result: ApplyResult) -> None:
        dialog = self.dialog
        assert dialog is not None and self.config is not None
        ident = dialog.values().get("identifier", "")
        label = self.config.fields["identifier"].label if "identifier" in self.config.fields else "Identifier"
        password = dialog.password if result.encryption_kept else None
        signed = dialog.info.signed
        first = dialog.info.pages[0]
        ratio = self.devicePixelRatioF() or 1.0
        thumb_dpi = int(min(THUMB_W / (first.width / 72), THUMB_H / (first.height / 72)) * ratio * 1.5)
        self._close_dialog()
        self.saved.show_result(result, label=label, identifier=ident, signed=signed)
        self.stack.setCurrentWidget(self.saved)
        self._thumb_req = self.engine.preview(
            result.output_path, 1, StampJob(stamps=(), values={}), password, max(24, thumb_dpi))

    def _on_thumbnail(self, req: int, png: bytes) -> None:
        if req == self._thumb_req and self.stack.currentWidget() is self.saved:
            self.saved.show_thumbnail(png)

    def _open_saved(self) -> None:
        if self.saved.result is not None:
            self.services.open_file(self.saved.result.output_path)

    def _show_saved(self) -> None:
        if self.saved.result is not None:
            self.services.show_in_folder(self.saved.result.output_path)

    # ------------------------------------------------------------ drag and drop

    @staticmethod
    def _dropped_paths(event: QDragEnterEvent | QDropEvent) -> list[Path]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        return [Path(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt name
        """Accept files dragged over the drop and saved screens."""
        if self.stack.currentWidget() in (self.drop, self.saved) and self._dropped_paths(event):
            event.acceptProposedAction()
            self.drop.set_dragging(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event: object) -> None:  # noqa: N802 - Qt name
        """Stop highlighting the drop target."""
        self.drop.set_dragging(False)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt name
        """Open the dropped file (one at a time)."""
        self.drop.set_dragging(False)
        paths = self._dropped_paths(event)
        if not paths or self.stack.currentWidget() not in (self.drop, self.saved):
            event.ignore()
            return
        event.acceptProposedAction()
        self.open_files(paths)

    # ------------------------------------------------------------ closing

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt name
        """Cancel any save and wait for the engine, so no temporary file is left (D-055)."""
        self._close_dialog()
        self._close_settings()
        self.engine.shutdown()
        super().closeEvent(event)
