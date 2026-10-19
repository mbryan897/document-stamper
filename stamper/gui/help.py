"""The Help window (Phase 4.7, D-133 to D-136).

A separate window, so the help can sit beside the screen it explains:
contents and search on the left, the topic on the right. It opens from
every screen's Help button and from F1, at the topic for that screen.

Topics are the Markdown files in ``assets/help`` (``helpbook.py``), shown
with Qt's own Markdown support in a ``QTextBrowser``: no web engine, no
browser, nothing fetched. Links only move between topics; a web address is
shown as text and never opened (D-116). Pictures are scaled to the width of
the text; clicking one shows it full size.
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QKeySequence,
    QMouseEvent,
    QPalette,
    QPixmap,
    QResizeEvent,
    QShortcut,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextFrameFormat,
    QTextImageFormat,
    QTextLength,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .fonts import MONO_FAMILY
from .helpbook import HelpBook, Hit, slugify
from .theme import COLORS

__all__ = ["HELP_SHORTCUT", "HelpBrowser", "HelpWindow", "help_button"]

HELP_SHORTCUT = QKeySequence.StandardKey.HelpContents  # F1 on Windows
PREFERRED = QSize(1100, 760)
MINIMUM = QSize(760, 480)
CONTENTS_W = 320
SEARCH_DEBOUNCE_MS = 150
IMAGE_MARGIN = 28  # room beside a picture inside the text column
_TOPIC_ROLE = Qt.ItemDataRole.UserRole


def help_button() -> QPushButton:
    """The "Help" link-style button every screen carries."""
    b = QPushButton("Help")
    b.setObjectName("link")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setToolTip("Help for this screen (F1)")
    b.setAccessibleName("Help for this screen")
    return b


class HelpBrowser(QTextBrowser):
    """Shows one topic: Markdown, pictures fitted to the width, matches highlighted."""

    link_clicked = Signal(str)  # a link to another topic or heading
    image_clicked = Signal(str, str)  # (path relative to the help folder, description)

    def __init__(self, folder: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.folder = folder
        self.setObjectName("helpText")
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setAccessibleName("Help topic")
        self.document().setDocumentMargin(24)
        self.anchorClicked.connect(self._anchor)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Link, QColor(COLORS["ink"]))
        pal.setColor(QPalette.ColorRole.Base, QColor(COLORS["surface"]))
        self.setPalette(pal)
        self._markdown = ""
        self._terms: list[str] = []
        self._width_drawn = 0
        self._refit = QTimer(self)
        self._refit.setSingleShot(True)
        self._refit.setInterval(120)
        self._refit.timeout.connect(self._redraw_for_width)

    # ---------------------------------------------------------- content

    def show_markdown(self, markdown: str) -> None:
        """Show a topic's Markdown, styled to match the app."""
        self._markdown = markdown
        self._width_drawn = self.viewport().width()
        self.document().clear()
        self.setMarkdown(markdown)
        self._polish()

    def _polish(self) -> None:
        """Mono font for code, ink headings, a little more line spacing, tidy tables."""
        doc = self.document()
        block = doc.begin()
        ink = QColor(COLORS["ink"])
        while block.isValid():
            cur = QTextCursor(block)
            level = block.blockFormat().headingLevel()
            fmt = QTextBlockFormat()
            if level:
                fmt.setTopMargin(18 if level > 1 else 0)
                fmt.setBottomMargin(6)
                cur.mergeBlockFormat(fmt)
                head = QTextCharFormat()
                head.setForeground(ink)
                cur.select(QTextCursor.SelectionType.BlockUnderCursor)
                cur.mergeCharFormat(head)
            else:
                fmt.setLineHeight(125, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
                if block.textList() is None and cur.currentTable() is None:
                    fmt.setBottomMargin(10)  # paragraphs apart, as on a page
                cur.mergeBlockFormat(fmt)
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.charFormat().isAnchor():  # the importer used the app palette's blue
                    c = QTextCursor(doc)
                    c.setPosition(frag.position())
                    c.setPosition(frag.position() + frag.length(), QTextCursor.MoveMode.KeepAnchor)
                    link = QTextCharFormat()
                    link.setForeground(ink)
                    c.mergeCharFormat(link)
                if frag.charFormat().fontFixedPitch():
                    c = QTextCursor(doc)
                    c.setPosition(frag.position())
                    c.setPosition(frag.position() + frag.length(), QTextCursor.MoveMode.KeepAnchor)
                    code = QTextCharFormat()
                    code.setFontFamilies([MONO_FAMILY])
                    code.setBackground(QColor(COLORS["ground"]))
                    c.mergeCharFormat(code)
                it += 1
            block = block.next()
        for frame in doc.rootFrame().childFrames():
            ff = frame.frameFormat()
            if ff.isTableFormat():
                tf = frame.frameFormat().toTableFormat()
                tf.setBorderCollapse(True)
                tf.setBorder(1)
                tf.setBorderBrush(QColor(COLORS["border"]))
                tf.setBorderStyle(QTextFrameFormat.BorderStyle.BorderStyle_Solid)
                tf.setCellPadding(6)
                tf.setCellSpacing(0)
                tf.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
                frame.setFrameFormat(tf)

    def loadResource(self, kind: int, url: QUrl | str) -> object:  # noqa: N802 - Qt name
        """Pictures come from the help folder, scaled to fit the text column."""
        if kind != QTextDocument.ResourceType.ImageResource.value:
            return super().loadResource(kind, url)
        name = url.toString() if isinstance(url, QUrl) else str(url)
        path = (self.folder / name).resolve()
        if self.folder.resolve() not in path.parents or not path.is_file():
            return QImage()
        image = QImage(str(path))
        if image.isNull():
            return image
        ratio = self.devicePixelRatioF() or 1.0
        room = max(160, self.viewport().width() - 2 * int(self.document().documentMargin()) - IMAGE_MARGIN)
        if image.width() > room * ratio:
            image = image.scaledToWidth(int(room * ratio), Qt.TransformationMode.SmoothTransformation)
        image.setDevicePixelRatio(ratio if image.width() <= room * ratio else image.width() / room)
        return image

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt name
        """Refit the pictures when the column changes width."""
        super().resizeEvent(event)
        if self._markdown and abs(self.viewport().width() - self._width_drawn) > 24:
            self._refit.start()

    def _redraw_for_width(self) -> None:
        bar = self.verticalScrollBar()
        top = bar.value() / max(bar.maximum(), 1)
        self.show_markdown(self._markdown)
        self.highlight(self._terms)  # the old highlights pointed into the old document
        bar.setValue(int(top * bar.maximum()))

    # ---------------------------------------------------------- moving around

    def heading_block(self, anchor: str) -> int:
        """The position of the heading with this anchor, or -1."""
        block = self.document().begin()
        while block.isValid():
            if block.blockFormat().headingLevel() and slugify(block.text()) == anchor:
                return block.position()
            block = block.next()
        return -1

    def scroll_to(self, anchor: str) -> None:
        """Scroll so the heading is at the top; the top of the topic when there's none."""
        pos = self.heading_block(anchor) if anchor else -1
        bar = self.verticalScrollBar()
        if pos < 0:
            bar.setValue(0)
            return
        block = self.document().findBlock(pos)
        layout = self.document().documentLayout()
        bar.setValue(int(layout.blockBoundingRect(block).top()))
        cur = QTextCursor(block)
        self.setTextCursor(cur)

    def highlight(self, terms: list[str]) -> int:
        """Mark every match of each term; returns how many."""
        self._terms = list(terms)
        selections: list[QTextEdit.ExtraSelection] = []
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(COLORS["amber_bg"]))
        fmt.setForeground(QColor(COLORS["amber_text"]))
        fmt.setFontWeight(QFont.Weight.Bold)
        doc = self.document()
        for term in terms:
            cur = QTextCursor(doc)
            while True:
                cur = doc.find(term, cur)
                if cur.isNull():
                    break
                sel = QTextEdit.ExtraSelection()
                sel.cursor = cur
                sel.format = fmt
                selections.append(sel)
        self.setExtraSelections(selections)
        return len(selections)

    def _anchor(self, url: QUrl) -> None:
        target = url.toString()
        if "://" in target or target.startswith("mailto:"):
            return  # never opened from the help (D-116); the address is shown as text
        self.link_clicked.emit(target)

    def image_at(self, pos) -> tuple[str, str]:  # noqa: ANN001 - QPoint
        """``(picture path, description)`` under a point in the viewport, or empty strings."""
        cur = self.cursorForPosition(pos)
        after = QTextCursor(cur)
        after.movePosition(QTextCursor.MoveOperation.NextCharacter)
        for c in (cur, after):  # a cursor's format is the character before it
            fmt = c.charFormat()
            if fmt.isImageFormat():
                img: QTextImageFormat = fmt.toImageFormat()
                return img.name(), str(img.property(QTextFormat.Property.ImageAltText) or "")
        return "", ""

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt name
        """A click on a picture opens it full size."""
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and not self.textCursor().hasSelection():
            name, title = self.image_at(event.position().toPoint())
            if name:
                self.image_clicked.emit(name, title)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt name
        """A hand over pictures, so it's clear they can be clicked."""
        super().mouseMoveEvent(event)
        over = bool(self.image_at(event.position().toPoint())[0]) and not self.anchorAt(event.position().toPoint())
        if over:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        elif not self.anchorAt(event.position().toPoint()):
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)


class ImageViewer(QDialog):
    """A help picture at full size, scrolling if it's bigger than the screen."""

    def __init__(self, path: Path, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title or "Picture")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        area = QScrollArea()
        area.setWidgetResizable(False)
        label = QLabel()
        label.setPixmap(QPixmap(str(path)))
        label.setAccessibleName(title or "Picture")
        area.setWidget(label)
        lay.addWidget(area, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        screen = (parent.screen() if parent else None) or QGuiApplication.primaryScreen()
        room = screen.availableGeometry().size() if screen else QSize(1200, 800)
        want = label.sizeHint() + QSize(60, 90)
        self.resize(want.boundedTo(room - QSize(40, 60)))


class _ResultRow(QWidget):
    """One search result: the section heading and topic, then the matching line."""

    def __init__(self, hit: Hit, terms: list[str]) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(2)
        where = hit.section.heading if hit.section.anchor else hit.topic.title
        head = QLabel(f"<b>{html.escape(where)}</b>")
        head.setWordWrap(True)
        lay.addWidget(head)
        if hit.section.anchor:
            topic = QLabel(html.escape(hit.topic.title))
            topic.setObjectName("muted")
            topic.setWordWrap(True)
            lay.addWidget(topic)
        if hit.snippet:
            text = html.escape(hit.snippet)
            for t in sorted(terms, key=len, reverse=True):
                text = _mark(text, html.escape(t))
            snip = QLabel(text)
            snip.setObjectName("help")
            snip.setWordWrap(True)
            snip.setTextFormat(Qt.TextFormat.RichText)
            lay.addWidget(snip)
        for lab in self.findChildren(QLabel):
            lab.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def _mark(text: str, term: str) -> str:
    """Bold every case-insensitive match of ``term`` in already-escaped text."""
    if not term:
        return text
    out, low, tl, i = [], text.casefold(), term.casefold(), 0
    while True:
        j = low.find(tl, i)
        if j < 0:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:j])
        out.append(f"<b style=\"color:{COLORS['amber_text']}\">{text[j:j + len(term)]}</b>")
        i = j + len(term)


class HelpWindow(QWidget):
    """Contents, search and one topic at a time, with Back and Forward."""

    def __init__(self, book: HelpBook | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.book = book or HelpBook.load(replacements=_replacements())
        self.setObjectName("ground")
        self.setWindowTitle("Document Stamper Help")
        self.setMinimumSize(MINIMUM)
        self.resize(PREFERRED)
        self.topic = ""
        self.anchor = ""
        self.terms: list[str] = []
        self._history: list[tuple[str, str]] = []
        self._at = -1
        self._build()
        self._fill_contents()
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self.run_search)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())

    # ---------------------------------------------------------- layout

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        left = QWidget()
        left.setObjectName("panel")
        left.setFixedWidth(CONTENTS_W)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(16, 16, 12, 16)
        ll.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search help")
        self.search.setAccessibleName("Search help")
        self.search.setClearButtonEnabled(True)
        self.search.installEventFilter(self)
        ll.addWidget(self.search)
        self.side = QStackedWidget()
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setAccessibleName("Contents")
        self.tree.setObjectName("helpContents")
        self.tree.setIndentation(14)
        self.tree.setWordWrap(True)
        self.tree.itemClicked.connect(self._tree_clicked)
        self.tree.itemActivated.connect(self._tree_clicked)
        self.results = QListWidget()
        self.results.setAccessibleName("Search results")
        self.results.setObjectName("helpResults")
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.results.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)  # rows sized once
        self.results.itemClicked.connect(self._result_opened)
        self.results.itemActivated.connect(self._result_opened)
        self.no_results = QLabel("")
        self.no_results.setObjectName("help")
        self.no_results.setWordWrap(True)
        self.no_results.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.side.addWidget(self.tree)
        self.side.addWidget(self.results)
        self.side.addWidget(self.no_results)
        ll.addWidget(self.side, 1)
        self.count = QLabel("")
        self.count.setObjectName("muted")
        ll.addWidget(self.count)
        outer.addWidget(left)

        right = QWidget()
        right.setObjectName("surface")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        bar = QFrame()
        bar.setObjectName("footer")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 8, 16, 8)
        bl.setSpacing(8)
        self.back_button = QPushButton("‹ Back")
        self.back_button.setObjectName("small")
        self.back_button.setToolTip("Back (Alt+Left)")
        self.back_button.clicked.connect(self.back)
        self.forward_button = QPushButton("Forward ›")
        self.forward_button.setObjectName("small")
        self.forward_button.setToolTip("Forward (Alt+Right)")
        self.forward_button.clicked.connect(self.forward)
        self.where = QLabel("")
        self.where.setObjectName("muted")
        bl.addWidget(self.back_button)
        bl.addWidget(self.forward_button)
        bl.addSpacing(8)
        bl.addWidget(self.where, 1)
        rl.addWidget(bar)
        self.browser = HelpBrowser(self.book.folder)
        self.browser.setFrameShape(QFrame.Shape.NoFrame)
        self.browser.link_clicked.connect(self.follow)
        self.browser.image_clicked.connect(self._show_image)
        rl.addWidget(self.browser, 1)
        outer.addWidget(right, 1)

        QShortcut(QKeySequence.StandardKey.Find, self, self.focus_search)
        QShortcut(QKeySequence(Qt.Modifier.ALT | Qt.Key.Key_Left), self, self.back)
        QShortcut(QKeySequence(Qt.Modifier.ALT | Qt.Key.Key_Right), self, self.forward)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._escape)
        self._update_nav()

    def _fill_contents(self) -> None:
        self.tree.clear()
        self._topic_items: dict[str, QTreeWidgetItem] = {}
        bold = QFont(self.font())
        bold.setBold(True)
        for part in self.book.parts:
            pi = QTreeWidgetItem([part.title])
            pi.setFont(0, bold)
            pi.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(pi)
            for tid in part.topics:
                topic = self.book.topics[tid]
                ti = QTreeWidgetItem([topic.title])
                ti.setData(0, _TOPIC_ROLE, tid)
                ti.setToolTip(0, topic.title)
                pi.addChild(ti)
                self._topic_items[tid] = ti
                for s in topic.sections:
                    if s.level == 2:
                        si = QTreeWidgetItem([s.heading])
                        si.setData(0, _TOPIC_ROLE, f"{tid}#{s.anchor}")
                        ti.addChild(si)
            pi.setExpanded(True)

    # ---------------------------------------------------------- opening

    def open(self, link: str = "", *, terms: list[str] | None = None, record: bool = True) -> bool:
        """Show a topic (``topic`` or ``topic#heading``); the first topic when ``link`` is empty.

        Returns False, and shows the first topic, when the link doesn't exist.
        """
        found = self.book.resolve(link, self.topic) if link else None
        ok = found is not None or not link
        if found is None:
            found = (self.book.ordered()[0].id, "")
        topic, anchor = found
        if topic != self.topic:
            self.browser.show_markdown(self.book.topics[topic].markdown)
        self.topic, self.anchor = topic, anchor
        self.terms = list(terms or [])
        self.browser.highlight(self.terms)
        self.browser.scroll_to(anchor)
        t = self.book.topics[topic]
        self.where.setText(f"{t.part}  ›  {t.title}")
        self.setWindowTitle(f"{t.title} - Document Stamper Help")
        self._select_in_tree(topic, anchor)
        if record:
            self._history = self._history[: self._at + 1]
            if not self._history or self._history[-1] != (topic, anchor):
                self._history.append((topic, anchor))
            self._at = len(self._history) - 1
        self._update_nav()
        return ok

    def show_topic(self, link: str) -> None:
        """Open at ``link`` and bring the window to the front (Help buttons and F1)."""
        if self.search.text():
            self.search.clear()
            self.run_search()
        self.open(link)
        if not self.isVisible():
            self._place()
        self.show()
        self.raise_()
        self.activateWindow()
        self.browser.setFocus()

    def follow(self, link: str) -> None:
        """A link inside a topic."""
        self.open(link)

    def back(self) -> None:
        """The topic shown before this one."""
        if self._at > 0:
            self._at -= 1
            self.open("#".join(self._history[self._at]).rstrip("#"), record=False)

    def forward(self) -> None:
        """The topic Back left."""
        if self._at < len(self._history) - 1:
            self._at += 1
            self.open("#".join(self._history[self._at]).rstrip("#"), record=False)

    def _update_nav(self) -> None:
        self.back_button.setEnabled(self._at > 0)
        self.forward_button.setEnabled(self._at < len(self._history) - 1)

    def _select_in_tree(self, topic: str, anchor: str) -> None:
        item = self._topic_items.get(topic)
        if item is None:
            return
        self.tree.blockSignals(True)
        for other in self._topic_items.values():
            if other is not item:
                other.setExpanded(False)
        item.setExpanded(True)
        target = item
        if anchor:
            for i in range(item.childCount()):
                if item.child(i).data(0, _TOPIC_ROLE) == f"{topic}#{anchor}":
                    target = item.child(i)
        self.tree.setCurrentItem(target)
        self.tree.scrollToItem(target)
        self.tree.blockSignals(False)

    def _tree_clicked(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        link = item.data(0, _TOPIC_ROLE)
        if link:
            self.open(link)

    def _place(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        room = screen.availableGeometry()
        size = PREFERRED.boundedTo(room.size() - QSize(40, 60))
        self.resize(size)
        self.move(room.x() + (room.width() - size.width()) // 2, room.y() + (room.height() - size.height()) // 2)

    def _show_image(self, name: str, title: str) -> None:
        path = (self.book.folder / name).resolve()
        if path.is_file() and self.book.folder.resolve() in path.parents:
            ImageViewer(path, title, self).exec()

    # ---------------------------------------------------------- search

    def focus_search(self) -> None:
        """Ctrl+F: put the cursor in the search box."""
        self.search.setFocus()
        self.search.selectAll()

    def run_search(self) -> list[Hit]:
        """Search for what's in the box and list the results (the contents when it's empty)."""
        self._search_timer.stop()
        query = self.search.text().strip()
        self.results.clear()
        if not query:
            self.side.setCurrentWidget(self.tree)
            self.count.setText("")
            if self.terms:
                self.terms = []
                self.browser.highlight([])
            return []
        hits = self.book.search(query)
        terms = [t for t in query.split() if t]
        if not hits:
            self.no_results.setText(f"Nothing in the help matches “{query}”. Try fewer words, or a shorter "
                                    "form of a word (sign finds signed and signature).")
            self.side.setCurrentWidget(self.no_results)
            self.count.setText("")
            return hits
        self.side.setCurrentWidget(self.results)
        for hit in hits:
            item = QListWidgetItem()
            item.setData(_TOPIC_ROLE, hit.link)
            item.setToolTip(f"{hit.topic.title}: {hit.section.heading}")
            self.results.addItem(item)
            self.results.setItemWidget(item, _ResultRow(hit, terms))
        self._fit_results()
        self.count.setText(f"{len(hits)} place{'s' if len(hits) != 1 else ''} found")
        return hits

    def _fit_results(self) -> None:
        """Make each result row exactly as wide as the list, and as tall as its wrapped text."""
        width = max(120, self.results.viewport().width() - 2)
        for i in range(self.results.count()):
            item = self.results.item(i)
            row = self.results.itemWidget(item)
            if row is None:
                continue
            row.setFixedWidth(width)
            lay = row.layout()
            height = lay.totalHeightForWidth(width) if lay.hasHeightForWidth() else row.sizeHint().height()
            item.setSizeHint(QSize(width, height))

    def open_result(self, row: int) -> None:
        """Open a search result, with the words found highlighted."""
        item = self.results.item(row)
        if item is not None:
            self.results.setCurrentRow(row)
            self.open(item.data(_TOPIC_ROLE), terms=[t for t in self.search.text().split() if t])

    def _result_opened(self, item: QListWidgetItem) -> None:
        self.open_result(self.results.row(item))

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001, N802 - Qt signature
        """Enter in the search box opens the first result; Down moves into the list."""
        if obj is self.search and event.type() == event.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.run_search()
                if self.results.count():
                    self.open_result(max(self.results.currentRow(), 0))
                return True
            if key == Qt.Key.Key_Down and self.side.currentWidget() is self.results and self.results.count():
                self.results.setFocus()
                self.results.setCurrentRow(max(self.results.currentRow(), 0))
                return True
        return super().eventFilter(obj, event)

    def _escape(self) -> None:
        if self.search.text():
            self.search.clear()
            self.run_search()
        else:
            self.close()


def _replacements() -> dict[str, str]:
    from ..cli import get_version  # the same version --version prints

    return {"@VERSION@": get_version()}
