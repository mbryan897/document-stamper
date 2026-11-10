"""Check a built DocumentStamper.exe before it's handed out (build.ps1, Phase 5).

Run with the build venv's Python (it has pefile and PyInstaller):

    python packaging\\check_exe.py dist\\DocumentStamper.exe --version 0.1.0.dev0

Stops (exit 1) on the first group of problems it finds, after listing them:

1. The file is a 64-bit Windows exe with ASLR and DEP on (DYNAMIC_BASE,
   NX_COMPAT); high-entropy ASLR is reported if missing. No UPX sections.
2. Its icon is exactly ``assets/icon/stamper.ico``: every image in the exe's
   icon resources matches an image in the .ico, byte for byte, and none is
   missing. (Explorer may still show an old icon from its cache.)
3. Its version resource names the product and the version being built.
4. It is the single-file build: the bundle carries the help, the icon PNGs,
   the fonts, ``config/default.json`` and ``LICENSE``, each identical to the
   repo copy, and none of the Qt parts or tools that are left out on purpose
   (``spec_common.UNUSED``, tkinter, test code, a user's ``config.json``).

Prints a short report; nothing is written.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path

import pefile
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parent.parent
ICO = ROOT / "assets" / "icon" / "stamper.ico"

# Folders and files copied from the repo into the bundle (spec_common.DATAS).
BUNDLED = [
    ("assets/help", "**/*"),
    ("assets/icon/png", "*.png"),
    ("assets/fonts", "*.ttf"),
    ("assets/fonts", "*.txt"),
    ("config", "default.json"),
    (".", "LICENSE"),
]
# Must not be in the bundle (lower case, matched anywhere in the bundled name).
FORBIDDEN = (
    "pyside6/translations/", "opengl32sw.dll", "qt6quick", "qt6qml", "qt6virtualkeyboard",
    "platforminputcontexts/", "qt6pdf", "imageformats/qpdf",  # spec_common.UNUSED
    "_tkinter", "tcl86", "tk86",  # tkinter
    "pytest", "tests/",
)

IMAGE_FILE_MACHINE_AMD64 = 0x8664
DYNAMIC_BASE = 0x0040
NX_COMPAT = 0x0100
HIGH_ENTROPY_VA = 0x0020


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ico_images(path: Path) -> list[tuple[int, bytes]]:
    """(size in px, image bytes) for each image in an .ico file."""
    data = path.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or kind != 1 or count == 0:
        raise SystemExit(f"{path} isn't an icon file")
    out = []
    for i in range(count):
        w, _h, _c, _r, _p, _b, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        out.append((w or 256, data[offset : offset + size]))
    return out


def exe_icon_images(pe: pefile.PE) -> list[bytes]:
    blobs = []
    root = getattr(pe, "DIRECTORY_ENTRY_RESOURCE", None)
    if root is None:
        return blobs
    for kind in root.entries:
        if kind.id != pefile.RESOURCE_TYPE["RT_ICON"]:
            continue
        for name in kind.directory.entries:
            for lang in name.directory.entries:
                d = lang.data.struct
                blobs.append(pe.get_data(d.OffsetToData, d.Size))
    return blobs


def exe_version_strings(pe: pefile.PE) -> dict[str, str]:
    out: dict[str, str] = {}
    for info in getattr(pe, "FileInfo", None) or []:
        for entry in info:
            for table in getattr(entry, "StringTable", []) or []:
                for k, v in table.entries.items():
                    out[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
    return out


def check_pe(pe: pefile.PE, problems: list[str], notes: list[str]) -> None:
    if pe.FILE_HEADER.Machine != IMAGE_FILE_MACHINE_AMD64:
        problems.append(f"not a 64-bit Windows exe (machine {pe.FILE_HEADER.Machine:#x})")
    dll = pe.OPTIONAL_HEADER.DllCharacteristics
    if not dll & DYNAMIC_BASE:
        problems.append("ASLR is off (DYNAMIC_BASE not set)")
    if not dll & NX_COMPAT:
        problems.append("DEP is off (NX_COMPAT not set)")
    if not dll & HIGH_ENTROPY_VA:
        notes.append("high-entropy ASLR is off (HIGH_ENTROPY_VA not set)")
    else:
        notes.append("64-bit, ASLR (high entropy) and DEP on")
    sections = [s.Name.rstrip(b"\0").decode("ascii", "replace") for s in pe.sections]
    if any(s.upper().startswith("UPX") for s in sections):
        problems.append(f"packed with UPX (sections {sections}); antivirus flags these, and upx=False is meant")


def check_icon(pe: pefile.PE, problems: list[str], notes: list[str]) -> None:
    want = ico_images(ICO)
    want_hashes = {sha(b): size for size, b in want}
    have = exe_icon_images(pe)
    have_hashes = {sha(b) for b in have}
    if not have:
        problems.append("the exe has no icon")
        return
    missing = sorted(size for h, size in want_hashes.items() if h not in have_hashes)
    extra = len(have_hashes - set(want_hashes))
    if missing:
        problems.append(f"icon sizes {missing} px in stamper.ico aren't in the exe (old icon?)")
    if extra:
        problems.append(f"the exe has {extra} icon image(s) that aren't in stamper.ico (old or default icon)")
    if not missing and not extra:
        sizes = ", ".join(str(s) for s in sorted(want_hashes.values()))
        notes.append(f"icon matches assets\\icon\\stamper.ico ({sizes} px)")


def check_version(pe: pefile.PE, expected: str, problems: list[str], notes: list[str]) -> None:
    strings = exe_version_strings(pe)
    if not strings:
        problems.append("the exe has no version resource (STAMPER_VERSION_FILE not passed to the spec?)")
        return
    for key, want in (("ProductName", "Document Stamper"), ("FileVersion", expected),
                      ("OriginalFilename", "DocumentStamper.exe")):
        if strings.get(key) != want:
            problems.append(f"version resource {key} is {strings.get(key)!r}, expected {want!r}")
    notes.append(f"version resource: {strings.get('ProductName')} {strings.get('FileVersion')}"
                 f" - {strings.get('Comments', '')}")


def check_bundle(exe: Path, problems: list[str], notes: list[str]) -> None:
    try:
        reader = CArchiveReader(str(exe))
    except Exception as exc:  # noqa: BLE001 - any failure means it isn't a one-file build
        problems.append(f"no PyInstaller archive inside the exe ({exc}); is this the folder build's launcher?")
        return
    names = {n.replace("\\", "/"): n for n in reader.toc}
    lower = [n.lower() for n in names]
    for bad in FORBIDDEN:
        hits = [n for n in lower if bad in n]
        if hits:
            problems.append(f"bundle contains {bad!r} ({hits[0]}{' ...' if len(hits) > 1 else ''})")
    user_configs = [n for n in names if n.lower().endswith("config.json")]
    if user_configs:
        problems.append(f"a config.json is bundled ({user_configs}); only config/default.json belongs inside")

    checked = 0
    for folder, pattern in BUNDLED:
        base = ROOT / folder
        files = [p for p in base.glob(pattern) if p.is_file()]
        if not files:
            problems.append(f"nothing in the repo matches {folder}/{pattern}")
        for src in files:
            rel = src.relative_to(ROOT).as_posix()
            if rel not in names:
                problems.append(f"{rel} is not in the bundle")
                continue
            if sha(reader.extract(names[rel])) != sha(src.read_bytes()):
                problems.append(f"{rel} in the bundle differs from the repo copy (stale build?)")
            checked += 1
    if "assets/help/index.json" not in names:
        problems.append("the help (assets/help) is missing from the bundle")
    notes.append(f"single-file bundle: {len(names)} entries; {checked} asset files match the repo")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("exe", type=Path)
    ap.add_argument("--version", required=True, help="the version the exe must report")
    args = ap.parse_args()

    problems: list[str] = []
    notes: list[str] = []
    pe = pefile.PE(str(args.exe), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
    check_pe(pe, problems, notes)
    check_icon(pe, problems, notes)
    check_version(pe, args.version, problems, notes)
    pe.close()
    check_bundle(args.exe, problems, notes)

    for n in notes:
        print(f"  -     {n}")
    for p in problems:
        print(f"  FAIL  {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
