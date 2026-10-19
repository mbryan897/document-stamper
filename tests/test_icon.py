"""The app icon files (Phase 5): the .ico for the exes, the PNGs for the window.

The files are read with ``struct`` only (no image library), checking what
Windows and Qt rely on: every size is there, at the size it claims, with
transparency.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from stamper import sendto
from stamper.gui.icon import ICON_SIZES, ico_path, icon_dir

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_size(data: bytes) -> tuple[int, int, int]:
    """(width, height, colour type) from a PNG's IHDR."""
    assert data[:8] == PNG_SIGNATURE
    assert data[12:16] == b"IHDR"
    width, height, _depth, colour = struct.unpack(">IIBB", data[16:26])
    return width, height, colour


def _ico_entries(data: bytes) -> list[tuple[int, bytes]]:
    """(size, image bytes) for each entry of an .ico file."""
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1), "not an icon file"
    entries = []
    for i in range(count):
        w, h, _colours, _res, _planes, bits, length, offset = struct.unpack(
            "<BBBBHHII", data[6 + 16 * i : 22 + 16 * i])
        assert w == h, "icon images are square"
        assert bits == 32, "32-bit colour with alpha"
        entries.append((w or 256, data[offset : offset + length]))
    return entries


def test_ico_has_every_size_windows_asks_for() -> None:
    entries = _ico_entries(ico_path().read_bytes())
    assert sorted(size for size, _ in entries) == list(ICO_SIZES)


def test_ico_images_are_the_size_they_claim() -> None:
    for size, blob in _ico_entries(ico_path().read_bytes()):
        if blob[:8] == PNG_SIGNATURE:
            w, h, colour = _png_size(blob)
            assert (w, h, colour) == (size, size, 6), f"{size}px PNG entry"  # 6 = RGBA
        else:
            header_len, w, h2, _planes, bits = struct.unpack("<IiiHH", blob[:16])
            assert header_len == 40 and bits == 32
            assert (w, h2) == (size, 2 * size), f"{size}px BMP entry"  # DIB height includes the mask
            mask_row = ((size + 31) // 32) * 4
            assert len(blob) == 40 + size * size * 4 + size * mask_row


def test_ico_256_is_png_and_the_small_sizes_are_bitmaps() -> None:
    kinds = {size: blob[:8] == PNG_SIGNATURE for size, blob in _ico_entries(ico_path().read_bytes())}
    assert kinds[256] is True
    assert not any(is_png for size, is_png in kinds.items() if size < 256)


@pytest.mark.parametrize("size", ICON_SIZES)
def test_window_png_is_there_at_its_size(size: int) -> None:
    path = icon_dir() / "png" / f"stamper-{size}.png"
    assert _png_size(path.read_bytes()) == (size, size, 6)


def test_icon_corners_are_transparent() -> None:
    """The cut-out worked: no painted checkerboard left in a corner of the 32 px icon."""
    for size, blob in _ico_entries(ico_path().read_bytes()):
        if size != 32:
            continue
        pixels = blob[40 : 40 + size * size * 4]  # BGRA, bottom-up
        for x, y in ((0, 0), (size - 1, 0), (0, size - 1), (size - 1, size - 1)):
            alpha = pixels[(y * size + x) * 4 + 3]
            assert alpha == 0, f"corner ({x}, {y}) of the 32 px image isn't transparent"


def test_qt_loads_the_icon(qapp: object) -> None:
    from stamper.gui.icon import app_icon

    icon = app_icon()
    assert not icon.isNull()
    assert {s.width() for s in icon.availableSizes()} >= {16, 32, 48, 256}


def test_window_gets_the_icon(qapp: object) -> None:
    from stamper.gui.app import make_app

    assert not make_app().windowIcon().isNull()


def test_source_shortcut_uses_the_ico(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAMPER_APP_DIR", raising=False)
    t = sendto.target()
    assert t.icon == ico_path()
    assert t.icon.is_file()


def test_packaged_shortcut_uses_the_exe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import sys

    exe = tmp_path / "DocumentStamper.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert sendto.target().icon == exe.resolve()
