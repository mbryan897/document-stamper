"""Active content is reported, never removed or run (D-119); what a save keeps of earlier revisions."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from stamper.handlers.combine import CombineInput, combine_pdfs
from stamper.handlers.pdf import PdfHandler, active_content_notes
from tests.stamping import default_job

KEEPS = "The stamped copy keeps them unchanged."


def _blank(path: Path) -> pymupdf.Document:
    doc = pymupdf.open()
    doc.new_page()
    return doc


def javascript_openaction(path: Path) -> Path:
    doc = _blank(path)
    js = doc.get_new_xref()
    doc.update_object(js, "<< /S /JavaScript /JS (app.alert\\('hi'\\);) >>")
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"{js} 0 R")
    doc.save(path)
    return path


def launch_action(path: Path) -> Path:
    doc = _blank(path)
    page = doc[0]
    page.insert_link({"kind": pymupdf.LINK_LAUNCH, "from": pymupdf.Rect(10, 10, 100, 30), "file": "calc.exe"})
    doc.save(path)
    return path


def embedded_file(path: Path) -> Path:
    doc = _blank(path)
    doc.embfile_add("notes.txt", b"attached", filename="notes.txt")
    doc.embfile_add("more.txt", b"attached too", filename="more.txt")
    doc.save(path)
    return path


def incremental_update(path: Path) -> Path:
    """Two saved versions: the first page text replaced, plus an object nothing refers to."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "x")
    xref = page.get_contents()[0]
    doc.update_stream(xref, b"BT /helv 12 Tf 72 72 Td (ORIGINAL-SECRET) Tj ET", compress=False)
    doc.save(path, deflate=False, garbage=0)
    doc.close()
    doc = pymupdf.open(path)
    xref = doc[0].get_contents()[0]
    doc.update_stream(xref, b"BT /helv 12 Tf 72 72 Td (REPLACED) Tj ET", compress=False)
    orphan = doc.get_new_xref()
    doc.update_object(orphan, "<< /Note (ORPHANED-OBJECT) >>")
    doc.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    doc.close()
    return path


def field_calc_script(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    for name, y, value in (("a", 100, "first"), ("b", 140, "untouched")):
        w = pymupdf.Widget()
        w.field_name, w.field_type, w.field_value = name, pymupdf.PDF_WIDGET_TYPE_TEXT, value
        w.rect = pymupdf.Rect(72, y, 300, y + 24)
        if name == "b":
            w.script_calc = 'event.value = "CHANGED BY SCRIPT";'
        page.add_widget(w)
    doc.save(path)
    return path


def _field_values(path: Path) -> dict[str, str]:
    with pymupdf.open(path) as doc:
        return {w.field_name: w.field_value for p in doc for w in p.widgets()}


@pytest.mark.parametrize(("make", "expect"), [
    (javascript_openaction, "JavaScript"),
    (launch_action, "an action that opens another file or program"),
    (embedded_file, "2 attached files"),
])
def test_active_content_is_named_and_kept(make: object, expect: str, tmp_path: Path) -> None:
    src = make(tmp_path / "src.pdf")  # type: ignore[operator]
    handler = PdfHandler()
    info = handler.inspect(src)
    assert expect in info.active_content
    note = f"This file contains {expect}"
    plan = handler.plan(src, default_job())
    assert any(w.startswith(note) and w.endswith(KEEPS) for w in plan.warnings), plan.warnings
    result = handler.apply(src, default_job(), tmp_path / "out.pdf")
    assert any(w.startswith(note) for w in result.warnings)
    # Kept, not stripped: the copy carries the same thing.
    assert expect in handler.inspect(result.output_path).active_content


def test_plain_file_has_no_active_content(corpus_dir: Path) -> None:
    info = PdfHandler().inspect(corpus_dir / "letter_portrait.pdf")
    assert info.active_content == () and info.revisions == 1
    assert active_content_notes(info) == []


def test_revisions_are_noted(tmp_path: Path) -> None:
    src = incremental_update(tmp_path / "edited.pdf")
    info = PdfHandler().inspect(src)
    assert info.revisions == 2
    (note,) = active_content_notes(info)
    assert note == ("This file was edited after it was first saved (2 saved versions). "
                    "The stamped copy keeps only the latest version, as a viewer shows it.")


def test_what_a_save_keeps_of_earlier_revisions(tmp_path: Path) -> None:
    """Recorded for the evidentiary review (D-119): a stamp keeps unused objects; a combine doesn't.

    Single copy (full save, garbage=0): the superseded page text is gone, the
    orphaned object is still inside. Combined copy (insert_pdf, garbage=1):
    both are gone.
    """
    src = incremental_update(tmp_path / "edited.pdf")
    raw = src.read_bytes()
    assert b"ORIGINAL-SECRET" in raw and b"ORPHANED-OBJECT" in raw  # the source really has both
    single = PdfHandler().apply(src, default_job(), tmp_path / "single.pdf").output_path
    data = single.read_bytes()
    assert b"ORIGINAL-SECRET" not in data
    assert b"ORPHANED-OBJECT" in data
    assert data.count(b"startxref") == 1

    other = tmp_path / "other.pdf"
    _blank(other).save(other)
    work = tmp_path / "work"
    work.mkdir()
    with combine_pdfs([CombineInput(src), CombineInput(other)], work) as combined:
        out = PdfHandler().apply(combined.path, default_job(), tmp_path / "combined.pdf").output_path
    with pymupdf.open(out) as doc:
        texts = [doc.xref_object(x) for x in range(1, doc.xref_length())]
        streams = [doc.xref_stream(x) or b"" for x in range(1, doc.xref_length()) if doc.xref_is_stream(x)]
    assert not any("ORPHANED-OBJECT" in t for t in texts)
    assert not any(b"ORIGINAL-SECRET" in s for s in streams)


def test_field_scripts_never_run(tmp_path: Path) -> None:
    src = field_calc_script(tmp_path / "form.pdf")
    before = _field_values(src)
    assert before == {"a": "first", "b": "untouched"}
    handler = PdfHandler()
    assert "JavaScript" in handler.inspect(src).active_content
    handler.render_preview(src, 1, default_job())
    handler.plan(src, default_job())
    single = handler.apply(src, default_job(), tmp_path / "single.pdf").output_path
    assert _field_values(single) == before
    other = tmp_path / "other.pdf"
    _blank(other).save(other)
    work = tmp_path / "work"
    work.mkdir()
    with combine_pdfs([CombineInput(src), CombineInput(other)], work) as combined:
        out = handler.apply(combined.path, default_job(), tmp_path / "combined.pdf").output_path
    assert _field_values(out) == before
