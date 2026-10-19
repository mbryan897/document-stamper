"""The help (Phase 4.7, D-133 to D-137): the book, its links and pictures, search,
the Help window, every screen's Help button and F1, and checks that keep the
help and the interface from drifting apart.

The drift checks work on the source code, not on a running window:

- every **bold** phrase in the help must be wording that exists in the
  interface code (a renamed button fails the test until the help follows);
- every button and tick box in the interface must be named in the help;
- every command-line option must be documented, and nothing documented may be
  missing from the parser.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QUrl
from PySide6.QtGui import QTextDocument
from pytestqt.qtbot import QtBot

from stamper.cli import build_parser
from stamper.gui import color_picker, multi, page_numbers, profiles
from stamper.gui.app import make_app
from stamper.gui.help import HelpBrowser, HelpWindow
from stamper.gui.helpbook import HelpBook, plain_text, slugify, split_link
from stamper.gui.main_window import MainWindow
from tests.gui_support import (  # noqa: F401 - fixtures
    WAIT_MS,
    FakeServices,
    choose,
    copy_to,
    fake,
    open_dialog,
    type_into,
    wait_ready,
    window,
)
from tests.stamping import make_signed_pdf

REPO = Path(__file__).resolve().parents[1]
HELP = REPO / "assets" / "help"
GUI = REPO / "stamper" / "gui"
SIZE_BUDGET = 4 * 1024 * 1024  # the help, pictures included; the exe carries all of it (D-136)

# Interface words the help doesn't need to name, with the reason.
NOT_DOCUMENTED: dict[str, str] = {
    "": "the single-file button's text is set when shown",
    "Close": "the picture viewer's only button",
}


@pytest.fixture(scope="module")
def book() -> HelpBook:
    return HelpBook.load(HELP, replacements={"@VERSION@": "0.0.test"})


# ------------------------------------------------------------------ the book


def test_every_topic_loads_with_a_title(book: HelpBook) -> None:
    assert len(book.parts) == 3
    ids = [t.id for t in book.ordered()]
    assert len(ids) == len(set(ids))
    files = {p.stem for p in HELP.glob("*.md")}
    assert files == set(ids), "every .md file is listed in index.json, and only those"
    for topic in book.ordered():
        assert topic.title and topic.sections


def test_every_link_and_anchor_resolves(book: HelpBook) -> None:
    broken = [(t.id, link) for t in book.ordered() for link in book.links(t.id)
              if book.resolve(link, t.id) is None]
    assert broken == []


def test_every_picture_exists_and_is_shown(book: HelpBook) -> None:
    shown = {img for t in book.ordered() for img in book.images(t.id)}
    missing = sorted(i for i in shown if not (HELP / i).is_file())
    assert missing == [], "run tools/make_help_screenshots.py"
    on_disk = {f"images/{p.name}" for p in (HELP / "images").glob("*")}
    assert sorted(on_disk - shown) == [], "pictures no topic shows only make the app bigger"


def test_the_help_stays_small() -> None:
    total = sum(p.stat().st_size for p in HELP.rglob("*") if p.is_file())
    assert total < SIZE_BUDGET, f"the help is {total / 1024 / 1024:.1f} MB; see D-136 before raising the budget"


def test_version_is_filled_in(book: HelpBook) -> None:
    assert "0.0.test" in book.topics["about"].markdown
    assert "@VERSION@" not in book.topics["about"].markdown


def test_slugs_and_links() -> None:
    assert slugify("Before stamping") == "before-stamping"
    assert slugify("The white box covers, it doesn't remove") == "the-white-box-covers-it-doesn-t-remove"
    assert slugify("Choose files…") == "choose-files"
    assert split_link("saving.md#the-saved-screen") == ("saving", "the-saved-screen")
    assert split_link("#lists") == ("", "lists")
    assert plain_text("Click **Stamp and save…** in `the` [dialog](x.md). ![p](images/a.png)") == \
        "Click Stamp and save… in the dialog."


def test_stale_context_ids_are_caught(book: HelpBook) -> None:
    assert book.resolve("saving#the-saved-screen") == ("saving", "the-saved-screen")
    assert book.resolve("saving#no-such-heading") is None
    assert book.resolve("no-such-topic") is None


# ------------------------------------------------------------------ search


def test_search_needs_every_word(book: HelpBook) -> None:
    both = book.search("white box")
    assert both and all("white" in h.section.text.casefold() + h.topic.title.casefold() for h in both)
    assert all("box" in h.section.text.casefold() + h.topic.title.casefold() for h in both)
    assert book.search("white zebra") == []


def test_search_ignores_case_and_finds_parts_of_words(book: HelpBook) -> None:
    hits = book.search("SIGN")
    assert hits[0].topic.id == "protected-files"
    assert any(h.link == "protected-files#digitally-signed-files" for h in hits[:3])


def test_search_ranks_headings_first(book: HelpBook) -> None:
    hits = book.search("exit codes")
    assert hits[0].link == "command-line#exit-codes"


def test_search_snippet_shows_the_word(book: HelpBook) -> None:
    hit = next(h for h in book.search("bak") if h.topic.id == "where-the-config-lives")
    assert "bak" in hit.snippet.casefold()
    assert len(hit.snippet) <= 120


def test_empty_search_finds_nothing(book: HelpBook) -> None:
    assert book.search("") == []
    assert book.search("   ") == []


# ------------------------------------------------------------------ the window


def test_help_window_opens_topics_and_goes_back(qtbot: QtBot) -> None:
    make_app()
    h = HelpWindow()
    qtbot.addWidget(h)
    assert h.open("saving#the-saved-screen")
    assert (h.topic, h.anchor) == ("saving", "the-saved-screen")
    assert h.browser.heading_block("the-saved-screen") >= 0
    h.open("command-line")
    h.back()
    assert h.topic == "saving"
    h.forward()
    assert h.topic == "command-line"
    assert not h.open("no-such-topic")  # falls back to the first topic
    assert h.topic == "what-it-does"


def test_help_window_search_lists_and_highlights(qtbot: QtBot) -> None:
    make_app()
    h = HelpWindow()
    qtbot.addWidget(h)
    h.show()
    h.search.setText("restrictions")
    hits = h.run_search()
    assert hits and h.results.count() == len(hits)
    assert h.side.currentWidget() is h.results
    h.open_result(0)
    assert h.topic == hits[0].topic.id
    assert len(h.browser.extraSelections()) > 0
    h.search.setText("zzzqqq")
    h.run_search()
    assert h.side.currentWidget() is h.no_results
    h.search.clear()
    h.run_search()
    assert h.side.currentWidget() is h.tree
    assert h.browser.extraSelections() == []


def test_web_addresses_are_never_opened(qtbot: QtBot) -> None:
    make_app()
    b = HelpBrowser(HELP)
    qtbot.addWidget(b)
    seen: list[str] = []
    b.link_clicked.connect(seen.append)
    b.anchorClicked.emit(QUrl("https://example.com/"))
    b.anchorClicked.emit(QUrl("saving.md#the-saved-screen"))
    assert seen == ["saving.md#the-saved-screen"]


def test_pictures_only_come_from_the_help_folder(qtbot: QtBot) -> None:
    make_app()
    b = HelpBrowser(HELP)
    qtbot.addWidget(b)
    kind = QTextDocument.ResourceType.ImageResource.value
    assert not b.loadResource(kind, QUrl("images/drop.png")).isNull()
    assert b.loadResource(kind, QUrl("../../config/default.json")).isNull()
    assert b.loadResource(kind, QUrl("images/missing.png")).isNull()


def test_pictures_fit_the_text_column(qtbot: QtBot) -> None:
    make_app()
    b = HelpBrowser(HELP)
    qtbot.addWidget(b)
    b.resize(500, 400)
    b.show()
    image = b.loadResource(QTextDocument.ResourceType.ImageResource.value, QUrl("images/drop.png"))
    assert image.width() / image.devicePixelRatio() <= 500


def test_clicking_a_picture_names_it(qtbot: QtBot) -> None:
    make_app()
    h = HelpWindow()
    qtbot.addWidget(h)
    h.resize(1000, 700)
    h.show()
    h.open("opening-files")
    qtbot.wait(50)
    doc = h.browser.document()
    block = doc.begin()
    while block.isValid() and "￼" not in block.text():
        block = block.next()
    assert block.isValid()
    rect = doc.documentLayout().blockBoundingRect(block)
    h.browser.verticalScrollBar().setValue(int(rect.top()))
    point = QPoint(int(rect.left() + 40), int(rect.top() - h.browser.verticalScrollBar().value() + 20))
    assert h.browser.image_at(point)[0] == "images/drop.png"


# ------------------------------------------------------------------ Help buttons and F1


def _check(window: MainWindow, expected: str) -> None:
    """F1 and the screen's Help button both open the expected topic."""
    help_window = window.help_for_screen()
    want = window.help.book.resolve(expected)
    assert want is not None, expected
    assert (help_window.topic, help_window.anchor) == want
    help_window.open("about")
    screen = window.stack.currentWidget()
    screen.help_button.click()
    assert (help_window.topic, help_window.anchor) == want


def test_help_from_the_drop_dialog_and_saved_screens(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                                     corpus_dir: Path, tmp_path: Path) -> None:
    _check(window, "opening-files")
    src = copy_to(corpus_dir / "provider_return_99-2026-999901.pdf", tmp_path)
    d = open_dialog(qtbot, window, src)
    _check(window, "stamping-one-file")
    choose(d, "doc_type", "Search warrant return")
    wait_ready(qtbot, d)
    fake.save_paths.append(tmp_path / "out.pdf")
    d.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.saved, timeout=WAIT_MS)
    _check(window, "saving#the-saved-screen")


def test_help_from_profile_settings_follows_the_section(qtbot: QtBot, window: MainWindow) -> None:
    s = window.open_settings()
    assert s is not None
    for i, topic in enumerate(profiles.SECTION_HELP):
        s.show_section(i)
        _check(window, topic)
    s.show_section(1)
    s.fields_page.select_key("identifier")
    s.fields_page.more_help.click()
    assert window.help is not None and window.help.topic == "validation-rules"


def test_help_from_the_several_files_screens(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                             corpus_dir: Path, tmp_path: Path) -> None:
    a = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path, "a.pdf")
    b = copy_to(corpus_dir / "letter_landscape.pdf", tmp_path, "b.pdf")
    signed = make_signed_pdf(corpus_dir, tmp_path / "signed.pdf")

    def files(paths: list[Path]) -> None:
        window.show_drop()
        window.open_several(paths)
        qtbot.waitUntil(lambda: window.stack.currentWidget() is window.files_screen, timeout=WAIT_MS)

    def dialog(mode: str) -> None:
        window.choose_mode(mode)
        qtbot.waitUntil(lambda: window.dialog is not None and window.dialog.ready(), timeout=WAIT_MS)

    files([a, b, signed])
    _check(window, "several-files")
    window.choose_mode("combine")
    qtbot.waitUntil(lambda: window.prepare is not None, timeout=WAIT_MS)
    _check(window, "several-files#before-stamping")

    files([a, b])
    dialog("combine")
    _check(window, "several-files#stamping-the-combined-file")

    files([a, b])
    dialog("batch")
    _check(window, "several-files#stamping-each-file-separately")
    d = window.dialog
    assert d is not None
    choose(d, "doc_type", "Search warrant return")
    type_into(qtbot, d, "identifier", "99-2026-999901")
    wait_ready(qtbot, d)
    d.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.batch_saved, timeout=WAIT_MS)
    _check(window, "several-files#the-results")


def test_partial_copies_get_their_own_topic(window: MainWindow) -> None:
    window.drop.leftover_frame.show()
    try:
        assert window.help_topic() == "messages#partial-copies-left-behind"
    finally:
        window.drop.leftover_frame.hide()


def test_closing_the_window_closes_the_help(qtbot: QtBot, fake: FakeServices) -> None:
    from tests.stamping import RECORDS_CONFIG

    make_app()
    w = MainWindow(config_path=RECORDS_CONFIG, services=fake.services())
    qtbot.addWidget(w)
    w.show()
    h = w.show_help("about")
    assert h.isVisible()
    closed: list[bool] = []
    h.destroyed.connect(lambda: closed.append(True))
    w.close()
    qtbot.waitUntil(lambda: bool(closed), timeout=WAIT_MS)
    assert w.help is None


# ------------------------------------------------------------------ the help and the interface agree


def _interface_strings() -> set[str]:
    """Every string written in the interface code; f-string values become ``N``."""
    out: set[str] = set()
    for path in [*GUI.glob("*.py"), REPO / "stamper" / "sendto.py"]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.add(node.value)
            elif isinstance(node, ast.JoinedStr):
                out.add("".join(v.value if isinstance(v, ast.Constant) else "N" for v in node.values))
    return out


def _bold(book: HelpBook) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for t in book.ordered():
        for m in re.finditer(r"\*\*(.+?)\*\*", t.markdown):
            text = m.group(1)
            if text.endswith("."):
                continue  # a whole sentence in bold is emphasis, not a label
            found.add((t.id, text))
    return found


def test_bold_words_are_interface_wording(book: HelpBook) -> None:
    strings = _interface_strings()
    trimmed = {s.strip().rstrip(":") for s in strings}
    unknown = sorted((tid, text) for tid, text in _bold(book) if text not in strings and text not in trimmed)
    assert unknown == [], "bold is for words the interface shows; rename in the help or use italics"


def _controls() -> set[str]:
    """Button, tick box and choice wording in the interface."""
    names = {"QPushButton", "QCheckBox", "QRadioButton", "_small"}
    out: set[str] = set()
    for path in GUI.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and node.args:
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
                arg = node.args[0]
                if name in names and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out.add(arg.value)
    out.update(profiles.SECTIONS)
    out.update(text for _, text in profiles._NORMALIZERS)
    out.update(text for _, text in profiles._DIRECTIONS)
    out.update(page_numbers.CHOICE_LABELS.values())
    out.update(("Add", "Duplicate", "Delete", "Make default", "Add field", "Add list", "Add choice"))
    out.update(("Combine without valid signatures", "Save copies without valid signatures", "Stamp them anyway"))
    out.update(("Dark colors", "Standard colors"))
    assert color_picker.STAMP_COLORS and multi.COMBINE  # the modules these come from are imported
    return out


def test_every_control_is_in_the_help(book: HelpBook) -> None:
    text = "\n".join(t.markdown for t in book.ordered())
    missing = sorted(c for c in _controls() - set(NOT_DOCUMENTED) if c not in text)
    assert missing == [], "a button or tick box the help doesn't mention; document it or add it to NOT_DOCUMENTED"


def _parser_options() -> set[str]:
    opts: set[str] = set()
    for action in build_parser()._actions:
        opts.update(o for o in action.option_strings if o.startswith("--"))
    return opts


def test_every_command_line_option_is_documented(book: HelpBook) -> None:
    md = book.topics["command-line"].markdown
    missing = sorted(o for o in _parser_options() if f"`{o}" not in md)
    assert missing == []


def test_no_documented_option_is_made_up(book: HelpBook) -> None:
    text = "\n".join(t.markdown for t in book.ordered())
    mentioned = set(re.findall(r"(?<![\w-])--[a-z][a-z-]+", text))
    removed = {"--password-for"}  # documented as removed (D-126)
    assert sorted(mentioned - _parser_options() - removed) == []


# ------------------------------------------------------------------ found while taking the pictures (D-137)


def test_a_long_file_name_doesnt_widen_the_panel(qtbot: QtBot, window: MainWindow, corpus_dir: Path,
                                                 tmp_path: Path) -> None:
    """A suggested name with no spaces used to push every field past the panel's right edge."""
    src = copy_to(corpus_dir / "provider_return_99-2026-999901.pdf", tmp_path)
    d = open_dialog(qtbot, window, src)
    choose(d, "doc_type", "Search warrant return")
    type_into(qtbot, d, "identifier", "99-2026-99990")  # no longer matches: the original name is kept whole
    wait_ready(qtbot, d)
    assert len(d.name_label.text()) > 45
    assert d.form.width() <= d.scroll.viewport().width()
    assert d.name_label.toolTip() == d.name_label.text()
