"""Safe Windows filenames for stamped copies.

The builder only suggests a name. Refusing to save over the original is the
caller's job (Phase 2/3).

Rules
-----
- Each field value: Unicode NFC; characters Windows forbids (``<>:"/\\|?*``)
  and control characters become ``-``; whitespace becomes ``-``; the
  separator character inside a value becomes ``-`` so parts stay distinct;
  runs of ``-`` collapse; ``-``, ``.``, spaces and the separator are trimmed
  from each end.
- The original name is cleaned the same way, except that underscores and the
  separator are kept, so ``provider_return`` stays ``provider_return``.
- Parts are joined with the separator; empty parts are dropped.
- Length: at most ``MAX_FILENAME_LENGTH`` (120) UTF-16 code units including
  the extension. Windows measures names in UTF-16, so that is what counts.
  Over the limit, the original name is shortened first, then the other parts
  from last to first. The first part (the identifier by default) is cut only
  if it alone doesn't fit.
- Trailing dots and spaces are removed; a reserved device name (``CON``,
  ``COM1``, ``LPT¹`` ...) gets ``_`` appended to its base.
- If nothing is left, the name falls back to ``<original>_stamped``, then
  ``document``.

The same inputs always give the same name.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Profile

__all__ = [
    "ILLEGAL_FILENAME_CHARS",
    "MAX_FILENAME_LENGTH",
    "build_filename",
    "clean_part",
    "is_reserved_name",
    "suggest_filename",
]

MAX_FILENAME_LENGTH = 120
ILLEGAL_FILENAME_CHARS = frozenset('<>:"/\\|?*')

_SUPERSCRIPTS = "¹²³"
_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{dev}{n}" for dev in ("COM", "LPT") for n in [*"0123456789", *_SUPERSCRIPTS]}
)
_HYPHEN_RUN = re.compile(r"-{2,}")
_WHITESPACE = re.compile(r"\s+")
_MIN_PART = 8  # don't shorten a part below this unless nothing else works


def _len16(text: str) -> int:
    """Length in UTF-16 code units, which is how Windows counts."""
    return len(text.encode("utf-16-le")) // 2


def _cut16(text: str, limit: int) -> str:
    """Longest prefix of text that fits in ``limit`` UTF-16 code units."""
    out, used = [], 0
    for ch in text:
        w = 2 if ord(ch) > 0xFFFF else 1
        if used + w > limit:
            break
        out.append(ch)
        used += w
    return "".join(out)


def _strip_edges(text: str, separator: str) -> str:
    return text.strip("-. " + separator)


def clean_part(text: str, separator: str = "_", *, keep_separator: bool = False) -> str:
    """Make one piece of a filename safe; see the module rules."""
    text = unicodedata.normalize("NFC", text)
    text = "".join(
        "-" if (c in ILLEGAL_FILENAME_CHARS or ord(c) < 32 or ord(c) == 127) else c
        for c in text
    )
    text = _WHITESPACE.sub("-", text)
    if not keep_separator and separator and separator != "-":
        text = text.replace(separator, "-")
    text = _HYPHEN_RUN.sub("-", text)
    return _strip_edges(text, "" if keep_separator else separator)


def is_reserved_name(stem: str) -> bool:
    """True if Windows treats this name (before any dot) as a device."""
    base = stem.split(".", 1)[0].rstrip(" ")
    return base.upper() in _RESERVED


def _fit(parts: list[str], original: str, sep: str, budget: int) -> tuple[list[str], str]:
    def total() -> int:
        pieces = [p for p in [*parts, original] if p]
        return _len16(sep.join(pieces))

    def shrink(text: str, over: int, floor: int) -> str:
        target = max(floor, _len16(text) - over)
        return _strip_edges(_cut16(text, target), sep)

    # 1. Original name, down to _MIN_PART, then dropped entirely.
    if total() > budget and original:
        original = shrink(original, total() - budget, _MIN_PART)
        if total() > budget:
            original = ""
    # 2. Other parts, last to first, never the first part here.
    for idx in range(len(parts) - 1, 0, -1):
        if total() <= budget:
            break
        parts[idx] = shrink(parts[idx], total() - budget, _MIN_PART)
    for idx in range(len(parts) - 1, 0, -1):
        if total() <= budget:
            break
        parts[idx] = ""
    # 3. Last resort: the first part.
    if parts and total() > budget:
        parts[0] = shrink(parts[0], total() - budget, 1)
    return parts, original


def build_filename(
    values: Sequence[str],
    *,
    separator: str = "_",
    original_name: str | None = None,
    extension: str = ".pdf",
    max_length: int = MAX_FILENAME_LENGTH,
    fallback_name: str | None = None,
) -> str:
    """Build a safe filename from field values in order.

    ``values`` are the ticked fields' values, in the order they should
    appear. ``original_name`` is the source file's name without extension, or
    None to leave it out. ``fallback_name`` (default: ``original_name``) is
    used as ``<fallback>_stamped`` if nothing else is left.
    """
    ext = extension if extension.startswith(".") or not extension else "." + extension
    ext = clean_part(ext.lstrip("."), separator, keep_separator=True)
    ext = f".{ext}" if ext else ""
    parts = [clean_part(v, separator) for v in values]
    parts = [p for p in parts if p]
    original = clean_part(original_name, separator, keep_separator=True) if original_name else ""

    budget = max(1, max_length - _len16(ext))
    parts, original = _fit(parts, original, separator, budget)
    stem = separator.join(p for p in [*parts, original] if p)
    stem = stem.rstrip(". ")

    if not stem:
        fallback = clean_part(fallback_name or original_name or "", separator, keep_separator=True)
        stem = _cut16(f"{fallback}_stamped" if fallback else "document", budget).rstrip(". ")

    if is_reserved_name(stem):
        base, dot, rest = stem.partition(".")
        stem = f"{base.rstrip(' ')}_{dot}{rest}"
    return stem + ext


def suggest_filename(
    profile: Profile,
    values: Mapping[str, str],
    original_filename: str,
    *,
    ticked: Sequence[str] | None = None,
    max_length: int = MAX_FILENAME_LENGTH,
) -> str:
    """Suggested name for a stamped copy, using a profile's ``output_name``.

    ``values`` holds normalized field values plus ``date`` if it's ticked.
    ``ticked`` is the File name selector's choice, in order; None means the
    profile's defaults. The extension comes from ``original_filename``.
    """
    src = PureWindowsPath(original_filename)
    chosen = profile.output_name.fields if ticked is None else ticked
    return build_filename(
        [values.get(f, "") for f in chosen],
        separator=profile.output_name.separator,
        original_name=src.stem if profile.output_name.include_original_name else None,
        extension=src.suffix or ".pdf",
        max_length=max_length,
        fallback_name=src.stem,
    )
