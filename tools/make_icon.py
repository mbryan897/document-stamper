"""Build the app icon files from the master picture (Phase 5, icon).

Input:  ``assets/icon/source/stamper-icon-master.png`` (1024 x 1024 RGBA,
        made once by ``tools/icon_cutout.py``).
Output, all committed so a build never needs this script:

- ``assets/icon/stamper.ico``: the Windows icon, put into both exes by
  PyInstaller and used by the Send to shortcut when run from source. Holds
  16, 20, 24, 32, 40, 48, 64, 96, 128 and 256 px: the sizes Explorer, the
  taskbar, Alt+Tab and the title bar ask for at 100-200 % display scaling.
  Sizes under 256 are stored as 32-bit BMPs (every Windows tool reads
  them); 256 is stored as PNG, as Windows expects for that size.
- ``assets/icon/png/stamper-<n>.png``: the same sizes plus 512, for the Qt
  window and taskbar icon (``stamper.gui.icon``). Qt picks the closest size.

Scaling is done on premultiplied colour, so the transparent edges don't pick
up a dark fringe. Sizes of 48 px and under get a light sharpen, because the
picture is detailed and turns soft when shrunk that far.

Needs Pillow, which is not an app dependency:
    .venv-icon\\Scripts\\python tools\\make_icon.py
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "assets" / "icon" / "source" / "stamper-icon-master.png"
ICON_DIR = ROOT / "assets" / "icon"
ICO = ICON_DIR / "stamper.ico"
PNG_DIR = ICON_DIR / "png"

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
PNG_SIZES = (*ICO_SIZES, 512)


def scaled(master: Image.Image, size: int) -> Image.Image:
    """The master at ``size`` px, scaled on premultiplied colour."""
    img = master.convert("RGBa").resize((size, size), Image.Resampling.LANCZOS).convert("RGBA")
    if size <= 48:
        r, g, b, a = img.split()
        rgb = Image.merge("RGB", (r, g, b)).filter(
            ImageFilter.UnsharpMask(radius=0.6 if size <= 24 else 0.8, percent=60, threshold=1))
        img = Image.merge("RGBA", (*rgb.split(), a))
    return img


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def bmp_entry(img: Image.Image) -> bytes:
    """An ICO image entry as a 32-bit DIB: BITMAPINFOHEADER, BGRA rows bottom-up, AND mask."""
    w, h = img.size
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    rows = []
    data = img.tobytes("raw", "BGRA")
    stride = w * 4
    for y in range(h - 1, -1, -1):
        rows.append(data[y * stride : (y + 1) * stride])
    mask_stride = ((w + 31) // 32) * 4
    alpha = img.getchannel("A").tobytes()
    mask_rows = []
    for y in range(h - 1, -1, -1):
        bits = bytearray(mask_stride)
        for x in range(w):
            if alpha[y * w + x] == 0:  # fully transparent: set the AND bit
                bits[x // 8] |= 0x80 >> (x % 8)
        mask_rows.append(bytes(bits))
    return header + b"".join(rows) + b"".join(mask_rows)


def build_ico(images: dict[int, Image.Image]) -> bytes:
    """An .ico file holding ``images`` (size -> picture), smallest first."""
    sizes = sorted(images)
    blobs = [png_bytes(images[s]) if s >= 256 else bmp_entry(images[s]) for s in sizes]
    out = bytearray(struct.pack("<HHH", 0, 1, len(sizes)))
    offset = 6 + 16 * len(sizes)
    for size, blob in zip(sizes, blobs, strict=True):
        dim = 0 if size >= 256 else size  # 0 means 256 in the directory
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    for blob in blobs:
        out += blob
    return bytes(out)


def main() -> int:
    master = Image.open(MASTER).convert("RGBA")
    if master.size != (1024, 1024):
        raise SystemExit(f"{MASTER.name} must be 1024 x 1024, not {master.size}")
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    images = {size: scaled(master, size) for size in PNG_SIZES}
    for size, img in images.items():
        (PNG_DIR / f"stamper-{size}.png").write_bytes(png_bytes(img))
    ICO.write_bytes(build_ico({s: images[s] for s in ICO_SIZES}))
    print(f"wrote {ICO.relative_to(ROOT)} ({ICO.stat().st_size:,} bytes) and {len(PNG_SIZES)} PNGs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
