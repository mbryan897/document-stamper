"""One-time: cut the app icon out of its source picture (Phase 5, icon).

The source (``assets/icon/source/stamper-icon-original.jpg``) is a JPEG, so it
has no transparency: the "transparent" background is a grey-and-white
checkerboard painted into the pixels. This removes it and writes
``assets/icon/source/stamper-icon-master.png``, a 1024 x 1024 RGBA picture
that ``tools/make_icon.py`` turns into every size the app needs.

How: the blue tile is a rounded square, drawn here as an exact anti-aliased
shape (measured from the picture). The parts that hang over its edge (the
stamp handle at the top, the PDF ribbon and the page on the left) are found
as strongly coloured or dark pixels joined to the tile; their edge pixels are
un-blended from the checkerboard. The tile's soft drop shadow is dropped,
because it was painted onto the checkerboard and can't be recovered cleanly.

Run again only if the source picture changes, and check the result by eye:
the measurements below belong to this picture.

Needs Pillow, NumPy and SciPy, which are not app dependencies:
    python -m venv .venv-icon
    .venv-icon\\Scripts\\pip install pillow numpy scipy
    .venv-icon\\Scripts\\python tools\\icon_cutout.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "icon" / "source" / "stamper-icon-original.jpg"
MASTER = ROOT / "assets" / "icon" / "source" / "stamper-icon-master.png"

# Measured from the source picture (1024 x 1024).
CHECKER_SQUARES = 50  # squares per side
CHECKER_DARK, CHECKER_LIGHT = 191.0, 245.0  # top-left square is dark
TILE = (101.6, 101.8, 921.6, 921.6)  # left, top, right, bottom
TILE_RADIUS = 138
SUPERSAMPLE = 4
MASTER_SIZE = 1024
MARGIN = 0.02  # each side, as a fraction of the master


def rounded_rect_alpha(h: int, w: int) -> np.ndarray:
    """Coverage (0-1) of the rounded tile per pixel, anti-aliased by supersampling."""
    x0, y0, x1, y1 = TILE
    r = TILE_RADIUS
    yy, xx = np.mgrid[0 : h * SUPERSAMPLE, 0 : w * SUPERSAMPLE]
    px = (xx + 0.5) / SUPERSAMPLE
    py = (yy + 0.5) / SUPERSAMPLE
    cx = np.clip(px, x0 + r, x1 - r)
    cy = np.clip(py, y0 + r, y1 - r)
    inside = ((px - cx) ** 2 + (py - cy) ** 2 <= r * r) & (px >= x0) & (px <= x1) & (py >= y0) & (py <= y1)
    return inside.reshape(h, SUPERSAMPLE, w, SUPERSAMPLE).mean(axis=(1, 3))


def cut_out(src: np.ndarray) -> np.ndarray:
    """RGBA float array: the icon with the painted checkerboard removed."""
    h, w, _ = src.shape
    tile = rounded_rect_alpha(h, w)
    yy, xx = np.mgrid[0:h, 0:w]
    sq = w / CHECKER_SQUARES
    dark = (np.floor(xx / sq) + np.floor(yy / sq)) % 2 == 0
    checker = np.where(dark, CHECKER_DARK, CHECKER_LIGHT)[..., None]

    diff = np.abs(src - checker).max(axis=2)
    sat = src.max(axis=2) - src.min(axis=2)
    lum = src.mean(axis=2)
    strong = (sat > 70) | (lum < 95) | ((diff > 110) & (sat > 35))
    strong &= tile < 0.5
    strong &= (yy < TILE[1]) | (xx < TILE[0])  # overhangs exist only at the top and left
    strong = ndi.binary_opening(strong, iterations=1)

    seed = ndi.binary_dilation(tile > 0.5, iterations=3)
    labels, _ = ndi.label(strong | seed)
    joined = np.isin(labels, np.unique(labels[seed]))
    extra = joined & (tile < 0.5)
    extra = ndi.binary_fill_holes(extra | (tile > 0.5)) & (tile < 0.5)
    extra = ndi.binary_closing(extra, iterations=2)

    soft = ndi.gaussian_filter((extra | (tile > 0.01)).astype(float), 0.7)
    soft = np.where(ndi.binary_dilation(extra, iterations=2), soft, 0)
    alpha = np.clip(np.maximum(tile, soft), 0, 1)

    rgb = src.copy()
    a3 = alpha[..., None]
    edge = (alpha > 0.05) & (alpha < 0.98) & (tile < 0.5)
    unblended = (src - (1 - a3) * checker) / np.maximum(a3, 0.05)
    rgb[edge] = np.clip(unblended[edge], 0, 255)
    return np.dstack([rgb, alpha * 255])


def to_square(rgba: np.ndarray) -> Image.Image:
    """Crop to the artwork, centre it on a square transparent canvas, scale to the master size."""
    img = Image.fromarray(rgba.round().clip(0, 255).astype(np.uint8), "RGBA")
    left, top, right, bottom = img.getbbox()
    side = max(right - left, bottom - top)
    pad = round(side * MARGIN / (1 - 2 * MARGIN))
    canvas = Image.new("RGBA", (side + 2 * pad, side + 2 * pad), (0, 0, 0, 0))
    canvas.paste(img.crop((left, top, right, bottom)),
                 (pad + (side - (right - left)) // 2, pad + (side - (bottom - top)) // 2))
    premul = canvas.convert("RGBa").resize((MASTER_SIZE, MASTER_SIZE), Image.Resampling.LANCZOS)
    return premul.convert("RGBA")


def main() -> int:
    src = np.asarray(Image.open(SOURCE).convert("RGB")).astype(float)
    master = to_square(cut_out(src))
    master.save(MASTER, optimize=True)
    print(f"wrote {MASTER.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
