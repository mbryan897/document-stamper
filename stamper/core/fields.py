"""Field values: normalization, validation and filename pre-fill.

Validation never raises for bad input. It returns a ``ValidationResult`` so
the stamp dialog can show live feedback as the user types.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import PureWindowsPath

from .config import Config, FieldSpec, FormatSpec, Profile
from .patterns import PatternTest

__all__ = [
    "NORMALIZERS",
    "ValidationResult",
    "fold_dashes",
    "normalize",
    "prefill_fields",
    "validate_field",
    "value_from_filename",
]

# Hyphen, non-breaking hyphen, figure dash, en dash, em dash, horizontal bar,
# minus sign, small and fullwidth hyphen-minus.
_DASHES = dict.fromkeys(
    map(ord, "‐‑‒–—―−﹘﹣－"), "-"
)


def fold_dashes(text: str) -> str:
    """Replace dash look-alikes (en dash, em dash, minus ...) with ``-``."""
    return text.translate(_DASHES)


NORMALIZERS: dict[str, Callable[[str], str]] = {
    "trim": str.strip,
    "upper": str.upper,
    "dashes": fold_dashes,
}


def normalize(value: str, steps: Sequence[str]) -> str:
    """Apply normalizers in the order given."""
    for step in steps:
        value = NORMALIZERS[step](value)
    return value


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Outcome of validating one field value.

    ``value`` is the normalized value (what gets stamped). ``format`` is the
    display name of the format that matched, if any. ``message`` is written
    for the end user, for both valid and invalid values. ``date`` is the
    calendar date a valid value encodes, when the matching format has a
    year, month and day (``SOYYMMDD-######``), or the value of a ``date``
    field; otherwise None. Callers decide what a future date means (O-003).
    """

    valid: bool
    value: str
    message: str
    format: str | None = None
    date: date | None = None


def _format_test(fmt: FormatSpec, value: str) -> PatternTest:
    if fmt.compiled is not None:
        return fmt.compiled.test(value)
    assert fmt.compiled_regex is not None
    if fmt.compiled_regex.fullmatch(value):
        return PatternTest(True, f"Matches {fmt.display_name}", len(value))
    return PatternTest(False, "", 0)


def _validate_text(spec: FieldSpec, value: str) -> ValidationResult:
    if not spec.formats:
        return ValidationResult(True, value, "")
    tests = [(fmt, _format_test(fmt, value)) for fmt in spec.formats]
    for fmt, t in tests:
        if t.ok:
            encoded = fmt.compiled.date_of(value) if fmt.compiled is not None else None
            return ValidationResult(
                True, value, f"Matches {fmt.display_name}.", fmt.display_name, encoded
            )
    names = ", ".join(f.display_name for f in spec.formats)
    lead = (
        f"{spec.label} must match {names}."
        if len(spec.formats) == 1
        else f"{spec.label} must match one of: {names}."
    )
    # Hint from the format the value got furthest into.
    best_fmt, best = max(tests, key=lambda ft: ft[1].matched)
    hint = best.message
    if hint and len(spec.formats) > 1:
        hint = f"Closest is {best_fmt.display_name}: {hint[0].lower()}{hint[1:]}"
    return ValidationResult(False, value, f"{lead} {hint}." if hint else lead)


def validate_field(config: Config, field_name: str, raw: str | None) -> ValidationResult:
    """Normalize and validate one value for a field defined in ``config``.

    Raises KeyError for an unknown field name (a programming error, since
    config validation checks profile field lists).
    """
    spec = config.fields[field_name]
    value = normalize(raw or "", spec.normalize)
    if value.strip() == "":
        if spec.required:
            return ValidationResult(False, value, f"{spec.label} is required.")
        return ValidationResult(True, "", "")

    if spec.type == "choice":
        assert spec.taxonomy is not None
        choices = config.taxonomies[spec.taxonomy]
        key = value.strip().casefold()
        for choice in choices:
            if choice.strip().casefold() == key:
                return ValidationResult(True, choice, "")
        return ValidationResult(
            False, value, f"{spec.label} must be one of: {', '.join(choices)}."
        )

    if spec.type == "date":
        fmt = config.settings.date_formatter
        parsed = fmt.parse(value)
        if parsed is None:
            return ValidationResult(
                False, value, f"Enter {spec.label.lower()} as a real date in the form {fmt.source}."
            )
        return ValidationResult(True, fmt.format(parsed), "", fmt.source, parsed)

    return _validate_text(spec, value)


_BOUNDARY_BEFORE = r"(?<![A-Za-z0-9])"
_BOUNDARY_AFTER = r"(?![A-Za-z0-9])"


def value_from_filename(config: Config, field_name: str, filename: str) -> str | None:
    """Find exactly one valid value for a field inside a filename.

    The field's normalizers are applied to the filename stem first, then each
    simple-pattern format is searched for with letter/digit boundaries on
    both sides, so a match can't sit inside a longer run of digits. Every hit
    is re-checked as a whole value (dates must be real). Formats with ``*``
    and advanced ``regex`` formats are skipped, since they could match almost
    anything.

    Returns the value if exactly one distinct valid value is found, otherwise
    None. Also None if the field doesn't use ``from_filename``.
    """
    spec = config.fields[field_name]
    if not spec.from_filename or spec.type != "text":
        return None
    stem = normalize(PureWindowsPath(filename).stem, spec.normalize)
    found: set[str] = set()
    for fmt in spec.formats:
        compiled = fmt.compiled
        if compiled is None or compiled.has_wildcard:
            continue
        rx = re.compile(f"{_BOUNDARY_BEFORE}(?:{compiled.body}){_BOUNDARY_AFTER}", re.ASCII)
        for m in rx.finditer(stem):
            candidate = m.group(0)
            if compiled.matches(candidate):
                found.add(candidate)
    return found.pop() if len(found) == 1 else None


def prefill_fields(config: Config, profile: Profile) -> list[str]:
    """The profile's fields that are pre-filled from the filename (``from_filename``).

    These are the fields whose value is dropped from the original-name part of
    the suggested filename when it's already there (O-008).
    """
    return [f for f in profile.fields if config.fields[f].from_filename]
