"""The journal keeps the name of the file being stamped, never its folder (F-03).

UX-024 put the source in each entry so the crash notice can name it; folder
names can hold case details, so only the name is written. Entries written by
earlier versions hold a full path and still read, showing the name only.
Synthetic files and a made-up, distinctive folder name only.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from stamper.core import pending
from stamper.handlers.combine import TEMP_PREFIX, CombineInput, combine_pdfs
from stamper.handlers.pdf import PdfHandler
from tests.gui_support import copy_to
from tests.stamping import FAKE_ID, default_job

PLANT = "Zq-Planted-Folder-4417"


@pytest.fixture
def journal(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The text of each entry as written, captured before the save deletes it."""
    texts: list[str] = []
    real = pending.record

    def spy(tmp: Path, **kw: object) -> Path | None:
        entry = real(tmp, **kw)  # type: ignore[arg-type]
        if entry is not None:
            texts.append(entry.read_text(encoding="utf-8"))
        return entry

    monkeypatch.setattr(pending, "record", spy)
    return texts


def test_single_file_entry_has_the_name_not_the_folder(journal: list[str], corpus_dir: Path,
                                                       tmp_path: Path) -> None:
    folder = tmp_path / PLANT
    folder.mkdir()
    src = copy_to(corpus_dir / "letter_portrait.pdf", folder, "Report original.pdf")
    out_dir = tmp_path / "copies"
    out_dir.mkdir()
    PdfHandler().apply(src, default_job(), out_dir / f"{FAKE_ID} copy.pdf")
    assert len(journal) == 1
    text = journal[0]
    assert json.loads(text)["source"] == "Report original.pdf"
    assert PLANT not in text and str(folder) not in text


def test_combine_entry_has_the_first_files_name_not_a_folder(journal: list[str], corpus_dir: Path,
                                                             tmp_path: Path) -> None:
    folder = tmp_path / PLANT
    folder.mkdir()
    a = copy_to(corpus_dir / "letter_portrait.pdf", folder, "first.pdf")
    b = copy_to(corpus_dir / "rotate_90.pdf", folder, "second.pdf")
    with combine_pdfs([CombineInput(a), CombineInput(b)], tmp_path) as combined:
        PdfHandler().apply(combined.path, default_job(), tmp_path / "combined copy.pdf")
    assert len(journal) == 1
    text = journal[0]
    assert json.loads(text)["source"] == "first.pdf"
    assert PLANT not in text and TEMP_PREFIX not in text  # neither the originals' nor the work folder


def test_an_old_full_path_entry_still_names_only_the_file(tmp_path: Path) -> None:
    tmp = tmp_path / "~stamper-old.tmp"
    tmp.write_bytes(b"%PDF-1.7 partial")
    pending.journal_dir().mkdir(parents=True, exist_ok=True)
    entry = {"path": str(tmp.resolve()), "pid": 999999, "created": "2026-01-01T00:00:00",
             "source": rf"C:\Cases\{PLANT}\Report original.pdf"}
    (pending.journal_dir() / "old.json").write_text(json.dumps(entry), encoding="utf-8")
    found = pending.leftovers(alive=lambda _pid: False)
    text = pending.describe(found)
    assert text.endswith("The stamped copy of Report original.pdf wasn't finished. Stamp it again.")
    assert PLANT not in text and "Cases" not in text


def test_a_new_combine_entry_reads_as_its_first_file() -> None:
    lo = pending.Leftover(Path("x.json"), Path("~stamper-x.tmp"), 1, datetime.datetime(2026, 1, 1), "first.pdf")
    assert pending.describe([lo]).endswith("The stamped copy of first.pdf wasn't finished. Stamp it again.")
