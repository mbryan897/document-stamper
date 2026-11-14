"""Profile settings: edit config.json through a form (Phase 4, D-082 to D-086).

Four sections, picked on the left:

- **Profiles**: name, default, the fields on the stamp dialog and their order,
  the stamps (text with an Insert menu, a live example line and a preview on a
  blank Letter page), the file name and document properties.
- **Fields**: label, type, required, clean-up steps, validation rules with a
  plain-English explanation and help, and test boxes for a value and a file
  name.
- **Lists**: the choices for choice fields.
- **General**: the date format, which config file this is, and the Explorer
  Send to shortcut.

Edits go to a ``ConfigDraft``; every change re-runs the loader's checks and
lists the problems under the form. "Save changes" stays disabled while there
are problems, and writes the file safely with a ``.bak`` (D-080). A file
that can't be written is read-only here: Export still works. Cancel discards.

Every engine call (the preview) goes through the engine thread (D-070).
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QResizeEvent, QShortcut, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import sendto
from ..core.config import ANCHORS, Config, ConfigError, Profile, load_config
from ..core.fields import MAX_VALUE_LENGTH, validate_field, value_from_filename
from ..core.filenames import suggest_filename, visible
from ..core.locations import ConfigSource, Locations, SourceKind, describe, save_config, save_target
from ..core.paths import same_file
from ..core.patterns import PatternError, explain, parse_date_format, regex_risk
from ..core.template import BUILTIN_TOKENS, TemplateError, builtin_values, render
from ..handlers.base import StampJob
from ..handlers.pdf import write_sample_page
from .color_picker import ColorButton, light_color_note
from .config_draft import ConfigDraft, DraftRefused, diff_summary, sample_values
from .engine import EngineClient, EngineFailure
from .help import help_button
from .services import Services
from .wrap_label import WrapAnywhereLabel

__all__ = ["PATTERN_HELP", "ProfileSettings", "SECTIONS", "SECTION_HELP", "plain_problem"]

SECTIONS = ("Profiles", "Fields", "Lists", "General")
SECTION_HELP = ("profiles-and-stamps", "fields-and-lists", "fields-and-lists#lists", "where-the-config-lives")
PREVIEW_DEBOUNCE_MS = 150
PREVIEW_W = 220
MAX_PROBLEMS = 8
PROBLEM_LINES = 6  # footer lines before the problem list scrolls (D-093)
STATUS_LINES = 4
SAMPLE_ORIGINAL = "provider_return.pdf"
MAX_NAME_SHOWN = 80  # a name from config in a footer message

_ANCHOR_LABELS = {a: a.replace("-", " ").capitalize() for a in ANCHORS}
_DIRECTIONS = (("along-top", "Along top edge"), ("along-right", "Along right edge"))
_TYPES = (("text", "Text"), ("choice", "Choice from a list"), ("date", "Date"))
_NORMALIZERS = (
    ("trim", "Remove spaces at the start and end"),
    ("upper", "Make letters uppercase"),
    ("dashes", "Turn look-alike dashes (– — −) into -"),
)
_BUILTIN_LABELS = {
    "page": "Page number",
    "total": "Number of pages",
    "original_name": "Original file name",
    "date": "Today's date",
}

PATTERN_HELP = """<p>Write the rule the way the value looks:</p>
<table cellspacing="0" cellpadding="3">
<tr><td><b>#</b></td><td>one digit 0–9</td></tr>
<tr><td><b>@</b></td><td>one letter A–Z</td></tr>
<tr><td><b>?</b></td><td>one letter or digit</td></tr>
<tr><td><b>*</b></td><td>any text, at least one character (at most two * in a rule)</td></tr>
<tr><td><b>YYYY</b> or <b>YY</b></td><td>a 4- or 2-digit year (YY is 20YY)</td></tr>
<tr><td><b>MM</b>, <b>DD</b></td><td>month and day; together they must be a real date</td></tr>
<tr><td><b>\\</b>x</td><td>the character x itself, for example <b>\\#</b></td></tr>
<tr><td>anything else</td><td>itself, exactly</td></tr>
</table>
<p>Example: <b>SOYYMMDD-######</b> accepts SO261017-000412.
Letters compare exactly, so keep "Make letters uppercase" ticked for rules with letters.</p>"""


_PLAIN = (
    (re.compile(r'profiles\[\d+\] "([^"]*)": '), r'Profile "\1": '),
    (re.compile(r"stamps\[(\d+)\]"), lambda m: f"stamp {int(m.group(1)) + 1}"),
    (re.compile(r"\boutput_name\.fields\b"), "file name"),
    (re.compile(r"\boutput_name\.separator\b"), "file name separator"),
    (re.compile(r"\bsettings\.date_format\b"), "date format"),
    (re.compile(r"\bmargin_in\b"), "margin"),
    (re.compile(r"\bfont_size_pt\b"), "text size"),
    (re.compile(r"^fields\.(\w+)"), r"Field \1"),
    (re.compile(r"^taxonomies\.(.+?):"), r'List "\1":'),
)


def plain_problem(problem: str) -> str:
    """The loader's sentence with its file locations in plain words.

    ``profiles[0] "Records": stamps[0] text uses {x} …`` becomes
    ``Profile "Records": stamp 1 text uses {x} …``.
    """
    for rx, repl in _PLAIN:
        problem = rx.sub(repl, problem)  # type: ignore[arg-type]
    problem = re.sub(r"(stamp \d+)\.", r"\1 ", problem)
    return problem[:1].upper() + problem[1:]


# ------------------------------------------------------------------ small widgets


def _label(text: str = "", name: str = "", *, wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(wrap)
    if name:
        lab.setObjectName(name)
    return lab


def _shown(name: str) -> str:
    """A name from config for a footer message: hidden characters shown, length capped."""
    name = visible(name)
    return name if len(name) <= MAX_NAME_SHOWN else name[:MAX_NAME_SHOWN - 1] + "…"


def _section(text: str) -> QLabel:
    return _label(text, "sectionLabel")


def _small(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("small")
    return b


def _restyle(widget: QWidget, name: str) -> None:
    widget.setObjectName(name)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _clear(layout: QVBoxLayout | QHBoxLayout | QGridLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())  # type: ignore[arg-type]


class OrderedChecklist(QWidget):
    """Tick boxes in an order the user can change; emits the ticked keys in order."""

    changed = Signal(list)

    def __init__(self, items: Sequence[tuple[str, str]], ticked: Sequence[str], name: str) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setAccessibleName(name)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        labels = dict(items)
        order = [k for k in ticked if k in labels] + [k for k, _ in items if k not in ticked]
        for key in order:
            it = QListWidgetItem(labels[key])
            it.setData(Qt.ItemDataRole.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if key in ticked else Qt.CheckState.Unchecked)
            self.list.addItem(it)
        rows = max(2, min(len(order), 7))
        self.list.setFixedHeight(rows * 30 + 8)
        self.list.itemChanged.connect(lambda _i: self._emit())
        lay.addWidget(self.list, 1)
        col = QVBoxLayout()
        self.up = _small("Up")
        self.down = _small("Down")
        self.up.setAccessibleName(f"Move up in {name}")
        self.down.setAccessibleName(f"Move down in {name}")
        self.up.clicked.connect(lambda: self._move(-1))
        self.down.clicked.connect(lambda: self._move(1))
        col.addWidget(self.up)
        col.addWidget(self.down)
        col.addStretch(1)
        lay.addLayout(col)

    def ticked(self) -> list[str]:
        """The ticked keys, top to bottom."""
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]

    def set_ticked(self, key: str, on: bool) -> None:
        """Tick or untick one key (tests and keyboard)."""
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == key:
                it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def _move(self, step: int) -> None:
        row = self.list.currentRow()
        new = row + step
        if row < 0 or not 0 <= new < self.list.count():
            return
        self.list.blockSignals(True)
        it = self.list.takeItem(row)
        self.list.insertItem(new, it)
        self.list.setCurrentRow(new)
        self.list.blockSignals(False)
        self._emit()

    def _emit(self) -> None:
        self.changed.emit(self.ticked())


class _MasterList(QWidget):
    """The list on the left of a page, with buttons under it."""

    selected = Signal(int)

    def __init__(self, name: str, buttons: Sequence[tuple[str, Callable[[], None]]]) -> None:
        super().__init__()
        self.setFixedWidth(210)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setAccessibleName(name)
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(self.selected.emit)
        lay.addWidget(self.list, 1)
        grid = QGridLayout()
        grid.setSpacing(6)
        self.buttons: dict[str, QPushButton] = {}
        for i, (text, slot) in enumerate(buttons):
            b = _small(text)
            b.clicked.connect(slot)
            self.buttons[text] = b
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)

    def fill(self, labels: Sequence[str], current: int) -> None:
        """Replace the rows, keeping ``current`` selected."""
        self.list.blockSignals(True)
        self.list.clear()
        self.list.addItems(list(labels))
        self.list.blockSignals(False)
        if labels:
            self.list.setCurrentRow(max(0, min(current, len(labels) - 1)))

    def set_text(self, row: int, text: str) -> None:
        """Change one row's text without changing the selection."""
        it = self.list.item(row)
        if it is not None:
            it.setText(text)


class _Combo(QComboBox):
    """A combo box the mouse wheel doesn't change unless it has focus (UX-003).

    Otherwise the wheel goes to the page, so scrolling past a combo scrolls
    instead of changing a setting.
    """

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt name
        """Change the value only when focused; otherwise let the page scroll."""
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


def _combo() -> QComboBox:
    """A combo box that can shrink with the form instead of forcing it wider."""
    box = _Combo()
    box.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # the wheel doesn't give it focus
    box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    box.setMinimumContentsLength(8)
    return box


def _scroll(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setObjectName("surface")
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(widget)
    return area


class _BoundedText(QScrollArea):
    """A word-wrapped label that grows with its text up to ``max_lines``, then scrolls.

    Used above the footer buttons, so a long list of problems can't push
    "Save changes" off the window (D-093).
    """

    def __init__(self, label: QLabel, max_lines: int) -> None:
        super().__init__()
        self.label = label
        self.max_lines = max_lines
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.viewport().setAutoFillBackground(False)
        label.setAutoFillBackground(False)
        self.setStyleSheet("QScrollArea { background: transparent; }"
                           " QScrollArea > QWidget > QWidget { background: transparent; }")
        self.setWidget(label)

    def _height(self) -> int:
        if not self.label.text():
            return 0
        cap = self.label.fontMetrics().lineSpacing() * self.max_lines + 4
        return min(self.label.heightForWidth(max(self.viewport().width(), 120)), cap)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt name
        """As tall as the text, up to ``max_lines``."""
        return QSize(super().sizeHint().width(), self._height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt name
        """No minimum width; the same height as ``sizeHint``, so the cap holds."""
        return QSize(0, self._height())

    def refresh(self) -> None:
        """Re-measure after the text changed."""
        self.updateGeometry()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt name
        """A new width wraps the text differently."""
        super().resizeEvent(event)
        self.updateGeometry()


# ------------------------------------------------------------------ pages


class _Page(QWidget):
    def __init__(self, owner: ProfileSettings) -> None:
        super().__init__()
        self.owner = owner
        self.setObjectName("surface")

    @property
    def draft(self) -> ConfigDraft:
        return self.owner.draft

    def rebuild(self) -> None:
        """Rebuild from the draft (after a structural change, or when shown)."""

    def refresh_live(self) -> None:
        """Update examples and other derived text after any edit."""


class StampEditor(QFrame):
    """One stamp of a profile."""

    def __init__(self, page: ProfilesPage, profile: int, index: int) -> None:
        super().__init__()
        self.setObjectName("card")
        self.page, self.profile, self.index = page, profile, index
        data = page.draft.profiles[profile]["stamps"][index]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(_label(f"Stamp {index + 1}", "fieldLabel"), 1)
        self.remove = _small("Remove stamp")
        self.remove.clicked.connect(self._remove)
        head.addWidget(self.remove)
        lay.addLayout(head)

        row = QHBoxLayout()
        self.text = QLineEdit(data.get("text", ""))
        self.text.setObjectName("code")
        self.text.setAccessibleName(f"Stamp {index + 1} text")
        self.text.setCursorPosition(0)
        self.text.textEdited.connect(self._text_edited)
        row.addWidget(self.text, 1)
        self.insert = QToolButton()
        self.insert.setText("Insert…")
        self.insert.setObjectName("insert")
        self.insert.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.insert.setMenu(self._insert_menu())
        self.insert.setAccessibleName(f"Insert a field into stamp {index + 1}")
        row.addWidget(self.insert)
        lay.addLayout(row)
        lay.addWidget(_label(
            "Put [ ] around text that should disappear when a field inside it is empty, "
            "for example [Title: {doc_title}].", "help"))
        self.example = _label("", "mono")
        self.example.setAccessibleName(f"Stamp {index + 1} example")
        lay.addWidget(self.example)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        self.anchor = _combo()
        for a in ANCHORS:
            self.anchor.addItem(_ANCHOR_LABELS[a], a)
        self.anchor.setCurrentIndex(max(0, self.anchor.findData(data.get("anchor", "top-right"))))
        self.anchor.currentIndexChanged.connect(lambda _i: self._set("anchor", self.anchor.currentData()))
        self.direction = _combo()
        for v, t in _DIRECTIONS:
            self.direction.addItem(t, v)
        self.direction.setCurrentIndex(max(0, self.direction.findData(data.get("direction", "along-top"))))
        self.direction.currentIndexChanged.connect(
            lambda _i: self._set("direction", self.direction.currentData()))
        self.margin = QDoubleSpinBox()
        self.margin.setRange(0.25, 3.0)
        self.margin.setSingleStep(0.05)
        self.margin.setDecimals(2)
        self.margin.setSuffix(" in")
        self.margin.setValue(float(data.get("margin_in", 0.25)))
        self.margin.valueChanged.connect(lambda v: self._set("margin_in", round(v, 2)))
        self.size = QDoubleSpinBox()
        self.size.setRange(6.0, 24.0)
        self.size.setSingleStep(0.5)
        self.size.setDecimals(1)
        self.size.setSuffix(" pt")
        self.size.setValue(float(data.get("font_size_pt", 9.0)))
        self.size.valueChanged.connect(lambda v: self._set("font_size_pt", round(v, 1)))
        self.color = ColorButton(data.get("color", "#2B3A8F"), page.owner.services.ask_color)
        self.color.changed.connect(self._color_changed)
        self.box = QCheckBox("White box behind the stamp")
        self.box.setChecked(bool(data.get("background_box", True)))
        self.box.toggled.connect(lambda on: self._set("background_box", on))
        for r, (text, w) in enumerate((("Position", self.anchor), ("Direction", self.direction),
                                        ("Margin", self.margin), ("Text size", self.size),
                                        ("Color", self.color))):
            lab = _label(text, "muted", wrap=False)
            lab.setBuddy(w)
            w.setAccessibleName(f"Stamp {index + 1} {text.lower()}")
            grid.addWidget(lab, r // 2, (r % 2) * 2)
            grid.addWidget(w, r // 2, (r % 2) * 2 + 1)
        grid.addWidget(self.box, 2, 2, 1, 2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        lay.addLayout(grid)
        self.color_note = _label("", "msgBad")
        lay.addWidget(self.color_note)
        self._show_color_note()

    def _insert_menu(self) -> QMenu:
        menu = QMenu(self)
        prof = self.page.draft.profiles[self.profile]
        for key in prof.get("fields", []):
            menu.addAction(f"{self.page.draft.label(key)}  {{{key}}}", lambda k=key: self._insert(f"{{{k}}}"))
        menu.addSeparator()
        for key in BUILTIN_TOKENS:
            menu.addAction(f"{_BUILTIN_LABELS[key]}  {{{key}}}", lambda k=key: self._insert(f"{{{k}}}"))
        menu.addSeparator()
        menu.addAction("Optional part  [ ]", lambda: self._insert("[]", back=1))
        return menu

    def _insert(self, text: str, back: int = 0) -> None:
        self.text.insert(text)
        if back:
            self.text.setCursorPosition(self.text.cursorPosition() - back)
        self.text.setFocus()
        self._text_edited(self.text.text())

    def _color_changed(self, hex_color: str) -> None:
        self._set("color", hex_color)
        self._show_color_note()

    def _show_color_note(self) -> None:
        note = light_color_note(self.color.color())
        self.color_note.setText(note)
        self.color_note.setVisible(bool(note))

    def _text_edited(self, text: str) -> None:
        self._set("text", text, debounce=True)

    def _set(self, key: str, value: Any, *, debounce: bool = False) -> None:
        self.page.draft.profiles[self.profile]["stamps"][self.index][key] = value
        self.page.owner.changed(debounce=debounce)

    def _remove(self) -> None:
        try:
            self.page.draft.snapshot(f"stamp {self.index + 1}")
            self.page.draft.remove_stamp(self.profile, self.index)
        except DraftRefused as e:
            self.page.owner.say(str(e))
            return
        self.page.rebuild_detail()
        self.page.owner.changed()
        self.page.owner.say(f"Removed stamp {self.index + 1}.", undo=True)

    def refresh_example(self, config: Config | None, profile: Profile | None) -> None:
        """Show the stamp text as it would print on page 1 of 3, with made-up values."""
        if config is None or profile is None:
            self.example.setText("")
            self.example.hide()
            return
        text = self.page.draft.profiles[self.profile]["stamps"][self.index].get("text", "")
        values = {**sample_values(config, profile, self.page.owner.today),
                  **builtin_values(page=1, total=3, original_name=Path(SAMPLE_ORIGINAL).stem,
                                   today=self.page.owner.today, date_format=config.settings.date_formatter)}
        try:
            out = render(text, values)
        except (TemplateError, KeyError):
            out = ""
        self.example.setText(f"Example: {out}" if out else "Example: (nothing is printed)")
        self.example.show()


class ProfilesPage(_Page):
    """Profiles: fields, stamps, file name and document properties."""

    def __init__(self, owner: ProfileSettings) -> None:
        super().__init__(owner)
        self.current = 0
        self.stamp_editors: list[StampEditor] = []
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(20)
        self.master = _MasterList("Profiles", [
            ("Add", self._add), ("Duplicate", self._duplicate),
            ("Delete", self._delete), ("Make default", self._make_default),
        ])
        self.master.selected.connect(self._select)
        outer.addWidget(self.master)

        self.detail = QWidget()
        self.detail.setObjectName("surface")
        self.form = QVBoxLayout(self.detail)
        self.form.setContentsMargins(0, 0, 8, 0)
        self.form.setSpacing(14)
        outer.addWidget(_scroll(self.detail), 1)

        side = QVBoxLayout()
        side.addWidget(_section("Preview"))
        self.preview = QLabel()
        self.preview.setObjectName("thumbnail")
        self.preview.setFixedSize(PREVIEW_W, int(PREVIEW_W * 11 / 8.5))
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setAccessibleName("Preview of the stamps on a blank page")
        side.addWidget(self.preview)
        self.preview_note = _label("A blank Letter page, with made-up values.", "help")
        self.preview_note.setFixedWidth(PREVIEW_W)
        side.addWidget(self.preview_note)
        side.addStretch(1)
        outer.addLayout(side)

    # ---------------------------------------------------------- master list

    def _labels(self) -> list[str]:
        return [p["name"] + ("  (default)" if p.get("default") else "") for p in self.draft.profiles]

    def rebuild(self) -> None:
        self.current = min(self.current, len(self.draft.profiles) - 1)
        self.master.fill(self._labels(), self.current)
        self.rebuild_detail()

    def _select(self, row: int) -> None:
        if row >= 0 and row != self.current:
            self.current = row
            self.rebuild_detail()
            self.owner.request_preview()

    def _structure(self, select: int) -> None:
        self.current = select
        self.rebuild()
        self.owner.changed()

    def _add(self) -> None:
        self._structure(self.draft.add_profile())

    def _duplicate(self) -> None:
        self._structure(self.draft.duplicate_profile(self.current))

    def _delete(self) -> None:
        name = _shown(self.draft.profiles[self.current]["name"])
        try:
            self.draft.snapshot(f'"{name}"')
            self.draft.delete_profile(self.current)
        except DraftRefused as e:
            self.owner.say(str(e))
            return
        self._structure(max(0, self.current - 1))
        self.owner.say(f'Deleted the profile "{name}".', undo=True)

    def _make_default(self) -> None:
        self.draft.set_default(self.current)
        self.master.fill(self._labels(), self.current)
        self.owner.changed()

    # ---------------------------------------------------------- detail

    @property
    def prof(self) -> dict[str, Any]:
        return self.draft.profiles[self.current]

    def rebuild_detail(self) -> None:
        """Rebuild the form for the selected profile."""
        _clear(self.form)
        self.stamp_editors = []
        p = self.prof
        self.name = QLineEdit(p.get("name", ""))
        self.name.setAccessibleName("Profile name")
        self.name.textEdited.connect(self._rename)
        name_label = _label("Name", "fieldLabel")
        name_label.setBuddy(self.name)
        self.form.addWidget(name_label)
        self.form.addWidget(self.name)

        self.form.addWidget(_section("Fields on the stamp dialog"))
        self.form.addWidget(_label("Tick the fields to ask for, in the order shown.", "help"))
        items = [(k, self.draft.label(k)) for k in self.draft.fields]
        self.fields = OrderedChecklist(items, p.get("fields", []), "Fields on the stamp dialog")
        self.fields.changed.connect(self._fields_changed)
        self.form.addWidget(self.fields)

        self.form.addWidget(_section("Stamps"))
        for j in range(len(p.get("stamps", []))):
            ed = StampEditor(self, self.current, j)
            self.stamp_editors.append(ed)
            self.form.addWidget(ed)
        add = _small("Add a stamp")
        add.clicked.connect(self._add_stamp)
        self.form.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)

        self.form.addWidget(_section("File name"))
        self.form.addWidget(_label("Ticked by default in the stamp dialog's File name selector.", "help"))
        on = p.get("output_name", {})
        self.name_fields = self._name_checklist()
        self.form.addWidget(self.name_fields)
        row = QHBoxLayout()
        sep_label = _label("Separator", "muted", wrap=False)
        self.separator = QLineEdit(on.get("separator", "_"))
        self.separator.setObjectName("code")
        self.separator.setMaximumWidth(80)
        self.separator.setAccessibleName("File name separator")
        sep_label.setBuddy(self.separator)
        self.separator.textEdited.connect(lambda t: self._set_output("separator", t))
        self.include_original = QCheckBox("Add the original file name")
        self.include_original.setChecked(bool(on.get("include_original_name", True)))
        self.include_original.toggled.connect(lambda v: self._set_output("include_original_name", v))
        row.addWidget(sep_label)
        row.addWidget(self.separator)
        row.addSpacing(16)
        row.addWidget(self.include_original)
        row.addStretch(1)
        self.form.addLayout(row)
        self.name_example = WrapAnywhereLabel()  # D-137
        self.name_example.setObjectName("mono")
        self.name_example.setAccessibleName("Example file name")
        self.form.addWidget(self.name_example)

        self.form.addWidget(_section("Document properties"))
        self.form.addWidget(_label(
            "Written into the PDF's properties so other systems can index the copy. "
            "Names use letters, digits and underscores.", "help"))
        self.meta_rows = QVBoxLayout()
        self.meta_rows.setSpacing(6)
        self.form.addLayout(self.meta_rows)
        self.meta: list[tuple[QLineEdit, QLineEdit]] = []
        for k, v in p.get("metadata", {}).items():
            self._add_meta_row(k, v)
        add_meta = _small("Add a property")
        add_meta.clicked.connect(lambda: (self._add_meta_row("", ""), self._meta_changed()))
        self.form.addWidget(add_meta, 0, Qt.AlignmentFlag.AlignLeft)
        self.form.addStretch(1)
        self.refresh_live()

    def _rename(self, text: str) -> None:
        self.prof["name"] = text
        self.master.set_text(self.current, self._labels()[self.current])
        self.owner.changed(debounce=True)

    def _fields_changed(self, keys: list[str]) -> None:
        self.prof["fields"] = keys
        # A field taken off the profile can't stay in its file name.
        on = self.prof.setdefault("output_name", {})
        on["fields"] = [f for f in on.get("fields", []) if f in keys or f == "date"]
        # The file-name list and the Insert menus follow the profile's fields.
        old = self.name_fields
        self.name_fields = self._name_checklist()
        self.form.replaceWidget(old, self.name_fields)
        old.hide()
        old.deleteLater()
        for ed in self.stamp_editors:
            ed.insert.setMenu(ed._insert_menu())
        self.owner.changed()

    def _name_checklist(self) -> OrderedChecklist:
        p = self.prof
        items = [(k, self.draft.label(k)) for k in p.get("fields", [])] + [("date", "Today's date")]
        box = OrderedChecklist(items, p.get("output_name", {}).get("fields", []), "File name fields")
        box.changed.connect(lambda keys: self._set_output("fields", keys))
        return box

    def _add_stamp(self) -> None:
        self.draft.add_stamp(self.current)
        self.rebuild_detail()
        self.owner.changed()

    def _set_output(self, key: str, value: Any) -> None:
        self.prof.setdefault("output_name", {})[key] = value
        self.owner.changed(debounce=key == "separator")

    def _add_meta_row(self, key: str, value: str) -> None:
        row = QHBoxLayout()
        k = QLineEdit(key)
        k.setPlaceholderText("Name")
        k.setAccessibleName("Property name")
        v = QLineEdit(value)
        v.setObjectName("code")
        v.setPlaceholderText("Value, e.g. {identifier}")
        v.setAccessibleName("Property value")
        rm = _small("Remove")
        pair = (k, v)
        k.textEdited.connect(lambda _t: self._meta_changed())
        v.textEdited.connect(lambda _t: self._meta_changed())

        def remove() -> None:
            name = _shown(k.text().strip())
            if name:  # an unnamed row isn't in the draft, so there's nothing to undo
                self.draft.snapshot(f'"{name}"')
            self.meta.remove(pair)
            for w in (k, v, rm):
                w.hide()
                w.deleteLater()
            self._meta_changed()
            if name:
                self.owner.say(f'Removed the property "{name}".', undo=True)

        rm.clicked.connect(remove)
        row.addWidget(k, 2)
        row.addWidget(v, 3)
        row.addWidget(rm)
        self.meta_rows.addLayout(row)
        self.meta.append(pair)

    def _meta_changed(self) -> None:
        out: dict[str, str] = {}
        dupes: list[str] = []
        for k, v in self.meta:
            key = k.text().strip()
            if not key:
                continue
            if key in out:
                dupes.append(key)
            out[key] = v.text()
        self.prof["metadata"] = out
        self.owner.set_local_problem(
            "metadata", f'Document property "{dupes[0]}" is listed twice; give each a different name.'
            if dupes else "")
        self.owner.changed(debounce=True)

    def refresh_live(self) -> None:
        config = self.owner.valid_config
        profile = None
        if config is not None:
            try:
                profile = config.profile(self.prof["name"])
            except KeyError:
                profile = None
        for ed in self.stamp_editors:
            ed.refresh_example(config, profile)
        if hasattr(self, "name_example"):
            if config is not None and profile is not None:
                vals = {**sample_values(config, profile, self.owner.today),
                        "date": config.settings.date_formatter.format(self.owner.today)}
                name = suggest_filename(profile, vals, SAMPLE_ORIGINAL)
                self.name_example.setText(f"Example: {name}")
            else:
                self.name_example.setText("")

    def selected_profile(self) -> Profile | None:
        """The validated profile shown, or None while there are problems."""
        config = self.owner.valid_config
        if config is None:
            return None
        try:
            return config.profile(self.prof["name"])
        except KeyError:
            return None

    def show_preview(self, png: bytes | None, message: str = "") -> None:
        """Show the preview image, or a message where it would be."""
        if png is None:
            self.preview.clear()
            self.preview.setText(message)
            self.preview.setWordWrap(True)
            return
        pix = QPixmap()
        pix.loadFromData(png, "PNG")
        ratio = self.devicePixelRatioF() or 1.0
        pix.setDevicePixelRatio(ratio)
        self.preview.setPixmap(pix)


class FormatEditor(QFrame):
    """One validation rule of a text field."""

    def __init__(self, page: FieldsPage, key: str, index: int) -> None:
        super().__init__()
        self.setObjectName("card")
        self.page, self.key, self.index = page, key, index
        fmt = page.draft.fields[key]["formats"][index]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(_label(f"Rule {index + 1}", "fieldLabel"), 1)
        rm = _small("Remove rule")
        rm.clicked.connect(self._remove)
        head.addWidget(rm)
        lay.addLayout(head)
        self.advanced = fmt.get("regex") is not None
        self.rule = QLineEdit(fmt.get("regex") if self.advanced else (fmt.get("pattern") or ""))
        self.rule.setObjectName("code")
        self.rule.setAccessibleName(f"Rule {index + 1}")
        self.rule.setPlaceholderText("For example SOYYMMDD-######")
        self.rule.textEdited.connect(lambda _t: self._write())
        lay.addWidget(self.rule)
        self.explanation = _label("", "msgNeutral")
        lay.addWidget(self.explanation)
        row = QHBoxLayout()
        name_label = _label("Name shown to users (optional)", "muted", wrap=False)
        self.name = QLineEdit(fmt.get("name") or "")
        self.name.setAccessibleName(f"Rule {index + 1} name")
        name_label.setBuddy(self.name)
        self.name.textEdited.connect(lambda _t: self._write())
        row.addWidget(name_label)
        row.addWidget(self.name, 1)
        lay.addLayout(row)
        self.regex_box = QCheckBox("Advanced: use a regular expression instead")
        self.regex_box.setChecked(self.advanced)
        self.regex_box.toggled.connect(self._toggle_regex)
        lay.addWidget(self.regex_box)
        self._explain()

    def _write(self) -> None:
        text = self.rule.text()
        name = self.name.text().strip() or None
        fmt: dict[str, Any] = {"name": name, "pattern": None, "regex": None}
        fmt["regex" if self.advanced else "pattern"] = text
        self.page.draft.fields[self.key]["formats"][self.index] = fmt
        self._explain()
        self.page.owner.changed(debounce=True)

    def _toggle_regex(self, on: bool) -> None:
        self.advanced = on
        self._write()

    def _explain(self) -> None:
        text = self.rule.text()
        if self.advanced:
            problem = _regex_problem(text)
            if problem:
                self.explanation.setText(problem)
                _restyle(self.explanation, "msgBad")
                return
            self.explanation.setText(
                "Regular expression (ASCII); it must match the whole value. Dates aren't checked, "
                "and it isn't used to fill in from the file name.")
            _restyle(self.explanation, "msgNeutral")
            return
        if not text:
            self.explanation.setText("Write a rule, for example ##-YYYY-######.")
            _restyle(self.explanation, "msgNeutral")
            return
        try:
            self.explanation.setText(explain(text))
            _restyle(self.explanation, "msgOk")
        except PatternError as e:
            self.explanation.setText(f"This rule can't be read: {e}.")
            _restyle(self.explanation, "msgBad")

    def _remove(self) -> None:
        self.page.draft.snapshot(f"rule {self.index + 1}")
        del self.page.draft.fields[self.key]["formats"][self.index]
        self.page.rebuild_detail()
        self.page.owner.changed()
        self.page.owner.say(
            f'Removed rule {self.index + 1} from "{_shown(self.page.draft.label(self.key))}".', undo=True)


def _regex_problem(text: str) -> str:
    """Why an advanced rule can't be used, said before it is ever run (D-118); empty if it can."""
    if not text:
        return ""
    try:
        risk = regex_risk(text)
    except re.error as e:
        return f"This regular expression doesn't compile: {e}."
    if risk:
        return (f"This rule can take a very long time to check ({risk}), so it can't be saved. "
                "Rewrite it without that, or use a simple pattern.")
    return ""


class FieldsPage(_Page):
    """Fields: label, type, rules and test boxes."""

    def __init__(self, owner: ProfileSettings) -> None:
        super().__init__(owner)
        self.current = 0
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(20)
        self.master = _MasterList("Fields", [("Add field", self._add), ("Delete", self._delete)])
        self.master.selected.connect(self._select)
        outer.addWidget(self.master)
        self.detail = QWidget()
        self.detail.setObjectName("surface")
        self.form = QVBoxLayout(self.detail)
        self.form.setContentsMargins(0, 0, 8, 0)
        self.form.setSpacing(12)
        outer.addWidget(_scroll(self.detail), 1)
        self.try_value: QLineEdit | None = None
        self.try_file: QLineEdit | None = None

    @property
    def keys(self) -> list[str]:
        return list(self.draft.fields)

    @property
    def key(self) -> str:
        return self.keys[self.current]

    def _labels(self) -> list[str]:
        return [self.draft.label(k) for k in self.keys]

    def rebuild(self) -> None:
        self.current = min(self.current, len(self.keys) - 1)
        self.master.fill(self._labels(), self.current)
        self.rebuild_detail()

    def select_key(self, key: str) -> None:
        """Show one field (tests and links)."""
        self.current = self.keys.index(key)
        self.rebuild()

    def _select(self, row: int) -> None:
        if row >= 0 and row != self.current:
            self.current = row
            self.rebuild_detail()

    def _add(self) -> None:
        key = self.draft.add_field()
        self.current = self.keys.index(key)
        self.rebuild()
        self.owner.changed()
        self.label_edit.setFocus()
        self.label_edit.selectAll()

    def _delete(self) -> None:
        label = _shown(self.draft.label(self.key))
        try:
            self.draft.snapshot(f'"{label}"')
            self.draft.delete_field(self.key)
        except DraftRefused as e:
            self.owner.say(str(e))
            return
        self.current = max(0, self.current - 1)
        self.rebuild()
        self.owner.changed()
        self.owner.say(f'Deleted the field "{label}".', undo=True)

    def rebuild_detail(self) -> None:
        """Rebuild the form for the selected field."""
        _clear(self.form)
        self.try_value = self.try_file = None
        key = self.key
        spec = self.draft.fields[key]
        kind = spec.get("type", "text")

        self.label_edit = QLineEdit(spec.get("label", ""))
        self.label_edit.setAccessibleName("Field label")
        self.label_edit.textEdited.connect(self._relabel)
        lab = _label("Label", "fieldLabel")
        lab.setBuddy(self.label_edit)
        self.form.addWidget(lab)
        self.form.addWidget(self.label_edit)
        self.form.addWidget(_label(f"In stamp text and file names this field is {{{key}}}.", "help"))
        used = self.draft.field_usage(key)
        self.form.addWidget(_label(
            f"Used by: {', '.join(used)}." if used else "Not used by any profile yet.", "muted"))

        row = QHBoxLayout()
        type_label = _label("Type", "muted", wrap=False)
        self.type_combo = _combo()
        self.type_combo.setAccessibleName("Field type")
        type_label.setBuddy(self.type_combo)
        for v, t in _TYPES:
            self.type_combo.addItem(t, v)
        self.type_combo.setCurrentIndex(max(0, self.type_combo.findData(kind)))
        self.type_combo.setEnabled(key != "identifier")
        self.type_combo.currentIndexChanged.connect(lambda _i: self._retype(self.type_combo.currentData()))
        row.addWidget(type_label)
        row.addWidget(self.type_combo, 1)
        self.required = QCheckBox("Required")
        self.required.setChecked(bool(spec.get("required")))
        self.required.toggled.connect(lambda v: self._set("required", v))
        row.addSpacing(16)
        row.addWidget(self.required)
        self.form.addLayout(row)
        if key == "identifier":
            self.form.addWidget(_label("The identifier is always a text field.", "help"))

        if kind == "choice":
            row = QHBoxLayout()
            list_label = _label("Choices from", "muted", wrap=False)
            self.taxonomy = _combo()
            self.taxonomy.setAccessibleName("List of choices")
            list_label.setBuddy(self.taxonomy)
            for name in self.draft.taxonomies:
                self.taxonomy.addItem(name, name)
            self.taxonomy.setCurrentIndex(self.taxonomy.findData(spec.get("taxonomy")))
            self.taxonomy.currentIndexChanged.connect(
                lambda _i: self._set("taxonomy", self.taxonomy.currentData()))
            row.addWidget(list_label)
            row.addWidget(self.taxonomy, 1)
            self.form.addLayout(row)
            if not self.draft.taxonomies:
                self.form.addWidget(_label("Add a list under Lists first.", "msgBad"))

        if kind == "date":
            self.form.addWidget(_label(
                f"Entered as {self.draft.data.get('settings', {}).get('date_format', 'MM-DD-YYYY')} "
                "(set under General).", "help"))

        self.form.addWidget(_section("Clean-up before checking"))
        self.normalizers: dict[str, QCheckBox] = {}
        current = spec.get("normalize", [])
        for step, text in _NORMALIZERS:
            box = QCheckBox(text)
            box.setChecked(step in current)
            box.toggled.connect(lambda _v: self._normalize_changed())
            self.normalizers[step] = box
            self.form.addWidget(box)

        if kind == "text":
            self.form.addWidget(_section("Rules"))
            self.form.addWidget(_label(
                "A value must match one of these rules. With no rules, any value is accepted.", "help"))
            self.format_editors = [FormatEditor(self, key, i) for i in range(len(spec.get("formats", [])))]
            for ed in self.format_editors:
                self.form.addWidget(ed)
            row = QHBoxLayout()
            add = _small("Add a rule")
            add.clicked.connect(self._add_format)
            help_btn = QPushButton("How to write a rule")
            help_btn.setObjectName("link")
            help_btn.setCheckable(True)
            more = QPushButton("More in Help")
            more.setObjectName("link")
            more.setCursor(Qt.CursorShape.PointingHandCursor)
            more.clicked.connect(lambda: self.owner.help_requested.emit("validation-rules"))
            self.more_help = more
            row.addWidget(add)
            row.addWidget(help_btn)
            row.addWidget(more)
            row.addStretch(1)
            self.form.addLayout(row)
            self.help = _label(PATTERN_HELP, "help")
            self.help.setTextFormat(Qt.TextFormat.RichText)
            self.help.setVisible(False)
            help_btn.toggled.connect(self.help.setVisible)
            self.form.addWidget(self.help)
            self.from_filename = QCheckBox("Fill in from the file name when it contains a match")
            self.from_filename.setChecked(bool(spec.get("from_filename")))
            self.from_filename.toggled.connect(self._toggle_from_filename)
            self.form.addWidget(self.from_filename)

        self.form.addWidget(_section("Try it"))
        self.form.addWidget(_label("A value, as someone would type it:", "muted"))
        self.try_value = QLineEdit()
        self.try_value.setMaxLength(MAX_VALUE_LENGTH)
        self.try_value.setObjectName("identifier" if kind == "text" else "")
        self.try_value.setPlaceholderText("Type a value to check")
        self.try_value.setAccessibleName("Try a value")
        self.try_value.textEdited.connect(lambda _t: self.refresh_live())
        self.form.addWidget(self.try_value)
        self.try_value_result = _label("", "msgNeutral")
        self.form.addWidget(self.try_value_result)
        if kind == "text" and spec.get("from_filename"):
            self.form.addWidget(_label("A file name, to see what would be filled in:", "muted"))
            self.try_file = QLineEdit()
            self.try_file.setPlaceholderText("Type a file name, e.g. SO261017-000412_return.pdf")
            self.try_file.setAccessibleName("Try a file name")
            self.try_file.textEdited.connect(lambda _t: self.refresh_live())
            self.form.addWidget(self.try_file)
            self.try_file_result = _label("", "msgNeutral")
            self.form.addWidget(self.try_file_result)
        self.form.addStretch(1)
        self.refresh_live()

    def _relabel(self, text: str) -> None:
        self.draft.fields[self.key]["label"] = text
        self.master.set_text(self.current, text or self.key)
        self.owner.changed(debounce=True)

    def _set(self, name: str, value: Any) -> None:
        self.draft.fields[self.key][name] = value
        self.owner.changed()

    def _retype(self, kind: str) -> None:
        spec = self.draft.fields[self.key]
        rules = len(spec.get("formats", [])) if kind != "text" else 0
        fill = kind != "text" and bool(spec.get("from_filename"))
        label = _shown(self.draft.label(self.key))
        if rules or fill:  # only text fields keep these (UX-003)
            self.draft.snapshot(f'"{label}"')
        self.draft.set_field_type(self.key, kind)
        self.rebuild_detail()
        self.owner.changed()
        if rules or fill:
            lost = []
            if rules:
                lost.append(f"{rules} rule was removed." if rules == 1 else f"{rules} rules were removed.")
            if fill:
                lost.append('"Fill in from the file name" was turned off.')
            self.owner.say(f'Changed "{label}" to {dict(_TYPES)[kind]}. ' + " ".join(lost), undo=True)

    def _normalize_changed(self) -> None:
        self.draft.fields[self.key]["normalize"] = [s for s, _ in _NORMALIZERS if self.normalizers[s].isChecked()]
        self.owner.changed()

    def _add_format(self) -> None:
        self.draft.fields[self.key].setdefault("formats", []).append({"name": None, "pattern": "", "regex": None})
        self.rebuild_detail()
        self.format_editors[-1].rule.setFocus()
        self.owner.changed()

    def _toggle_from_filename(self, on: bool) -> None:
        self.draft.fields[self.key]["from_filename"] = on
        self.rebuild_detail()
        self.owner.changed()

    def refresh_live(self) -> None:
        if self.try_value is None:
            return
        config = self.owner.valid_config
        key = self.key
        if config is None or key not in config.fields:
            self.try_value_result.setText("Fix the problems listed below to try values.")
            _restyle(self.try_value_result, "msgNeutral")
            if self.try_file is not None:
                self.try_file_result.setText("")
            return
        text = self.try_value.text()
        if text:
            r = validate_field(config, key, text)
            if r.valid:
                msg = r.message or "Accepted."
                if r.date:
                    msg = f"{msg.rstrip('.')}, dated {r.date:%B} {r.date.day}, {r.date.year}."
                if r.value != text:
                    msg += f" Saved as {r.value}."
                self.try_value_result.setText(msg)
                _restyle(self.try_value_result, "msgOk")
            else:
                self.try_value_result.setText(r.message)
                _restyle(self.try_value_result, "msgBad")
        else:
            self.try_value_result.setText("")
        if self.try_file is not None:
            name = self.try_file.text()
            if not name:
                self.try_file_result.setText("")
                return
            found = value_from_filename(config, key, name)
            if found is not None:
                self.try_file_result.setText(f"Would fill in {found}.")
                _restyle(self.try_file_result, "msgOk")
            else:
                self.try_file_result.setText(
                    "Nothing would be filled in: no match, or more than one different match. "
                    "Rules with * or a regular expression are never used for this.")
                _restyle(self.try_file_result, "msgNeutral")


class ListsPage(_Page):
    """Lists: the choices for choice fields."""

    def __init__(self, owner: ProfileSettings) -> None:
        super().__init__(owner)
        self.current = 0
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(20)
        self.master = _MasterList("Lists", [("Add list", self._add), ("Delete", self._delete)])
        self.master.selected.connect(self._select)
        outer.addWidget(self.master)
        self.detail = QWidget()
        self.detail.setObjectName("surface")
        self.form = QVBoxLayout(self.detail)
        self.form.setContentsMargins(0, 0, 8, 0)
        self.form.setSpacing(12)
        outer.addWidget(_scroll(self.detail), 1)

    @property
    def names(self) -> list[str]:
        return list(self.draft.taxonomies)

    def rebuild(self) -> None:
        names = self.names
        self.current = max(0, min(self.current, len(names) - 1))
        self.master.fill(names, self.current)
        self.rebuild_detail()

    def _select(self, row: int) -> None:
        if row >= 0 and row != self.current:
            self.current = row
            self.rebuild_detail()

    def _add(self) -> None:
        name = self.draft.add_taxonomy()
        self.current = self.names.index(name)
        self.rebuild()
        self.owner.changed()

    def _delete(self) -> None:
        if not self.names:
            return
        name = self.names[self.current]
        try:
            self.draft.snapshot(f'"{_shown(name)}"')
            self.draft.delete_taxonomy(name)
        except DraftRefused as e:
            self.owner.say(str(e))
            return
        self.current = max(0, self.current - 1)
        self.rebuild()
        self.owner.changed()
        self.owner.say(f'Deleted the list "{_shown(name)}".', undo=True)

    def rebuild_detail(self) -> None:
        """Rebuild the form for the selected list."""
        _clear(self.form)
        if not self.names:
            self.form.addWidget(_label("No lists yet. Choice fields take their choices from a list.", "muted"))
            self.form.addStretch(1)
            return
        name = self.names[self.current]
        self.name_edit = QLineEdit(name)
        self.name_edit.setAccessibleName("List name")
        self.name_edit.editingFinished.connect(self._rename)
        lab = _label("Name", "fieldLabel")
        lab.setBuddy(self.name_edit)
        self.form.addWidget(lab)
        self.form.addWidget(self.name_edit)
        used = self.draft.taxonomy_usage(name)
        self.form.addWidget(_label(f"Used by: {', '.join(used)}." if used else "Not used by any field.", "muted"))
        self.form.addWidget(_section("Choices"))
        self.form.addWidget(_label("Double-click a choice to change it. The order here is the order shown.",
                                   "help"))
        row = QHBoxLayout()
        self.items = QListWidget()
        self.items.setAccessibleName("Choices")
        for item in self.draft.taxonomies[name]:
            it = QListWidgetItem(item)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
            self.items.addItem(it)
        self.items.setMinimumHeight(220)
        self.items.itemChanged.connect(lambda _i: self._write())
        row.addWidget(self.items, 1)
        col = QVBoxLayout()
        for text, slot in (("Add choice", self._add_item), ("Remove", self._remove_item),
                           ("Up", lambda: self._move(-1)), ("Down", lambda: self._move(1))):
            b = _small(text)
            b.clicked.connect(slot)
            col.addWidget(b)
        col.addStretch(1)
        row.addLayout(col)
        self.form.addLayout(row)
        self.form.addStretch(1)

    def _rename(self) -> None:
        old = self.names[self.current]
        new = self.name_edit.text()
        if new.strip() == old:
            return
        try:
            self.draft.rename_taxonomy(old, new)
        except DraftRefused as e:
            self.owner.say(str(e))
            self.name_edit.setText(old)
            return
        self.master.set_text(self.current, new.strip())
        self.owner.changed()

    def _write(self) -> None:
        name = self.names[self.current]
        self.draft.taxonomies[name] = [self.items.item(i).text() for i in range(self.items.count())]
        self.owner.changed()

    def _add_item(self) -> None:
        it = QListWidgetItem("New choice")
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
        self.items.blockSignals(True)
        self.items.addItem(it)
        self.items.blockSignals(False)
        self.items.setCurrentItem(it)
        self._write()
        self.items.editItem(it)

    def _remove_item(self) -> None:
        row = self.items.currentRow()
        if row >= 0:
            choice = _shown(self.items.item(row).text())
            self.draft.snapshot(f'"{choice}"')
            self.items.blockSignals(True)
            self.items.takeItem(row)
            self.items.blockSignals(False)
            self._write()
            self.owner.say(f'Removed the choice "{choice}".', undo=True)

    def _move(self, step: int) -> None:
        row = self.items.currentRow()
        new = row + step
        if row < 0 or not 0 <= new < self.items.count():
            return
        self.items.blockSignals(True)
        it = self.items.takeItem(row)
        self.items.insertItem(new, it)
        self.items.setCurrentRow(new)
        self.items.blockSignals(False)
        self._write()


class GeneralPage(_Page):
    """General: date format, this config file, Send to."""

    def __init__(self, owner: ProfileSettings) -> None:
        super().__init__(owner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body = QWidget()
        body.setObjectName("surface")
        self.form = QVBoxLayout(body)
        self.form.setContentsMargins(0, 0, 8, 0)
        self.form.setSpacing(12)
        outer.addWidget(_scroll(body))  # scrolls like the other sections on a short window

    def rebuild(self) -> None:
        _clear(self.form)
        self.form.addWidget(_section("Date format"))
        self.form.addWidget(_label(
            "Used for {date} in stamps and file names, and for date fields. "
            "Write it with YYYY, YY, MM and DD, for example MM-DD-YYYY.", "help"))
        self.date_format = QLineEdit(self.draft.data.setdefault("settings", {}).get("date_format", "MM-DD-YYYY"))
        self.date_format.setObjectName("code")
        self.date_format.setMaximumWidth(260)
        self.date_format.setAccessibleName("Date format")
        self.date_format.textEdited.connect(self._date_format)
        self.form.addWidget(self.date_format)
        self.date_example = _label("", "msgNeutral")
        self.form.addWidget(self.date_example)

        self.form.addWidget(_section("This config"))
        src = self.owner.source
        target = self.owner.target
        lines = [f"Loaded from {src.path} ({src.kind.description})."]
        if target is None:
            lines.append("It can't be changed from here. Use Export to save a copy with your changes.")
        elif target.path != src.path:
            lines.append(f"Saving creates {target.path} ({target.kind.description}).")
        else:
            lines.append("Saving updates this file and keeps the previous version as "
                         f"{target.path.name}.bak.")
        self.form.addWidget(_label(" ".join(lines), "help"))
        row = QHBoxLayout()
        self.import_button = _small("Import…")
        self.export_button = _small("Export…")
        self.import_button.clicked.connect(self.owner.import_config)
        self.export_button.clicked.connect(self.owner.export_config)
        row.addWidget(self.import_button)
        row.addWidget(self.export_button)
        row.addStretch(1)
        self.form.addLayout(row)

        self.form.addWidget(_section("Send to menu"))
        self.form.addWidget(_label(
            "Adds Document Stamper to the menu you get by right-clicking a file in Explorer "
            "and choosing Send to.", "help"))
        self.sendto_status = _label("", "muted")
        self.form.addWidget(self.sendto_status)
        row = QHBoxLayout()
        self.sendto_add = _small("Add to the Send to menu")
        self.sendto_remove = _small("Remove")
        self.sendto_add.clicked.connect(self._sendto_add)
        self.sendto_remove.clicked.connect(self._sendto_remove)
        row.addWidget(self.sendto_add)
        row.addWidget(self.sendto_remove)
        row.addStretch(1)
        self.form.addLayout(row)
        self.form.addStretch(1)
        self.refresh_live()
        self._sendto_refresh()

    def _date_format(self, text: str) -> None:
        self.draft.data.setdefault("settings", {})["date_format"] = text
        self.owner.changed(debounce=True)

    def refresh_live(self) -> None:
        if not hasattr(self, "date_example"):
            return
        try:
            fmt = parse_date_format(self.date_format.text())
        except PatternError as e:
            self.date_example.setText(f"This format can't be used: {e}.")
            _restyle(self.date_example, "msgBad")
            return
        self.date_example.setText(f"Today is written {fmt.format(self.owner.today)}.")
        _restyle(self.date_example, "msgOk")

    def _sendto_refresh(self) -> None:
        st = self.owner.sendto_status()
        self.sendto_status.setText(st.message)
        on_windows = self.owner.sendto_available()
        self.sendto_add.setText("Re-create" if st.state is not sendto.SendToState.MISSING
                                else "Add to the Send to menu")
        self.sendto_add.setEnabled(on_windows and st.state is not sendto.SendToState.CURRENT)
        self.sendto_remove.setEnabled(on_windows and st.state is not sendto.SendToState.MISSING)
        if not on_windows:
            self.sendto_status.setText(st.message + " (Available on Windows only.)")

    def _sendto_add(self) -> None:
        try:
            self.owner.sendto_create()
        except sendto.SendToError as e:
            self.owner.say(str(e))
        else:
            self.owner.say("Added to the Send to menu.")
        self._sendto_refresh()

    def _sendto_remove(self) -> None:
        try:
            self.owner.sendto_remove()
        except sendto.SendToError as e:
            self.owner.say(str(e))
        else:
            self.owner.say("Removed from the Send to menu.")
        self._sendto_refresh()


# ------------------------------------------------------------------ the screen


class ProfileSettings(QWidget):
    """The Profile settings screen. Emits ``saved(ConfigSource)`` or ``closed``."""

    saved = Signal(object)  # the ConfigSource now holding the config
    closed = Signal()
    help_requested = Signal(str)  # a help topic, from a link inside a section

    def __init__(
        self,
        *,
        config: Config,
        source: ConfigSource,
        locs: Locations,
        engine: EngineClient,
        services: Services,
        today: date | None = None,
        sendto_shortcut: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ground")
        self.draft = ConfigDraft(config)
        self.source = source
        self.locs = locs
        self.target = save_target(source, locs)
        self.engine = engine
        self.services = services
        self.today = today or date.today()
        self._sendto_shortcut = sendto_shortcut
        self.valid_config: Config | None = config
        self.problems: list[str] = []
        self._local: dict[str, str] = {}
        self._preview_req = 0
        self._sample_dir = Path(tempfile.mkdtemp(prefix="stamper-sample-"))
        self._sample = self._sample_dir / "sample.pdf"
        # Written on the engine thread, which draws the previews after it in order: the window's
        # thread never uses PyMuPDF, which isn't thread-safe.
        self.engine.run_task(lambda _ctx, path=self._sample: write_sample_page(path))

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(PREVIEW_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._revalidate)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(PREVIEW_DEBOUNCE_MS)
        self._preview_timer.timeout.connect(self._do_preview)

        self._build()
        engine.previewed.connect(self._on_preview)
        engine.preview_failed.connect(self._on_preview_failed)
        self.show_section(0)
        self._revalidate()

    # ------------------------------------------------------------ layout

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QWidget()
        body.setObjectName("surface")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(28, 20, 28, 12)
        bl.setSpacing(12)
        head = QHBoxLayout()
        title = _label("Profile settings", "h1", wrap=False)
        head.addWidget(title)
        head.addStretch(1)
        self.help_button = help_button()
        head.addWidget(self.help_button)
        bl.addLayout(head)
        self.source_label = _label("", "muted")
        bl.addWidget(self.source_label)
        self.banner, banner_lay = self._notice()
        self.banner_text = _label("")
        banner_lay.addWidget(self.banner_text)
        bl.addWidget(self.banner)

        row = QHBoxLayout()
        row.setSpacing(20)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setAccessibleName("Sections")
        self.nav.setFixedWidth(130)
        self.nav.addItems(list(SECTIONS))
        self.nav.currentRowChanged.connect(self.show_section)
        row.addWidget(self.nav, 0)
        self.stack = QStackedWidget()
        self.profiles_page = ProfilesPage(self)
        self.fields_page = FieldsPage(self)
        self.lists_page = ListsPage(self)
        self.general_page = GeneralPage(self)
        self.pages: list[_Page] = [self.profiles_page, self.fields_page, self.lists_page, self.general_page]
        for p in self.pages:
            self.stack.addWidget(p)
        row.addWidget(self.stack, 1)
        bl.addLayout(row, 1)
        # The sections scroll on their own; this outer scroll only engages when the window is
        # shorter than the sections' smallest size, so nothing above the buttons is clipped (D-093).
        self.body_scroll = _scroll(body)
        outer.addWidget(self.body_scroll, 1)

        foot = QWidget()
        foot.setObjectName("footer")
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(28, 12, 28, 14)
        fl.setSpacing(8)
        self.problem_label = _label("", "msgBad")
        self.problem_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.problem_box = _BoundedText(self.problem_label, PROBLEM_LINES)
        self.problem_box.hide()
        fl.addWidget(self.problem_box)
        self.status = _label("", "help")
        self.status.setTextFormat(Qt.TextFormat.PlainText)  # it can hold names from config
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_box = _BoundedText(self.status, STATUS_LINES)
        self.status_box.hide()
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_box, 1)
        self.undo_button = QPushButton("Undo")
        self.undo_button.setObjectName("link")
        self.undo_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.undo_button.setAccessibleName("Undo the last removal")
        self.undo_button.clicked.connect(self.undo)
        self.undo_button.hide()
        status_row.addWidget(self.undo_button, 0, Qt.AlignmentFlag.AlignTop)
        fl.addLayout(status_row)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel)
        self.save_button = QPushButton("Save changes")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)
        fl.addLayout(buttons)
        outer.addWidget(foot)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.cancel,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence.StandardKey.Save, self, self.save,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._update_source()

    @staticmethod
    def _notice() -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("info")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 10, 14, 10)
        frame.hide()
        return frame, lay

    def _update_source(self) -> None:
        self.source_label.setText(f"Config: {self.source.path} ({self.source.kind.description})")
        if self.target is None:
            self.banner_text.setText(
                "This config file can't be changed here: it's read-only or in a folder you can't "
                "write to. You can still look around, and Export a copy with your changes.")
            _restyle(self.banner, "warning")
            self.banner.show()
        elif self.source.kind is SourceKind.BUILTIN:
            self.banner_text.setText(
                f"You're looking at the built-in settings. Saving creates {self.target.path}, "
                f"{self.target.kind.description}.")
            _restyle(self.banner, "info")
            self.banner.show()
        else:
            self.banner.hide()

    def help_topic(self) -> str:
        """The help topic for the section showing."""
        return SECTION_HELP[max(0, self.nav.currentRow())]

    def show_section(self, index: int) -> None:
        """Switch to a section: 0 Profiles, 1 Fields, 2 Lists, 3 General."""
        if self.nav.currentRow() != index:
            self.nav.blockSignals(True)
            self.nav.setCurrentRow(index)
            self.nav.blockSignals(False)
        page = self.pages[index]
        page.rebuild()
        self.stack.setCurrentWidget(page)
        if page is self.profiles_page:
            self.request_preview()

    # ------------------------------------------------------------ edits

    def changed(self, *, debounce: bool = False) -> None:
        """An edit happened: re-check (at once, or after typing pauses)."""
        self.say("")
        self._update_buttons(checking=True)
        if debounce:
            self._debounce.start()
        else:
            self._debounce.stop()
            self._revalidate()

    def set_local_problem(self, key: str, text: str) -> None:
        """A problem the form found that the loader can't see (a duplicate property name)."""
        if text:
            self._local[key] = text
        else:
            self._local.pop(key, None)

    def _revalidate(self) -> None:
        self.valid_config, problems = self.draft.validate()
        self.problems = [*self._local.values(), *problems]
        shown = self.problems[:MAX_PROBLEMS]
        more = len(self.problems) - len(shown)
        text = "\n".join(f"• {plain_problem(p)}" for p in shown) + (f"\n…and {more} more." if more > 0 else "")
        self.problem_label.setText(f"Fix these before saving:\n{text}" if self.problems else "")
        self.problem_label.setVisible(bool(self.problems))
        self.problem_box.setVisible(bool(self.problems))
        self.problem_box.refresh()
        for p in self.pages:
            p.refresh_live()
        self._update_buttons()
        self.request_preview()

    def _update_buttons(self, *, checking: bool = False) -> None:
        ok = not checking and not self.problems and self.target is not None
        self.save_button.setEnabled(ok and (self.draft.dirty or self.source.kind is SourceKind.BUILTIN))

    def can_save(self) -> bool:
        """True when "Save changes" is enabled."""
        return self.save_button.isEnabled()

    def say(self, text: str, *, undo: bool = False) -> None:
        """A short message above the buttons (scrolls past a few lines).

        ``undo`` shows the Undo link beside it while the draft has a snapshot.
        Any later message, including the blank one every edit makes, hides it.
        """
        self.status.setText(text)
        self.status_box.setVisible(bool(text))
        self.status_box.refresh()
        self.undo_button.setVisible(bool(text) and undo and self.draft.can_undo)

    def undo(self) -> None:
        """Put back what the last removal or type change took away."""
        label = self.draft.undo()
        if label is None:
            return
        self._local.clear()  # the form is rebuilt from the draft, which can't hold a duplicate property
        self.show_section(self.nav.currentRow())
        self.changed()
        self.say(f"Restored {label}.")

    # ------------------------------------------------------------ preview

    def request_preview(self) -> None:
        """Redraw the preview soon (debounced)."""
        self._preview_timer.start()

    def _do_preview(self) -> None:
        page = self.profiles_page
        if self.stack.currentWidget() is not page:
            return
        config, profile = self.valid_config, page.selected_profile()
        if config is None or profile is None:
            page.show_preview(None, "The preview comes back when the problems below are fixed.")
            return
        job = StampJob.from_profile(config, profile, sample_values(config, profile, self.today), today=self.today)
        ratio = self.devicePixelRatioF() or 1.0
        dpi = max(24, int(PREVIEW_W * ratio / 8.5))
        self._preview_req = self.engine.preview(self._sample, 1, job, None, dpi)

    def _on_preview(self, req: int, png: bytes) -> None:
        if req == self._preview_req:
            self.profiles_page.show_preview(png)

    def _on_preview_failed(self, req: int, failure: EngineFailure) -> None:
        if req == self._preview_req:
            self.profiles_page.show_preview(None, failure.message)

    def preview_ready(self) -> bool:
        """True when no preview is waiting (tests)."""
        return not self._preview_timer.isActive() and self.profiles_page.preview.pixmap() is not None \
            and not self.profiles_page.preview.pixmap().isNull()

    # ------------------------------------------------------------ save, cancel

    def save(self) -> None:
        """Write the draft to the save target, with a .bak of the old file."""
        self._debounce.stop()
        self._revalidate()
        if not self.can_save() or self.valid_config is None or self.target is None:
            return
        try:
            save_config(self.valid_config, self.target.path, expect=self.target.digest)
        except OSError as e:
            self.say(f"Couldn't save to {self.target.path}: {e.strerror or e}. Nothing was changed.")
            return
        self.draft.mark_saved()
        self.source = describe(self.target.path, self.target.kind)
        self.target = save_target(self.source, self.locs)
        self._update_source()
        self.saved.emit(self.source)

    def may_leave(self) -> bool:
        """True if there are no unsaved changes, or the user agrees to discard them."""
        return not self.draft.dirty or self.services.confirm(
            self, "Discard changes?", "Your changes to the profile settings haven't been saved.",
            "Discard changes")

    def cancel(self) -> None:
        """Leave without saving; asks first if there are changes."""
        if self.may_leave():
            self.closed.emit()

    # ------------------------------------------------------------ import, export

    def import_config(self) -> None:
        """Load another config file into the editor (saved only when the user saves)."""
        path = self.services.ask_import_file(self)
        if path is None:
            return
        try:
            imported = load_config(path)
        except ConfigError as e:
            shown = e.problems[:MAX_PROBLEMS]
            self.say(f"{path.name} can't be imported:\n" + "\n".join(f"• {p}" for p in shown))
            return
        lines = diff_summary(self.draft.data, imported)
        bak = f" The file it replaces is kept as {self.target.path.name}.bak." if self.target else ""
        text = (f"{path.name} replaces all profiles, fields and lists:\n\n"
                + "\n".join(f"• {line}" for line in lines)
                + f"\n\nNothing changes on disk until you click Save changes.{bak}")
        if not self.services.confirm(self, "Import this config?", text, "Import"):
            return
        self.draft.replace(imported.model_dump(mode="json"))
        self._local.clear()
        self.show_section(self.nav.currentRow())
        self.changed()
        self.say(f"Imported {path.name}. Check it, then Save changes.")

    def export_config(self) -> None:
        """Save a copy of what's on screen to a file the user picks."""
        self._revalidate()
        if self.valid_config is None:
            self.say("Fix the problems listed above before exporting.")
            return
        suggested = Path.home() / "DocumentStamper-config.json"
        path = self.services.ask_export_path(self, suggested)
        if path is None:
            return
        if same_file(path, self.source.path):
            self.say("To change this file, use Save changes. Export saves a copy somewhere else.")
            return
        try:
            save_config(self.valid_config, path, backup=False)
        except OSError as e:
            self.say(f"Couldn't export to {path}: {e.strerror or e}.")
            return
        self.say(f"Exported to {path}.")

    # ------------------------------------------------------------ Send to

    def sendto_available(self) -> bool:
        """Shortcuts can only be made on Windows."""
        return os.name == "nt"

    def sendto_status(self) -> sendto.SendToStatus:
        """The shortcut's state (a test may point it at another file)."""
        return sendto.status(self._sendto_shortcut)

    def sendto_create(self) -> None:
        """Create or replace the Send to shortcut."""
        sendto.create(self._sendto_shortcut)

    def sendto_remove(self) -> None:
        """Remove the Send to shortcut."""
        sendto.remove(self._sendto_shortcut)

    # ------------------------------------------------------------ closing

    def dispose(self) -> None:
        """Stop listening to the engine and remove the sample page."""
        for signal, slot in ((self.engine.previewed, self._on_preview),
                             (self.engine.preview_failed, self._on_preview_failed)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._debounce.stop()
        self._preview_timer.stop()
        shutil.rmtree(self._sample_dir, ignore_errors=True)

