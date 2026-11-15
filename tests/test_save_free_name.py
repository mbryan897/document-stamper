"""Save As suggests a free name and asks again instead of refusing after it closes (UX-020).

The window still never replaces a file (D-075); the check just before the
save stays the authority (D-055). Synthetic data only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from stamper.core.filenames import MAX_FILENAME_LENGTH, first_free_name
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
from tests.stamping import sha256
from tools import make_corpus

SO = make_corpus.FAKE_CASE_SO


def _len16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


# ------------------------------------------------------------ the helper


def test_free_name_is_kept(tmp_path: Path) -> None:
    assert first_free_name(tmp_path, "name.pdf") == "name.pdf"


def test_skips_an_existing_name(tmp_path: Path) -> None:
    (tmp_path / "name.pdf").write_bytes(b"x")
    (tmp_path / "name_2.pdf").write_bytes(b"x")
    assert first_free_name(tmp_path, "name.pdf") == "name_3.pdf"


@pytest.mark.parametrize("ch", ["a", "\U0001d538"], ids=["ascii", "surrogate-pair"])
def test_a_full_length_name_gets_a_suffix_within_the_limit(tmp_path: Path, ch: str) -> None:
    stem = ch * ((MAX_FILENAME_LENGTH - 4) // _len16(ch))
    name = stem + ".pdf"
    assert _len16(name) == MAX_FILENAME_LENGTH
    (tmp_path / name).write_bytes(b"x")
    free = first_free_name(tmp_path, name)
    assert free.endswith("_2.pdf") and _len16(free) <= MAX_FILENAME_LENGTH
    assert free[:-len("_2.pdf")] == stem[:len(free) - len("_2.pdf")]  # the stem was cut, whole characters only
    assert "\ud835" not in free  # no half of a surrogate pair


def test_gives_the_suggestion_back_when_all_99_are_taken(tmp_path: Path) -> None:
    (tmp_path / "name.pdf").write_bytes(b"x")
    for n in range(2, 100):
        (tmp_path / f"name_{n}.pdf").write_bytes(b"x")
    assert first_free_name(tmp_path, "name.pdf") == "name.pdf"


# ------------------------------------------------------------ the window


def _fill(qtbot: QtBot, window: MainWindow, src: Path):  # noqa: ANN202
    d = open_dialog(qtbot, window, src)
    type_into(qtbot, d, "identifier", SO)
    choose(d, "doc_type", "Court order")
    wait_ready(qtbot, d)
    assert d.save_button.isEnabled(), d.blockers()
    return d


def test_suggestion_skips_an_existing_copy(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                          corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    d = _fill(qtbot, window, src)
    (tmp_path / d.suggested_name()).write_bytes(b"an earlier copy")
    d.save_button.click()  # no answer: cancelled
    assert fake.save_suggestions == [tmp_path / f"{SO}_Court-order_letter_portrait_2.pdf"]


def test_picking_an_existing_file_asks_again_until_cancel(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                                        corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    other = tmp_path / "sub"
    other.mkdir()
    taken = other / "already_here.pdf"
    taken.write_bytes(b"keep me")
    d = _fill(qtbot, window, src)
    fake.save_paths.extend([taken, src])  # an existing file, then the original; then cancel
    d.save_button.click()
    assert fake.save_suggestions[1:] == [other / "already_here_2.pdf", tmp_path / "letter_portrait_2.pdf"]
    assert len(fake.save_suggestions) == 3  # the third Save As was cancelled, which ends it
    assert d.save_message() == ("That's the original file. The stamped copy is always saved as a new file; "
                                "choose a different name or folder.")
    assert window.stack.currentWidget() is d and not d.saving
    assert taken.read_bytes() == b"keep me"


def test_asking_again_then_a_free_name_saves(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                            corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    taken = tmp_path / "already_here.pdf"
    taken.write_bytes(b"keep me")
    d = _fill(qtbot, window, src)
    fake.save_paths.extend([taken, tmp_path / "fresh.pdf"])
    d.save_button.click()
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.saved, timeout=WAIT_MS)
    assert len(fake.save_suggestions) == 2 and (tmp_path / "fresh.pdf").is_file()
    assert taken.read_bytes() == b"keep me"


def test_a_file_appearing_before_the_save_is_refused(qtbot: QtBot, window: MainWindow, fake: FakeServices,
                                                    corpus_dir: Path, tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    before = sha256(src)
    out = tmp_path / "late.pdf"
    d = _fill(qtbot, window, src)
    real_start = d.start_save

    def appear_then_save(path: Path) -> None:
        path.write_bytes(b"someone else's file")  # after the dialog's check, before the rename
        real_start(path)

    monkeypatch.setattr(d, "start_save", appear_then_save)
    fake.save_paths.append(out)
    d.save_button.click()
    qtbot.waitUntil(lambda: not d.saving, timeout=WAIT_MS)
    assert window.stack.currentWidget() is d
    assert "late.pdf" in d.save_message()
    assert out.read_bytes() == b"someone else's file" and sha256(src) == before
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("~stamper-")]
