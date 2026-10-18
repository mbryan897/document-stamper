"""Write the 14-page placeholder return for the Phase 3 hallway test.

Synthetic placeholder text only (see tests/corpus/README.md). The file is
unsigned; sign it with a self-signed test certificate afterwards (see the
README's "Digitally signed PDF" section and tools/make_test_cert.py).

Usage (PowerShell, from the repo root, project venv):
    python tools\\make_hallway_sample.py tests\\corpus\\manual\\hallway_unsigned.pdf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymupdf

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.make_corpus import LETTER, _body, _new_page  # noqa: E402

PAGES = 14


def build(path: Path) -> Path:
    """Write the sample: 14 Letter pages of placeholder text, each with a header line at the top."""
    doc = pymupdf.open()
    for n in range(1, PAGES + 1):
        page = _new_page(doc, LETTER, f"Synthetic provider return - page {n} of {PAGES}")
        # A header line running close to the top-right corner, as many returns have,
        # so the white box behind the stamp has something to cover.
        page.insert_text((300, 30), "PLACEHOLDER PROVIDER  -  RECORDS RESPONSE  -  NOT REAL DATA",
                         fontsize=8, fontname="helv", color=(0.35, 0.35, 0.35))
        if n == 1:
            _body(page, "Synthetic provider return - page 1 of 14", margin=72)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path, garbage=3, deflate=True)
    return path


def main(argv: list[str] | None = None) -> int:
    """Command line: write the sample to the path given."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("out", type=Path, help="where to write the unsigned 14-page PDF")
    args = p.parse_args(argv)
    print(f"Wrote {build(args.out)} ({PAGES} pages, unsigned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
