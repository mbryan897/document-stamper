"""The live preview: every corpus page type renders, redraws are debounced, timings."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from stamper.gui.main_window import MainWindow
from stamper.gui.stamp_dialog import DEBOUNCE_MS, StampDialog
from tests.gui_support import FakeServices, fake, open_dialog, type_into, wait_ready, window  # noqa: F401
from tests.stamping import make_signed_pdf, password_for
from tools import make_corpus

PNG = b"\x89PNG\r\n\x1a\n"


def _open(qtbot: QtBot, window: MainWindow, fake: FakeServices, path: Path) -> StampDialog:
    pw = password_for(path.name)
    if pw:
        fake.passwords.append(pw)
    return open_dialog(qtbot, window, path)


@pytest.mark.parametrize("name", list(make_corpus.CORPUS))
def test_every_corpus_page_previews(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                    corpus_dir: Path, name: str) -> None:
    d = _open(qtbot, window, fake, corpus_dir / name)
    pages = range(1, d.info.page_count + 1) if d.info.page_count <= 6 else (1, 2, d.info.page_count)
    for n in pages:
        d._go(n - d.page)
        wait_ready(qtbot, d)
        png = d.preview_png()
        assert png is not None and png.startswith(PNG), (name, n, d.preview_text())
        assert d.preview.pixmap() is not None and not d.preview.pixmap().isNull()
        assert d.page_label.text() == f"Page {n} of {d.info.page_count}"


def test_signed_file_previews(qtbot: QtBot, window: MainWindow, corpus_dir: Path, tmp_path: Path) -> None:
    d = open_dialog(qtbot, window, make_signed_pdf(corpus_dir, tmp_path / "signed.pdf"))
    assert (d.preview_png() or b"").startswith(PNG)


def test_typing_is_debounced(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "large_520_pages.pdf")
    e = window.engine
    previews, plans = e.previews_started, e.plans_started
    start = time.perf_counter()
    type_into(qtbot, d, "identifier", "SO261017-999902")  # 15 keystrokes
    typed_ms = (time.perf_counter() - start) * 1000
    assert typed_ms < DEBOUNCE_MS, "typing was too slow for this test to mean anything"
    assert e.previews_started == previews  # nothing yet: still inside the debounce window
    wait_ready(qtbot, d)
    assert e.previews_started == previews + 1
    assert e.plans_started == plans + 1


def test_clicks_redraw_at_once(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "letter_portrait.pdf")
    e = window.engine
    before = e.previews_started
    d._set_anchor("top-left")
    assert e.previews_started == before + 1
    d.box_check.setChecked(False)
    assert e.previews_started == before + 2
    wait_ready(qtbot, d)


def test_page_navigation_previews_only(qtbot: QtBot, window: MainWindow, corpus_dir: Path) -> None:
    d = open_dialog(qtbot, window, corpus_dir / "rotate_mixed.pdf")
    e = window.engine
    plans = e.plans_started
    d._go(1)
    d._go(1)
    wait_ready(qtbot, d)
    assert d.page == 3 and e.plans_started == plans  # paging doesn't re-check the document
    assert d.next_button.isEnabled()
    d._go(10)
    assert d.page == 4 and not d.next_button.isEnabled()


TIMED = [
    ("letter_portrait.pdf", 1),
    ("large_520_pages.pdf", 1),
    ("large_520_pages.pdf", 520),
    ("encrypted_user_password.pdf", 1),
    ("owner_restrictions.pdf", 1),
    ("scanned_image_only.pdf", 1),
]


def test_preview_timings(qtbot: QtBot, window: MainWindow, fake: FakeServices, corpus_dir: Path) -> None:
    """Time from a change to the redrawn preview on screen, after the debounce."""
    report = []
    for name, page in TIMED:
        d = _open(qtbot, window, fake, corpus_dir / name)
        d._go(page - 1)
        wait_ready(qtbot, d)
        shown, done = [], []
        for i in range(5):
            start = time.perf_counter()
            d._set_anchor("top-left" if i % 2 == 0 else "top-right")
            qtbot.waitUntil(lambda: d._preview_done == d._preview_req, timeout=15000)
            shown.append((time.perf_counter() - start) * 1000)
            qtbot.waitUntil(d.ready, timeout=15000)
            done.append((time.perf_counter() - start) * 1000)
        shown.sort()
        done.sort()
        report.append(f"{name:30} p{page:<4} preview {shown[2]:6.1f} ms   whole check done {done[2]:6.1f} ms")
        assert shown[2] < 500 and done[2] < 1000, report[-1]
        window.show_drop()
    print("\nAfter a click, median of 5 (preview on screen; whole-document check finished):\n" + "\n".join(report))
