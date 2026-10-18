"""Stamp placement math.

Everything here works in the page's *visible* orientation: the page as a
viewer shows it, after the CropBox is applied and ``/Rotate`` is honoured.
Coordinates are in points (1/72 in), origin at the visible top-left, x to the
right, y down.

The engine then maps visible coordinates back to *unrotated* page coordinates,
which is what PyMuPDF's drawing calls take: origin at the CropBox's top-left
corner, y down, before ``/Rotate``. ``visible_to_unrotated`` is the same
transform as PyMuPDF's ``page.derotation_matrix``; a test checks the two agree
for every rotation in the corpus.

This module has no PyMuPDF import, so it can be tested on its own.

Placement rules
---------------
- The stamp is a box: the text's extent (advance width by ascent + descent)
  plus ``padding`` on every side. The *box* edge sits ``margin`` from the
  anchored page edges, so with the background box on, nothing is drawn
  inside the margin.
- ``along-top``: text runs left to right, upright.
- ``along-right``: text is turned 90 degrees clockwise and reads top to
  bottom; the tops of the letters face the right edge.
- Anchors place the box in a 3 x 3 grid: left/center/right by
  top/middle/bottom. Center and middle ignore the margin on that axis.
- A placement *fits* when the whole box lies inside the page shrunk by the
  margin on all four sides. The caller decides what to do when it doesn't
  (shrink the font, then refuse); nothing here ever cuts text short.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "ANCHORS",
    "Box",
    "Direction",
    "Placement",
    "TextMetrics",
    "insert_rotation",
    "place",
    "unrotated_vector_to_visible",
    "visible_direction",
    "visible_rect_to_unrotated",
    "visible_size",
    "visible_to_unrotated",
]

Direction = Literal["along-top", "along-right"]

ANCHORS: tuple[str, ...] = (
    "top-left", "top-center", "top-right",
    "middle-left", "middle-center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
)

_ROTATIONS = (0, 90, 180, 270)

# Tolerance for floating-point comparisons, in points (1/1000 of a point).
EPS = 1e-3


@dataclass(frozen=True, slots=True)
class Box:
    """An axis-aligned rectangle; x0 <= x1 and y0 <= y1."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        """Horizontal extent."""
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        """Vertical extent."""
        return self.y1 - self.y0

    def contains(self, other: Box, eps: float = EPS) -> bool:
        """True if ``other`` lies entirely inside this box."""
        return (
            other.x0 >= self.x0 - eps
            and other.y0 >= self.y0 - eps
            and other.x1 <= self.x1 + eps
            and other.y1 <= self.y1 + eps
        )

    def intersects(self, other: Box, eps: float = EPS) -> bool:
        """True if the two boxes share more than an edge."""
        return (
            self.x0 < other.x1 - eps
            and other.x0 < self.x1 - eps
            and self.y0 < other.y1 - eps
            and other.y0 < self.y1 - eps
        )

    def inset(self, d: float) -> Box:
        """This box shrunk by ``d`` on every side."""
        return Box(self.x0 + d, self.y0 + d, self.x1 - d, self.y1 - d)

    def as_tuple(self) -> tuple[float, float, float, float]:
        """(x0, y0, x1, y1)."""
        return (self.x0, self.y0, self.x1, self.y1)

    @staticmethod
    def from_points(a: tuple[float, float], b: tuple[float, float]) -> Box:
        """The box spanned by two opposite corners, in any order."""
        return Box(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


@dataclass(frozen=True, slots=True)
class TextMetrics:
    """Size of one line of text at a given font size, in points.

    ``width`` is the advance width along the line. ``ascent`` and ``descent``
    are both positive distances above and below the baseline.
    """

    width: float
    ascent: float
    descent: float

    @property
    def height(self) -> float:
        """Ascent plus descent."""
        return self.ascent + self.descent


@dataclass(frozen=True, slots=True)
class Placement:
    """Where a stamp goes on one page, in visible coordinates.

    ``box`` is the background box (text extent plus padding). ``origin`` is
    the baseline start of the text: where the first character's baseline
    begins. ``needed`` and ``available`` are lengths along the direction the
    text runs, for error messages; ``cross_needed`` and ``cross_available``
    are the same across it.
    """

    box: Box
    origin: tuple[float, float]
    direction: Direction
    fits: bool
    needed: float
    available: float
    cross_needed: float
    cross_available: float


def _check_rotation(rotation: int) -> int:
    r = rotation % 360
    if r not in _ROTATIONS:
        raise ValueError(f"page rotation must be a multiple of 90, not {rotation}")
    return r


def visible_size(crop_width: float, crop_height: float, rotation: int) -> tuple[float, float]:
    """Visible page size from the unrotated CropBox size and ``/Rotate``."""
    r = _check_rotation(rotation)
    return (crop_height, crop_width) if r in (90, 270) else (crop_width, crop_height)


def place(
    page_width: float,
    page_height: float,
    *,
    anchor: str,
    direction: Direction,
    margin: float,
    metrics: TextMetrics,
    padding: float,
) -> Placement:
    """Place a stamp on a page of the given *visible* size.

    Always returns a placement; check ``fits`` before drawing it.
    """
    if anchor not in ANCHORS:
        raise ValueError(f"unknown anchor {anchor!r}")
    if direction not in ("along-top", "along-right"):
        raise ValueError(f"unknown direction {direction!r}")
    vertical, horizontal = anchor.split("-")

    run = metrics.width + 2 * padding  # length along the text
    cross = metrics.height + 2 * padding  # thickness across the text
    if direction == "along-top":
        w, h = run, cross
    else:
        w, h = cross, run

    if horizontal == "left":
        x0 = margin
    elif horizontal == "center":
        x0 = (page_width - w) / 2
    else:
        x0 = page_width - margin - w
    if vertical == "top":
        y0 = margin
    elif vertical == "middle":
        y0 = (page_height - h) / 2
    else:
        y0 = page_height - margin - h
    box = Box(x0, y0, x0 + w, y0 + h)

    if direction == "along-top":
        origin = (x0 + padding, y0 + padding + metrics.ascent)
        available = page_width - 2 * margin
        cross_available = page_height - 2 * margin
    else:
        # Turned 90 degrees clockwise: letter tops face right, so the
        # descenders sit on the left and the text starts at the top.
        origin = (x0 + padding + metrics.descent, y0 + padding)
        available = page_height - 2 * margin
        cross_available = page_width - 2 * margin

    inner = Box(0.0, 0.0, page_width, page_height).inset(margin)
    fits = inner.width > 0 and inner.height > 0 and inner.contains(box)
    return Placement(
        box=box,
        origin=origin,
        direction=direction,
        fits=fits,
        needed=run,
        available=max(0.0, available),
        cross_needed=cross,
        cross_available=max(0.0, cross_available),
    )


def visible_to_unrotated(
    point: tuple[float, float], rotation: int, crop_width: float, crop_height: float
) -> tuple[float, float]:
    """Map a visible point to unrotated page coordinates.

    ``crop_width`` and ``crop_height`` are the CropBox's unrotated size.
    Equivalent to ``point * page.derotation_matrix`` in PyMuPDF.
    """
    r = _check_rotation(rotation)
    x, y = point
    if r == 0:
        return (x, y)
    if r == 90:
        return (y, crop_height - x)
    if r == 180:
        return (crop_width - x, crop_height - y)
    return (crop_width - y, x)


def visible_rect_to_unrotated(
    box: Box, rotation: int, crop_width: float, crop_height: float
) -> Box:
    """Map a visible box to unrotated page coordinates (still axis-aligned)."""
    a = visible_to_unrotated((box.x0, box.y0), rotation, crop_width, crop_height)
    b = visible_to_unrotated((box.x1, box.y1), rotation, crop_width, crop_height)
    return Box.from_points(a, b)


def unrotated_vector_to_visible(vec: tuple[float, float], rotation: int) -> tuple[float, float]:
    """Turn a direction in unrotated coordinates into the visible one.

    A page with ``/Rotate`` r is shown turned r degrees clockwise.
    """
    r = _check_rotation(rotation)
    dx, dy = vec
    for _ in range(r // 90):
        dx, dy = -dy, dx  # 90 degrees clockwise with y pointing down
    return (dx, dy)


def visible_direction(direction: Direction) -> tuple[float, float]:
    """The unit vector a reader's eye follows, in visible coordinates."""
    return (1.0, 0.0) if direction == "along-top" else (0.0, 1.0)


def insert_rotation(page_rotation: int, direction: Direction) -> int:
    """Value for PyMuPDF's ``insert_text(rotate=...)``.

    PyMuPDF turns text counter-clockwise by ``rotate`` degrees in unrotated
    coordinates, and the viewer then turns the page clockwise by
    ``/Rotate``. Along-top text needs to come out at 0 degrees, so the two
    cancel; along-right needs a further 90 degrees clockwise (270
    counter-clockwise).
    """
    r = _check_rotation(page_rotation)
    extra = 0 if direction == "along-top" else 270
    return (r + extra) % 360
