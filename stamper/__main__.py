"""Entry point for ``python -m stamper``. Phase 0: print the version and exit."""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version

DIST_NAME = "document-stamper"


def get_version() -> str:
    """Return the installed package version, or a marker if not installed."""
    try:
        return version(DIST_NAME)
    except PackageNotFoundError:
        return "0+unknown (not installed; run: python -m pip install -e .)"


def main() -> int:
    """Print the program name and version."""
    print(f"Document Stamper {get_version()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())