"""Entry point for ``python -m stamper`` and ``stamper.exe``.

With arguments, runs the command line. With none, prints a short usage
message; the window arrives in Phase 3.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from .cli import build_parser, get_version
from .cli import main as cli_main

__all__ = ["get_version", "main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line, or print usage when there are no arguments."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        parser = build_parser()
        sys.stdout.write(f"Document Stamper {get_version()}\n")
        parser.print_usage(sys.stdout)
        sys.stdout.write("Run stamper --help for all options. The window isn't available yet.\n")
        return 0
    return cli_main(args)


if __name__ == "__main__":
    sys.exit(main())
