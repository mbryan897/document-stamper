"""The stamp dialog: preview on the left, fields and placement on the right.

Every engine call goes through the handler, on the engine thread (D-018,
D-070). The preview redraws on every change, 150 ms after typing stops and
at once for clicks (D-068). A whole-document check (``Handler.plan``, D-074)
follows each change; "Stamp and save…" is enabled only when every required
field is valid, every confirmation is ticked and that check has passed for
exactly what is on screen.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

from PySide6.QtCore import QEventLoop, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QResizeEvent, QShortcut, QStandardItemModel
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.config import ANCHORS, Config, Profile, StampSpec
from ..core.fields import MAX_VALUE_LENGTH, ValidationResult, prefill_fields, validate_field, value_from_filename
from ..core.filenames import suggest_filename, visible
from ..core.template import TemplateError, template_tokens
from ..handlers.base import (
    ApplyResult,
    Cancelled,
    DocumentInfo,
    MissingGlyph,
    PlanResult,
    StampDoesNotFit,
    StampJob,
)
from ..handlers.pdf import PROPERTIES_NOTE_PREFIX, same_file
from .engine import ApplyRequest, EngineClient, EngineFailure
from .help import help_button
from .page_numbers import (
    CHOICE_LABELS,
    WITH_IDENTIFIER,
    apply_page_numbers,
    main_stamp_index,
    page_number_option,
)
from .services import Services
from .wrap_label import WrapAnywhereLabel

__all__ = ["DEBOUNCE_MS", "StampDialog", "describe_date", "group_warnings"]

DEBOUNCE_MS = 150  # typing; clicks redraw at once
MAX_PREVIEW_DPI = 150
MIN_PREVIEW_DPI = 30
MAX_LISTED_WARNINGS = 6
_ANCHOR_NAMES = {a: a.replace("-", " ").capitalize() for a in ANCHORS}
_FORM_FIELD = "overlaps a form field or annotation"


def describe_date(d: date) -> str:
    """A date written out for messages: ``October 17, 2026``."""
    return f"{d:%B} {d.day}, {d.year}"


def _pages_text(pages: Sequence[int]) -> str:
    if len(pages) == 1:
        return f"page {pages[0]}"
    listed = [str(p) for p in pages[:8]]
    more = len(pages) - len(listed)
    body = ", ".join(listed[:-1]) + f" and {listed[-1]}" if not more else ", ".join(listed) + f" and {more} more"
    return f"pages {body}"


def _before_saving(warnings: Sequence[str]) -> list[str]:
    """The plan's notes without the document-properties line, which belongs on the saved screen (D-125)."""
    return [w for w in warnings if not w.startswith(PROPERTIES_NOTE_PREFIX)]


def group_warnings(warnings: Sequence[str]) -> list[str]:
    """Engine warnings for the dialog: form-field warnings grouped into one line (D-066).

    Other warnings are the engine's own sentences; past ``MAX_LISTED_WARNINGS``
    they are summarised as "… and N more".
    """
    form_pages: list[int] = []
    other: list[str] = []
    for w in warnings:
        if _FORM_FIELD in w and w.startswith("Page "):
            page = int(w.split(",", 1)[0].removeprefix("Page "))
            if page not in form_pages:
                form_pages.append(page)
        else:
            other.append(w)
    out: list[str] = []
    if form_pages:
        out.append(
            f"On {_pages_text(form_pages)} a form field covers part of the stamp and is drawn on "
            "top of it. Choose another position under Placement to avoid it."
        )
    if len(other) > MAX_LISTED_WARNINGS:
        shown = other[: MAX_LISTED_WARNINGS - 1]
        out.extend(shown)
        out.append(f"… and {len(other) - len(shown)} more notes like these.")
    else:
        out.extend(other)
    return out


def _notice(kind: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName(kind)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(8)
    return frame, lay


def _wrap_label(text: str = "", name: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if name:
        label.setObjectName(name)
    return label


class _FieldRow:
    """One field's widgets and its latest validation."""

    def __init__(self, name: str, label: QLabel, editor: QWidget, message: QLabel, note: QLabel) -> None:
        self.name = name
        self.label = label
        self.editor = editor
        self.message = message
        self.note = note  # future-date and missing-character notes
        self.result: ValidationResult | None = None
        self.prefilled: str | None = None

    def raw(self) -> str:
        if isinstance(self.editor, QComboBox):
            return self.editor.currentData() or ""
        assert isinstance(self.editor, QLineEdit)
        return self.editor.text()


class StampDialog(QWidget):
    """The stamp screen for one file. Emits ``saved`` or ``cancelled``."""

    saved = Signal(object)  # ApplyResult
    cancelled = Signal()

    def __init__(
        self,
        *,
        config: Config,
        profile: Profile,
        path: Path,
        info: DocumentInfo,
        password: str | None,
        engine: EngineClient,
        services: Services,
        today: date | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ground")
        self.config = config
        self.profile = profile
        self.path = Path(path)
        self.info = info
        self.password = password
        self.engine = engine
        self.services = services
        self.today = today or date.today()

        main = profile.stamps[main_stamp_index(profile.stamps)]
        self.anchor: str = main.anchor
        self.direction: str = main.direction
        self.page = 1
        self.page_numbers = WITH_IDENTIFIER
        self.revision = 0
        self.saving = False
        self._preview_req = 0
        self._plan_req = 0
        self._plan_revision = -1  # revision the latest plan request was for
        self._plan: PlanResult | None = None  # passed, for _plan_revision
        self._plan_error: EngineFailure | None = None
        self._plan_done_revision = -1
        self._last_png: bytes | None = None
        self._preview_done = 0  # last preview request answered
        self._save_error = ""

        self._rows: dict[str, _FieldRow] = {}
        self._ticks: dict[str, QCheckBox] = {}
        self._redraw = QTimer(self)
        self._redraw.setSingleShot(True)
        self._redraw.setInterval(DEBOUNCE_MS)
        self._redraw.timeout.connect(self._request)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(DEBOUNCE_MS)
        self._resize_timer.timeout.connect(self._preview_only)

        self._build()
        self._prefill()
        self._connect_engine()
        for row in self._rows.values():
            self._validate(row)
        self._refresh_page_numbers()
        self._update_name()
        self._changed(immediate=True)

    # ------------------------------------------------------------ layout

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_preview(), 1)
        outer.addWidget(self._build_panel(), 0)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._escape,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            QShortcut(QKeySequence(key), self, self._enter,
                      context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence(Qt.Key.Key_PageDown), self, lambda: self._go(1),
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence(Qt.Key.Key_PageUp), self, lambda: self._go(-1),
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)

    def _build_preview(self) -> QWidget:
        box = QWidget()
        box.setObjectName("ground")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(12)

        head = QHBoxLayout()
        head_name, head_meta = self._head_texts()
        name = QLabel(head_name)
        name.setObjectName("fileName")
        name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        name.setMinimumWidth(60)
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.head_name = name
        meta = QLabel(head_meta)
        meta.setObjectName("muted")
        self.head_meta = meta
        head.addWidget(name, 1)
        head.addWidget(meta)
        head.addSpacing(12)
        self.prev_button = QToolButton()
        self.prev_button.setObjectName("pageNav")
        self.prev_button.setText("‹")
        self.prev_button.setAccessibleName("Previous page")
        self.prev_button.setToolTip("Previous page (Page Up)")
        self.prev_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.prev_button.clicked.connect(lambda: self._go(-1))
        self.page_label = QLabel()
        self.page_label.setMinimumWidth(96)
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_button = QToolButton()
        self.next_button.setObjectName("pageNav")
        self.next_button.setText("›")
        self.next_button.setAccessibleName("Next page")
        self.next_button.setToolTip("Next page (Page Down)")
        self.next_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.next_button.clicked.connect(lambda: self._go(1))
        head.addWidget(self.prev_button)
        head.addWidget(self.page_label)
        head.addWidget(self.next_button)
        lay.addLayout(head)
        self._extend_head(lay)

        well = QFrame()
        well.setObjectName("previewWell")
        well_lay = QVBoxLayout(well)
        well_lay.setContentsMargins(16, 16, 16, 16)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.preview.setAccessibleName("Page preview")
        self.preview_message = _wrap_label("", "previewMessage")
        self.preview_holder = QWidget()  # shown instead of the page when it can't be drawn
        holder = QVBoxLayout(self.preview_holder)
        holder.setContentsMargins(32, 0, 32, 0)
        holder.addStretch(1)
        holder.addWidget(self.preview_message)  # no alignment flag: keeps the wrapped height right
        holder.addStretch(1)
        self.preview_holder.hide()
        well_lay.addWidget(self.preview, 1)
        well_lay.addWidget(self.preview_holder, 1)
        self.preview_well = well
        lay.addWidget(self._wrap_well(well), 1)

        hint = QLabel("The amber dashed line shows what the stamp covers. It isn't printed.")
        hint.setObjectName("muted")
        lay.addWidget(hint)
        return box

    # Hooks for the multi-file dialogs (D-108); single-file behaviour is the default.

    def _head_texts(self) -> tuple[str, str]:
        """The file name and the "N pages, size" line above the preview."""
        size_mb = self.path.stat().st_size / (1024 * 1024) if self.path.exists() else 0.0
        pages = "1 page" if self.info.page_count == 1 else f"{self.info.page_count} pages"
        size = f"{size_mb:.1f} MB" if size_mb >= 0.1 else "under 0.1 MB"
        return visible(self.path.name), f"{pages}, {size}"

    def _extend_head(self, layout: QVBoxLayout) -> None:
        """Add widgets under the preview header (none for one file)."""

    def _wrap_well(self, well: QFrame) -> QWidget:
        """The widget holding the preview well (the well itself for one file)."""
        return well

    def _title_text(self) -> str:
        return "Stamp this document"

    def help_topic(self) -> str:
        """The help topic for this screen (F1 and the Help button)."""
        return "stamping-one-file"

    def _name_source(self) -> str:
        """The original file name the suggested name is built from."""
        return visible(self.path.name)

    def _accept_flags(self) -> tuple[bool, bool]:
        """``(accept_signature_loss, accept_restrictions)`` for the save."""
        return (bool(self.signature_tick and self.signature_tick.isChecked()),
                bool(self.restriction_tick and self.restriction_tick.isChecked()))

    def _save_folder(self) -> Path:
        """The folder Save As starts in."""
        return self.path.parent

    def _progress_text(self, done: int, total: int) -> str:
        return f"Stamping page {done + 1} of {total}…"

    def _build_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("panel")
        panel.setFixedWidth(452)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("surface")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("surface")
        form = QVBoxLayout(body)
        form.setContentsMargins(28, 22, 28, 22)
        form.setSpacing(18)
        self.form = body

        title = QLabel(self._title_text())
        title.setObjectName("h1")
        sub = QLabel(f"Profile: {self.profile.name}")
        sub.setObjectName("muted")
        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(title)
        head.addWidget(sub)
        form.addLayout(head)

        stamped = self._stamped_fields()
        for name in self.profile.fields:
            form.addLayout(self._build_field(name, name in stamped))

        form.addWidget(self._build_confirmations())
        form.addLayout(self._build_placement())
        form.addLayout(self._build_filename())
        self.notes_frame, self.notes_layout = _notice("notice")
        self.notes_frame.hide()
        form.addWidget(self.notes_frame)
        form.addStretch(1)
        scroll.setWidget(body)
        self.scroll = scroll
        lay.addWidget(scroll, 1)
        lay.addWidget(self._build_footer())
        return panel

    def _stamped_fields(self) -> set[str]:
        out: set[str] = set()
        for stamp in self.profile.stamps:
            try:
                out.update(template_tokens(stamp.text))
            except TemplateError:
                pass
        return out

    def _build_field(self, name: str, stamped: bool) -> QVBoxLayout:
        spec = self.config.fields[name]
        extra = [] if spec.required else ["optional"]
        if not stamped:
            extra.append("not stamped")
        label = QLabel(spec.label + (f"  ({', '.join(extra)})" if extra else ""))
        label.setObjectName("fieldLabel")

        editor: QWidget
        if spec.type == "choice":
            combo = QComboBox()
            combo.addItem("Choose…" if spec.required else "(none)", "")
            assert spec.taxonomy is not None
            for item in self.config.taxonomies[spec.taxonomy]:
                combo.addItem(item, item)
            combo.currentIndexChanged.connect(lambda _i, n=name: self._field_edited(n, immediate=True))
            editor = combo
        else:
            line = QLineEdit()
            line.setMaxLength(MAX_VALUE_LENGTH)  # D-118: also stops a pasted wall of text
            line.setClearButtonEnabled(False)
            if spec.type == "date":
                line.setPlaceholderText(self.config.settings.date_format)
            if name == "identifier" or spec.from_filename:
                line.setObjectName("identifier")  # Mono font, 0/O and 1/l distinct
            line.textEdited.connect(lambda _t, n=name: self._field_edited(n))
            editor = line
        editor.setAccessibleName(spec.label)
        label.setBuddy(editor)
        message = _wrap_label("", "msgNeutral")
        note = _wrap_label("", "msgBad")
        note.hide()
        editor.setAccessibleDescription("")

        col = QVBoxLayout()
        col.setSpacing(6)
        col.addWidget(label)
        col.addWidget(editor)
        col.addWidget(message)
        col.addWidget(note)
        self._rows[name] = _FieldRow(name, label, editor, message, note)
        return col

    def _build_confirmations(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.signature_tick: QCheckBox | None = None
        self.restriction_tick: QCheckBox | None = None
        self.owner_button: QPushButton | None = None

        if self.info.signed:
            frame, fl = _notice("warning")
            fl.addWidget(_wrap_label(
                "This PDF has a digital signature. The stamped copy won't keep it, "
                "but your original stays signed and unchanged."))
            self.signature_tick = QCheckBox("Save a copy without a valid signature")
            self.signature_tick.toggled.connect(lambda _c: self._update_button())
            fl.addWidget(self.signature_tick)
            lay.addWidget(frame)
        if self.info.restricted:
            frame, fl = _notice("warning")
            fl.addWidget(_wrap_label(
                "This PDF's owner restrictions don't allow changes. The stamped copy "
                "keeps the same restrictions."))
            self.restriction_tick = QCheckBox("Stamp it anyway")
            self.restriction_tick.toggled.connect(lambda _c: self._update_button())
            fl.addWidget(self.restriction_tick)
            self.owner_button = QPushButton("Enter the owner password instead…")
            self.owner_button.setObjectName("link")
            self.owner_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.owner_button.clicked.connect(self._ask_owner_password)
            fl.addWidget(self.owner_button, 0, Qt.AlignmentFlag.AlignLeft)
            lay.addWidget(frame)
        if self.info.encrypted and not self.info.restricted:
            frame, fl = _notice("info")
            fl.addWidget(_wrap_label("The copy keeps the original's password and permissions."))
            lay.addWidget(frame)
        box.setVisible(lay.count() > 0)
        return box

    def _build_placement(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(10)
        heading = QLabel("Placement")
        heading.setObjectName("sectionLabel")
        col.addWidget(heading)

        row = QHBoxLayout()
        row.setSpacing(16)
        grid_box = QVBoxLayout()
        grid_box.setSpacing(4)
        pos_label = QLabel("Position")
        pos_label.setObjectName("muted")
        grid_box.addWidget(pos_label)
        grid = QGridLayout()
        grid.setSpacing(4)
        self.anchor_group = QButtonGroup(self)
        self.anchor_group.setExclusive(True)
        self.anchor_buttons: dict[str, QToolButton] = {}
        for i, anchor in enumerate(ANCHORS):
            b = QToolButton()
            b.setObjectName("anchor")
            b.setCheckable(True)
            b.setChecked(anchor == self.anchor)
            b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            b.setAccessibleName(_ANCHOR_NAMES[anchor])
            b.setToolTip(_ANCHOR_NAMES[anchor])
            b.clicked.connect(lambda _c=False, a=anchor: self._set_anchor(a))
            self.anchor_group.addButton(b)
            self.anchor_buttons[anchor] = b
            grid.addWidget(b, i // 3, i % 3)
        grid_box.addLayout(grid)
        grid_box.addStretch(1)
        row.addLayout(grid_box)

        dir_box = QVBoxLayout()
        dir_box.setSpacing(4)
        dir_label = QLabel("Direction")
        dir_label.setObjectName("muted")
        dir_box.addWidget(dir_label)
        self.direction_group = QButtonGroup(self)
        self.direction_buttons: dict[str, QPushButton] = {}
        for value, text in (("along-top", "Along top edge"), ("along-right", "Along right edge")):
            b = QPushButton(text)
            b.setObjectName("choice")
            b.setCheckable(True)
            b.setChecked(value == self.direction)
            b.clicked.connect(lambda _c=False, v=value: self._set_direction(v))
            self.direction_group.addButton(b)
            self.direction_buttons[value] = b
            dir_box.addWidget(b)
        dir_box.addStretch(1)
        row.addLayout(dir_box, 1)
        col.addLayout(row)

        pn_label = QLabel("Page numbers")
        pn_label.setObjectName("muted")
        self.page_numbers_combo = QComboBox()
        self.page_numbers_combo.setAccessibleName("Page numbers")
        pn_label.setBuddy(self.page_numbers_combo)
        self.page_numbers_combo.activated.connect(self._page_numbers_picked)
        self.page_numbers_note = _wrap_label("", "help")
        self.page_numbers_note.hide()
        col.addWidget(pn_label)
        col.addWidget(self.page_numbers_combo)
        col.addWidget(self.page_numbers_note)

        main = self.profile.stamps[main_stamp_index(self.profile.stamps)]
        self.box_check = QCheckBox("White box behind stamp")
        self.box_check.setChecked(main.background_box)  # D-011: on unless the profile says otherwise
        self.box_check.toggled.connect(lambda _c: self._changed(immediate=True))
        col.addWidget(self.box_check)
        box_help = _wrap_label(
            "Keeps the stamp readable over printed text. Turn off to print the stamp "
            "directly on the page.", "help")
        col.addWidget(box_help)

        self.placement_error = _wrap_label("", "previewMessage")
        self.placement_error.hide()
        col.addWidget(self.placement_error)
        return col

    def _build_filename(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(8)
        heading = QLabel("File name")
        heading.setObjectName("sectionLabel")
        col.addWidget(heading)
        help_ = QLabel("Put these in the name of the stamped copy:")
        help_.setObjectName("help")
        col.addWidget(help_)
        defaults = list(self.profile.output_name.fields)
        order = defaults + [f for f in self.profile.fields if f not in defaults]
        if "date" not in order:
            order.append("date")
        ticks = QGridLayout()
        ticks.setHorizontalSpacing(16)
        ticks.setVerticalSpacing(6)
        for i, name in enumerate(order):
            text = "Today's date" if name == "date" else self.config.fields[name].label
            box = QCheckBox(text)
            box.setChecked(name in defaults)
            box.toggled.connect(lambda _c: self._update_name())
            self._ticks[name] = box
            ticks.addWidget(box, i // 2, i % 2)
        col.addLayout(ticks)
        self.name_label = WrapAnywhereLabel()  # a long name must not widen the panel (D-137)
        self.name_label.setObjectName("mono")
        self.name_label.setAccessibleName("Suggested file name")
        col.addWidget(self.name_label)
        return col

    def _build_footer(self) -> QWidget:
        foot = QWidget()
        foot.setObjectName("footer")
        lay = QVBoxLayout(foot)
        lay.setContentsMargins(28, 14, 28, 16)
        lay.setSpacing(10)

        self.save_error = _wrap_label("", "previewMessage")
        self.save_error.hide()
        lay.addWidget(self.save_error)

        self.progress_row = QWidget()
        pr = QVBoxLayout(self.progress_row)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.setSpacing(6)
        self.progress_label = QLabel("")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        pr.addWidget(self.progress_label)
        pr.addWidget(self.progress)
        self.progress_row.hide()
        lay.addWidget(self.progress_row)

        self.status = _wrap_label("", "help")
        lay.addWidget(self.status)
        buttons = QHBoxLayout()
        self.help_button = help_button()
        buttons.addWidget(self.help_button)
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._escape)
        self.save_button = QPushButton("Stamp and save…")
        self.save_button.setObjectName("primary")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)
        lay.addLayout(buttons)
        return foot

    # ------------------------------------------------------------ fields

    def _prefill(self) -> None:
        for name in self.profile.fields:
            found = value_from_filename(self.config, name, self.path.name)
            row = self._rows[name]
            if found is not None and isinstance(row.editor, QLineEdit):
                row.editor.setText(found)
                row.prefilled = found

    def _field_edited(self, name: str, *, immediate: bool = False) -> None:
        self._validate(self._rows[name])
        self._update_name()
        self._changed(immediate=immediate)

    def _validate(self, row: _FieldRow) -> None:
        spec = self.config.fields[row.name]
        raw = row.raw()
        result = validate_field(self.config, row.name, raw)
        row.result = result
        empty = not raw.strip()
        parts: list[str] = []
        if row.prefilled is not None and result.value == row.prefilled:
            parts.append("Taken from the file name.")
        if result.valid and result.format and spec.type == "text":
            msg = f"Matches {result.format}"
            msg += f", dated {describe_date(result.date)}." if result.date else "."
            parts.append(msg)
        elif not result.valid:
            parts.append(result.message)
        text = " ".join(parts)
        bad = not result.valid and not empty
        row.message.setText(text)
        row.message.setVisible(bool(text))
        row.message.setObjectName("msgBad" if bad else ("msgOk" if result.valid and text else "msgNeutral"))
        row.message.style().unpolish(row.message)
        row.message.style().polish(row.message)
        row.editor.setAccessibleDescription(text)
        if isinstance(row.editor, QLineEdit):
            row.editor.setProperty("invalid", bad)
            row.editor.style().unpolish(row.editor)
            row.editor.style().polish(row.editor)

        note = ""
        if result.valid and result.date and result.date > self.today and spec.type == "text":
            note = (f"The date in this {spec.label.lower()}, {describe_date(result.date)}, "
                    "is in the future. Check it before saving.")
        self._set_note(row, note)

    def _set_note(self, row: _FieldRow, text: str) -> None:
        row.note.setText(text)
        row.note.setVisible(bool(text))

    def values(self) -> dict[str, str]:
        """The normalized value of every field, as it will be stamped."""
        return {n: (r.result.value if r.result else "") for n, r in self._rows.items()}

    def focus_first(self) -> None:
        """Put the keyboard focus on the first field that still needs a value, else the first."""
        rows = list(self._rows.values())
        target = next((r for r in rows if r.result is None or not r.result.valid), rows[0] if rows else None)
        (target.editor if target else self.save_button).setFocus()

    def editor(self, name: str) -> QWidget:
        """The input widget for a field (tests and keyboard focus)."""
        return self._rows[name].editor

    def message(self, name: str) -> str:
        """The message under a field."""
        return self._rows[name].message.text()

    def note(self, name: str) -> str:
        """The amber note under a field (future date, missing character), if shown."""
        row = self._rows[name]
        return row.note.text() if not row.note.isHidden() else ""

    def invalid_fields(self) -> list[str]:
        """Labels of fields that aren't valid yet, in form order."""
        return [self.config.fields[n].label for n, r in self._rows.items()
                if r.result is None or not r.result.valid]

    # ------------------------------------------------------------ placement

    def _set_anchor(self, anchor: str) -> None:
        self.anchor = anchor
        self.anchor_buttons[anchor].setChecked(True)
        self._refresh_page_numbers()
        self._changed(immediate=True)

    def _set_direction(self, direction: str) -> None:
        self.direction = direction
        self.direction_buttons[direction].setChecked(True)
        self._changed(immediate=True)

    def _placed_stamps(self) -> list[StampSpec]:
        stamps = list(self.profile.stamps)
        m = main_stamp_index(stamps)
        stamps[m] = stamps[m].model_copy(update={"anchor": self.anchor, "direction": self.direction})
        return stamps

    def _refresh_page_numbers(self) -> None:
        option = page_number_option(self._placed_stamps())
        combo = self.page_numbers_combo
        combo.blockSignals(True)
        combo.clear()
        for choice in option.choices:
            combo.addItem(CHOICE_LABELS[choice], choice)
        model = combo.model()
        assert isinstance(model, QStandardItemModel)
        for i, ok in enumerate(option.enabled):
            item = model.item(i)
            item.setEnabled(ok)
        if self.page_numbers not in option.choices:
            self.page_numbers = option.current
        combo.setCurrentIndex(option.choices.index(self.page_numbers))
        combo.setEnabled(sum(option.enabled) > 1)
        combo.blockSignals(False)
        self.page_numbers_note.setText(option.note)
        self.page_numbers_note.setVisible(bool(option.note))

    def _page_numbers_picked(self, index: int) -> None:
        self.page_numbers = self.page_numbers_combo.itemData(index)
        self._changed(immediate=True)

    def stamps(self) -> tuple[StampSpec, ...]:
        """The stamps this job will draw (placement and Page numbers applied)."""
        return apply_page_numbers(self._placed_stamps(), self.page_numbers)

    def job(self) -> StampJob:
        """The job for what is on screen now."""
        return StampJob.from_profile(
            self.config, self.profile, self.values(),
            background_box=self.box_check.isChecked(),
            stamps=self.stamps(),
            today=self.today,
        )

    # ------------------------------------------------------------ file name

    def ticked(self) -> list[str]:
        """The File name selector's ticked fields, in display order."""
        return [n for n, box in self._ticks.items() if box.isChecked()]

    def suggested_name(self) -> str:
        """The suggested file name for the stamped copy (D-016, D-067)."""
        values = {**self.values(), "date": self.config.settings.date_formatter.format(self.today)}
        return suggest_filename(self.profile, values, self._name_source(), ticked=self.ticked(),
                                dedupe=prefill_fields(self.config, self.profile))

    def _update_name(self) -> None:
        self.name_label.setText(self.suggested_name())

    # ------------------------------------------------------------ preview and plan

    def _connect_engine(self) -> None:
        e = self.engine
        e.previewed.connect(self._on_preview)
        e.preview_failed.connect(self._on_preview_failed)
        e.planned.connect(self._on_plan)
        e.plan_failed.connect(self._on_plan_failed)
        e.progress.connect(self._on_progress)
        e.applied.connect(self._on_applied)
        e.apply_failed.connect(self._on_apply_failed)

    def disconnect_engine(self) -> None:
        """Stop listening to the engine (the screen is being closed)."""
        e = self.engine
        for signal, slot in (
            (e.previewed, self._on_preview), (e.preview_failed, self._on_preview_failed),
            (e.planned, self._on_plan), (e.plan_failed, self._on_plan_failed),
            (e.progress, self._on_progress), (e.applied, self._on_applied),
            (e.apply_failed, self._on_apply_failed),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        e.forget()

    def _changed(self, *, immediate: bool = False) -> None:
        """Something that changes the stamp changed: redraw and re-check."""
        self.revision += 1
        self._save_error = ""
        self.save_error.hide()
        self._update_button()
        if immediate:
            self._redraw.stop()
            self._request()
        else:
            self._redraw.start()

    def _preview_dpi(self) -> int:
        page = self.info.pages[self.page - 1]
        size = self.preview.size()
        w = max(size.width(), 200)
        h = max(size.height(), 260)
        ratio = self.devicePixelRatioF() or 1.0
        dpi = min(w * ratio / (page.width / 72), h * ratio / (page.height / 72))
        return int(max(MIN_PREVIEW_DPI, min(MAX_PREVIEW_DPI, dpi)))

    def _request(self) -> None:
        if self.saving:
            return
        job = self.job()
        self._preview_req = self.engine.preview(self.path, self.page, job, self.password, self._preview_dpi())
        self._plan_revision = self.revision
        self._plan_req = self.engine.plan(self.path, job, self.password, self.info.fingerprint)
        self._update_page_label()
        self._update_button()

    def _update_page_label(self) -> None:
        self.page_label.setText(f"Page {self.page} of {self.info.page_count}")
        self.prev_button.setEnabled(self.page > 1)
        self.next_button.setEnabled(self.page < self.info.page_count)

    def _preview_only(self) -> None:
        if not self.saving:
            self._preview_req = self.engine.preview(
                self.path, self.page, self.job(), self.password, self._preview_dpi())

    def _go(self, step: int) -> None:
        new = min(max(1, self.page + step), self.info.page_count)
        if new != self.page:
            self.page = new
            self._update_page_label()
            if self._redraw.isActive():
                return  # the pending redraw will show the new page
            self._preview_only()

    def _on_preview(self, req: int, png: bytes) -> None:
        if req != self._preview_req:
            return
        self._preview_done = req
        self._last_png = png
        pix = QPixmap()
        pix.loadFromData(png, "PNG")
        pix.setDevicePixelRatio(self.devicePixelRatioF() or 1.0)
        self.preview.setPixmap(pix)
        self.preview_holder.hide()
        self.preview.show()

    def _on_preview_failed(self, req: int, failure: EngineFailure) -> None:
        if req != self._preview_req:
            return
        self._preview_done = req
        self._last_png = None
        self.preview.clear()
        self.preview_message.setText(failure.message)
        self.preview.hide()
        self.preview_holder.show()

    def _on_plan(self, req: int, result: PlanResult) -> None:
        if req != self._plan_req:
            return
        self._plan, self._plan_error = result, None
        self._plan_done_revision = self._plan_revision
        self._show_plan()

    def _on_plan_failed(self, req: int, failure: EngineFailure) -> None:
        if req != self._plan_req:
            return
        self._plan, self._plan_error = None, failure
        self._plan_done_revision = self._plan_revision
        self._show_plan()

    def _show_plan(self) -> None:
        for row in self._rows.values():
            if row.note.property("glyph"):
                row.note.setProperty("glyph", False)
                self._validate(row)
        self.placement_error.hide()

        while self.notes_layout.count():
            item = self.notes_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if self._plan_error is not None:
            err = self._plan_error.error
            if isinstance(err, MissingGlyph):
                row = next((r for r in self._rows.values() if err.char in r.raw()), None)
                if row is not None:
                    row.note.setProperty("glyph", True)
                    self._set_note(row, str(err))
                else:
                    self.placement_error.setText(str(err))
                    self.placement_error.show()
            else:
                self.placement_error.setText(self._plan_error.message)
                self.placement_error.show()
            self.notes_frame.hide()
        else:
            assert self._plan is not None
            notes = group_warnings(_before_saving(self._plan.warnings))
            if notes:
                title = QLabel("Before you save")
                title.setStyleSheet("font-weight: 700;")
                self.notes_layout.addWidget(title)
                for n in notes:
                    self.notes_layout.addWidget(_wrap_label(f"Note: {n}"))
            self.notes_frame.setVisible(bool(notes))
        self._update_button()

    def ready(self) -> bool:
        """True when nothing is pending: the preview and the whole-document check are current."""
        return (not self._redraw.isActive() and not self._resize_timer.isActive() and not self.saving
                and self._preview_done == self._preview_req
                and self._plan_done_revision == self.revision)

    def preview_png(self) -> bytes | None:
        """The PNG on screen now, or None when the preview shows a message instead."""
        return self._last_png

    def preview_text(self) -> str:
        """The message shown in place of the preview, if any."""
        return self.preview_message.text() if not self.preview_holder.isHidden() else ""

    def placement_problem(self) -> str:
        """The error shown under Placement (a stamp that doesn't fit, overlapping stamps)."""
        return self.placement_error.text() if not self.placement_error.isHidden() else ""

    def save_message(self) -> str:
        """The message above the buttons (a refused output, a cancelled or failed save)."""
        return self.save_error.text() if not self.save_error.isHidden() else ""

    def plan_warnings(self) -> list[str]:
        """The notes shown under "Before you save" (empty if none or not checked yet)."""
        return group_warnings(_before_saving(self._plan.warnings)) if self._plan is not None else []

    # ------------------------------------------------------------ the button

    def blockers(self) -> list[str]:
        """Why "Stamp and save…" is disabled, as sentences; empty when it's enabled."""
        out: list[str] = []
        missing = self.invalid_fields()
        if missing:
            out.append(f"Fill in or fix: {', '.join(missing)}.")
        if self.signature_tick is not None and not self.signature_tick.isChecked():
            out.append("Tick \"Save a copy without a valid signature\" to continue.")
        if self.restriction_tick is not None and not self.restriction_tick.isChecked():
            out.append("Tick \"Stamp it anyway\" to continue.")
        if self._plan_done_revision != self.revision:
            out.append("Checking all pages…")
        elif self._plan_error is not None:
            err = self._plan_error.error
            row = (next((r for r in self._rows.values() if err.char in r.raw()), None)
                   if isinstance(err, MissingGlyph) else None)
            where = self.config.fields[row.name].label if row else "Placement"
            out.append(f"Fix the problem shown under {where} to continue.")
        if self.saving:
            out.append("Saving…")  # the progress bar says what is happening
        return out

    def _update_button(self) -> None:
        reasons = self.blockers()
        self.save_button.setEnabled(not reasons)
        self.status.setText(" ".join(reasons))
        self.status.setVisible(bool(reasons) and not self.saving)

    def _enter(self) -> None:
        if self.save_button.isEnabled():
            self.save_button.click()

    def _escape(self) -> None:
        if self.saving:
            self._cancel_save()
        else:
            self._redraw.stop()
            self.cancelled.emit()

    # ------------------------------------------------------------ owner password

    def _ask_owner_password(self) -> None:
        problem = ""
        while True:
            pw = self.services.ask_password(self, visible(self.path.name), problem, True)
            if pw is None:
                return
            try:
                info = self.engine_inspect_sync(pw)
            except Exception as e:  # WrongPassword and friends: say so, ask again
                problem = str(e)
                continue
            if info.restricted:
                problem = "That password opens the file but doesn't lift its restrictions."
                continue
            self.password = pw
            self.info = info
            if self.restriction_tick is not None:
                self.restriction_tick.setChecked(True)
                self.restriction_tick.setText("Owner password given; restrictions lifted")
                self.restriction_tick.setEnabled(False)
            if self.owner_button is not None:
                self.owner_button.hide()
            self._changed(immediate=True)
            return

    def engine_inspect_sync(self, password: str) -> DocumentInfo:
        """Inspect on the engine thread and wait for the answer (used by the owner-password prompt)."""
        loop = QEventLoop()
        box: dict[str, object] = {}
        req = self.engine.inspect(self.path, password)

        def ok(r: int, info: DocumentInfo) -> None:
            if r == req:
                box["info"] = info
                loop.quit()

        def bad(r: int, failure: EngineFailure) -> None:
            if r == req:
                box["error"] = failure.error
                loop.quit()

        self.engine.inspected.connect(ok)
        self.engine.inspect_failed.connect(bad)
        try:
            loop.exec()
        finally:
            self.engine.inspected.disconnect(ok)
            self.engine.inspect_failed.disconnect(bad)
        if "error" in box:
            raise box["error"]  # type: ignore[misc]
        return box["info"]  # type: ignore[return-value]

    # ------------------------------------------------------------ saving

    def _refuse(self, message: str) -> None:
        self._save_error = message
        self.save_error.setText(message)
        self.save_error.show()

    def check_output(self, out: Path) -> str:
        """Why the window won't save to ``out``, or an empty string (D-008, D-075)."""
        if same_file(self.path, out):
            return ("That's the original file. The stamped copy is always saved as a new file; "
                    "choose a different name or folder.")
        if out.exists():
            return (f"{visible(out.name)} already exists. Choose another name; the window never replaces "
                    "an existing file.")
        if not out.parent.is_dir():
            return f"The folder {out.parent} doesn't exist."
        return ""

    def _save(self) -> None:
        if not self.save_button.isEnabled():
            return
        suggested = self._save_folder() / self.suggested_name()
        out = self.services.ask_save_path(self, suggested)
        if out is None:
            return
        problem = self.check_output(out)
        if problem:
            self._refuse(problem)
            return
        self.start_save(out)

    def start_save(self, out: Path) -> None:
        """Start stamping to ``out`` on the engine thread (no checks; ``_save`` does them)."""
        self.saving = True
        self._redraw.stop()
        self.form.setEnabled(False)
        self.prev_button.setEnabled(False)
        self.next_button.setEnabled(False)
        self.save_error.hide()
        self.progress.setRange(0, self.info.page_count)
        self.progress.setValue(0)
        self.progress_label.setText(self._progress_text(0, self.info.page_count))
        self.progress_row.show()
        self.cancel_button.setEnabled(True)
        self._update_button()
        accept_signature, accept_restrictions = self._accept_flags()
        self.engine.apply(ApplyRequest(
            path=self.path, job=self.job(), out_path=out, password=self.password,
            accept_signature_loss=accept_signature,
            accept_restrictions=accept_restrictions,
            expect=self.info.fingerprint,
        ))

    def _cancel_save(self) -> None:
        if self.saving and self.cancel_button.isEnabled():
            self.progress_label.setText("Cancelling…")
            self.engine.cancel()

    def _on_progress(self, done: int, total: int) -> None:
        if not self.saving:
            return
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        if done >= total:
            # Past the last cancel check that matters; the save itself can't be stopped.
            self.progress_label.setText("Saving…")
            self.cancel_button.setEnabled(False)
        else:
            self.progress_label.setText(self._progress_text(done, total))

    def _end_save(self) -> None:
        self.saving = False
        self.form.setEnabled(True)
        self.progress_row.hide()
        self.cancel_button.setEnabled(True)
        self._update_page_label()
        self._update_button()

    def _on_applied(self, result: ApplyResult) -> None:
        if not self.saving:
            return
        self._end_save()
        self.saved.emit(result)

    def _on_apply_failed(self, failure: EngineFailure) -> None:
        if not self.saving:
            return
        self._end_save()
        if isinstance(failure.error, Cancelled):
            self._refuse("Stamping was cancelled. Nothing was saved.")
        else:
            self._refuse(failure.message)
        if isinstance(failure.error, StampDoesNotFit):
            self._changed(immediate=True)

    # ------------------------------------------------------------ Qt

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt name
        """Redraw the preview at the new size (debounced)."""
        super().resizeEvent(event)
        if not self.saving and self._preview_req:
            self._resize_timer.start()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt name
        """The mockup's 1200 × 740 working area."""
        return QSize(1200, 740)
