"""What the stamped copy says about itself (UX-040, UX-030).

UX-040: the default profile records which original the copy came from and when
it was stamped, through the ordinary per-profile document properties.
UX-030: the note about the original's XMP tells the sender what a receiver sees.

Synthetic data only.
"""

from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path

import pymupdf
import pytest
from pytestqt.qtbot import QtBot

from stamper.gui.main_window import MainWindow
from stamper.handlers import pdf
from stamper.handlers.base import ApplyResult
from stamper.handlers.combine import SOURCES_PROPERTY, CombineInput, combine_pdfs
from stamper.handlers.pdf import PROPERTIES_NOTE_PREFIX, PdfHandler
from tests.gui_support import fake, window  # noqa: F401 - fixtures
from tests.stamping import FAKE_ID, REPO, default_config, default_job

# Literal, not imported, so each test fails on its own assertion against older code.
XMP_NOTE = ("The original has a second set of properties (XMP) that this copy keeps unchanged. "
            "Some programs may show the original's title and dates for this copy.")
XMP_PART = ("XMP metadata: the original's, kept unchanged; some programs may show its title and dates "
            "for this copy.")
TODAY = date(2026, 11, 15)
STAMPED_ON = "11-15-2026"  # the shipped config's MM-DD-YYYY
H = PdfHandler()


def _info(doc: pymupdf.Document) -> dict[str, str]:
    kind, val = doc.xref_get_key(-1, "Info")
    if kind != "xref":
        return {}
    xref = int(val.split()[0])
    return {key: doc.xref_get_key(xref, key)[1] for key in doc.xref_get_keys(xref)}


def _copy(corpus_dir: Path, folder: Path, name: str) -> Path:
    dst = folder / name
    shutil.copyfile(corpus_dir / "letter_portrait.pdf", dst)
    return dst


def _with(corpus_dir: Path, path: Path, *, source: str | None = None, xmp: bool = False) -> Path:
    with pymupdf.open(corpus_dir / "letter_portrait.pdf") as d:
        d.set_metadata({"title": "Sample title", "creationDate": "D:20250101120000Z"})
        if source is not None:
            _, val = d.xref_get_key(-1, "Info")
            d.xref_set_key(int(val.split()[0]), "Source", pymupdf.get_pdf_str(source))
        if xmp:
            d.set_xml_metadata("<x:xmpmeta xmlns:x='adobe:ns:meta/'><sample>Sample title</sample></x:xmpmeta>")
        d.save(path)
    return path


def _xmp_stream(doc: pymupdf.Document) -> bytes:
    kind, val = doc.xref_get_key(doc.pdf_catalog(), "Metadata")
    assert kind == "xref"
    return doc.xref_stream_raw(int(val.split()[0]))


def _props(warnings: tuple[str, ...]) -> str:
    return next(w for w in warnings if w.startswith(PROPERTIES_NOTE_PREFIX))


# ------------------------------------------------------------------ UX-040: provenance


def test_default_profile_writes_source_and_date() -> None:
    assert default_config().default_profile.metadata == {
        "Identifier": "{identifier}", "Source": "{original_name}", "StampedOn": "{date}"}


def test_stamped_copy_records_its_original_and_date(corpus_dir: Path, tmp_path: Path) -> None:
    src = _copy(corpus_dir, tmp_path, "return 99-2026-999901.pdf")
    r = H.apply(src, default_job(today=TODAY), tmp_path / "out.pdf")
    expected = {"Identifier": FAKE_ID, "Source": "return 99-2026-999901", "StampedOn": STAMPED_ON}
    assert r.metadata_written == expected
    assert r.metadata_replaced == {}
    with pymupdf.open(tmp_path / "out.pdf") as d:
        info = _info(d)
    assert {k: info[k] for k in expected} == expected
    note = _props(r.warnings)
    assert 'Source: "return 99-2026-999901"' in note and f'StampedOn: "{STAMPED_ON}"' in note


def test_plan_lists_the_provenance_properties(corpus_dir: Path, tmp_path: Path) -> None:
    src = _copy(corpus_dir, tmp_path, "receipt.pdf")
    r = H.plan(src, default_job(today=TODAY))
    assert r.metadata_written["Source"] == "receipt" and r.metadata_written["StampedOn"] == STAMPED_ON
    note = _props(r.warnings)
    assert 'Source: "receipt"' in note and f'StampedOn: "{STAMPED_ON}"' in note


HOSTILE = [
    "a (b) c",
    "unbalanced ( only",
    "closing ) only",
    "back\\slash\\",
    ") /Evil (x",
    "\\) /Injected (1",
    ">> /OpenAction << /S /JavaScript /JS (app.alert(1)) >>",
    "<feff0041> hex look",
    "ü é 中文 😀 ( \\ )",
    "t\there\r\nnext line",
    "x" * 1000,
]


@pytest.mark.parametrize("name", HOSTILE)
def test_hostile_original_name_round_trips_and_adds_no_keys(corpus_dir: Path, tmp_path: Path, name: str) -> None:
    """Every character a file name can carry is escaped into one PDF string: nothing breaks out."""
    out = tmp_path / "out.pdf"
    with pymupdf.open(corpus_dir / "letter_portrait.pdf") as doc:
        trailer = set(doc.xref_get_keys(-1))
        written, replaced = H._write_metadata(doc, default_job(today=TODAY), name, TODAY, [])
        doc.save(out)
    assert written["Source"] == name and replaced == {}
    with pymupdf.open(out) as d:
        info = _info(d)
        assert set(d.xref_get_keys(-1)) <= trailer | {"Info"}
        assert d.xref_get_key(d.pdf_catalog(), "OpenAction")[0] == "null"
    assert set(info) == {"Identifier", "Source", "StampedOn", "ModDate"}
    assert info["Source"] == name
    assert info["Identifier"] == FAKE_ID and info["StampedOn"] == STAMPED_ON


def test_control_characters_become_dots_and_add_no_keys(corpus_dir: Path, tmp_path: Path) -> None:
    """Windows names can't hold characters 1-31; DEL (127) can. PyMuPDF writes such characters as "·"."""
    out = tmp_path / "out.pdf"
    with pymupdf.open(corpus_dir / "letter_portrait.pdf") as doc:
        H._write_metadata(doc, default_job(today=TODAY), "a\x01b\x7fc) /K (d", TODAY, [])
        doc.save(out)
    with pymupdf.open(out) as d:
        info = _info(d)
    assert set(info) == {"Identifier", "Source", "StampedOn", "ModDate"}
    assert info["Source"] == "a·b·c) /K (d"


def test_hostile_name_on_disk_round_trips(corpus_dir: Path, tmp_path: Path) -> None:
    """The same through apply, with a name Windows allows."""
    src = _copy(corpus_dir, tmp_path, "x) Evil (y ü 中 (1).pdf")
    r = H.apply(src, default_job(today=TODAY), tmp_path / "out.pdf")
    with pymupdf.open(r.output_path) as d:
        info = _info(d)
    assert info["Source"] == "x) Evil (y ü 中 (1)"
    assert set(info) == {"Identifier", "Source", "StampedOn", "ModDate"}


def test_existing_source_is_replaced_with_a_warning(corpus_dir: Path, tmp_path: Path) -> None:
    src = _with(corpus_dir, tmp_path / "second.pdf", source="first scan")
    r = H.apply(src, default_job(today=TODAY), tmp_path / "out.pdf")
    assert r.metadata_replaced == {"Source": ("first scan", "second")}
    warning = 'Document property Source was "first scan" in the original; the copy has "second".'
    assert warning in r.warnings and pdf.needs_check(warning)
    assert 'Source: "second"' in _props(r.warnings)


def test_restamping_a_copy_replaces_the_date_only_when_it_differs(corpus_dir: Path, tmp_path: Path) -> None:
    src = _copy(corpus_dir, tmp_path, "receipt.pdf")
    later = tmp_path / "later"
    later.mkdir()
    H.apply(src, default_job(today=TODAY), later / "receipt.pdf")  # same name, so Source is unchanged
    r = H.apply(later / "receipt.pdf", default_job(today=date(2026, 11, 16)), tmp_path / "out.pdf")
    assert r.metadata_replaced == {"StampedOn": (STAMPED_ON, "11-16-2026")}


def test_combined_source_is_the_first_file_listed_in_stamper_sources(corpus_dir: Path, tmp_path: Path) -> None:
    """{original_name} is the first file's name (D-104); StamperSources lists them all (D-102)."""
    first = _copy(corpus_dir, tmp_path, "part one.pdf")
    second = _copy(corpus_dir, tmp_path, "part two.pdf")
    combined = combine_pdfs([CombineInput(first), CombineInput(second)], tmp_path)
    try:
        r = PdfHandler().apply(combined.path, default_job(today=TODAY), tmp_path / "combined.pdf")
    finally:
        combined.cleanup()
    with pymupdf.open(r.output_path) as d:
        info = _info(d)
    assert info["Source"] == "part one"
    n = combined.page_count // 2
    assert info[SOURCES_PROPERTY].splitlines() == [f"part one.pdf|1-{n}", f"part two.pdf|{n + 1}-{2 * n}"]
    assert r.metadata_replaced == {}


# ------------------------------------------------------------------ UX-030: the XMP wording


def test_xmp_note_in_plan_and_apply_and_xmp_untouched(corpus_dir: Path, tmp_path: Path) -> None:
    src = _with(corpus_dir, tmp_path / "scan.pdf", xmp=True)
    with pymupdf.open(src) as d:
        xmp_before, raw_before = d.get_xml_metadata(), _xmp_stream(d)
    planned = H.plan(src, default_job(today=TODAY))
    r = H.apply(src, default_job(today=TODAY), tmp_path / "out.pdf")
    for warnings in (planned.warnings, r.warnings):
        assert XMP_NOTE in warnings and not pdf.needs_check(XMP_NOTE)
        assert _props(warnings).endswith(XMP_PART)
    with pymupdf.open(r.output_path) as d:
        assert d.get_xml_metadata() == xmp_before
        assert _xmp_stream(d) == raw_before  # byte for byte


def test_properties_note_xmp_part() -> None:
    assert pdf.properties_note({"ModDate": None}, "", True).endswith(" " + XMP_PART)
    assert pdf.properties_note({"ModDate": None}, "", False).endswith(" XMP metadata: none.")


def test_saved_screen_shows_the_xmp_note_as_information(qtbot: QtBot, window: MainWindow,  # noqa: F811
                                                         corpus_dir: Path, tmp_path: Path) -> None:
    src = _with(corpus_dir, tmp_path / "scan.pdf", xmp=True)
    r: ApplyResult = H.apply(src, default_job(today=TODAY), tmp_path / "out.pdf")
    saved = window.saved
    saved.show_result(r, label="Case number", identifier=FAKE_ID, signed=False, source=src)
    window.stack.setCurrentWidget(saved)
    texts = saved.notes.texts()
    assert XMP_NOTE in texts and texts[-1].endswith(XMP_PART)
    assert not saved.notes.check_frame.isVisible()


def test_help_quotes_the_xmp_note() -> None:
    help_dir = REPO / "assets" / "help"
    rows = [line for line in (help_dir / "messages.md").read_text(encoding="utf-8").splitlines() if "XMP" in line]
    assert len(rows) == 1
    assert re.match(r'\| "(.*?)" \|', rows[0]).group(1) == XMP_NOTE  # type: ignore[union-attr]
    changes = (help_dir / "what-changes.md").read_text(encoding="utf-8")
    assert "programs may show the original's title and dates for this copy" in changes
    assert "`Source`" in changes and "`StampedOn`" in changes
