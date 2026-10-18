"""Entry point for ``python -m stamper`` and ``stamper.exe``.

Which interface runs is decided by ``route`` (D-063):

- no arguments: the window, on the drop screen;
- only existing files, and nothing that starts with ``-``: the window. One
  file opens its stamp dialog (dragging a file onto the exe, or Send To,
  passes one bare path); several files (Send To passes several) open the
  files screen, to order them and choose Combine or Batch (D-100);
- anything else: the command line, exactly as in Phase 2. Any option, or
  ``--``, forces the command line: ``stamper -- file.pdf``.

``stamper-cli`` (``stamper-cli.exe`` in the packaged folder) always runs
the command line.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .cli import get_version
from .cli import main as cli_main

__all__ = ["Route", "get_version", "main", "route"]


@dataclass(frozen=True, slots=True)
class Route:
    """Where a command line goes: ``"window"`` with its files, or ``"cli"``."""

    kind: str
    files: tuple[Path, ...] = ()


def route(argv: Sequence[str]) -> Route:
    """Decide between the window and the command line for these arguments."""
    args = list(argv)
    if not args:
        return Route("window")
    if any(a.startswith("-") for a in args):
        return Route("cli")
    paths = tuple(Path(a) for a in args)
    if all(p.is_file() for p in paths):
        return Route("window", paths)
    return Route("cli")


def _ensure_streams() -> None:
    """Give the command line somewhere to write when there is no console.

    The windowed ``DocumentStamper.exe`` has no console, so ``sys.stdout``
    and ``sys.stderr`` are None there; output is dropped instead of crashing
    and the exit code still says what happened. Scripts use ``stamper-cli.exe``
    (D-087).
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115 - lives for the process


def main(argv: Sequence[str] | None = None) -> int:
    """Run the window or the command line; returns the exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    decision = route(args)
    if decision.kind == "cli":
        _ensure_streams()
        return cli_main(args)
    from .gui.app import run_window  # Qt is only imported for the window

    return run_window(decision.files)


if __name__ == "__main__":
    sys.exit(main())
