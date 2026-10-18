"""Rules shared by the two multi-file modes (D-095), with no Qt and no PDF code.

- Pre-filling a field from several file names (D-106): a value is filled in
  only when every file that has one agrees; disagreement leaves the field
  blank and names the files, so two cases can't be combined by accident but
  a legitimate mix isn't blocked.
- The name a combined file's suggested file name starts from (D-105).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from .config import Config
from .fields import value_from_filename

__all__ = [
    "COMBINED_SUFFIX",
    "FilenameAgreement",
    "agree_from_filenames",
    "combined_original_name",
    "disagreement_note",
]

COMBINED_SUFFIX = "_combined"


@dataclass(frozen=True, slots=True)
class FilenameAgreement:
    """What several file names say about one field.

    ``found`` lists ``(file name, value)`` for every file whose name has a
    value. ``value`` is the agreed value, or None when no file has one or
    they disagree (``conflict``).
    """

    field: str
    found: tuple[tuple[str, str], ...]
    value: str | None
    conflict: bool


def agree_from_filenames(config: Config, field_name: str, names: Sequence[str]) -> FilenameAgreement:
    """Pre-fill rule for several files: fill only when every file that has a value agrees."""
    found: list[tuple[str, str]] = []
    for name in names:
        value = value_from_filename(config, field_name, PureWindowsPath(name).name)
        if value is not None:
            found.append((PureWindowsPath(name).name, value))
    distinct = {v for _, v in found}
    if len(distinct) == 1:
        return FilenameAgreement(field_name, tuple(found), distinct.pop(), False)
    return FilenameAgreement(field_name, tuple(found), None, len(distinct) > 1)


def disagreement_note(label: str, agreement: FilenameAgreement) -> str:
    """The amber note shown when file names suggest different values, else an empty string."""
    if not agreement.conflict:
        return ""
    listed = "; ".join(f"{name} → {value}" for name, value in agreement.found)
    return (f"The file names suggest different values for {label.lower()}: {listed}. "
            "Check that these files belong together, then type the value.")


def combined_original_name(first: Path | str) -> str:
    """The original name a combined file's suggested name is built from: ``<first stem>_combined.pdf``."""
    p = PureWindowsPath(str(first))
    return f"{p.stem}{COMBINED_SUFFIX}{p.suffix or '.pdf'}"
