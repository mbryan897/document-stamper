"""The combine step (D-095 to D-104): order, stamps across the file, bookmarks, protection, fields, clean-up."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pymupdf
import pytest

from stamper.handlers.base import Cancelled, ConfirmationRequired, PasswordRequired, UnsupportedDocument
from stamper.handlers.combine import (
    SOURCES_PROPERTY,
    TEMP_PREFIX,
    CombineInput,
    combine_pdfs,
    field_suffix,
)
from stamper.handlers.pdf import PdfHandler
from tests.gui_support import copy_to
from tests.multi_support import assert_top_right_upright, make_form, page_body, root_names
from tests.stamping import FAKE_ID, default_job, make_signed_pdf, make_xfa_pdf, open_pdf, sha256
from tools import make_corpus

PW = make_corpus.USER_PASSWORD


def _three(corpus_dir: Path, folder: Path) -> list[CombineInput]:
    """Signed, rotated, password-protected: a non-alphabetical order."""
    signed = make_signed_pdf(corpus_dir, folder / "signed_return.pdf")
    rotated = copy_to(corpus_dir / "rotate_90.pdf", folder)
    locked = copy_to(corpus_dir / "encrypted_user_password.pdf", folder)
    return [CombineInput(signed), CombineInput(rotated), CombineInput(locked, PW)]


def _temp_folders(folder: Path) -> list[Path]:
    return [p for p in folder.iterdir() if p.name.startswith(TEMP_PREFIX)]


def test_combine_order_stamps_bookmarks_protection(corpus_dir: Path, tmp_path: Path) -> None:
    inputs = _three(corpus_dir, tmp_path)
    before = {i.path: sha256(i.path) for i in inputs}
    combined = combine_pdfs(inputs, tmp_path, accept_signature_loss=True)
    try:
        assert [s.path.name for s in combined.sources] == ["signed_return.pdf", "rotate_90.pdf",
                                                           "encrypted_user_password.pdf"]
        assert combined.protected == ("encrypted_user_password.pdf",)
        assert combined.signed == ("signed_return.pdf",)
        assert "encrypted_user_password.pdf" in combined.protection_warning()
        assert combined.path.name == "signed_return.pdf"  # {original_name} is the first file's (D-104)

        out = tmp_path / "combined_out.pdf"
        result = PdfHandler().apply(combined.path, default_job(), out, accept_signature_loss=True)
    finally:
        combined.cleanup()

    assert _temp_folders(tmp_path) == []
    for path, digest in before.items():
        assert sha256(path) == digest, f"{path.name} was modified"

    total = combined.page_count
    by_page = {p.page: p for p in result.placements}
    with pymupdf.open(out) as doc:
        assert not doc.needs_pass and not doc.metadata["encryption"]  # D-096
        assert doc.page_count == total == 1 + 3 + 2
        # Pages in the chosen order, each looking like its source page.
        expected_sources = [(inputs[0].path, None, 0), (inputs[1].path, None, 0), (inputs[1].path, None, 1),
                            (inputs[1].path, None, 2), (inputs[2].path, PW, 0), (inputs[2].path, PW, 1)]
        for page, (src, pw, n) in zip(doc, expected_sources):
            with open_pdf(src, pw) as orig:
                assert page.rotation == orig[n].rotation
                assert page_body(page, FAKE_ID) == page_body(orig[n], FAKE_ID)
        # Stamp top-right and upright on every page, numbered across the file (D-099).
        for page in doc:
            n = page.number + 1
            assert_top_right_upright(page, by_page[n], f"{FAKE_ID}  |  Report  |  Page {n} of {total}")
        # One bookmark per source, its own bookmarks nested under it.
        top = [(t[1], t[2]) for t in doc.get_toc() if t[0] == 1]
        assert top == [("signed_return.pdf", 1), ("rotate_90.pdf", 2), ("encrypted_user_password.pdf", 5)]
        # Source names and page ranges recorded; no folders (D-102).
        kind, value = doc.xref_get_key(int(doc.xref_get_key(-1, "Info")[1].split()[0]), SOURCES_PROPERTY)
        assert kind == "string"
        assert value.splitlines() == ["signed_return.pdf|1-1", "rotate_90.pdf|2-4", "encrypted_user_password.pdf|5-6"]
        assert str(tmp_path) not in value


def test_nested_bookmarks_kept(tmp_path: Path) -> None:
    a = tmp_path / "a.pdf"
    with pymupdf.open() as d:
        for _ in range(3):
            d.new_page()
        d.set_toc([[1, "Intro", 1], [2, "Detail", 2], [1, "End", 3]])
        d.save(a)
    b = tmp_path / "b.pdf"
    with pymupdf.open() as d:
        d.new_page()
        d.save(b)
    with combine_pdfs([CombineInput(b), CombineInput(a)], tmp_path) as combined, pymupdf.open(combined.path) as doc:
        assert doc.get_toc() == [[1, "b.pdf", 1], [1, "a.pdf", 2], [2, "Intro", 2], [3, "Detail", 3], [2, "End", 4]]


def test_hidden_layers_stay_hidden(tmp_path: Path) -> None:
    src = tmp_path / "layers.pdf"
    with pymupdf.open() as d:
        page = d.new_page()
        hidden = d.add_ocg("Hidden", on=False)
        shown = d.add_ocg("Shown", on=True)
        page.insert_text((72, 300), "hidden layer text", oc=hidden)
        page.insert_text((72, 320), "shown layer text", oc=shown)
        d.save(src)
    other = tmp_path / "other.pdf"
    with pymupdf.open() as d:
        d.new_page()
        d.save(other)
    with combine_pdfs([CombineInput(other), CombineInput(src)], tmp_path) as combined, \
            pymupdf.open(combined.path) as doc:
        text = doc[1].get_text()
        assert "shown layer text" in text and "hidden layer text" not in text
        states = {v["name"]: v["on"] for v in doc.get_ocgs().values()}
        assert states == {"Hidden": False, "Shown": True}


def test_layers_off_by_default_stay_hidden(tmp_path: Path) -> None:
    # BaseState /OFF: a layer not listed under ON is hidden, though it isn't in the OFF list.
    src = tmp_path / "base_off.pdf"
    with pymupdf.open() as d:
        page = d.new_page()
        layer = d.add_ocg("Default off", on=True)
        page.insert_text((72, 300), "base-off layer text", oc=layer)
        cat = d.pdf_catalog()
        d.xref_set_key(cat, "OCProperties/D/BaseState", "/OFF")
        d.xref_set_key(cat, "OCProperties/D/ON", "[]")
        d.xref_set_key(cat, "OCProperties/D/OFF", "[]")
        d.save(src)
    other = tmp_path / "other.pdf"
    with pymupdf.open() as d:
        d.new_page()
        d.save(other)
    with combine_pdfs([CombineInput(other), CombineInput(src)], tmp_path) as combined, \
            pymupdf.open(combined.path) as doc:
        assert "base-off layer text" not in doc[1].get_text()
        assert {v["name"]: v["on"] for v in doc.get_ocgs().values()} == {"Default off": False}


def _blank(path: Path) -> Path:
    with pymupdf.open() as d:
        d.new_page()
        d.save(path)
    return path


def _damaged(path: Path) -> Path:
    """A PDF whose cross-reference offset is wrong, so it opens only after repair."""
    _blank(path)
    data = path.read_bytes()
    at = data.rindex(b"startxref") + len(b"startxref")
    path.write_bytes(data[:at] + b"\n999999\n%%EOF\n")
    return path


def _tagged(path: Path) -> Path:
    with pymupdf.open() as d:
        d.new_page()
        d.xref_set_key(d.pdf_catalog(), "StructTreeRoot", "<< /Type /StructTreeRoot >>")
        d.save(path)
    return path


def _page_labels(path: Path) -> Path:
    with pymupdf.open() as d:
        d.new_page()
        d.set_page_labels([{"startpage": 0, "prefix": "A-", "style": "D", "firstpagenum": 1}])
        d.save(path)
    return path


@pytest.mark.parametrize(
    ("make", "note"),
    [
        (_damaged, "damaged.pdf was damaged and repaired when opened"),
        (_tagged, "Accessibility tags in tagged.pdf weren't kept"),
        (_page_labels, "Page labels from the originals weren't kept"),
    ],
    ids=["repaired", "tagged", "page-labels"],
)
def test_combine_notes_what_it_couldnt_keep(tmp_path: Path, make: Callable[[Path], Path], note: str) -> None:
    name = {"_damaged": "damaged.pdf", "_tagged": "tagged.pdf", "_page_labels": "labels.pdf"}[make.__name__]
    src = make(tmp_path / name)
    with combine_pdfs([CombineInput(_blank(tmp_path / "plain.pdf")), CombineInput(src)], tmp_path) as combined:
        assert any(note in n for n in combined.notes), combined.notes


def test_renames_named_for_each_source_with_the_same_name(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    files = [make_form(tmp_path / "a.pdf", "A"), make_form(tmp_path / "x" / "r.pdf", "X"),
             make_form(tmp_path / "y" / "r.pdf", "Y")]
    with combine_pdfs([CombineInput(f) for f in files], tmp_path) as combined:
        assert set(combined.renamed_fields) == {"r.pdf (file 2)", "r.pdf (file 3)"}
        assert ("case_no", "case_no" + field_suffix(2)) in combined.renamed_fields["r.pdf (file 2)"]
        assert ("case_no", "case_no" + field_suffix(3)) in combined.renamed_fields["r.pdf (file 3)"]
        assert sum("Form fields in r.pdf (file" in n for n in combined.notes) == 2


def test_attachments_kept_and_named_by_source(tmp_path: Path) -> None:
    a = tmp_path / "a.pdf"
    with pymupdf.open() as d:
        d.new_page()
        d.embfile_add("data.txt", b"synthetic attachment")
        d.save(a)
    b = tmp_path / "b.pdf"
    with pymupdf.open() as d:
        d.new_page()
        d.save(b)
    with combine_pdfs([CombineInput(a), CombineInput(b)], tmp_path) as combined, \
            pymupdf.open(combined.path) as doc:
        assert doc.embfile_names() == ["a - data.txt"]
        assert doc.embfile_get("a - data.txt") == b"synthetic attachment"
        assert any("attached file" in n for n in combined.notes)


def test_form_fields_stay_independent_after_saving(tmp_path: Path) -> None:
    one = make_form(tmp_path / "form_one.pdf", "ONE")
    two = make_form(tmp_path / "form_two.pdf", "TWO")
    with combine_pdfs([CombineInput(one), CombineInput(two)], tmp_path) as combined:
        out = tmp_path / "stamped.pdf"
        PdfHandler().apply(combined.path, default_job(), out)
        renamed = combined.renamed_fields["form_two.pdf"]
    assert renamed == [(n, n + field_suffix(2)) for n in ("case_no", "agree", "person", "choice")]

    with pymupdf.open(out) as doc:
        assert sorted(root_names(doc)) == sorted(["case_no", "agree", "person", "choice",
                                                  "case_no_f2", "agree_f2", "person_f2", "choice_f2"])
        names = {w.field_name for p in doc for w in p.widgets()}
        assert {"person.first", "person_f2.first", "person.last", "person_f2.last"} <= names
        # The radio group is still one group per file: two kids under one parent each.
        kids = [w for p in doc for w in p.widgets() if w.field_name in ("choice", "choice_f2")]
        assert sorted(w.field_name for w in kids) == ["choice", "choice", "choice_f2", "choice_f2"]

    # Fill file 1's fields, save, reopen: file 2 keeps its own values. Then the other way round.
    def fill(path: Path, target: Path, updates: dict[str, str]) -> None:
        with pymupdf.open(path) as doc:
            for page in doc:
                for w in page.widgets():
                    if w.field_name in updates:
                        w.field_value = updates[w.field_name]
                        w.update()
            doc.save(target)

    def values(path: Path) -> dict[str, str]:
        with pymupdf.open(path) as doc:
            return {w.field_name: str(w.field_value) for p in doc for w in p.widgets()
                    if w.field_type == pymupdf.PDF_WIDGET_TYPE_TEXT}

    first = tmp_path / "filled_one.pdf"
    fill(out, first, {"case_no": "CHANGED-1", "person.first": "Alpha"})
    v = values(first)
    assert v["case_no"] == "CHANGED-1" and v["person.first"] == "Alpha"
    assert v["case_no_f2"] == "TWO-case" and v["person_f2.first"] == "TWO-first"

    second = tmp_path / "filled_two.pdf"
    fill(first, second, {"case_no_f2": "CHANGED-2", "person_f2.last": "Omega"})
    v = values(second)
    assert v["case_no_f2"] == "CHANGED-2" and v["person_f2.last"] == "Omega"
    assert v["case_no"] == "CHANGED-1" and v["person.last"] == "ONE-last"


def test_suffix_avoids_names_already_taken(tmp_path: Path) -> None:
    one = make_form(tmp_path / "one.pdf", "ONE")
    two = tmp_path / "two.pdf"
    with pymupdf.open() as d:  # already has a field called case_no_f2
        page = d.new_page()
        for name in ("case_no", "case_no_f2"):
            w = pymupdf.Widget()
            w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
            w.field_name = name
            w.rect = pymupdf.Rect(72, 100 if name == "case_no" else 140, 300, 120 if name == "case_no" else 160)
            page.add_widget(w)
        d.save(two)
    with combine_pdfs([CombineInput(one), CombineInput(two)], tmp_path) as combined:
        assert combined.renamed_fields["two.pdf"] == [("case_no", "case_no" + field_suffix(2, 2))]


def test_confirmations_and_passwords_checked_first(corpus_dir: Path, tmp_path: Path) -> None:
    inputs = _three(corpus_dir, tmp_path)
    with pytest.raises(ConfirmationRequired) as e:
        combine_pdfs(inputs, tmp_path)
    assert e.value.reason == "signature" and "signed_return.pdf" in str(e.value)
    restricted = copy_to(corpus_dir / "owner_restrictions.pdf", tmp_path)
    with pytest.raises(ConfirmationRequired) as e:
        combine_pdfs([inputs[1], CombineInput(restricted)], tmp_path)
    assert e.value.reason == "restrictions" and "owner_restrictions.pdf" in str(e.value)
    with pytest.raises(PasswordRequired):
        combine_pdfs([inputs[1], CombineInput(inputs[2].path)], tmp_path)
    xfa = make_xfa_pdf(corpus_dir, tmp_path / "form.pdf")
    with pytest.raises(UnsupportedDocument):
        combine_pdfs([inputs[1], CombineInput(xfa)], tmp_path)
    assert _temp_folders(tmp_path) == []


def test_cancel_mid_combine_leaves_nothing(corpus_dir: Path, tmp_path: Path) -> None:
    inputs = _three(corpus_dir, tmp_path)
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] >= 3  # after the first file has been copied

    with pytest.raises(Cancelled):
        combine_pdfs(inputs, tmp_path, accept_signature_loss=True, cancel=cancel)
    assert _temp_folders(tmp_path) == []


def test_large_file_plus_four(corpus_dir: Path, tmp_path: Path, record_property: Callable[[str, object], None]) -> None:
    """Timing check for D-110: the 520-page file plus four others, combined and stamped.

    The times are recorded as test properties (``--junitxml`` shows them).
    """
    names = ["large_520_pages.pdf", "letter_portrait.pdf", "rotate_mixed.pdf", "mixed_sizes.pdf",
             "scanned_image_only.pdf"]
    inputs = [CombineInput(corpus_dir / n) for n in names]
    t0 = time.perf_counter()
    with combine_pdfs(inputs, tmp_path) as combined:
        t1 = time.perf_counter()
        result = PdfHandler().apply(combined.path, default_job(), tmp_path / "big.pdf")
        t2 = time.perf_counter()
    assert result.pages_stamped == combined.page_count > 520
    record_property("combine_seconds", round(t1 - t0, 2))
    record_property("stamp_seconds", round(t2 - t1, 2))
    record_property("pages", combined.page_count)
