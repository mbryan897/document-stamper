"""Save As, stamping off the UI thread, cancel, and the saved screen."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QElapsedTimer, Qt, QTimer
from pytestqt.qtbot import QtBot

from stamper.gui.main_window import MainWindow
from stamper.gui.stamp_dialog import StampDialog
from stamper.handlers.pdf import BOX_PADDING_PT
from tests.gui_support import (  # noqa: F401 - fixtures
    FakeServices,
    choose,
    copy_to,
    fake,
    open_dialog,
    type_into,
    wait_ready,
    window,
)
from tests.stamping import MARGIN_PT, make_signed_pdf, open_pdf, pixel_bbox, sha256, unrotated_vector_to_visible
from tools import make_corpus

SO = make_corpus.FAKE_CASE_SO
TOL_PT = 2.0


def _fill(qtbot: QtBot, d: StampDialog, doc_type: str = "Court order") -> None:
    type_into(qtbot, d, "identifier", SO)
    choose(d, "doc_type", doc_type)
    wait_ready(qtbot, d)
    assert d.save_button.isEnabled(), d.blockers()


def _leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name.startswith("~stamper-"))


# ------------------------------------------------------------ refusals


def test_refuses_the_original(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                              corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    before = sha256(src)
    d = open_dialog(qtbot, window, src)
    _fill(qtbot, d)
    fake.save_paths.append(src)
    d.save_button.click()
    assert d.save_message() == ("That's the original file. The stamped copy is always saved as a new "
                                "file; choose a different name or folder.")
    assert window.stack.currentWidget() is d
    assert sha256(src) == before and sorted(p.name for p in tmp_path.iterdir()) == [src.name]


def test_refuses_an_existing_file(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                  corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    other = tmp_path / "already_here.pdf"
    other.write_bytes(b"%PDF-1.7 placeholder")
    d = open_dialog(qtbot, window, src)
    _fill(qtbot, d)
    fake.save_paths.append(other)
    d.save_button.click()
    assert d.save_message() == ("already_here.pdf already exists. Choose another name; the window "
                                "never replaces an existing file.")
    assert other.read_bytes() == b"%PDF-1.7 placeholder"
    # A new name works.
    fake.save_paths.append(tmp_path / "new.pdf")
    d.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.saved, timeout=15000)


def test_cancelled_save_dialog_does_nothing(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                            corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    d = open_dialog(qtbot, window, src)
    _fill(qtbot, d)
    d.save_button.click()  # no answer queued: the user cancelled Save As
    assert fake.save_suggestions == [tmp_path / f"{SO}_Court-order_letter_portrait.pdf"]
    assert window.stack.currentWidget() is d and not d.saving


# ------------------------------------------------------------ a real save


def _check_top_right_upright(out: Path, src: Path, text_for_page, box_on: bool = True) -> None:  # noqa: ANN001
    """The Phase 2 exit-check rules (D-060) on every page of a saved copy."""
    with open_pdf(out) as doc, open_pdf(src) as orig:
        assert doc.page_count == orig.page_count
        for page in doc:
            n = page.number + 1
            expected = text_for_page(n, doc.page_count)
            lines = [line for b in page.get_text("dict")["blocks"] for line in b.get("lines", [])
                     if any(SO in s["text"] for s in line["spans"])]
            assert len(lines) == 1, f"page {n}"
            assert "".join(s["text"] for s in lines[0]["spans"]) == expected
            assert all("Atkinson" in s["font"] for s in lines[0]["spans"])
            direction = unrotated_vector_to_visible(tuple(lines[0]["dir"]), page.rotation)
            assert direction == pytest.approx((1.0, 0.0), abs=1e-3), f"page {n} reads {direction}"
            pix = page.get_pixmap(dpi=72, alpha=False)
            ink = pixel_bbox(pix)
            assert ink is not None, f"page {n}: no stamp ink"
            w, h = page.rect.width, page.rect.height
            assert w - MARGIN_PT - BOX_PADDING_PT - TOL_PT - 1 <= ink.x1 <= w - MARGIN_PT + 1, (n, ink, w)
            assert MARGIN_PT - 1 <= ink.y0 <= MARGIN_PT + BOX_PADDING_PT + 9, (n, ink)
            assert (ink.x0 + ink.x1) / 2 > w / 2 and ink.y1 < h / 2


def test_successful_save_passes_the_exit_check(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                               corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "rotate_mixed.pdf", tmp_path)
    before = sha256(src)
    d = open_dialog(qtbot, window, src)
    _fill(qtbot, d)
    out = tmp_path / d.suggested_name()
    fake.save_paths.append(out)
    d.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.saved, timeout=15000)
    assert out.is_file() and out.name == f"{SO}_Court-order_rotate_mixed.pdf"
    assert sha256(src) == before
    assert _leftovers(tmp_path) == []
    _check_top_right_upright(out, src, lambda n, t: f"{SO}  |  Court order  |  Page {n} of {t}")
    with open_pdf(out) as doc:
        assert doc.metadata is not None
    saved = window.saved
    assert saved.path_label.text() == str(out)
    assert saved.original_label.text() == f"{src} – unchanged"
    assert "All 4 pages carry case number" in saved.lead.text() and SO in saved.lead.text()
    qtbot.waitUntil(lambda: saved.thumbnail.pixmap() is not None and not saved.thumbnail.pixmap().isNull(),
                    timeout=15000)
    saved.open_button.click()
    saved.folder_button.click()
    assert fake.opened == [out] and fake.shown == [out]
    saved.another_button.click()
    assert window.stack.currentWidget() is window.drop


def test_signed_save_notes_and_original(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                        corpus_dir: Path, tmp_path: Path) -> None:
    src = make_signed_pdf(corpus_dir, tmp_path / "signed_return.pdf")
    before = sha256(src)
    d = open_dialog(qtbot, window, src)
    type_into(qtbot, d, "identifier", SO)
    choose(d, "doc_type", "Search warrant return")
    d.signature_tick.setChecked(True)  # type: ignore[union-attr]
    wait_ready(qtbot, d)
    out = tmp_path / "copy.pdf"
    fake.save_paths.append(out)
    d.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.saved, timeout=15000)
    assert sha256(src) == before
    notes = window.saved.notes
    assert "The original is digitally signed; the stamped copy's signature won't validate." in notes.texts()
    assert notes.check_frame.isVisible()  # under "Check the copy" (UX-019)
    assert window.saved.original_label.text() == f"{src} – unchanged, still digitally signed"


def test_encrypted_copy_keeps_password(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                       corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "encrypted_user_password.pdf", tmp_path)
    fake.passwords.append(make_corpus.USER_PASSWORD)
    d = open_dialog(qtbot, window, src)
    assert d.password_box is not None and d.password_box.keep_radio is not None
    d.password_box.keep_radio.setChecked(True)  # asked, since it needed a password to open
    _fill(qtbot, d)
    out = tmp_path / "copy.pdf"
    fake.save_paths.append(out)
    d.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.saved, timeout=15000)
    with open_pdf(out, make_corpus.USER_PASSWORD) as doc:
        assert doc.metadata and doc.metadata.get("encryption")
        assert SO in doc[0].get_text()
    qtbot.waitUntil(lambda: not window.saved.thumbnail.pixmap().isNull(), timeout=15000)


# ------------------------------------------------------------ the 520-page file


def _large(qtbot: QtBot, window: MainWindow, corpus_dir: Path, tmp_path: Path) -> tuple[StampDialog, Path]:
    src = copy_to(corpus_dir / "large_520_pages.pdf", tmp_path)
    d = open_dialog(qtbot, window, src)
    _fill(qtbot, d)
    return d, src


def test_cancel_at_once_leaves_nothing(qtbot: QtBot, window: MainWindow, corpus_dir: Path,
                                       tmp_path: Path) -> None:
    d, src = _large(qtbot, window, corpus_dir, tmp_path)
    before = sha256(src)
    out = tmp_path / "big_copy.pdf"
    d.start_save(out)
    qtbot.keyClick(d, Qt.Key.Key_Escape)  # Esc cancels a running save
    qtbot.waitUntil(lambda: not d.saving, timeout=15000)
    assert d.save_message() == "Stamping was cancelled. Nothing was saved."
    assert not out.exists() and _leftovers(tmp_path) == []
    assert sha256(src) == before
    assert window.stack.currentWidget() is d  # still here, ready to try again
    assert d.save_button.isEnabled()


def test_cancel_midway_leaves_nothing(qtbot: QtBot, window: MainWindow, corpus_dir: Path,
                                      tmp_path: Path) -> None:
    d, src = _large(qtbot, window, corpus_dir, tmp_path)
    out = tmp_path / "big_copy.pdf"
    seen: list[int] = []

    def on_progress(done: int, total: int) -> None:  # runs on the engine thread
        seen.append(done)
        if done >= 260:
            window.engine.cancel()

    window.engine._worker.progress.connect(on_progress, Qt.ConnectionType.DirectConnection)
    d.start_save(out)
    qtbot.waitUntil(lambda: not d.saving, timeout=30000)
    assert 260 <= max(seen) < 520
    assert d.save_message() == "Stamping was cancelled. Nothing was saved."
    assert not out.exists() and _leftovers(tmp_path) == []


def test_window_stays_responsive_while_saving(qtbot: QtBot, window: MainWindow, corpus_dir: Path,
                                              tmp_path: Path) -> None:
    d, src = _large(qtbot, window, corpus_dir, tmp_path)
    out = tmp_path / "big_copy.pdf"
    clock = QElapsedTimer()
    gaps: list[int] = []
    last = [0]

    def tick() -> None:
        now = clock.elapsed()
        gaps.append(now - last[0])
        last[0] = now

    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(tick)
    clock.start()
    timer.start()
    values: list[int] = []
    window.engine.progress.connect(lambda done, total: values.append(d.progress.value()))
    start = time.perf_counter()
    d.start_save(out)
    assert d.progress_row.isVisible() and d.cancel_button.isEnabled()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.saved, timeout=60000)
    elapsed = time.perf_counter() - start
    timer.stop()
    assert out.is_file()
    assert values and max(values) == 520  # the bar moved to the end
    assert len(gaps) >= 5, f"the UI timer ticked only {len(gaps)} times in {elapsed:.2f} s"
    assert max(gaps[1:]) < 250, f"event loop blocked for {max(gaps[1:])} ms"
    print(f"\n520 pages saved from the window in {elapsed:.2f} s; longest UI gap {max(gaps[1:])} ms")
