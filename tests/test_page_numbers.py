"""The Page numbers option (O-004, D-065): splitting a stamp, and the stamps it produces."""

from __future__ import annotations

from pathlib import Path

import pytest

from stamper.core.config import StampSpec
from stamper.gui.page_numbers import (
    PROFILE,
    WITH_IDENTIFIER,
    Split,
    apply_page_numbers,
    main_stamp_index,
    page_number_option,
    split_page_numbers,
)
from stamper.handlers.base import StampJob
from stamper.handlers.pdf import PdfHandler
from tests.stamping import FAKE_ID, open_pdf, records_config, sha256

RECORDS_STAMP = "{identifier}  |  {doc_type}  |  Page {page} of {total}"


@pytest.mark.parametrize(
    ("text", "kept", "moved"),
    [
        (RECORDS_STAMP, "{identifier}  |  {doc_type}", "Page {page} of {total}"),
        ("{identifier} p. {page}/{total}", "{identifier}", "p. {page}/{total}"),
        ("Page {page} of {total} — {identifier}", "{identifier}", "Page {page} of {total}"),
        ("{identifier} | Page {page} of {total} | {doc_type}", "{identifier} | {doc_type}",
         "Page {page} of {total}"),
        ("{identifier}  |  Page {page} of {total} pages", "{identifier}", "Page {page} of {total} pages"),
        ("{identifier}-{page}", "{identifier}", "{page}"),
        ("No. {page}", "", "No. {page}"),
        ("{page}", "", "{page}"),
        ("{identifier}  |  Page {page}", "{identifier}", "Page {page}"),
    ],
)
def test_split(text: str, kept: str, moved: str) -> None:
    assert split_page_numbers(text) == Split(kept, moved)


@pytest.mark.parametrize("text", ["{identifier}  |  {doc_type}", "{page} {identifier} {total}", "{bad"])
def test_no_split(text: str) -> None:
    assert split_page_numbers(text) is None


def _stamp(text: str, anchor: str = "top-right") -> StampSpec:
    return StampSpec(text=text, anchor=anchor)  # type: ignore[arg-type]


def test_option_default_profile_stamp() -> None:
    opt = page_number_option([_stamp(RECORDS_STAMP)])
    assert opt.choices == (WITH_IDENTIFIER, "bottom-left", "bottom-center", "bottom-right")
    assert all(opt.enabled) and opt.current == WITH_IDENTIFIER and opt.stamp == 0


def test_option_without_page_token() -> None:
    opt = page_number_option([_stamp("{identifier}")])
    assert opt.enabled == (False,)
    assert "no page number" in opt.note


def test_option_inseparable() -> None:
    opt = page_number_option([_stamp("{page} {identifier} {total}")])
    assert opt.enabled == (False,)
    assert "can't be separated" in opt.note


def test_option_positions_in_use_are_disabled() -> None:
    stamps = [_stamp(RECORDS_STAMP, "bottom-right"), _stamp("{doc_type}", "bottom-left")]
    opt = page_number_option(stamps)
    assert dict(zip(opt.choices, opt.enabled)) == {
        WITH_IDENTIFIER: True, "bottom-left": False, "bottom-center": True, "bottom-right": False}


def test_option_existing_separate_stamp() -> None:
    stamps = [_stamp("{identifier}"), _stamp("Page {page} of {total}", "bottom-center")]
    opt = page_number_option(stamps)
    assert opt.current == "bottom-center" and opt.stamp == 1
    assert WITH_IDENTIFIER not in opt.choices
    assert "already a separate stamp" in opt.note
    moved = apply_page_numbers(stamps, "bottom-left")
    assert [s.anchor for s in moved] == ["top-right", "bottom-left"]
    assert [s.text for s in moved] == [s.text for s in stamps]


def test_option_separate_stamp_not_at_bottom() -> None:
    stamps = [_stamp("{identifier}"), _stamp("{page}/{total}", "middle-left")]
    opt = page_number_option(stamps)
    assert opt.current == PROFILE and opt.choices[0] == PROFILE
    assert apply_page_numbers(stamps, PROFILE) == tuple(stamps)


def test_apply_moves_page_numbers_to_bottom() -> None:
    source = StampSpec(text=RECORDS_STAMP, direction="along-right", font_size_pt=10,
                       color="#112233", margin_in=0.5, background_box=False)
    out = apply_page_numbers([source], "bottom-center")
    assert len(out) == 2
    first, bottom = out
    assert first.text == "{identifier}  |  {doc_type}"
    assert (first.anchor, first.direction) == ("top-right", "along-right")
    assert bottom.text == "Page {page} of {total}"
    assert (bottom.anchor, bottom.direction) == ("bottom-center", "along-top")
    assert (bottom.font_size_pt, bottom.color, bottom.margin_in, bottom.background_box) == (
        10, "#112233", 0.5, False)
    assert apply_page_numbers([source], WITH_IDENTIFIER) == (source,)


def test_main_stamp_index() -> None:
    assert main_stamp_index([_stamp("{doc_type}"), _stamp("{identifier}")]) == 1
    assert main_stamp_index([_stamp("{doc_type}")]) == 0


@pytest.mark.parametrize("choice", ["bottom-left", "bottom-center", "bottom-right"])
def test_page_numbers_on_rendered_output(choice: str, corpus_dir: Path, tmp_path: Path) -> None:
    cfg = records_config()
    prof = cfg.default_profile
    stamps = apply_page_numbers(prof.stamps, choice)
    job = StampJob.from_profile(cfg, prof, {"identifier": FAKE_ID, "doc_type": "Court order"},
                                stamps=stamps)
    assert [s.text for s in job.stamps] == ["{identifier}  |  {doc_type}", "Page {page} of {total}"]
    src = corpus_dir / "rotate_mixed.pdf"
    before = sha256(src)
    result = PdfHandler().apply(src, job, tmp_path / "out.pdf")
    assert sha256(src) == before
    with open_pdf(result.output_path) as doc:
        for page in doc:
            n, w, h = page.number + 1, page.rect.width, page.rect.height
            mine = [p for p in result.placements if p.page == n]
            top = next(p for p in mine if p.stamp == 1)
            bottom = next(p for p in mine if p.stamp == 2)
            assert top.text == f"{FAKE_ID}  |  Court order"
            assert bottom.text == f"Page {n} of {doc.page_count}"
            assert top.box[1] < h / 2 < bottom.box[1]
            centre = (bottom.box[0] + bottom.box[2]) / 2
            expected = {"bottom-left": centre < w / 3, "bottom-center": w / 3 < centre < 2 * w / 3,
                        "bottom-right": centre > 2 * w / 3}[choice]
            assert expected, (choice, bottom.box, w)
            text = page.get_text()
            assert f"Page {n} of {doc.page_count}" in text and f"{FAKE_ID}  |  Court order" in text
            assert f"Court order  |  Page" not in text


# ------------------------------------------------------------ optional groups (D-083)


def test_group_around_the_page_part_moves_with_it() -> None:
    split = split_page_numbers("{identifier}  |  [Page {page} of {total}]")
    assert split == Split(kept="{identifier}", moved="[Page {page} of {total}]")


def test_page_part_inside_a_wider_group_stops_at_the_group() -> None:
    split = split_page_numbers("{identifier}  |  [{doc_type} p. {page}]")
    assert split is not None and split.moved == "p. {page}"


def test_group_elsewhere_is_kept() -> None:
    split = split_page_numbers("{identifier}  |  [Title: {doc_title}]  |  Page {page} of {total}")
    assert split == Split(kept="{identifier}  |  [Title: {doc_title}]", moved="Page {page} of {total}")
