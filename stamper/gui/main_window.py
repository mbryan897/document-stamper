"""The main window: drop screen, stamp dialog and saved screen.

One window, screens in a stack. The drop screen takes PDFs (drag and drop
or "Choose files…") and shows the active profile, with a way to switch
profiles or open another config file for testing. One file opens its stamp
dialog; problems opening it (not a PDF, missing, XFA, no password) are plain
messages on the drop screen, which stays put. Several files open the files
screen, where the user orders them and picks Combine or Batch (Phase 4.5,
``multi.py``).

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
    QKeySequence,
    QPixmap,
    QScreen,
    QShortcut,
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
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import pending
from ..core.config import Config, ConfigError, Profile, load_config
from ..core.filenames import visible
from ..core.locations import (
    ConfigSource,
    Locations,
    SourceKind,
    describe,
    find_config,
    load_state,
    locations,
    save_state,
    work_dir,
)
from ..core.pending import Leftover
from ..handlers.base import (
    ApplyResult,
    Cancelled,
    DocumentInfo,
    Handler,
    PasswordRequired,
    StampJob,
    WrongPassword,
)
from ..handlers.combine import CombinedFile, remove_stale_temp
from ..handlers.pdf import PdfHandler
from .engine import EngineClient, EngineFailure, TaskContext
from .fonts import MONO_FAMILY
from .help import HELP_SHORTCUT, HelpWindow, help_button
from .multi import (
    BATCH,
    COMBINE,
    LARGE_FILE_COUNT,
    LARGE_PAGE_COUNT,
    BatchDialog,
    BatchSavedScreen,
    BusyScreen,
    CombineDialog,
    FilesScreen,
    PrepareScreen,
    ScreenedFile,
    ScreenResult,
    combine_task,
    needs_prepare,
    screen_files,
)
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
        il.setContentsMargins(24, 12, 24, 12)  # the stretches pad a tall target; a short one still fits
        il.setSpacing(14)
        il.addStretch(1)
        hero = QLabel("Drop PDFs to stamp")
        hero.setObjectName("hero")
        hero.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lead = QLabel("One file, or several to combine or stamp separately. "
                      "You'll confirm the label and placement before anything is saved.")
        lead.setObjectName("lead")
        lead.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lead.setWordWrap(True)
        self.choose_button = QPushButton("Choose files…")
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

        # Partial copies left by a run that stopped (D-124): offered for deletion, never deleted silently.
        self.leftover_frame = QFrame()
        self.leftover_frame.setObjectName("warning")
        self.leftover_frame.setMinimumWidth(560)
        self.leftover_frame.setMaximumWidth(720)
        lf = QVBoxLayout(self.leftover_frame)
        lf.setContentsMargins(14, 12, 14, 12)
        lf.setSpacing(8)
        self.leftover_label = QLabel()
        self.leftover_label.setWordWrap(True)
        self.leftover_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # Several leftovers scroll inside the notice, so the drop target keeps its room.
        self.leftover_scroll = QScrollArea()
        self.leftover_scroll.setWidget(self.leftover_label)
        self.leftover_scroll.setWidgetResizable(True)
        self.leftover_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.leftover_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.leftover_scroll.setMaximumHeight(84)
        self.leftover_scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget {background: transparent;}")
        lf.addWidget(self.leftover_scroll)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.leftover_delete = QPushButton("Delete")
        self.leftover_show = QPushButton("Show in folder")
        self.leftover_keep = QPushButton("Keep")
        for b in (self.leftover_delete, self.leftover_show, self.leftover_keep):
            b.setObjectName("outline")
            buttons.addWidget(b)
        buttons.addStretch(1)
        lf.addLayout(buttons)
        self.leftover_frame.hide()
        outer.addWidget(self.leftover_frame, 0, Qt.AlignmentFlag.AlignHCenter)

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
        self.help_button = help_button()
        row.addWidget(with_label)
        row.addWidget(self.profile_combo)
        row.addWidget(self.settings_button)
        row.addWidget(self.config_button)
        row.addWidget(self.help_button)
        row.addStretch(1)
        outer.addLayout(row)
        self.config_label = QLabel("")
        self.config_label.setObjectName("muted")
        self.config_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.config_label)
        outer.addStretch(1)

    def help_topic(self) -> str:
        """The help topic for this screen (F1 and the Help button)."""
        if not self.leftover_frame.isHidden():
            return "messages#partial-copies-left-behind"
        return "opening-files"

    def show_message(self, text: str, *, warning: bool = False) -> None:
        """A message under the drop target, plain or amber; empty hides it."""
        self.message.setText(text)
        name = "warning" if warning and text else "notice"
        if self.message_frame.objectName() != name:
            self.message_frame.setObjectName(name)
            self.message_frame.style().unpolish(self.message_frame)
            self.message_frame.style().polish(self.message_frame)
        self.message_frame.setVisible(bool(text))

    def show_leftovers(self, text: str, count: int) -> None:
        """The partial-copy notice (D-124); ``count`` 0 hides it."""
        self.leftover_label.setText(text)
        self.leftover_delete.setText("Delete" if count == 1 else "Delete all")
        self.leftover_keep.setText("Keep" if count == 1 else "Keep all")
        self.leftover_show.setVisible(count == 1)
        self.leftover_frame.setVisible(count > 0)

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
        self.help_button = help_button()
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.folder_button)
        buttons.addWidget(self.another_button)
        buttons.addStretch(1)
        buttons.addWidget(self.help_button)
        right.addLayout(buttons)
        cl.addLayout(right, 1)
        outer.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)
        self.result: ApplyResult | None = None

    def help_topic(self) -> str:
        """The help topic for this screen."""
        return "saving#the-saved-screen"

    def show_result(self, result: ApplyResult, *, label: str, identifier: str, signed: bool,
                    extra_notes: Sequence[str] = (), original_text: str | None = None) -> None:
        """Fill the screen from an ApplyResult.

        ``extra_notes`` come first (a combined file's protection warning and
        bookmarks note, D-111); ``original_text`` replaces the "Original" line.
        """
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
        self.original_label.setText(original_text or (
            "Unchanged, still digitally signed" if signed else "Unchanged"))
        # Grouped like the dialog's notes (D-066), and capped: one line per shrunk page
        # on a long receipt bundle would push the buttons off the window (D-093).
        notes = group_warnings([*extra_notes, *result.warnings])
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
        self.prepare: PrepareScreen | None = None
        self._combined: CombinedFile | None = None
        self.help: HelpWindow | None = None
        self._task: tuple[str, int] | None = None  # ("screen" | "add" | "combine", request)
        self._mode = ""
        self._opening: tuple[int, Path, str | None] | None = None
        self._thumb_req = 0
        self._last_folder: Path | None = (
            Path(self.state.last_folder) if self.state.last_folder and Path(self.state.last_folder).is_dir()
            else None)

        self.stack = QStackedWidget()
        self.drop = DropScreen()
        self.saved = SavedScreen()
        self.files_screen = FilesScreen()
        self.busy = BusyScreen()
        self.batch_saved = BatchSavedScreen(self.services)
        self.stack.addWidget(self.drop)
        self.stack.addWidget(self.saved)
        self.stack.addWidget(self.files_screen)
        self.stack.addWidget(self.busy)
        self.stack.addWidget(self.batch_saved)
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
        self.engine.task_done.connect(self._on_task_done)
        self.engine.task_failed.connect(self._on_task_failed)
        self.engine.task_progress.connect(self._on_task_progress)
        self.files_screen.back.connect(lambda: self.show_drop())
        self.files_screen.add_requested.connect(self._add_files)
        self.files_screen.single_chosen.connect(self.open_file)
        self.files_screen.combine_chosen.connect(lambda: self.choose_mode(COMBINE))
        self.files_screen.batch_chosen.connect(lambda: self.choose_mode(BATCH))
        self.busy.cancel.connect(self.engine.cancel)
        self.batch_saved.another_button.clicked.connect(lambda: self.show_drop())

        for screen in (self.drop, self.saved, self.files_screen, self.busy, self.batch_saved):
            self._wire_help(screen)
        QShortcut(QKeySequence(HELP_SHORTCUT), self, self.help_for_screen)

        self.leftovers: list[Leftover] = []
        self.drop.leftover_delete.clicked.connect(self.delete_leftovers)
        self.drop.leftover_keep.clicked.connect(self.keep_leftovers)
        self.drop.leftover_show.clicked.connect(self._show_leftover)

        source = describe(Path(config_path), SourceKind.OTHER) if config_path else find_config(self.locs)
        loaded = self.load_config(source.path, source)
        # Keep the reason on screen when the config can't be used.
        self.show_drop("" if loaded else self.drop.message.text())
        remove_stale_temp(work_dir())  # combine folders left by a stopped run (D-120)
        if loaded:
            self._note_config_change()
        self.check_leftovers()

    def _note_config_change(self) -> None:
        """Say so when the config found at start-up isn't the one found last time (D-121)."""
        if self.source is None or self.source.kind is SourceKind.OTHER or self.config_path is None:
            return
        now, before = str(self.config_path), self.state.last_config
        if before and before != now:
            self.drop.show_message(
                "This is a different config from last time. "
                f"Now: {visible(now)} ({self.source.kind.description}). Before: {visible(before)}. "
                "Check the profile and fields before you stamp.", warning=True)
        if before != now:
            self.state.last_config = now
            save_state(self.state, self.locs)

    # ------------------------------------------------------------ help (D-134)

    def _wire_help(self, screen: QWidget) -> None:
        """Connect a screen's Help button (and any help links inside it) to the Help window."""
        button = getattr(screen, "help_button", None)
        if isinstance(button, QPushButton):
            button.clicked.connect(lambda _c=False, s=screen: self.show_help(s.help_topic()))
        requested = getattr(screen, "help_requested", None)
        if requested is not None:
            requested.connect(self.show_help)

    def help_topic(self) -> str:
        """The help topic for the screen showing now."""
        screen = self.stack.currentWidget()
        topic = getattr(screen, "help_topic", None)
        return topic() if callable(topic) else ""

    def help_for_screen(self) -> HelpWindow:
        """F1: help for the screen showing now."""
        return self.show_help(self.help_topic())

    def show_help(self, link: str = "") -> HelpWindow:
        """Open the Help window at a topic (one window, reused)."""
        if self.help is None:
            self.help = HelpWindow()
        self.help.show_topic(link)
        return self.help

    # ------------------------------------------------------------ partial copies (D-124)

    def check_leftovers(self, problem: str = "") -> None:
        """Look for partial copies a stopped run left behind and offer to delete them."""
        self.leftovers = pending.leftovers()
        self.drop.show_leftovers(pending.describe(self.leftovers, problem), len(self.leftovers))

    def delete_leftovers(self) -> None:
        """Delete the listed partial copies (each re-checked first)."""
        problems: list[str] = []
        for lo in self.leftovers:
            try:
                pending.delete(lo)
            except OSError as e:
                problems.append(str(e))
        self.check_leftovers(" ".join(problems))
        if problems and not self.leftovers:
            self.drop.show_message(" ".join(problems))

    def keep_leftovers(self) -> None:
        """Leave the partial copies where they are and stop asking."""
        for lo in self.leftovers:
            pending.keep(lo)
        self.check_leftovers()

    def _show_leftover(self) -> None:
        if self.leftovers:
            self.services.show_in_folder(self.leftovers[0].path)

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
            self.drop.show_message(f"{visible(path.name)} can't be used:\n{lines}{tail}")
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
        self._close_prepare()
        self._drop_combined()
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
        self._wire_help(settings)
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
        """"Choose files…": the native dialog, PDFs only; one or several."""
        if self.services.ask_open_files is not None:
            paths = self.services.ask_open_files(self, self._last_folder)
            if paths:
                self.open_files(paths)
            return
        path = self.services.ask_open_file(self, self._last_folder)
        if path is not None:
            self.open_file(path)

    def open_files(self, paths: Sequence[Path]) -> None:
        """Open what was dropped or passed on the command line: one file, or several (D-100)."""
        if len(paths) > 1:
            self.open_several(paths)
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
            self.show_drop(f"{visible(path.name)} isn't a PDF file. Choose a PDF to stamp.")
            return
        self._last_folder = path.parent
        self.state.last_folder = str(path.parent)
        save_state(self.state, self.locs)
        self.show_drop(f"Opening {visible(path.name)}…")
        self._opening = (self.engine.inspect(path), path, None)

    def _on_inspected(self, req: int, info: DocumentInfo) -> None:
        if self._opening is None or self._opening[0] != req:
            return
        _, path, password = self._opening
        self._opening = None
        if info.xfa:
            self.show_drop(
                f"{visible(path.name)} is an XFA form, which this tool can't stamp. "
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
            pw = self.services.ask_password(self, visible(path.name), problem, False)
            if pw is None:
                self.show_drop(f"{visible(path.name)} wasn't opened: it needs a password.")
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
        self._wire_help(dialog)
        self.dialog = dialog
        self.stack.addWidget(dialog)
        self.stack.setCurrentWidget(dialog)
        dialog.focus_first()
        return dialog

    # ------------------------------------------------------------ several files (Phase 4.5)

    def open_several(self, paths: Sequence[Path]) -> None:
        """Check several files on the engine thread, then show the files screen."""
        if self.config is None or self.profile is None:
            self.show_drop("Open a config file that works before choosing PDFs.")
            return
        paths = [Path(p) for p in paths]
        self.show_drop()
        folder = next((p.parent for p in paths if p.is_file()), None)
        if folder is not None:
            self._last_folder = folder
            self.state.last_folder = str(folder)
            save_state(self.state, self.locs)
        self.busy.start(f"Checking {len(paths)} files…")
        self.stack.setCurrentWidget(self.busy)
        handler = self.handler
        self._task = ("screen", self.engine.run_task(lambda ctx: screen_files(handler, paths, ctx=ctx)))

    def _add_files(self) -> None:
        paths: list[Path] | None
        if self.services.ask_open_files is not None:
            paths = self.services.ask_open_files(self, self._last_folder)
        else:
            one = self.services.ask_open_file(self, self._last_folder)
            paths = [one] if one is not None else None
        if not paths:
            return
        already = self.files_screen.paths()
        handler = self.handler
        self.busy.start(f"Checking {len(paths)} more file{'s' if len(paths) != 1 else ''}…")
        self.stack.setCurrentWidget(self.busy)
        self._task = ("add", self.engine.run_task(
            lambda ctx: screen_files(handler, paths, already=already, ctx=ctx)))

    def choose_mode(self, mode: str) -> None:
        """Combine or Batch was clicked: ask what's needed up front, then go on (D-109)."""
        files = self.files_screen.files()
        if len(files) < 2:
            return
        pages = sum(f.page_count for f in files)
        if len(files) > LARGE_FILE_COUNT or pages > LARGE_PAGE_COUNT:
            if not self.services.confirm(
                    self, "A large set of files",
                    f"{len(files)} files, {pages} pages in all. This may take a few minutes.",
                    "Go ahead"):
                return
        self._mode = mode
        if needs_prepare(files, mode):
            self._close_prepare()
            prepare = PrepareScreen(files, mode, self.engine)
            prepare.back.connect(self._back_to_files)
            prepare.proceed.connect(self._prepared)
            self._wire_help(prepare)
            self.prepare = prepare
            self.stack.addWidget(prepare)
            self.stack.setCurrentWidget(prepare)
            return
        self._proceed(files, accept_signature=False, accept_restrictions=False)

    def _prepared(self) -> None:
        assert self.prepare is not None
        files = self.prepare.files()
        for f in files:
            self.files_screen.update_file(f)
        sig, res = self.prepare.accepted()
        self._proceed(files, accept_signature=sig, accept_restrictions=res)

    def _proceed(self, files: list[ScreenedFile], *, accept_signature: bool, accept_restrictions: bool) -> None:
        if self._mode == BATCH:
            self._show_batch(files, accept_signature, accept_restrictions)
            return
        self.busy.start(f"Combining {len(files)} files…", "Pages go in the order shown.")
        self.stack.setCurrentWidget(self.busy)
        handler = self.handler

        def run(ctx: TaskContext) -> object:
            return combine_task(files, accept_signature_loss=accept_signature,
                                accept_restrictions=accept_restrictions, ctx=ctx, handler=handler)

        self._task = ("combine", self.engine.run_task(run))

    def _back_to_files(self, message: str = "") -> None:
        self._close_dialog()
        self._close_prepare()
        self._drop_combined()
        self.stack.setCurrentWidget(self.files_screen)
        self.files_screen.show_message(message)

    def _on_task_progress(self, req: int, done: int, total: int, text: str) -> None:
        if self._task is not None and self._task[1] == req:
            self.busy.report(done, total, text)

    def _on_task_done(self, req: int, result: object) -> None:
        if self._task is None or self._task[1] != req:
            return
        kind, _ = self._task
        self._task = None
        if kind in ("screen", "add"):
            assert isinstance(result, ScreenResult)
            self.files_screen.set_files(result, append=kind == "add")
            if kind == "screen" and not result.usable:
                lines = "\n".join(f"• {n} {r}." for n, r in result.removed)
                self.show_drop(f"None of the files can be stamped:\n{lines}")
                return
            self.stack.setCurrentWidget(self.files_screen)
            self.files_screen.list.setFocus()
        elif kind == "combine":
            combined, info = result  # type: ignore[misc]
            self._combined = combined
            self._show_combine(combined, info)

    def _on_task_failed(self, req: int, failure: EngineFailure) -> None:
        if self._task is None or self._task[1] != req:
            return
        kind, _ = self._task
        self._task = None
        if kind == "screen":
            self.show_drop("Checking the files was cancelled." if isinstance(failure.error, Cancelled)
                           else failure.message)
        elif kind == "add":
            self.stack.setCurrentWidget(self.files_screen)
        else:
            self._back_to_files("Combining was cancelled. Nothing was saved." if isinstance(failure.error, Cancelled)
                                else failure.message)

    def _show_combine(self, combined: CombinedFile, info: DocumentInfo) -> CombineDialog:
        assert self.config is not None and self.profile is not None
        self._close_dialog()
        self._close_prepare()
        dialog = CombineDialog(config=self.config, profile=self.profile, combined=combined, info=info,
                               engine=self.engine, services=self.services, today=self.today)
        dialog.cancelled.connect(lambda: self._back_to_files())
        dialog.saved.connect(self._on_saved)
        self._wire_help(dialog)
        self.dialog = dialog
        self.stack.addWidget(dialog)
        self.stack.setCurrentWidget(dialog)
        dialog.focus_first()
        return dialog

    def _show_batch(self, files: list[ScreenedFile], accept_signature: bool, accept_restrictions: bool) -> BatchDialog:
        assert self.config is not None and self.profile is not None
        self._close_dialog()
        self._close_prepare()
        dialog = BatchDialog(config=self.config, profile=self.profile, files=files,
                             accept_signature_loss=accept_signature, accept_restrictions=accept_restrictions,
                             engine=self.engine, handler=self.handler, services=self.services, today=self.today)
        dialog.cancelled.connect(lambda: self._back_to_files())
        dialog.batch_saved.connect(self._on_batch_saved)
        self._wire_help(dialog)
        self.dialog = dialog
        self.stack.addWidget(dialog)
        self.stack.setCurrentWidget(dialog)
        dialog.focus_first()
        return dialog

    def _on_batch_saved(self, outcomes: list[object]) -> None:
        self._close_dialog()
        self.batch_saved.show_outcomes(outcomes)  # type: ignore[arg-type]
        self.stack.setCurrentWidget(self.batch_saved)

    def _close_prepare(self) -> None:
        if self.prepare is not None:
            self.prepare.dispose()
            self.stack.removeWidget(self.prepare)
            self.prepare.deleteLater()
            self.prepare = None

    def _drop_combined(self) -> None:
        """Remove the temporary combined file, after whatever the engine is doing with it (D-055)."""
        combined, self._combined = self._combined, None
        if combined is not None:
            self.engine.run_task(lambda _ctx: combined.cleanup(), clear_cancel=False)

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
        extra: list[str] = []
        original: str | None = None
        if isinstance(dialog, CombineDialog):
            extra = dialog.saved_notes()
            n = len(dialog.originals)
            original = (f"All {n} files unchanged" + (", signed files still signed" if dialog.combined.signed else ""))
        self._close_dialog()
        self._drop_combined()
        self.saved.show_result(result, label=label, identifier=ident, signed=signed,
                               extra_notes=extra, original_text=original)
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
        if self.stack.currentWidget() in (self.drop, self.saved, self.batch_saved) and self._dropped_paths(event):
            event.acceptProposedAction()
            self.drop.set_dragging(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event: object) -> None:  # noqa: N802 - Qt name
        """Stop highlighting the drop target."""
        self.drop.set_dragging(False)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt name
        """Open the dropped file, or the files screen for several."""
        self.drop.set_dragging(False)
        paths = self._dropped_paths(event)
        if not paths or self.stack.currentWidget() not in (self.drop, self.saved, self.batch_saved):
            event.ignore()
            return
        event.acceptProposedAction()
        self.open_files(paths)

    # ------------------------------------------------------------ closing

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt name
        """Cancel any save and wait for the engine, so no temporary file is left (D-055)."""
        self._close_dialog()
        self._close_settings()
        self._close_prepare()
        combined, self._combined = self._combined, None
        if self.help is not None:
            self.help.close()  # it's a window of its own; it mustn't keep the app running
            self.help.deleteLater()
            self.help = None
        self.engine.shutdown()
        if combined is not None:
            combined.cleanup()  # the engine has stopped, so nothing holds the file open
        super().closeEvent(event)
