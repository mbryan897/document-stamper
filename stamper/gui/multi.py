"""Several files at once (Phase 4.5, D-095 to D-112): the window's multi-file screens.

The flow, all in the main window's stack:

1. ``FilesScreen`` — the files in order (drag to reorder, Move up / Move
   down, Remove, Add files…), files that can't be used listed with the
   reason, and the two choices: "Combine into one file" or "Stamp each file
   separately". No default (D-100).
2. ``PrepareScreen`` — only when needed: every password, one signature tick
   listing every signed file and one restrictions tick listing every
   restricted file, all before anything is saved (D-109). For Combine it
   also shows the D-096 warning.
3. ``BusyScreen`` — "Checking files…" and "Combining…", with progress and
   Cancel.
4. ``CombineDialog`` / ``BatchDialog`` — the stamp dialog, reused: one set
   of values and one placement (D-097), with the combined file's pages
   labelled by source (D-111) or a file list for a batch.
5. ``BatchSavedScreen`` — one row per file: saved, skipped or failed.

Every PyMuPDF call still runs on the engine thread (D-070): screening,
combining and the batch run there as tasks.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.config import Config, Profile
from ..core.filenames import visible
from ..core.multi import FilenameAgreement, agree_from_filenames, combined_original_name, disagreement_note
from ..core.template import TemplateError, template_tokens
from ..handlers.base import (
    Cancelled,
    DocumentInfo,
    Handler,
    MissingGlyph,
    PasswordRequired,
    StampError,
    UnsupportedDocument,
    WrongPassword,
)
from ..handlers.batch import BatchCheck, BatchItem, BatchOutcome, Status, check_batch, run_batch
from ..handlers.combine import CombinedFile, CombineInput, combine_pdfs
from ..handlers.pdf import same_file
from .engine import EngineClient, EngineFailure, TaskContext
from .help import help_button
from .services import Services
from .stamp_dialog import StampDialog, _notice, _wrap_label

__all__ = [
    "LARGE_FILE_COUNT",
    "LARGE_PAGE_COUNT",
    "BatchDialog",
    "BatchSavedScreen",
    "BusyScreen",
    "CombineDialog",
    "FilesScreen",
    "PrepareScreen",
    "ScreenResult",
    "ScreenedFile",
    "combine_task",
    "needs_prepare",
    "screen_files",
]

BATCH_LIST_ROWS = 3  # files shown above the batch preview before the list scrolls
LARGE_FILE_COUNT = 50  # soft warning above this many files (D-110)
LARGE_PAGE_COUNT = 2000  # ... or this many pages in total
COMBINE = "combine"
BATCH = "batch"


# ------------------------------------------------------------------ screening (engine thread)


@dataclass(frozen=True, slots=True)
class ScreenedFile:
    """A usable file: its info once opened (None while it still needs a password)."""

    path: Path
    info: DocumentInfo | None
    password: str | None = None

    @property
    def needs_password(self) -> bool:
        """True until a password that opens it has been given."""
        return self.info is None

    @property
    def page_count(self) -> int:
        """Pages, or 0 while it can't be opened yet."""
        return self.info.page_count if self.info else 0


@dataclass(frozen=True, slots=True)
class ScreenResult:
    """Files that can be used, in the order given, and ``(name, reason)`` for the rest."""

    usable: tuple[ScreenedFile, ...]
    removed: tuple[tuple[str, str], ...] = ()


def screen_files(handler: Handler, paths: Sequence[Path], *, already: Sequence[Path] = (),
                 ctx: TaskContext | None = None) -> ScreenResult:
    """Open each file once and sort it into usable or removed, with a reason (D-100).

    Not a PDF, missing, damaged beyond repair, XFA, or listed twice: removed.
    A file that needs a password stays, marked as needing it. Runs on the
    engine thread.
    """
    usable: list[ScreenedFile] = []
    removed: list[tuple[str, str]] = []
    seen: list[Path] = list(already)
    for i, raw in enumerate(paths):
        if ctx is not None:
            if ctx.cancelled():
                raise Cancelled("Checking the files was cancelled.")
            ctx.progress(i, len(paths), Path(raw).name)
        path = Path(raw)
        if not path.is_file():
            removed.append((path.name, "can't be found"))
            continue
        if any(same_file(path, p) for p in seen):
            removed.append((path.name, "is listed twice"))
            continue
        if not handler.can_open(path):
            removed.append((path.name, "isn't a PDF file"))
            continue
        try:
            info = handler.inspect(path)
        except (PasswordRequired, WrongPassword):
            usable.append(ScreenedFile(path, None))
            seen.append(path)
            continue
        except UnsupportedDocument as e:
            removed.append((path.name, _reason(str(e), path.name)))
            continue
        except StampError as e:
            removed.append((path.name, _reason(str(e), path.name)))
            continue
        if info.xfa:
            removed.append((path.name, "is an XFA form (print it to PDF first)"))
            continue
        usable.append(ScreenedFile(path, info))
        seen.append(path)
    if ctx is not None:
        ctx.progress(len(paths), len(paths), "")
    return ScreenResult(tuple(usable), tuple(removed))


def _reason(message: str, name: str) -> str:
    """An engine sentence about ``name`` shortened to the part after the name."""
    text = message.removeprefix(name).strip()
    return text.rstrip(".") or message


def needs_prepare(files: Sequence[ScreenedFile], mode: str) -> bool:
    """True when the Before stamping screen has something to ask or say (D-109)."""
    for f in files:
        if f.needs_password:
            return True
        assert f.info is not None
        if f.info.signed or f.info.restricted:
            return True
        if mode == COMBINE and f.info.encrypted:
            return True
    return False


def combine_task(files: Sequence[ScreenedFile], *, accept_signature_loss: bool,
                 accept_restrictions: bool, ctx: TaskContext, handler: Handler) -> tuple[CombinedFile, DocumentInfo]:
    """Combine on the engine thread, in the private work folder (D-115).

    If that can't be used, the working copy goes in the first file's folder and
    the dialog says why (``CombinedFile.location_warning``).
    """
    inputs = [CombineInput(f.path, f.password, f.info.fingerprint if f.info else None) for f in files]

    def progress(done: int, total: int, name: str) -> None:
        ctx.progress(done, total, name)

    combined = combine_pdfs(inputs, fallback=files[0].path.parent,
                            accept_signature_loss=accept_signature_loss,
                            accept_restrictions=accept_restrictions,
                            progress=progress, cancel=ctx.cancelled)
    try:
        info = handler.inspect(combined.path)
    except Exception:
        combined.cleanup()
        raise
    return combined, info


# ------------------------------------------------------------------ small widgets


def _title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("h1")
    return label


def _card() -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("savedCard")
    card.setMaximumWidth(920)
    card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(28, 18, 28, 18)
    lay.setSpacing(12)
    return card, lay


def _centre(outer: QVBoxLayout, card: QFrame) -> None:
    """Put a card in the middle of a wide window; it fills a narrow one."""
    row = QHBoxLayout()
    row.addStretch(0)
    row.addWidget(card, 1)
    row.addStretch(0)
    outer.addLayout(row, 1)


def _page_word(n: int) -> str:
    return "1 page" if n == 1 else f"{n} pages"


def _names(names: Sequence[str]) -> str:
    return ", ".join(names)


# ------------------------------------------------------------------ the files screen


class FilesScreen(QWidget):
    """The ordered file list and the choice between Combine and Batch."""

    combine_chosen = Signal()
    batch_chosen = Signal()
    single_chosen = Signal(object)  # Path: only one usable file was left
    add_requested = Signal()
    back = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ground")
        self._files: dict[str, ScreenedFile] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        card, lay = _card()

        head = QHBoxLayout()
        self.title = _title("Several files")
        head.addWidget(self.title, 1)
        self.back_button = QPushButton("Back")
        self.back_button.setObjectName("link")
        self.back_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_button.clicked.connect(self.back.emit)
        self.help_button = help_button()
        head.addWidget(self.help_button, 0, Qt.AlignmentFlag.AlignTop)
        head.addWidget(self.back_button, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)
        self.lead = _wrap_label("Put the files in order, then choose what to do with them.", "lead")
        lay.addWidget(self.lead)

        self.message_frame, ml = _notice("notice")
        ml.setContentsMargins(14, 8, 6, 8)
        self.message = _wrap_label("")
        holder = QScrollArea()  # a long "Left out" list scrolls instead of pushing the buttons off (D-093)
        holder.setWidgetResizable(True)
        holder.setFrameShape(QFrame.Shape.NoFrame)
        holder.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        holder.setWidget(self.message)
        holder.setMaximumHeight(72)
        holder.setMinimumHeight(24)
        self.message_holder = holder
        ml.addWidget(holder)
        self.message_frame.hide()
        lay.addWidget(self.message_frame)

        mid = QHBoxLayout()
        mid.setSpacing(12)
        self.list = QListWidget()
        self.list.setAccessibleName("Files, in order")
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setMinimumHeight(96)
        self.list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.list.model().rowsMoved.connect(lambda *_a: self._renumber())
        self.list.currentRowChanged.connect(lambda _r: self._update_buttons())
        mid.addWidget(self.list, 1)
        side = QVBoxLayout()
        side.setSpacing(8)
        self.up_button = QPushButton("Move up")
        self.up_button.clicked.connect(lambda: self.move(-1))
        self.down_button = QPushButton("Move down")
        self.down_button.clicked.connect(lambda: self.move(1))
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_current)
        self.add_button = QPushButton("Add files…")
        self.add_button.clicked.connect(self.add_requested.emit)
        for b in (self.up_button, self.down_button, self.remove_button, self.add_button):
            b.setObjectName("small")
            side.addWidget(b)
        side.addStretch(1)
        mid.addLayout(side)
        lay.addLayout(mid, 1)
        self.order_help = _wrap_label("Drag a file, or use Move up and Move down. For Combine, pages follow "
                                      "this order.", "help")
        lay.addWidget(self.order_help)

        self.choices = QWidget()
        cl = QGridLayout(self.choices)
        cl.setContentsMargins(0, 4, 0, 0)
        cl.setHorizontalSpacing(16)
        cl.setVerticalSpacing(4)
        self.combine_button = QPushButton("Combine into one file")
        self.combine_button.setObjectName("primary")
        self.combine_button.clicked.connect(self.combine_chosen.emit)
        self.batch_button = QPushButton("Stamp each file separately")
        self.batch_button.setObjectName("primary")
        self.batch_button.clicked.connect(self.batch_chosen.emit)
        cl.addWidget(self.combine_button, 0, 0)
        cl.addWidget(self.batch_button, 0, 1)
        cl.addWidget(_wrap_label("One stamped PDF, pages numbered across all files.", "help"), 1, 0)
        cl.addWidget(_wrap_label("One stamped copy of each file, same stamp on each.", "help"), 1, 1)
        cl.setColumnStretch(0, 1)
        cl.setColumnStretch(1, 1)
        lay.addWidget(self.choices)

        self.single_button = QPushButton("")
        self.single_button.setObjectName("primary")
        self.single_button.clicked.connect(self._single)
        self.single_button.hide()
        lay.addWidget(self.single_button, 0, Qt.AlignmentFlag.AlignLeft)

        _centre(outer, card)

    def help_topic(self) -> str:
        """The help topic for this screen."""
        return "several-files"

    # ---------------------------------------------------------- contents

    def set_files(self, result: ScreenResult, *, append: bool = False) -> None:
        """Show these files (or add them to the list) and the reasons any were left out."""
        if not append:
            self.list.clear()
            self._files.clear()
        for f in result.usable:
            key = str(f.path)
            self._files[key] = f
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.list.addItem(item)
        self._renumber()
        if result.removed:
            lines = "\n".join(f"• {name} {reason}." for name, reason in result.removed)
            self.show_message(f"Left out:\n{lines}")
        elif not append:
            self.show_message("")
        if self.list.count() and self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        self._update_buttons()

    def update_file(self, screened: ScreenedFile) -> None:
        """Replace a file's details (after its password was given)."""
        self._files[str(screened.path)] = screened
        self._renumber()

    def files(self) -> list[ScreenedFile]:
        """The usable files in the order shown."""
        out = []
        for i in range(self.list.count()):
            key = self.list.item(i).data(Qt.ItemDataRole.UserRole)
            out.append(self._files[key])
        return out

    def paths(self) -> list[Path]:
        """The files' paths in the order shown."""
        return [f.path for f in self.files()]

    def show_message(self, text: str) -> None:
        """A note above the list (files left out, a cancelled combine); empty hides it."""
        self.message.setText(text)
        self.message_frame.setVisible(bool(text))
        lines = text.count("\n") + 1 if text else 0
        self.message_holder.setFixedHeight(min(72, max(24, lines * 20 + 4)))

    def _renumber(self) -> None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            f = self._files[item.data(Qt.ItemDataRole.UserRole)]
            pages = "needs a password" if f.needs_password else _page_word(f.page_count)
            item.setText(f"{i + 1}.  {visible(f.path.name)}  —  {pages}")
            item.setToolTip(str(f.path))
        n = self.list.count()
        self.title.setText(f"{n} files" if n != 1 else "1 file")
        self._update_buttons()

    def _update_buttons(self) -> None:
        row, n = self.list.currentRow(), self.list.count()
        self.up_button.setEnabled(row > 0)
        self.down_button.setEnabled(0 <= row < n - 1)
        self.remove_button.setEnabled(row >= 0 and n > 0)
        several = n >= 2
        self.choices.setVisible(several)
        self.order_help.setVisible(several)
        self.single_button.setVisible(n == 1)
        if n == 1:
            self.single_button.setText(f"Stamp {visible(self.files()[0].path.name)} on its own")
        self.lead.setText("Put the files in order, then choose what to do with them." if several else
                          "Only one file is left. Add more files, or stamp this one on its own." if n == 1 else
                          "None of these files can be stamped. Go back and choose others.")

    # ---------------------------------------------------------- ordering

    def move(self, step: int) -> None:
        """Move the selected file up (-1) or down (+1)."""
        row = self.list.currentRow()
        new = row + step
        if row < 0 or not 0 <= new < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(new, item)
        self.list.setCurrentRow(new)
        self._renumber()

    def remove_current(self) -> None:
        """Take the selected file off the list."""
        row = self.list.currentRow()
        if row < 0:
            return
        item = self.list.takeItem(row)
        self._files.pop(item.data(Qt.ItemDataRole.UserRole), None)
        if self.list.count():
            self.list.setCurrentRow(min(row, self.list.count() - 1))
        self._renumber()

    def drag_row(self, row: int, to: int) -> None:
        """Move a row as a drag and drop would (``to`` is the drop position before removal)."""
        self.list.model().moveRows(QModelIndex(), row, 1, QModelIndex(), to)
        self._renumber()

    def _single(self) -> None:
        if self.list.count() == 1:
            self.single_chosen.emit(self.files()[0].path)


# ------------------------------------------------------------------ busy


class BusyScreen(QWidget):
    """A title, what is happening, a progress bar and Cancel."""

    cancel = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ground")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 32, 32, 32)
        outer.addStretch(1)
        card, lay = _card()
        card.setMaximumWidth(640)
        self.title = _title("")
        self.detail = _wrap_label("", "help")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel)
        lay.addWidget(self.title)
        lay.addWidget(self.detail)
        lay.addWidget(self.progress)
        lay.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignRight)
        outer.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)

    def help_topic(self) -> str:
        """The help topic for this screen (checking and combining several files)."""
        return "several-files"

    def start(self, title: str, detail: str = "") -> None:
        """Reset for a new piece of work."""
        self.title.setText(title)
        self.detail.setText(detail)
        self.progress.setRange(0, 0)  # busy until the first progress report
        self.cancel_button.setEnabled(True)
        self.cancel_button.setFocus()

    def report(self, done: int, total: int, text: str) -> None:
        """Show progress from the engine."""
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        if text:
            self.detail.setText(f"{text} ({min(done + 1, total)} of {total})")

    def _cancel(self) -> None:
        self.detail.setText("Cancelling…")
        self.cancel_button.setEnabled(False)
        self.cancel.emit()


# ------------------------------------------------------------------ before stamping


class _PasswordRow:
    def __init__(self, f: ScreenedFile, edit: QLineEdit, status: QLabel, button: QPushButton) -> None:
        self.file = f
        self.edit = edit
        self.status = status
        self.button = button
        self.req = 0


class PrepareScreen(QWidget):
    """Every password and confirmation for the set, collected before anything is saved (D-109)."""

    proceed = Signal()
    back = Signal()

    def __init__(self, files: Sequence[ScreenedFile], mode: str, engine: EngineClient,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ground")
        self.mode = mode
        self.engine = engine
        self._files: list[ScreenedFile] = list(files)
        self._rows: list[_PasswordRow] = []
        self.signature_tick: QCheckBox | None = None
        self.restriction_tick: QCheckBox | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        card, lay = _card()
        lay.addWidget(_title("Before stamping"))
        what = "combining" if mode == COMBINE else "stamping"
        lay.addWidget(_wrap_label(f"Check these before {what}. Nothing is saved until the stamp is confirmed.",
                                  "lead"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("surface")
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(0, 0, 8, 0)
        self.body_layout.setSpacing(12)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)

        self.status = _wrap_label("", "help")
        lay.addWidget(self.status)
        buttons = QHBoxLayout()
        self.help_button = help_button()
        buttons.addWidget(self.help_button)
        buttons.addStretch(1)
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self.back.emit)
        self.continue_button = QPushButton("Continue")
        self.continue_button.setObjectName("primary")
        self.continue_button.clicked.connect(self._continue)
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.continue_button)
        lay.addLayout(buttons)
        _centre(outer, card)

        engine.inspected.connect(self._on_inspected)
        engine.inspect_failed.connect(self._on_inspect_failed)
        self._build_body()

    def help_topic(self) -> str:
        """The help topic for this screen."""
        return "several-files#before-stamping"

    # ---------------------------------------------------------- body

    def _clear_body(self) -> None:
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _build_body(self) -> None:
        keep = {str(r.file.path): r.edit.text() for r in self._rows}
        self._clear_body()
        self._rows = []
        locked = [f for f in self._files if f.needs_password]
        if locked:
            frame, fl = _notice("info")
            fl.addWidget(_wrap_label("These files need a password to open:"))
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(6)
            for i, f in enumerate(locked):
                name = QLabel(visible(f.path.name))
                name.setWordWrap(True)
                edit = QLineEdit(keep.get(str(f.path), ""))
                edit.setEchoMode(QLineEdit.EchoMode.Password)
                edit.setAccessibleName(f"Password for {visible(f.path.name)}")
                button = QPushButton("Check")
                button.setObjectName("small")
                status = _wrap_label("", "msgNeutral")
                row = _PasswordRow(f, edit, status, button)
                edit.returnPressed.connect(lambda r=row: self.check_password(r))
                button.clicked.connect(lambda _c=False, r=row: self.check_password(r))
                grid.addWidget(name, i * 2, 0)
                grid.addWidget(edit, i * 2, 1)
                grid.addWidget(button, i * 2, 2)
                grid.addWidget(status, i * 2 + 1, 1, 1, 2)
                self._rows.append(row)
            grid.setColumnStretch(1, 1)
            fl.addLayout(grid)
            self.body_layout.addWidget(frame)

        opened = [f for f in self._files if f.info is not None]
        signed = [visible(f.path.name) for f in opened if f.info and f.info.signed]
        restricted = [visible(f.path.name) for f in opened if f.info and f.info.restricted]
        # Locked files count before their password is given, so the D-096 warning shows up front.
        protected = [visible(f.path.name) for f in self._files if f.info is None or f.info.encrypted]
        old_sig = bool(self.signature_tick and self.signature_tick.isChecked())
        old_res = bool(self.restriction_tick and self.restriction_tick.isChecked())
        self.signature_tick = None
        self.restriction_tick = None
        if signed:
            frame, fl = _notice("warning")
            result = "The combined file" if self.mode == COMBINE else "Their stamped copies"
            fl.addWidget(_wrap_label(
                f"{'This file is' if len(signed) == 1 else 'These files are'} digitally signed: "
                f"{_names(signed)}. {result} won't keep a valid signature; the originals stay signed "
                "and unchanged."))
            self.signature_tick = QCheckBox(
                "Combine without valid signatures" if self.mode == COMBINE else "Save copies without valid signatures")
            self.signature_tick.setChecked(old_sig)
            self.signature_tick.toggled.connect(lambda _c: self._update())
            fl.addWidget(self.signature_tick)
            self.body_layout.addWidget(frame)
        if restricted:
            frame, fl = _notice("warning")
            after = ("The combined file has no restrictions." if self.mode == COMBINE
                     else "Each copy keeps the same restrictions.")
            fl.addWidget(_wrap_label(
                f"Owner restrictions don't allow changes to {_names(restricted)}. {after}"))
            self.restriction_tick = QCheckBox("Stamp them anyway" if len(restricted) > 1 else "Stamp it anyway")
            self.restriction_tick.setChecked(old_res)
            self.restriction_tick.toggled.connect(lambda _c: self._update())
            fl.addWidget(self.restriction_tick)
            self.body_layout.addWidget(frame)
        if self.mode == COMBINE and protected:
            frame, fl = _notice("warning")
            fl.addWidget(_wrap_label(
                "The combined file is saved without a password or restrictions. "
                f"Protected files in it: {_names(protected)}."))
            self.body_layout.addWidget(frame)
        elif self.mode == BATCH and protected:
            frame, fl = _notice("info")
            fl.addWidget(_wrap_label("Each copy keeps its original's password and permissions."))
            self.body_layout.addWidget(frame)
        self.body_layout.addStretch(1)
        self._update()

    # ---------------------------------------------------------- passwords

    def check_password(self, row: _PasswordRow) -> None:
        """Try a typed password on the engine thread."""
        if not row.edit.text():
            row.status.setText("Type the password first.")
            return
        row.status.setText("Checking…")
        row.req = self.engine.inspect(row.file.path, row.edit.text())

    def _row_for(self, req: int) -> _PasswordRow | None:
        return next((r for r in self._rows if r.req == req and req), None)

    def _on_inspected(self, req: int, info: DocumentInfo) -> None:
        row = self._row_for(req)
        if row is None:
            return
        updated = replace(row.file, info=info, password=row.edit.text())
        self._files = [updated if f.path == row.file.path else f for f in self._files]
        self._build_body()  # the file may turn out to be signed or restricted
        done = next((r for r in self._rows if r.file.path == updated.path), None)
        if done is not None:
            done.status.setText("Opens the file.")

    def _on_inspect_failed(self, req: int, failure: EngineFailure) -> None:
        row = self._row_for(req)
        if row is None:
            return
        row.req = 0
        row.status.setText(failure.message)
        self._update()

    # ---------------------------------------------------------- state

    def files(self) -> list[ScreenedFile]:
        """The files with their passwords and details, in order."""
        return list(self._files)

    def password_rows(self) -> list[tuple[str, QLineEdit]]:
        """``(file name, password box)`` for each file that still needs a password (tests)."""
        return [(r.file.path.name, r.edit) for r in self._rows]

    def accepted(self) -> tuple[bool, bool]:
        """``(signature ticked, restrictions ticked)``; a missing tick counts as not needed."""
        return (bool(self.signature_tick and self.signature_tick.isChecked()),
                bool(self.restriction_tick and self.restriction_tick.isChecked()))

    def blockers(self) -> list[str]:
        """Why Continue is disabled, as sentences."""
        out: list[str] = []
        waiting = [visible(f.path.name) for f in self._files if f.needs_password]
        if waiting:
            out.append(f"Give the password for {_names(waiting)}.")
        if self.signature_tick is not None and not self.signature_tick.isChecked():
            out.append(f"Tick \"{self.signature_tick.text()}\" to continue.")
        if self.restriction_tick is not None and not self.restriction_tick.isChecked():
            out.append(f"Tick \"{self.restriction_tick.text()}\" to continue.")
        return out

    def _update(self) -> None:
        reasons = self.blockers()
        self.continue_button.setEnabled(not reasons)
        self.status.setText(" ".join(reasons))

    def _continue(self) -> None:
        if not self.blockers():
            self.proceed.emit()

    def dispose(self) -> None:
        """Stop listening to the engine."""
        for signal, slot in ((self.engine.inspected, self._on_inspected),
                             (self.engine.inspect_failed, self._on_inspect_failed)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass


# ------------------------------------------------------------------ shared by both dialogs


def _agreements(config: Config, profile: Profile, names: Sequence[str]) -> dict[str, FilenameAgreement]:
    return {f: agree_from_filenames(config, f, names) for f in profile.fields}


class _MultiMixin:
    """Pre-fill from several file names (D-106) for both multi-file dialogs."""

    _agree: dict[str, FilenameAgreement]

    def _prefill_from(self, names: Sequence[str]) -> None:
        self._agree = _agreements(self.config, self.profile, names)  # type: ignore[attr-defined]
        for name, agreement in self._agree.items():
            row = self._rows[name]  # type: ignore[attr-defined]
            if agreement.value is not None and isinstance(row.editor, QLineEdit):
                row.editor.setText(agreement.value)
                row.prefilled = agreement.value

    def _agreement_note(self, name: str) -> str:
        agreement = getattr(self, "_agree", {}).get(name)
        if agreement is None:
            return ""
        return disagreement_note(self.config.fields[name].label, agreement)  # type: ignore[attr-defined]

    def disagreement(self, name: str) -> str:
        """The amber note naming files whose names disagree for a field (empty if none)."""
        return self._agreement_note(name)


# ------------------------------------------------------------------ combine


class CombineDialog(_MultiMixin, StampDialog):
    """The stamp dialog on the temporary combined file (D-095, D-099, D-111)."""

    def __init__(
        self,
        *,
        config: Config,
        profile: Profile,
        combined: CombinedFile,
        info: DocumentInfo,
        engine: EngineClient,
        services: Services,
        today: date | None = None,
        parent: QWidget | None = None,
    ) -> None:
        self.combined = combined
        self.originals = tuple(s.path for s in combined.sources)
        super().__init__(config=config, profile=profile, path=combined.path, info=info, password=None,
                         engine=engine, services=services, today=today, parent=parent)
        self.save_button.setText("Stamp and save…")

    # hooks

    def _head_texts(self) -> tuple[str, str]:
        return (f"{len(self.combined.sources)} files", f"{_page_word(self.info.page_count)} in one file")

    def _title_text(self) -> str:
        return "Stamp the combined file"

    def help_topic(self) -> str:
        """The help topic for this screen."""
        return "several-files#stamping-the-combined-file"

    def _extend_head(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        jump = QLabel("Go to file")
        jump.setObjectName("muted")
        self.jump_combo = QComboBox()
        self.jump_combo.setAccessibleName("Go to file")
        self.jump_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.jump_combo.setMinimumContentsLength(14)
        jump.setBuddy(self.jump_combo)
        for i, src in enumerate(self.combined.sources):
            self.jump_combo.addItem(f"{i + 1}. {visible(src.path.name)}", src.first)
        self.jump_combo.activated.connect(self._jump)
        row.addWidget(jump)
        row.addWidget(self.jump_combo, 1)
        layout.addLayout(row)
        self.source_label = _wrap_label("", "muted")
        self.source_label.setAccessibleName("Source of this page")
        layout.addWidget(self.source_label)

    def _jump(self, index: int) -> None:
        self._go(int(self.jump_combo.itemData(index)) - self.page)

    def _update_page_label(self) -> None:
        super()._update_page_label()
        src, n = self.combined.source_for(self.page)
        self.source_label.setText(f"This page: {visible(src.path.name)}, page {n} of {src.page_count}")
        i = self.combined.sources.index(src)
        if self.jump_combo.currentIndex() != i:
            self.jump_combo.setCurrentIndex(i)

    def page_source_text(self) -> str:
        """The "file, page N of M" line under the page header (tests)."""
        return self.source_label.text().removeprefix("This page: ")

    def _build_confirmations(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.signature_tick = None
        self.restriction_tick = None
        self.owner_button = None
        for warning in (self.combined.location_warning(), self.combined.protection_warning()):
            if warning:
                frame, fl = _notice("warning")
                fl.addWidget(_wrap_label(warning))
                lay.addWidget(frame)
        lines = [f"Pages in this order: {', '.join(visible(s.path.name) for s in self.combined.sources)}."]
        if self.combined.signed:
            lines.append(f"Signed files ({_names(self.combined.signed)}) were confirmed; the originals stay signed.")
        if self._uses_original_name():
            lines.append(f"In the stamp, the original name is the first file's: {self.originals[0].stem}.")
        frame, fl = _notice("info")
        for line in lines:
            fl.addWidget(_wrap_label(line))
        lay.addWidget(frame)
        return box

    def _uses_original_name(self) -> bool:
        for stamp in self.profile.stamps:
            try:
                if "original_name" in template_tokens(stamp.text):
                    return True
            except TemplateError:
                pass
        return False

    def _prefill(self) -> None:
        self._prefill_from([p.name for p in self.originals])

    def _validate(self, row) -> None:  # noqa: ANN001 - _FieldRow is private to stamp_dialog
        super()._validate(row)
        note = self._agreement_note(row.name)
        if note and not row.note.text():
            self._set_note(row, note)

    def _name_source(self) -> str:
        return combined_original_name(self.originals[0])

    def _save_folder(self) -> Path:
        return self.originals[0].parent

    def _accept_flags(self) -> tuple[bool, bool]:
        return (bool(self.combined.signed), False)  # signatures were confirmed before combining

    def _progress_text(self, done: int, total: int) -> str:
        page = min(done + 1, total)
        src, _ = self.combined.source_for(page)
        return f"Stamping page {page} of {total} · {visible(src.path.name)}"

    def check_output(self, out: Path) -> str:
        """Also refuse saving over any of the originals (D-008)."""
        for p in self.originals:
            if same_file(p, out):
                return (f"That's {visible(p.name)}, one of the originals. The combined file is always saved as a "
                        "new file; choose a different name or folder.")
        return super().check_output(out)

    def saved_notes(self) -> list[str]:
        """Notes for the saved screen: the protection warning, then the combine notes."""
        notes = [w for w in (self.combined.location_warning(), self.combined.protection_warning()) if w]
        return notes + list(self.combined.notes)


# ------------------------------------------------------------------ batch


@dataclass
class _BatchRun:
    """State of the batch save in progress."""

    stage: str = ""  # "check" or "save"
    req: int = 0
    checks: list[BatchCheck] = field(default_factory=list)


class BatchDialog(_MultiMixin, StampDialog):
    """One set of values and one placement for every file, with a file list to preview each (D-097)."""

    batch_saved = Signal(object)  # list[BatchOutcome]

    def __init__(
        self,
        *,
        config: Config,
        profile: Profile,
        files: Sequence[ScreenedFile],
        accept_signature_loss: bool,
        accept_restrictions: bool,
        engine: EngineClient,
        handler: Handler,
        services: Services,
        today: date | None = None,
        parent: QWidget | None = None,
    ) -> None:
        self.handler = handler  # used only inside tasks, which run on the engine thread
        self.files = [f for f in files if f.info is not None]
        self._accepted = (accept_signature_loss, accept_restrictions)
        self.out_folder: Path | None = None  # None: next to each original
        self._run = _BatchRun()
        first = self.files[0]
        assert first.info is not None
        super().__init__(config=config, profile=profile, path=first.path, info=first.info,
                         password=first.password, engine=engine, services=services, today=today, parent=parent)
        self.save_button.setText(f"Stamp all {len(self.files)} files…")
        engine.task_done.connect(self._on_task_done)
        engine.task_failed.connect(self._on_task_failed)
        engine.task_progress.connect(self._on_task_progress)

    # hooks

    def _current_index(self) -> int:
        return next(i for i, f in enumerate(self.files) if f.path == self.path)

    def _head_texts(self) -> tuple[str, str]:
        i = self._current_index()
        return (visible(self.path.name), f"File {i + 1} of {len(self.files)} · {_page_word(self.info.page_count)}")

    def _title_text(self) -> str:
        return f"Stamp {len(self.files)} files"

    def help_topic(self) -> str:
        """The help topic for this screen."""
        return "several-files#stamping-each-file-separately"

    def _wrap_well(self, well: QFrame) -> QWidget:
        """The file list sits above the preview, not beside it.

        Beside it, the list left the page too narrow at the 880 × 560
        minimum (D-093) and the preview was cropped. Above it, the list
        shows up to three files and scrolls for more.
        """
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.file_list = QListWidget()
        self.file_list.setAccessibleName("Files in this batch")
        self.file_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.file_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for f in self.files:
            item = QListWidgetItem(visible(f.path.name))
            item.setToolTip(str(f.path))
            self.file_list.addItem(item)
        self.file_list.setCurrentRow(0)
        self.file_list.currentRowChanged.connect(self.show_file)
        rows = min(len(self.files), BATCH_LIST_ROWS)
        row_h = max(self.file_list.sizeHintForRow(0), 24)
        self.file_list.setFixedHeight(row_h * rows + 2 * self.file_list.frameWidth() + 6)
        lay.addWidget(self.file_list)
        lay.addWidget(well, 1)
        return box

    def show_file(self, index: int) -> None:
        """Preview another file in the batch (same values and placement)."""
        if not 0 <= index < len(self.files) or self.saving:
            return
        f = self.files[index]
        if f.path == self.path:
            return
        assert f.info is not None
        self.path, self.info, self.password = f.path, f.info, f.password
        self.page = 1
        name, meta = self._head_texts()
        self.head_name.setText(name)
        self.head_meta.setText(meta)
        if self.file_list.currentRow() != index:
            self.file_list.setCurrentRow(index)
        self._update_name()
        self._changed(immediate=True)

    def _build_confirmations(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.signature_tick = None
        self.restriction_tick = None
        self.owner_button = None
        infos = [f.info for f in self.files if f.info is not None]
        lines = []
        if any(i.encrypted for i in infos):
            lines.append("Each copy keeps its original's password and permissions.")
        signed = [visible(f.path.name) for f in self.files if f.info and f.info.signed]
        if signed:
            lines.append(f"Signed files ({_names(signed)}) were confirmed; the originals stay signed.")
        if lines:
            frame, fl = _notice("info")
            for line in lines:
                fl.addWidget(_wrap_label(line))
            lay.addWidget(frame)
        box.setVisible(bool(lines))
        return box

    def _build_filename(self) -> QVBoxLayout:
        col = super()._build_filename()
        col.addWidget(_wrap_label("Each copy is named from its own original. The name above is for the file "
                                  "shown.", "help"))
        where = QLabel("Save the copies")
        where.setObjectName("sectionLabel")
        col.addWidget(where)
        self.next_to_radio = QRadioButton("Next to each original")
        self.folder_radio = QRadioButton("In one folder")
        group = QButtonGroup(self)
        group.addButton(self.next_to_radio)
        group.addButton(self.folder_radio)
        self.next_to_radio.setChecked(True)
        self.next_to_radio.toggled.connect(lambda on: on and self._set_folder(None))
        self.folder_radio.clicked.connect(self._choose_folder)
        col.addWidget(self.next_to_radio)
        row = QHBoxLayout()
        row.addWidget(self.folder_radio)
        self.folder_label = _wrap_label("", "help")
        row.addWidget(self.folder_label, 1)
        col.addLayout(row)
        return col

    def _choose_folder(self) -> None:
        start = self.out_folder or self.files[0].path.parent
        chosen = self.services.ask_folder(self, start)
        if chosen is None:
            if self.out_folder is None:
                self.next_to_radio.setChecked(True)
            return
        self._set_folder(chosen)

    def _set_folder(self, folder: Path | None) -> None:
        """Save every copy in ``folder``, or next to each original when None."""
        self.out_folder = Path(folder) if folder else None
        if folder:
            self.folder_radio.setChecked(True)
        self.folder_label.setText(str(folder) if folder else "")

    def _prefill(self) -> None:
        self._prefill_from([f.path.name for f in self.files])

    def _validate(self, row) -> None:  # noqa: ANN001 - _FieldRow is private to stamp_dialog
        super()._validate(row)
        note = self._agreement_note(row.name)
        if note and not row.note.text():
            self._set_note(row, note)

    def _accept_flags(self) -> tuple[bool, bool]:
        return self._accepted

    def blockers(self) -> list[str]:
        """As for one file, except a page problem in the file shown doesn't stop the others (D-107)."""
        out = super().blockers()
        err = self._plan_error.error if self._plan_error is not None else None
        if err is not None and not isinstance(err, MissingGlyph):
            out = [r for r in out if not r.startswith("Fix the problem shown under")]
        return out

    # names and items

    def name_for(self, path: Path) -> str:
        """The suggested name for one file's copy."""
        return self.suggested_name(path.name)

    def items(self) -> list[BatchItem]:
        """Where each file's copy goes, with its password."""
        return [BatchItem(f.path, (self.out_folder or f.path.parent) / self.name_for(f.path), f.password,
                          f.info.fingerprint if f.info else None)
                for f in self.files]

    # saving

    def _save(self) -> None:
        if not self.save_button.isEnabled() or self.saving:
            return
        self._begin("check")
        items, job, handler = self.items(), self.job(), self.handler

        def check(ctx: TaskContext) -> list[BatchCheck]:
            return check_batch(handler, items, job,
                               progress=lambda d, t: ctx.progress(d, t, items[min(d, t - 1)].path.name),
                               cancel=ctx.cancelled)

        self._run.req = self.engine.run_task(check)

    def _begin(self, stage: str) -> None:
        self._run.stage = stage
        self.saving = True
        self._redraw.stop()
        self.form.setEnabled(False)
        self.file_list.setEnabled(False)
        self.prev_button.setEnabled(False)
        self.next_button.setEnabled(False)
        self.save_error.hide()
        self.progress.setRange(0, 0)
        self.progress_label.setText("Checking every file…" if stage == "check" else "Stamping…")
        self.progress_row.show()
        self.cancel_button.setEnabled(True)
        self._update_button()

    def _finish(self) -> None:
        self._run = _BatchRun()
        self.file_list.setEnabled(True)
        self._end_save()

    def _on_task_progress(self, req: int, done: int, total: int, text: str) -> None:
        if req != self._run.req or not self.saving:
            return
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        if self._run.stage == "check":
            self.progress_label.setText(f"Checking file {min(done + 1, total)} of {total}…")
        else:
            self.progress_label.setText(text)

    def _on_task_failed(self, req: int, failure: EngineFailure) -> None:
        if req != self._run.req:
            return
        self._finish()
        if isinstance(failure.error, Cancelled):
            self._refuse("Cancelled before anything was saved.")
        else:
            self._refuse(failure.message)

    def _on_task_done(self, req: int, result: object) -> None:
        if req != self._run.req:
            return
        if self._run.stage == "check":
            self._after_check(list(result))  # type: ignore[call-overload]
        else:
            self._finish()
            self.batch_saved.emit(list(result))  # type: ignore[call-overload]

    def _after_check(self, checks: list[BatchCheck]) -> None:
        for i, c in enumerate(checks):
            item = self.file_list.item(i)
            mark = "✓ " if c.ok else ("– " if c.status == Status.SKIPPED else "! ")
            item.setText(mark + visible(c.item.path.name))
            item.setToolTip(c.message or str(c.item.out_path))
        good = [c for c in checks if c.ok]
        bad = [c for c in checks if not c.ok]
        if not good:
            self._finish()
            listed = "\n".join(f"• {visible(c.item.path.name)}: {c.message}" for c in bad[:6])
            self._refuse(f"None of the files can be saved as set up:\n{listed}")
            return
        if bad:
            listed = "\n".join(f"• {visible(c.item.path.name)}: {c.message}" for c in bad[:8])
            more = f"\n…and {len(bad) - 8} more." if len(bad) > 8 else ""
            ok = self.services.confirm(
                self, "Some files can't be stamped",
                f"{len(bad)} of {len(checks)} files won't be saved:\n{listed}{more}\n\n"
                f"Stamp the other {len(good)}?",
                f"Stamp the other {len(good)}")
            if not ok:
                self._finish()
                self._refuse("Nothing was saved. Fix the problems, or remove those files from the list.")
                return
        self._run.checks = checks
        self._start_run(checks)

    def _start_run(self, checks: list[BatchCheck]) -> None:
        self._run.stage = "save"
        job, handler = self.job(), self.handler
        accept_signature, accept_restrictions = self._accepted
        names = [c.item.path.name for c in checks]
        count = len(checks)

        def save(ctx: TaskContext) -> list[BatchOutcome]:
            return run_batch(
                handler, checks, job,
                accept_signature_loss=accept_signature,
                accept_restrictions=accept_restrictions,
                progress=lambda d, t, i: ctx.progress(d, t, f"File {i + 1} of {count} · {names[i]}"),
                cancel=ctx.cancelled,
            )

        self.progress_label.setText("Stamping…")
        self._run.req = self.engine.run_task(save)

    def _cancel_save(self) -> None:
        if self.saving and self.cancel_button.isEnabled():
            self.progress_label.setText("Cancelling…")
            self.engine.cancel()

    def disconnect_engine(self) -> None:
        """Stop listening to the engine, including the batch task signals."""
        for signal, slot in ((self.engine.task_done, self._on_task_done),
                             (self.engine.task_failed, self._on_task_failed),
                             (self.engine.task_progress, self._on_task_progress)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        super().disconnect_engine()


# ------------------------------------------------------------------ batch saved


_STATUS_TEXT = {
    Status.SAVED: "Saved",
    Status.SKIPPED: "Skipped",
    Status.FAILED: "Failed",
    Status.CANCELLED: "Cancelled",
    Status.NOT_STARTED: "Not started",
}


class BatchSavedScreen(QWidget):
    """One row per file: saved (with Open), skipped or failed, with the reason (D-110)."""

    def __init__(self, services: Services, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ground")
        self.services = services
        self.outcomes: list[BatchOutcome] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        card, lay = _card()
        self.title = QLabel("")
        self.title.setObjectName("savedTitle")
        lay.addWidget(self.title)
        self.lead = _wrap_label("", "lead")
        lay.addWidget(self.lead)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rows_widget = QWidget()
        self.rows_widget.setObjectName("surface")
        self.grid = QGridLayout(self.rows_widget)
        self.grid.setContentsMargins(0, 0, 8, 0)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(8)
        scroll.setWidget(self.rows_widget)
        lay.addWidget(scroll, 1)
        buttons = QHBoxLayout()
        self.folder_button = QPushButton("Open folder")
        self.folder_button.setObjectName("primary")
        self.folder_button.clicked.connect(self._open_folder)
        self.another_button = QPushButton("Stamp more files")
        self.another_button.setObjectName("link")
        buttons.addWidget(self.folder_button)
        buttons.addWidget(self.another_button)
        buttons.addStretch(1)
        self.help_button = help_button()
        buttons.addWidget(self.help_button)
        lay.addLayout(buttons)
        _centre(outer, card)
        self.open_buttons: list[QPushButton] = []

    def help_topic(self) -> str:
        """The help topic for this screen."""
        return "several-files#the-results"

    def show_outcomes(self, outcomes: Sequence[BatchOutcome]) -> None:
        """Fill the list from the batch's outcomes."""
        self.outcomes = list(outcomes)
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.open_buttons = []
        saved = [o for o in outcomes if o.status == Status.SAVED]
        self.title.setText(f"Stamped {len(saved)} of {len(outcomes)} files")
        counts = {s: sum(1 for o in outcomes if o.status == s) for s in _STATUS_TEXT}
        parts = [f"{n} {(_STATUS_TEXT[s]).lower()}" for s, n in counts.items() if n]
        self.lead.setText(", ".join(parts).capitalize() + ". The originals are unchanged.")
        for r, o in enumerate(outcomes):
            name = QLabel(visible(o.item.path.name))
            name.setWordWrap(True)
            name.setToolTip(str(o.item.path))
            status = QLabel(_STATUS_TEXT.get(o.status, o.status))
            status.setObjectName("msgOk" if o.status == Status.SAVED else "msgBad")
            detail_text = str(o.result.output_path) if o.result else o.message
            detail = _wrap_label(detail_text, "help")
            self.grid.addWidget(name, r, 0, Qt.AlignmentFlag.AlignTop)
            self.grid.addWidget(status, r, 1, Qt.AlignmentFlag.AlignTop)
            self.grid.addWidget(detail, r, 2)
            if o.result is not None:
                b = QPushButton("Open")
                b.setObjectName("small")
                b.setAccessibleName(f"Open the copy of {visible(o.item.path.name)}")
                b.clicked.connect(lambda _c=False, p=o.result.output_path: self.services.open_file(p))
                self.grid.addWidget(b, r, 3, Qt.AlignmentFlag.AlignTop)
                self.open_buttons.append(b)
        self.grid.setColumnStretch(2, 1)
        self.grid.setRowStretch(len(outcomes), 1)
        self.folder_button.setEnabled(bool(saved))
        (self.folder_button if saved else self.another_button).setFocus()

    def statuses(self) -> list[tuple[str, str]]:
        """``(file name, status)`` per row (tests)."""
        return [(o.item.path.name, o.status) for o in self.outcomes]

    def _open_folder(self) -> None:
        saved = [o for o in self.outcomes if o.result is not None]
        if saved and saved[0].result is not None:
            self.services.show_in_folder(saved[0].result.output_path)
