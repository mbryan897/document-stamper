"""Partial copies left by a stopped run: journaled, offered for deletion, never deleted silently (D-124)."""

from __future__ import annotations

import io
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest
from pytestqt.qtbot import QtBot

from stamper.cli import main
from stamper.core import pending
from stamper.gui.main_window import MainWindow
from stamper.handlers.base import OutputRefused
from stamper.handlers.pdf import PdfHandler
from tests.gui_support import FakeServices, copy_to, fake  # noqa: F401 - fixtures
from tests.stamping import DEFAULT_CONFIG, FAKE_ID, default_job

REPO = Path(__file__).resolve().parent.parent

_CRASH = """
import os, sys
from pathlib import Path
from stamper.core import pending
tmp = Path(sys.argv[1])
tmp.write_bytes(b"%PDF-1.7 partial")
pending.record(tmp)
os._exit(1)  # killed before the rename: no cleanup runs
"""


def crash_leaving(tmp: Path) -> None:
    """Run a process that journals ``tmp``, writes it and dies without cleaning up."""
    subprocess.run([sys.executable, "-c", _CRASH, str(tmp)], cwd=REPO, env=os.environ.copy(), check=False)


def test_a_save_leaves_no_entry(corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    PdfHandler().apply(src, default_job(), tmp_path / "copy.pdf")
    assert list(pending.journal_dir().glob("*.json")) == []


def test_a_refused_save_leaves_no_entry_or_temp(corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    taken = tmp_path / "copy.pdf"
    taken.write_bytes(b"existing")
    with pymupdf.open(src) as doc, pytest.raises(OutputRefused):
        PdfHandler._save_atomic(doc, taken, pymupdf.PDF_ENCRYPT_KEEP, overwrite=False)
    assert list(pending.journal_dir().glob("*.json")) == []
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("~")] == []


def test_an_unwritable_journal_doesnt_stop_a_save(corpus_dir: Path, tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    monkeypatch.setenv("STAMPER_LOCAL_DIR", str(blocker))
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    assert PdfHandler().apply(src, default_job(), tmp_path / "copy.pdf").output_path.is_file()


def test_a_crash_is_found_and_deleted_on_request(tmp_path: Path) -> None:
    tmp = tmp_path / "~stamper-abc123.tmp"
    crash_leaving(tmp)
    assert tmp.is_file()
    found = pending.leftovers()
    assert [lo.path for lo in found] == [tmp.resolve()]
    assert found[0].size == len(b"%PDF-1.7 partial")
    pending.delete(found[0])
    assert not tmp.exists()
    assert pending.leftovers() == []


def test_keep_leaves_the_file_and_stops_asking(tmp_path: Path) -> None:
    tmp = tmp_path / "~stamper-keep.tmp"
    crash_leaving(tmp)
    (lo,) = pending.leftovers()
    pending.keep(lo)
    assert tmp.is_file()
    assert pending.leftovers() == []


def test_a_running_save_is_left_alone(tmp_path: Path) -> None:
    tmp = tmp_path / "~stamper-live.tmp"
    tmp.write_bytes(b"in progress")
    entry = pending.record(tmp)  # this process: still running
    assert entry is not None
    assert pending.leftovers() == []
    assert pending.leftovers(alive=lambda _pid: False)[0].path == tmp.resolve()


def test_a_file_that_is_gone_is_dropped_quietly(tmp_path: Path) -> None:
    tmp = tmp_path / "~stamper-gone.tmp"
    crash_leaving(tmp)
    tmp.unlink()
    assert pending.leftovers() == []
    assert list(pending.journal_dir().glob("*.json")) == []


def test_a_folder_in_its_place_is_never_deleted(tmp_path: Path) -> None:
    tmp = tmp_path / "~stamper-dir.tmp"
    crash_leaving(tmp)
    (lo,) = pending.leftovers()
    tmp.unlink()
    tmp.mkdir()
    (tmp / "evidence.pdf").write_bytes(b"keep me")
    with pytest.raises(OSError, match="no longer the partial copy"):
        pending.delete(lo)
    assert (tmp / "evidence.pdf").read_bytes() == b"keep me"
    assert pending.leftovers() == []


def test_a_link_in_its_place_is_never_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "evidence.pdf"
    target.write_bytes(b"keep me")
    tmp = tmp_path / "~stamper-link.tmp"
    crash_leaving(tmp)
    (lo,) = pending.leftovers()
    tmp.unlink()
    if os.name == "nt":
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(tmp), str(outside)], capture_output=True)
        if made.returncode != 0:
            pytest.skip("couldn't make a junction")
    else:
        tmp.symlink_to(target)
    with pytest.raises(OSError):
        pending.delete(lo)
    assert target.read_bytes() == b"keep me"
    assert pending.leftovers() == []


def test_a_name_that_isnt_ours_is_ignored(tmp_path: Path) -> None:
    other = tmp_path / "report.pdf"
    other.write_bytes(b"not ours")
    pending.journal_dir().mkdir(parents=True, exist_ok=True)
    (pending.journal_dir() / "planted.json").write_text(json.dumps({"path": str(other), "pid": 999999999}))
    assert pending.leftovers() == []
    assert other.read_bytes() == b"not ours"


def test_a_damaged_entry_is_dropped(tmp_path: Path) -> None:
    pending.journal_dir().mkdir(parents=True, exist_ok=True)
    bad = pending.journal_dir() / "bad.json"
    bad.write_text("{not json")
    assert pending.leftovers() == []
    assert not bad.exists()


def test_describe_one_and_several(tmp_path: Path) -> None:
    for name in ("~stamper-a.tmp", "~stamper-b.tmp"):
        crash_leaving(tmp_path / name)
    found = pending.leftovers()
    one = pending.describe(found[:1])
    assert one.startswith("A partial copy was left behind") and "not the original" in one
    several = pending.describe(found)
    assert several.count("• ") == 2 and "not originals" in several


def test_cli_notes_but_never_deletes(corpus_dir: Path, tmp_path: Path) -> None:
    tmp = tmp_path / "~stamper-cli.tmp"
    crash_leaving(tmp)
    out, err = io.StringIO(), io.StringIO()
    main([str(corpus_dir / "letter_portrait.pdf"), "--dry-run", "--config", str(DEFAULT_CONFIG),
          "--set", f"identifier={FAKE_ID}", "--set", "doc_type=Report"],
         stdout=out, stderr=err, stdin=io.StringIO(""))
    assert "A partial copy was left behind" in out.getvalue()
    assert "delete or keep it" in out.getvalue()
    assert tmp.is_file() and len(pending.leftovers()) == 1


def _window(qtbot: QtBot, fake: FakeServices) -> MainWindow:
    w = MainWindow(config_path=DEFAULT_CONFIG, services=fake.services())
    qtbot.addWidget(w)
    return w


def test_window_offers_delete(qtbot: QtBot, fake: FakeServices, tmp_path: Path) -> None:
    tmp = tmp_path / "~stamper-gui.tmp"
    crash_leaving(tmp)
    w = _window(qtbot, fake)
    assert not w.drop.leftover_frame.isHidden()
    assert "A partial copy was left behind" in w.drop.leftover_label.text()
    assert w.drop.leftover_delete.text() == "Delete"
    w.drop.leftover_show.click()
    assert fake.shown == [tmp.resolve()]
    assert tmp.is_file()
    w.drop.leftover_delete.click()
    assert not tmp.exists()
    assert w.drop.leftover_frame.isHidden()


def test_window_keep_all(qtbot: QtBot, fake: FakeServices, tmp_path: Path) -> None:
    for name in ("~stamper-1.tmp", "~stamper-2.tmp"):
        crash_leaving(tmp_path / name)
    w = _window(qtbot, fake)
    assert w.drop.leftover_keep.text() == "Keep all" and w.drop.leftover_show.isHidden()
    w.drop.leftover_keep.click()
    assert w.drop.leftover_frame.isHidden()
    assert (tmp_path / "~stamper-1.tmp").is_file() and (tmp_path / "~stamper-2.tmp").is_file()
    w2 = _window(qtbot, fake)
    assert w2.drop.leftover_frame.isHidden()


def test_window_without_leftovers_shows_nothing(qtbot: QtBot, fake: FakeServices) -> None:
    w = _window(qtbot, fake)
    assert w.drop.leftover_frame.isHidden()


# ---------------------------------------------------------------- the source's name (UX-024)


def _entry(tmp: Path, **extra: object) -> None:
    """A journal entry for ``tmp`` as a stopped run would leave it, with ``extra`` keys."""
    tmp.write_bytes(b"%PDF-1.7 partial")
    pending.journal_dir().mkdir(parents=True, exist_ok=True)
    data = {"path": str(tmp.resolve()), "pid": 999999, "created": "2026-01-01T00:00:00", **extra}
    (pending.journal_dir() / f"{tmp.stem}.json").write_text(json.dumps(data), encoding="utf-8")


def _found() -> list[pending.Leftover]:
    return pending.leftovers(alive=lambda _pid: False)


def test_record_stores_the_source_only_when_given(tmp_path: Path) -> None:
    with_source = pending.record(tmp_path / "~stamper-a.tmp", source=tmp_path / "Report 99-2026-999901.pdf")
    without = pending.record(tmp_path / "~stamper-b.tmp")
    assert with_source is not None and without is not None
    assert json.loads(with_source.read_text("utf-8"))["source"] == "Report 99-2026-999901.pdf"  # no folder (F-03)
    assert "source" not in json.loads(without.read_text("utf-8"))


def test_apply_records_its_source_and_not_the_output(corpus_dir: Path, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, object]] = []
    real = pending.record

    def spy(tmp: Path, **kw: object) -> Path | None:
        seen.append(kw)
        return real(tmp, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(pending, "record", spy)
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    PdfHandler().apply(src, default_job(), tmp_path / f"{FAKE_ID} copy.pdf")
    assert seen == [{"source": src}]


def test_batch_records_each_source(corpus_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from stamper.handlers.batch import BatchItem, check_batch, run_batch

    seen: list[object] = []
    real = pending.record
    monkeypatch.setattr(pending, "record", lambda tmp, **kw: (seen.append(kw.get("source")), real(tmp, **kw))[1])
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path / "a")
    b = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path / "b")
    items = [BatchItem(a, tmp_path / "a" / "out.pdf"), BatchItem(b, tmp_path / "b" / "out.pdf")]
    handler, job = PdfHandler(), default_job()
    run_batch(handler, check_batch(handler, items, job), job)
    assert seen == [a, b]


def test_an_old_entry_without_source_reads_as_before(tmp_path: Path) -> None:
    tmp = tmp_path / "~stamper-old.tmp"
    _entry(tmp)
    found = _found()
    assert found[0].source is None
    text = pending.describe(found)
    assert text.endswith("It's an unfinished stamped copy, not the original.")
    assert "Stamp it again" not in text


def test_describe_names_the_source(tmp_path: Path) -> None:
    tmp = tmp_path / "~stamper-src.tmp"
    _entry(tmp, source=r"C:\Cases\Report 99-2026-999901.pdf")
    text = pending.describe(_found())
    assert text.endswith("The stamped copy of Report 99-2026-999901.pdf wasn't finished. Stamp it again.")
    assert "C:\\Cases" not in text  # the name only, not the folder


def test_describe_several_names_each_source(tmp_path: Path) -> None:
    _entry(tmp_path / "~stamper-1.tmp", source="/x/one.pdf")
    _entry(tmp_path / "~stamper-2.tmp")
    text = pending.describe(_found())
    assert ": the stamped copy of one.pdf" in text
    assert text.count("the stamped copy of") == 1
    assert text.endswith("Stamp those files again.")


def test_a_combine_leftover_doesnt_claim_to_be_the_first_file(tmp_path: Path) -> None:
    from stamper.handlers.combine import TEMP_PREFIX

    assert pending.COMBINE_FOLDER_PREFIX == TEMP_PREFIX
    _entry(tmp_path / "~stamper-c.tmp", source=rf"C:\Users\x\AppData\{TEMP_PREFIX}1a2b3c4d\first.pdf")
    text = pending.describe(_found())
    assert "The stamped copy of a combined file starting with first.pdf wasn't finished." in text
    assert "The stamped copy of first.pdf" not in text


@pytest.mark.parametrize("source", [12345, None, ["a.pdf"], {"x": 1}, ""])
def test_a_source_that_isnt_a_string_is_ignored(tmp_path: Path, source: object) -> None:
    tmp = tmp_path / "~stamper-n.tmp"
    _entry(tmp, source=source)
    found = _found()
    assert [lo.path for lo in found] == [tmp.resolve()] and found[0].source is None
    assert "Stamp it again" not in pending.describe(found)


def test_a_huge_or_hostile_source_is_cut_and_shown_plainly(tmp_path: Path) -> None:
    _entry(tmp_path / "~stamper-h.tmp", source="C:\\x\\" + "A" * 10_000 + ".pdf")
    text = pending.describe(_found())
    assert len(text) < 1000 and "A" * (pending.MAX_SOURCE_NAME - 1) + "…" in text
    pending.journal_dir().joinpath("~stamper-h.json").unlink()
    _entry(tmp_path / "~stamper-r.tmp", source="/x/<b>bold<b>\n\u202eevil.pdf")
    text = pending.describe(_found())
    assert "<b>bold<b>⟨U+000A⟩⟨U+202E⟩evil.pdf" in text


def test_the_source_is_never_touched(tmp_path: Path) -> None:
    original = tmp_path / "original.pdf"
    original.write_bytes(b"original")
    tmp = tmp_path / "~stamper-t.tmp"
    _entry(tmp, source=str(original))
    pending.delete(_found()[0])
    assert original.read_bytes() == b"original" and not tmp.exists()


def test_window_shows_the_source_as_plain_text(qtbot: QtBot, fake: FakeServices, tmp_path: Path) -> None:
    tmp = tmp_path / "~stamper-w.tmp"
    crash_leaving(tmp)
    entry = next(pending.journal_dir().glob("*.json"))
    data = json.loads(entry.read_text("utf-8"))
    entry.write_text(json.dumps({**data, "source": "/x/<b>R<b>.pdf"}), encoding="utf-8")
    w = _window(qtbot, fake)
    from PySide6.QtCore import Qt
    assert w.drop.leftover_label.textFormat() == Qt.TextFormat.PlainText
    assert "The stamped copy of <b>R<b>.pdf wasn't finished. Stamp it again." in w.drop.leftover_label.text()


def test_combine_records_the_temporary_combined_file(corpus_dir: Path, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Combine stamps the temporary file through apply; the notice must not call it the first file."""
    from stamper.handlers.combine import CombineInput, combine_pdfs

    seen: list[Path] = []
    real = pending.record
    monkeypatch.setattr(pending, "record", lambda tmp, **kw: (seen.append(kw["source"]), real(tmp, **kw))[1])
    a = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    b = copy_to(corpus_dir / "rotate_90.pdf", tmp_path)
    with combine_pdfs([CombineInput(a), CombineInput(b)], tmp_path) as combined:
        PdfHandler().apply(combined.path, default_job(), tmp_path / "combined copy.pdf")
    assert len(seen) == 1 and seen[0].name == "letter_portrait.pdf" and seen[0] != a
    lo = pending.Leftover(Path("x"), tmp_path / "~stamper-x.tmp", 1, datetime.datetime(2026, 1, 1),
                          str(seen[0]))
    assert "a combined file starting with letter_portrait.pdf" in pending.describe([lo])
