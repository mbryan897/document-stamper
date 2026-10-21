"""The window with several files (Phase 4.5): routing, ordering, Combine, Batch, pre-fill, cancel, size.

Native dialogs never open: ``FakeServices`` answers. Synthetic files only.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QWidget
from pytestqt.qtbot import QtBot

from stamper.__main__ import route
from stamper.gui.app import make_app
from stamper.gui.main_window import MINIMUM_SIZE, MainWindow
from stamper.gui.multi import BatchDialog, CombineDialog, PrepareScreen
from stamper.gui.stamp_dialog import StampDialog
from stamper.handlers.batch import Status
from stamper.core.locations import work_dir
from stamper.handlers.combine import TEMP_PREFIX
from stamper.handlers.pdf import PdfHandler
from tests.gui_support import (  # noqa: F401 - fixtures
    TODAY,
    WAIT_MS,
    FakeServices,
    choose,
    close_window,
    copy_to,
    fake,
    type_into,
    wait_ready,
    window,
)
from tests.multi_support import make_tiny
from tests.stamping import RECORDS_CONFIG, make_signed_pdf, sha256
from tools import make_corpus

PW = make_corpus.USER_PASSWORD
CASE = "99-2026-999901"


# ------------------------------------------------------------ helpers


def _temp_left(folder: Path) -> list[Path]:
    """Combine working folders left: in the private work folder (D-115), or in ``folder`` (the fallback)."""
    places = [work_dir(), folder]
    return [p for place in places if place.is_dir() for p in place.iterdir() if p.name.startswith(TEMP_PREFIX)]


def _in_output_folder(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name.startswith("~"))


def open_several(qtbot: QtBot, w: MainWindow, paths: list[Path]) -> None:
    """Open several files and wait for the files screen."""
    w.open_files(paths)
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.files_screen, timeout=WAIT_MS)


def three_files(corpus_dir: Path, folder: Path) -> list[Path]:
    """Signed, password-protected, rotated."""
    signed = make_signed_pdf(corpus_dir, folder / "signed_return.pdf")
    locked = copy_to(corpus_dir / "encrypted_user_password.pdf", folder)
    rotated = copy_to(corpus_dir / "rotate_90.pdf", folder)
    return [signed, locked, rotated]


def prepare(qtbot: QtBot, w: MainWindow) -> PrepareScreen:
    qtbot.waitUntil(lambda: w.prepare is not None and w.stack.currentWidget() is w.prepare, timeout=WAIT_MS)
    assert w.prepare is not None
    return w.prepare


def give_passwords(qtbot: QtBot, screen: PrepareScreen) -> None:
    for name, edit in screen.password_rows():
        edit.setText(PW)
        edit.returnPressed.emit()
    qtbot.waitUntil(lambda: not screen.password_rows() or all(
        not f.needs_password for f in screen.files()), timeout=WAIT_MS)


def wait_dialog(qtbot: QtBot, w: MainWindow, kind: type) -> StampDialog:
    qtbot.waitUntil(lambda: isinstance(w.dialog, kind) and w.stack.currentWidget() is w.dialog, timeout=WAIT_MS)
    assert w.dialog is not None
    return wait_ready(qtbot, w.dialog)


def combine_three(qtbot: QtBot, w: MainWindow, files: list[Path]) -> tuple[CombineDialog, list[Path]]:
    """Signed, locked, rotated, reordered to rotated, signed, locked; through to the stamp dialog."""
    open_several(qtbot, w, files)
    fs = w.files_screen
    fs.list.setCurrentRow(2)
    fs.move(-1)
    fs.move(-1)  # rotated is first now
    order = fs.paths()
    assert [p.name for p in order] == ["rotate_90.pdf", "signed_return.pdf", "encrypted_user_password.pdf"]
    fs.combine_button.click()
    screen = prepare(qtbot, w)
    assert "encrypted_user_password.pdf" in screen.status.text()
    give_passwords(qtbot, screen)
    assert screen.signature_tick is not None and not screen.continue_button.isEnabled()
    screen.signature_tick.setChecked(True)
    assert screen.continue_button.isEnabled()
    screen.continue_button.click()
    d = wait_dialog(qtbot, w, CombineDialog)
    assert isinstance(d, CombineDialog)
    return d, order


# ------------------------------------------------------------ routing


def test_routing_one_file_and_several(corpus_dir: Path) -> None:
    one, two = corpus_dir / "letter_portrait.pdf", corpus_dir / "rotate_90.pdf"
    assert route([str(one)]).kind == "window"
    r = route([str(one), str(two)])
    assert r.kind == "window" and r.files == (one, two)
    assert route(["--batch", str(one), str(two)]).kind == "cli"


def test_one_file_still_opens_the_stamp_dialog(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    window.open_files([corpus_dir / "letter_portrait.pdf"])
    qtbot.waitUntil(lambda: window.dialog is not None, timeout=WAIT_MS)
    assert type(window.dialog) is StampDialog


def test_several_files_open_the_files_screen(qtbot: QtBot, window: MainWindow, corpus_dir: Path,
                                             tmp_path: Path) -> None:
    notes = tmp_path / "notes.pdf"
    notes.write_text("not a pdf", encoding="utf-8")
    from tests.stamping import make_xfa_pdf

    xfa = make_xfa_pdf(corpus_dir, tmp_path / "form.pdf")
    paths = [corpus_dir / "letter_portrait.pdf", notes, corpus_dir / "rotate_90.pdf", xfa,
             corpus_dir / "letter_portrait.pdf"]
    open_several(qtbot, window, paths)
    fs = window.files_screen
    assert [p.name for p in fs.paths()] == ["letter_portrait.pdf", "rotate_90.pdf"]
    text = fs.message.text()
    assert "notes.pdf isn't a PDF file" in text and "form.pdf is an XFA form" in text
    assert "letter_portrait.pdf is listed twice" in text
    assert fs.combine_button.isVisible() and fs.batch_button.isVisible()


def test_one_usable_file_offers_to_stamp_it_alone(qtbot: QtBot, window: MainWindow, corpus_dir: Path,
                                                  tmp_path: Path) -> None:
    notes = tmp_path / "notes.pdf"
    notes.write_text("not a pdf", encoding="utf-8")
    open_several(qtbot, window, [corpus_dir / "letter_portrait.pdf", notes])
    fs = window.files_screen
    assert not fs.combine_button.isVisible() and fs.single_button.isVisible()
    fs.single_button.click()
    qtbot.waitUntil(lambda: type(window.dialog) is StampDialog, timeout=WAIT_MS)


def test_choose_files_picks_several(qtbot: QtBot, window: MainWindow, fake: FakeServices, corpus_dir: Path) -> None:
    picked = [corpus_dir / "letter_portrait.pdf", corpus_dir / "rotate_90.pdf"]
    window.services.ask_open_files = lambda _p, _s: picked
    window.choose_file()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.files_screen, timeout=WAIT_MS)
    assert window.files_screen.paths() == picked


# ------------------------------------------------------------ ordering


def test_reordering_by_drag_and_buttons(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    names = ["letter_portrait.pdf", "rotate_90.pdf", "mixed_sizes.pdf"]
    open_several(qtbot, window, [corpus_dir / n for n in names])
    fs = window.files_screen
    fs.drag_row(0, 3)  # drag the first file to the end
    assert [p.name for p in fs.paths()] == ["rotate_90.pdf", "mixed_sizes.pdf", "letter_portrait.pdf"]
    assert fs.list.item(0).text().startswith("1.  rotate_90.pdf")
    fs.list.setCurrentRow(1)
    fs.down_button.click()
    assert [p.name for p in fs.paths()] == ["rotate_90.pdf", "letter_portrait.pdf", "mixed_sizes.pdf"]
    fs.up_button.click()
    fs.up_button.click()
    assert [p.name for p in fs.paths()] == ["mixed_sizes.pdf", "rotate_90.pdf", "letter_portrait.pdf"]
    assert not fs.up_button.isEnabled()
    fs.remove_button.click()
    assert [p.name for p in fs.paths()] == ["rotate_90.pdf", "letter_portrait.pdf"]


def test_order_reaches_the_combined_file(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                         corpus_dir: Path, tmp_path: Path) -> None:
    files = [copy_to(corpus_dir / n, tmp_path) for n in ("letter_portrait.pdf", "rotate_90.pdf", "mixed_sizes.pdf")]
    open_several(qtbot, window, files)
    window.files_screen.drag_row(2, 0)  # mixed_sizes first
    window.files_screen.combine_button.click()
    d = wait_dialog(qtbot, window, CombineDialog)
    choose(d, "doc_type", "Court order")
    type_into(qtbot, d, "identifier", CASE)
    wait_ready(qtbot, d)
    out = tmp_path / "ordered.pdf"
    fake.save_paths.append(out)
    d.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.saved, timeout=WAIT_MS)
    with pymupdf.open(out) as doc:
        assert [t[1] for t in doc.get_toc() if t[0] == 1] == ["mixed_sizes.pdf", "letter_portrait.pdf",
                                                              "rotate_90.pdf"]


# ------------------------------------------------------------ combine


def test_combine_signed_locked_rotated(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                       corpus_dir: Path, tmp_path: Path) -> None:
    files = three_files(corpus_dir, tmp_path)
    before = {p: sha256(p) for p in files}
    d, order = combine_three(qtbot, window, files)
    assert d.info.page_count == 3 + 1 + 2
    assert d.page_source_text() == "rotate_90.pdf, page 1 of 3"
    d.jump_combo.setCurrentIndex(2)
    d.jump_combo.activated.emit(2)
    assert d.page == 5 and d.page_source_text() == "encrypted_user_password.pdf, page 1 of 2"
    choose(d, "doc_type", "Court order")
    type_into(qtbot, d, "identifier", CASE)
    wait_ready(qtbot, d)
    assert d.save_button.isEnabled(), d.blockers()
    assert fake.save_suggestions == []
    out = tmp_path / "exhibit.pdf"
    fake.save_paths.append(out)
    d.save_button.click()
    assert fake.save_suggestions[0].parent == order[0].parent
    assert fake.save_suggestions[0].name.endswith("rotate_90_combined.pdf")
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.saved, timeout=WAIT_MS)
    notes = window.saved.notes.text()
    assert "Protected files in it: encrypted_user_password.pdf" in notes
    assert "Bookmarks: one for each of the 3 files" in notes
    assert window.saved.original_label.text().startswith("All 3 files unchanged")
    qtbot.waitUntil(lambda: not _temp_left(tmp_path), timeout=WAIT_MS)
    for p, digest in before.items():
        assert sha256(p) == digest
    with pymupdf.open(out) as doc:
        assert not doc.needs_pass
        assert [t[1] for t in doc.get_toc() if t[0] == 1] == [p.name for p in order]
        assert f"{CASE}  |  Court order  |  Page 6 of 6" in doc[5].get_text()


def test_combine_refuses_saving_over_an_original(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                                 corpus_dir: Path, tmp_path: Path) -> None:
    files = [copy_to(corpus_dir / n, tmp_path) for n in ("letter_portrait.pdf", "rotate_90.pdf")]
    open_several(qtbot, window, files)
    window.files_screen.combine_button.click()
    d = wait_dialog(qtbot, window, CombineDialog)
    assert "rotate_90.pdf, one of the originals" in d.check_output(files[1])


def test_back_from_combine_removes_the_temp_file(qtbot: QtBot, window: MainWindow, corpus_dir: Path,
                                                 tmp_path: Path) -> None:
    files = [copy_to(corpus_dir / n, tmp_path) for n in ("letter_portrait.pdf", "rotate_90.pdf")]
    open_several(qtbot, window, files)
    window.files_screen.combine_button.click()
    d = wait_dialog(qtbot, window, CombineDialog)
    assert _temp_left(tmp_path)
    assert _in_output_folder(tmp_path) == []  # the working copy is in the private folder (D-115)
    d.cancel_button.click()
    assert window.stack.currentWidget() is window.files_screen
    qtbot.waitUntil(lambda: not _temp_left(tmp_path), timeout=WAIT_MS)


def test_closing_the_window_removes_the_temp_file(qtbot: QtBot, fake: FakeServices, corpus_dir: Path,
                                                  tmp_path: Path) -> None:
    make_app()
    w = MainWindow(config_path=RECORDS_CONFIG, services=fake.services(), today=TODAY)
    qtbot.addWidget(w)
    w.show()
    files = [copy_to(corpus_dir / n, tmp_path) for n in ("letter_portrait.pdf", "rotate_90.pdf")]
    open_several(qtbot, w, files)
    w.files_screen.combine_button.click()
    wait_dialog(qtbot, w, CombineDialog)
    assert _temp_left(tmp_path)
    close_window(w)
    assert not _temp_left(tmp_path)


# ------------------------------------------------------------ cancel


class SlowHandler(PdfHandler):
    """Stamps slowly, so a test can press Cancel part-way through."""

    def apply(self, path, job, out_path, **kw):  # type: ignore[no-untyped-def, override]
        progress = kw.get("progress")

        def slow(done: int, total: int) -> None:
            time.sleep(0.02)
            if progress is not None:
                progress(done, total)

        kw["progress"] = slow
        return super().apply(path, job, out_path, **kw)


@pytest.fixture
def slow_window(qtbot: QtBot, fake: FakeServices) -> Iterator[MainWindow]:
    make_app()
    w = MainWindow(config_path=RECORDS_CONFIG, services=fake.services(), today=TODAY, handler=SlowHandler())
    qtbot.addWidget(w)
    w.resize(1200, 780)
    w.show()
    yield w
    close_window(w)


def test_cancel_mid_combine(qtbot: QtBot, window: MainWindow, corpus_dir: Path, tmp_path: Path,
                            monkeypatch: pytest.MonkeyPatch) -> None:
    import stamper.gui.multi as multi

    real = multi.combine_pdfs

    def slow_combine(inputs, folder=None, **kw):  # type: ignore[no-untyped-def]
        progress = kw["progress"]

        def slow(done: int, total: int, name: str) -> None:
            progress(done, total, name)
            time.sleep(0.4)

        kw["progress"] = slow
        return real(inputs, folder, **kw)

    monkeypatch.setattr(multi, "combine_pdfs", slow_combine)
    files = [copy_to(corpus_dir / n, tmp_path) for n in ("letter_portrait.pdf", "rotate_90.pdf", "mixed_sizes.pdf")]
    open_several(qtbot, window, files)
    window.files_screen.combine_button.click()
    qtbot.waitUntil(lambda: window.busy.progress.value() >= 1, timeout=WAIT_MS)
    assert _temp_left(tmp_path)
    window.busy.cancel_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.files_screen, timeout=WAIT_MS)
    assert "Combining was cancelled" in window.files_screen.message.text()
    assert not _temp_left(tmp_path)


def test_cancel_mid_batch_keeps_saved_copies(qtbot: QtBot, slow_window: MainWindow, corpus_dir: Path,
                                             tmp_path: Path) -> None:
    w = slow_window
    files = [copy_to(corpus_dir / n, tmp_path) for n in ("letter_portrait.pdf", "rotate_mixed.pdf",
                                                         "mixed_sizes.pdf")]
    open_several(qtbot, w, files)
    w.files_screen.batch_button.click()
    d = wait_dialog(qtbot, w, BatchDialog)
    choose(d, "doc_type", "Court order")
    type_into(qtbot, d, "identifier", CASE)
    wait_ready(qtbot, d)
    d.save_button.click()
    qtbot.waitUntil(lambda: "File 2 of 3" in d.progress_label.text(), timeout=WAIT_MS)
    d.cancel_button.click()
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.batch_saved, timeout=WAIT_MS)
    assert [s for _, s in w.batch_saved.statuses()] == [Status.SAVED, Status.CANCELLED, Status.NOT_STARTED]
    saved = [p for p in tmp_path.iterdir() if p.name.startswith(CASE)]
    assert len(saved) == 1 and "letter_portrait" in saved[0].name
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("~")]


# ------------------------------------------------------------ batch


def test_two_locked_files_checked_at_once(qtbot: QtBot, window: MainWindow, corpus_dir: Path,
                                         tmp_path: Path) -> None:
    one = copy_to(corpus_dir / "encrypted_user_password.pdf", tmp_path, "locked_one.pdf")
    two = copy_to(corpus_dir / "encrypted_user_password.pdf", tmp_path, "locked_two.pdf")
    open_several(qtbot, window, [one, two, copy_to(corpus_dir / "rotate_90.pdf", tmp_path)])
    window.files_screen.batch_button.click()
    screen = prepare(qtbot, window)
    rows = screen.password_rows()
    assert len(rows) == 2
    for _name, edit in rows:  # Check both before either answer arrives
        edit.setText(PW)
        edit.returnPressed.emit()
    rows[1][1].setText("typed-after-check")  # edited while its check runs
    qtbot.waitUntil(lambda: all(not f.needs_password for f in screen.files()), timeout=WAIT_MS)
    assert {f.path.name: f.password for f in screen.files() if f.path.name.startswith("locked")} == {
        "locked_one.pdf": PW, "locked_two.pdf": PW}


def test_batch_five_files(qtbot: QtBot, window: MainWindow, fake: FakeServices, corpus_dir: Path,
                          tmp_path: Path) -> None:
    locked = copy_to(corpus_dir / "encrypted_user_password.pdf", tmp_path)
    restricted = copy_to(corpus_dir / "owner_restrictions.pdf", tmp_path)
    plain = copy_to(corpus_dir / "rotate_90.pdf", tmp_path)
    tiny = make_tiny(tmp_path / "tiny.pdf")
    taken = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    files = [locked, restricted, plain, tiny, taken]
    before = {p: sha256(p) for p in files}
    open_several(qtbot, window, files)
    window.files_screen.batch_button.click()
    screen = prepare(qtbot, window)
    give_passwords(qtbot, screen)
    assert screen.restriction_tick is not None
    screen.restriction_tick.setChecked(True)
    screen.continue_button.click()
    d = wait_dialog(qtbot, window, BatchDialog)
    assert isinstance(d, BatchDialog)
    choose(d, "doc_type", "Court order")
    type_into(qtbot, d, "identifier", CASE)
    wait_ready(qtbot, d)
    existing = tmp_path / d.name_for(taken)
    existing.write_bytes(b"already here - must not be replaced")
    d.show_file(3)  # the tiny page: its problem shows, but doesn't block the others
    wait_ready(qtbot, d)
    assert d.placement_problem()
    assert d.save_button.isEnabled(), d.blockers()
    assert d.save_button.text() == "Stamp all 5 files…"
    fake.confirms.append(True)
    d.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.batch_saved, timeout=WAIT_MS)
    title, text = fake.questions[-1]
    assert "tiny.pdf" in text and "letter_portrait" in text and "Stamp the other 3?" in text
    assert window.batch_saved.statuses() == [
        ("encrypted_user_password.pdf", Status.SAVED), ("owner_restrictions.pdf", Status.SAVED),
        ("rotate_90.pdf", Status.SAVED), ("tiny.pdf", Status.FAILED), ("letter_portrait.pdf", Status.SKIPPED)]
    assert len(window.batch_saved.open_buttons) == 3
    assert existing.read_bytes() == b"already here - must not be replaced"
    for p, digest in before.items():
        assert sha256(p) == digest
    with pymupdf.open(tmp_path / d_name(window, locked)) as doc:
        assert doc.needs_pass and doc.authenticate(PW)  # protection kept (D-050)
    window.batch_saved.open_buttons[0].click()
    assert fake.opened
    window.batch_saved.folder_button.click()
    assert fake.shown


def d_name(w: MainWindow, src: Path) -> str:
    """The saved copy's name for a source, from the batch saved screen."""
    for o in w.batch_saved.outcomes:
        if o.item.path == src:
            return o.item.out_path.name
    raise AssertionError(src)


def test_batch_into_one_folder(qtbot: QtBot, window: MainWindow, fake: FakeServices, corpus_dir: Path,
                               tmp_path: Path) -> None:
    files = [copy_to(corpus_dir / n, tmp_path) for n in ("letter_portrait.pdf", "rotate_90.pdf")]
    out = tmp_path / "stamped"
    out.mkdir()
    open_several(qtbot, window, files)
    window.files_screen.batch_button.click()
    d = wait_dialog(qtbot, window, BatchDialog)
    assert isinstance(d, BatchDialog)
    window.services.ask_folder = lambda _p, _s: out
    d.folder_radio.click()
    assert d.out_folder == out
    choose(d, "doc_type", "Court order")
    type_into(qtbot, d, "identifier", CASE)
    wait_ready(qtbot, d)
    d.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.batch_saved, timeout=WAIT_MS)
    assert len(list(out.iterdir())) == 2


# ------------------------------------------------------------ identifier pre-fill (D-106)


@pytest.mark.parametrize("mode", ["combine", "batch"])
@pytest.mark.parametrize(
    ("names", "value", "conflict"),
    [
        (["return_99-2026-999901.pdf", "99-2026-999901_phone.pdf"], CASE, False),
        (["return_99-2026-999901.pdf", "return_99-2026-999903.pdf"], "", True),
        (["cover.pdf", "photos.pdf"], "", False),
    ],
    ids=["agree", "disagree", "none"],
)
def test_identifier_prefill(qtbot: QtBot, window: MainWindow, corpus_dir: Path, tmp_path: Path,
                            mode: str, names: list[str], value: str, conflict: bool) -> None:
    files = [copy_to(corpus_dir / "letter_portrait.pdf", tmp_path, n) for n in names]
    open_several(qtbot, window, files)
    (window.files_screen.combine_button if mode == "combine" else window.files_screen.batch_button).click()
    d = wait_dialog(qtbot, window, CombineDialog if mode == "combine" else BatchDialog)
    assert d.editor("identifier").text() == value  # type: ignore[attr-defined]
    note = d.note("identifier")
    if conflict:
        assert "return_99-2026-999901.pdf → 99-2026-999901" in note
        assert "return_99-2026-999903.pdf → 99-2026-999903" in note
    else:
        assert note == ""
    if value:
        assert d.message("identifier").startswith("Taken from the file name.")


# ------------------------------------------------------------ window size (D-093)


def _inside(window: MainWindow, button: QPushButton) -> bool:
    top = button.mapTo(window, QPoint(0, 0)).y()
    return button.isVisible() and top >= 0 and top + button.height() <= window.height()


def _clipped(widget: QWidget) -> bool:
    return widget.height() < widget.minimumSizeHint().height()


@pytest.fixture
def small(window: MainWindow, qtbot: QtBot) -> MainWindow:
    window.resize(MINIMUM_SIZE)
    qtbot.waitUntil(lambda: window.size() == MINIMUM_SIZE)
    return window


def test_files_screen_fits_at_minimum(qtbot: QtBot, small: MainWindow, corpus_dir: Path, tmp_path: Path) -> None:
    files = [copy_to(corpus_dir / "letter_portrait.pdf", tmp_path, f"file_{i:02}.pdf") for i in range(12)]
    notes = [tmp_path / f"bad_{i}.pdf" for i in range(3)]
    for n in notes:
        n.write_text("x", encoding="utf-8")
    open_several(qtbot, small, files + notes)
    qtbot.wait(50)
    fs = small.files_screen
    for b in (fs.combine_button, fs.batch_button, fs.up_button, fs.remove_button, fs.add_button):
        assert _inside(small, b), b.text()
    assert not _clipped(fs)


def test_prepare_screen_fits_at_minimum(qtbot: QtBot, small: MainWindow, corpus_dir: Path, tmp_path: Path) -> None:
    files = [copy_to(corpus_dir / "encrypted_user_password.pdf", tmp_path, f"locked_{i}.pdf") for i in range(6)]
    files.append(make_signed_pdf(corpus_dir, tmp_path / "signed.pdf"))
    files.append(copy_to(corpus_dir / "owner_restrictions.pdf", tmp_path))
    open_several(qtbot, small, files)
    small.files_screen.combine_button.click()
    screen = prepare(qtbot, small)
    qtbot.wait(50)
    assert _inside(small, screen.continue_button) and _inside(small, screen.back_button)
    assert not _clipped(screen)


def test_busy_screen_fits_at_minimum(small: MainWindow) -> None:
    small.busy.start("Combining 3 files…", "A long detail line. " * 20)
    small.stack.setCurrentWidget(small.busy)
    assert _inside(small, small.busy.cancel_button)
    assert not _clipped(small.busy)


def test_multi_dialogs_fit_at_minimum(qtbot: QtBot, small: MainWindow, corpus_dir: Path, tmp_path: Path) -> None:
    files = [copy_to(corpus_dir / "letter_portrait.pdf", tmp_path, f"return_99-2026-99990{i}.pdf") for i in range(1, 4)]
    open_several(qtbot, small, files)
    small.files_screen.combine_button.click()
    d = wait_dialog(qtbot, small, CombineDialog)
    assert _inside(small, d.save_button) and _inside(small, d.cancel_button)
    assert not _clipped(d)
    d.cancel_button.click()
    small.files_screen.batch_button.click()
    b = wait_dialog(qtbot, small, BatchDialog)
    assert _inside(small, b.save_button) and _inside(small, b.cancel_button)
    assert not _clipped(b)


def test_saved_screens_fit_at_minimum(small: MainWindow) -> None:
    from stamper.handlers.base import ApplyResult
    from stamper.handlers.batch import BatchItem, BatchOutcome

    result = ApplyResult(output_path=Path("out.pdf"), pages_stamped=40,
                         warnings=tuple(f"Page {n}, stamp 1: font reduced." for n in range(40)),
                         placements=(), metadata_written={}, metadata_replaced={}, encryption_kept=False)
    extra = ["The combined file is saved without a password or restrictions. Protected files in it: a.pdf.",
             "Bookmarks: one for each of the 3 files, with each file's own bookmarks under it."]
    small.saved.show_result(result, label="Case number", identifier="X", signed=False, extra_notes=extra,
                            original_text="All 3 files unchanged")
    small.stack.setCurrentWidget(small.saved)
    assert _inside(small, small.saved.open_button)
    outcomes = [BatchOutcome(BatchItem(Path(f"file_{i}.pdf"), Path(f"out_{i}.pdf")),
                             Status.FAILED if i % 3 else Status.SKIPPED, "A reason. " * 8) for i in range(30)]
    small.batch_saved.show_outcomes(outcomes)
    small.stack.setCurrentWidget(small.batch_saved)
    assert _inside(small, small.batch_saved.folder_button) and _inside(small, small.batch_saved.another_button)
    assert not _clipped(small.batch_saved)


# ------------------------------------------------------------ fixes found by looking at the screens (D-113)


def _indicator_drawn(box: QCheckBox) -> bool:
    """True if the tick box's square differs from the notice behind it."""
    from PySide6.QtWidgets import QStyle, QStyleOptionButton

    opt = QStyleOptionButton()
    box.initStyleOption(opt)
    rect = box.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, opt, box)
    image = box.grab().toImage()
    background = image.pixelColor(box.width() - 2, box.height() // 2)
    for x in range(rect.left(), rect.right() + 1):
        for y in range(rect.top(), rect.bottom() + 1):
            c = image.pixelColor(x, y)
            if abs(c.red() - background.red()) + abs(c.green() - background.green()) \
                    + abs(c.blue() - background.blue()) > 60:
                return True
    return False


def test_ticks_inside_notices_are_visible(qtbot: QtBot, window: MainWindow, corpus_dir: Path,
                                          tmp_path: Path) -> None:
    files = three_files(corpus_dir, tmp_path)
    open_several(qtbot, window, files)
    window.files_screen.combine_button.click()
    screen = prepare(qtbot, window)
    qtbot.wait(50)
    assert screen.signature_tick is not None
    assert _indicator_drawn(screen.signature_tick)


def test_protection_warning_shows_before_the_password(qtbot: QtBot, window: MainWindow, corpus_dir: Path,
                                                      tmp_path: Path) -> None:
    files = three_files(corpus_dir, tmp_path)
    open_several(qtbot, window, files)
    window.files_screen.combine_button.click()
    screen = prepare(qtbot, window)
    texts = [label.text() for label in screen.findChildren(QLabel)]
    assert any("saved without a password" in t and "encrypted_user_password.pdf" in t for t in texts)


def test_batch_preview_not_cropped_at_minimum(qtbot: QtBot, small: MainWindow, corpus_dir: Path,
                                              tmp_path: Path) -> None:
    files = [copy_to(corpus_dir / "letter_portrait.pdf", tmp_path, f"return_{i}.pdf") for i in range(5)]
    open_several(qtbot, small, files)
    small.files_screen.batch_button.click()
    b = wait_dialog(qtbot, small, BatchDialog)
    assert isinstance(b, BatchDialog)
    assert b.file_list.geometry().bottom() < b.preview.mapTo(b, QPoint(0, 0)).y()  # list above the page
    assert b.preview.width() >= 200  # the preview's own minimum; narrower crops the page
