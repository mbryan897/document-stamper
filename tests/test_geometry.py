"""Placement math (D-019): the most-tested module in the project."""

from __future__ import annotations

import itertools
from pathlib import Path

import pymupdf
import pytest

from stamper.core.geometry import (
    ANCHORS,
    Box,
    TextMetrics,
    insert_rotation,
    place,
    visible_rect_to_unrotated,
    visible_to_unrotated,
)
from tests.stamping import unrotated_vector_to_visible, visible_direction, visible_size

MARGIN = 18.0
PAD = 2.5
METRICS = TextMetrics(width=120.0, ascent=8.856, descent=2.844)

# Unrotated page sizes in points: Letter, Legal, A4, 3.125 x 8 in receipt, Letter landscape.
SIZES = {
    "letter": (612.0, 792.0),
    "legal": (612.0, 1008.0),
    "a4": (595.0, 842.0),
    "receipt": (225.0, 576.0),
    "landscape": (792.0, 612.0),
}
ROTATIONS = (0, 90, 180, 270)
DIRECTIONS = ("along-top", "along-right")

GRID = list(itertools.product(ANCHORS, DIRECTIONS, ROTATIONS, SIZES))


@pytest.mark.parametrize(("anchor", "direction", "rotation", "size"), GRID)
def test_placement_table(anchor: str, direction: str, rotation: int, size: str) -> None:
    cw, ch = SIZES[size]
    vw, vh = visible_size(cw, ch, rotation)
    pl = place(vw, vh, anchor=anchor, direction=direction, margin=MARGIN, metrics=METRICS, padding=PAD)
    page = Box(0, 0, vw, vh)
    inner = page.inset(MARGIN)

    assert pl.fits
    assert inner.contains(pl.box), "box crosses the margin"

    # Box size matches the text plus padding, turned for along-right.
    run, cross = METRICS.width + 2 * PAD, METRICS.height + 2 * PAD
    if direction == "along-top":
        assert pl.box.width == pytest.approx(run) and pl.box.height == pytest.approx(cross)
    else:
        assert pl.box.width == pytest.approx(cross) and pl.box.height == pytest.approx(run)

    vertical, horizontal = anchor.split("-")
    # Margins exactly 0.25 in from the anchored visible edges.
    if horizontal == "left":
        assert pl.box.x0 == pytest.approx(MARGIN)
    elif horizontal == "right":
        assert vw - pl.box.x1 == pytest.approx(MARGIN)
    else:
        assert pl.box.x0 == pytest.approx(vw - pl.box.x1)
    if vertical == "top":
        assert pl.box.y0 == pytest.approx(MARGIN)
    elif vertical == "bottom":
        assert vh - pl.box.y1 == pytest.approx(MARGIN)
    else:
        assert pl.box.y0 == pytest.approx(vh - pl.box.y1)

    # The baseline origin sits inside the box, padding in from the right side.
    ox, oy = pl.origin
    if direction == "along-top":
        assert ox == pytest.approx(pl.box.x0 + PAD)
        assert oy - METRICS.ascent == pytest.approx(pl.box.y0 + PAD)
        assert oy + METRICS.descent == pytest.approx(pl.box.y1 - PAD)
    else:
        # Reads top to bottom: starts at the top, descenders on the left.
        assert oy == pytest.approx(pl.box.y0 + PAD)
        assert ox - METRICS.descent == pytest.approx(pl.box.x0 + PAD)
        assert ox + METRICS.ascent == pytest.approx(pl.box.x1 - PAD)

    # Mapped back to unrotated coordinates, the box stays inside the CropBox.
    unrot = visible_rect_to_unrotated(pl.box, rotation, cw, ch)
    assert Box(0, 0, cw, ch).contains(unrot)
    assert unrot.width * unrot.height == pytest.approx(pl.box.width * pl.box.height)


@pytest.mark.parametrize("rotation", ROTATIONS)
@pytest.mark.parametrize("size", SIZES)
def test_visible_corners_map_onto_cropbox(rotation: int, size: str) -> None:
    cw, ch = SIZES[size]
    vw, vh = visible_size(cw, ch, rotation)
    assert visible_rect_to_unrotated(Box(0, 0, vw, vh), rotation, cw, ch) == Box(0, 0, cw, ch)


@pytest.mark.parametrize(
    ("rotation", "expected"),
    # Where the visible top-right corner lands in unrotated coordinates.
    [(0, (612, 0)), (90, (0, 0)), (180, (0, 792)), (270, (612, 792))],
)
def test_visible_top_right_corner(rotation: int, expected: tuple[float, float]) -> None:
    vw, _ = visible_size(612, 792, rotation)
    assert visible_to_unrotated((vw, 0), rotation, 612, 792) == pytest.approx(expected)


CROSS_CHECK_FILES = [
    "letter_portrait.pdf", "letter_landscape.pdf", "rotate_90.pdf", "rotate_180.pdf",
    "rotate_270.pdf", "rotate_mixed.pdf", "cropbox_offset.pdf", "mixed_sizes.pdf",
]


@pytest.mark.parametrize("name", CROSS_CHECK_FILES)
def test_mapping_matches_pymupdf_derotation(name: str, corpus_dir: Path) -> None:
    with pymupdf.open(corpus_dir / name) as doc:
        for page in doc:
            cw, ch = page.cropbox.width, page.cropbox.height
            assert visible_size(cw, ch, page.rotation) == pytest.approx((page.rect.width, page.rect.height))
            m = page.derotation_matrix
            for fx, fy in [(0, 0), (1, 0), (0, 1), (1, 1), (0.25, 0.75), (0.9, 0.1)]:
                p = (page.rect.width * fx, page.rect.height * fy)
                expected = pymupdf.Point(*p) * m
                assert visible_to_unrotated(p, page.rotation, cw, ch) == pytest.approx(
                    (expected.x, expected.y), abs=1e-3  # PyMuPDF matrices are float32
                ), f"{name} page {page.number + 1} point {p}"
            # Directions agree with page.rotation_matrix (vector part).
            rm = page.rotation_matrix
            for vec in [(1, 0), (0, 1), (-1, 0), (0, -1)]:
                v = pymupdf.Point(*vec) * rm - pymupdf.Point(0, 0) * rm
                assert unrotated_vector_to_visible(vec, page.rotation) == pytest.approx((v.x, v.y), abs=1e-3)


def test_cropbox_offset_page_is_the_cropped_size(corpus_dir: Path) -> None:
    with pymupdf.open(corpus_dir / "cropbox_offset.pdf") as doc:
        page = doc[0]
        assert (page.cropbox.x0, page.cropbox.y0) == (36, 54)
        vw, vh = visible_size(page.cropbox.width, page.cropbox.height, page.rotation)
        pl = place(vw, vh, anchor="top-right", direction="along-top", margin=MARGIN, metrics=METRICS, padding=PAD)
        # Measured from the visible (cropped) edge, not the MediaBox.
        assert pl.box.x1 == pytest.approx(504 - MARGIN)
        assert pl.box.y0 == pytest.approx(MARGIN)


@pytest.mark.parametrize(
    ("rotation", "direction", "expected"),
    [(r, "along-top", r) for r in ROTATIONS] + [(r, "along-right", (r + 270) % 360) for r in ROTATIONS],
)
def test_insert_rotation(rotation: int, direction: str, expected: int) -> None:
    assert insert_rotation(rotation, direction) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize("rotation", ROTATIONS)
@pytest.mark.parametrize("direction", DIRECTIONS)
def test_text_direction_comes_out_right(rotation: int, direction: str) -> None:
    """PyMuPDF's rotate= turns text counter-clockwise in unrotated space; the viewer
    then turns the page clockwise. The net direction must be the visible one."""
    angle = insert_rotation(rotation, direction)  # type: ignore[arg-type]
    vec = (1.0, 0.0)
    for _ in range(angle // 90):
        vec = (vec[1], -vec[0])  # counter-clockwise with y down
    assert unrotated_vector_to_visible(vec, rotation) == pytest.approx(visible_direction(direction))  # type: ignore[arg-type]


def test_along_right_reads_top_to_bottom() -> None:
    assert visible_direction("along-right") == (0.0, 1.0)
    assert visible_direction("along-top") == (1.0, 0.0)


def test_receipt_long_text_does_not_fit() -> None:
    vw, vh = visible_size(*SIZES["receipt"], 0)
    long = TextMetrics(width=300.0, ascent=8.856, descent=2.844)
    pl = place(vw, vh, anchor="top-right", direction="along-top", margin=MARGIN, metrics=long, padding=PAD)
    assert not pl.fits
    assert pl.needed == pytest.approx(305.0)
    assert pl.available == pytest.approx(225 - 36)
    # Along the right edge of the same receipt it fits.
    pl2 = place(vw, vh, anchor="top-right", direction="along-right", margin=MARGIN, metrics=long, padding=PAD)
    assert pl2.fits
    assert pl2.available == pytest.approx(576 - 36)


def test_cross_axis_too_thin() -> None:
    tall = TextMetrics(width=10.0, ascent=30.0, descent=10.0)
    pl = place(100, 60, anchor="top-left", direction="along-top", margin=MARGIN, metrics=tall, padding=PAD)
    assert not pl.fits
    assert pl.cross_needed == pytest.approx(45.0)
    assert pl.cross_available == pytest.approx(24.0)


def test_page_smaller_than_margins_never_fits() -> None:
    tiny = TextMetrics(width=1.0, ascent=1.0, descent=0.0)
    pl = place(30, 30, anchor="middle-center", direction="along-top", margin=MARGIN, metrics=tiny, padding=0)
    assert not pl.fits


@pytest.mark.parametrize("bad", [45, 30, -45])
def test_bad_rotation_rejected(bad: int) -> None:
    with pytest.raises(ValueError):
        visible_size(612, 792, bad)


def test_negative_rotation_normalized() -> None:
    assert visible_to_unrotated((10, 20), -90, 612, 792) == visible_to_unrotated((10, 20), 270, 612, 792)


def test_bad_anchor_and_direction() -> None:
    with pytest.raises(ValueError):
        place(612, 792, anchor="top-middle", direction="along-top", margin=18, metrics=METRICS, padding=PAD)
    with pytest.raises(ValueError):
        place(612, 792, anchor="top-right", direction="along-left", margin=18, metrics=METRICS, padding=PAD)  # type: ignore[arg-type]


def test_box_helpers() -> None:
    a = Box(0, 0, 10, 10)
    assert a.intersects(Box(5, 5, 15, 15))
    assert not a.intersects(Box(10, 0, 20, 10))  # sharing an edge isn't overlap
    assert a.contains(Box(1, 1, 9, 9))
    assert not a.contains(Box(-1, 1, 9, 9))
    assert Box.from_points((10, 0), (0, 10)) == a
    assert a.inset(2) == Box(2, 2, 8, 8)
