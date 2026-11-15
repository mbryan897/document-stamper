"""Field normalization, validation and filename pre-fill.

All identifiers are fabricated (9999xx suffixes) and only need to fit the
formats in tests/fixtures/records_sample.json.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from stamper.core.config import Config, load_config
from stamper.core.fields import NORMALIZERS, fold_dashes, normalize, validate_field, value_from_filename

FIXTURE = Path(__file__).parent / "fixtures" / "records_sample.json"
DEFAULT = Path(__file__).resolve().parents[1] / "config" / "default.json"

DASHED = "##-YYYY-######"
SO = "SOYYMMDD-######"


@pytest.fixture(scope="module")
def records() -> Config:
    """Sample config with the two agency-style case number formats."""
    return load_config(FIXTURE)


# ------------------------------------------------------------ case numbers
# (raw input, expected valid, expected normalized value or None, format name)
CASE_NUMBERS = [
    # valid in both formats
    ("99-2026-999901", True, "99-2026-999901", DASHED),
    ("SO261017-999902", True, "SO261017-999902", SO),
    # normalization
    ("so261017-999902", True, "SO261017-999902", SO),
    ("  SO261017-999902  ", True, "SO261017-999902", SO),
    ("\t99-2026-999901\n", True, "99-2026-999901", DASHED),
    ("SO261017\u2013999902", True, "SO261017-999902", SO),  # en dash
    ("SO261017\u2014999902", True, "SO261017-999902", SO),  # em dash
    ("99\u20122026\u2212999901", True, "99-2026-999901", DASHED),  # figure dash, minus
    # internal spaces are not removed
    ("SO261017 999902", False, None, None),
    ("SO261017 - 999902", False, None, None),
    ("99 -2026-999901", False, None, None),
    # wrong digit counts
    ("9-2026-999901", False, None, None),
    ("999-2026-999901", False, None, None),
    ("99-226-999901", False, None, None),
    ("99-2026-99990", False, None, None),
    ("99-2026-9999011", False, None, None),
    ("SO26101-999902", False, None, None),
    ("SO261017-99990", False, None, None),
    ("SO261017-9999021", False, None, None),
    # letters in the digit block
    ("99-2026-99990A", False, None, None),
    ("SO261017-99990O", False, None, None),
    ("SO2610I7-999902", False, None, None),
    # non-ASCII digits never pass as digits
    ("SO26101\uff17-999902", False, None, None),  # fullwidth 7
    ("99-2026-99990\u0661", False, None, None),  # Arabic-Indic 1
    # SO dates: month and day ranges
    ("SO260017-999902", False, None, None),  # month 00
    ("SO261317-999902", False, None, None),  # month 13
    ("SO261000-999902", False, None, None),  # day 00
    ("SO261032-999902", False, None, None),  # day 32
    ("SO260431-999902", False, None, None),  # April 31
    ("SO260430-999902", True, "SO260430-999902", SO),
    ("SO260230-999902", False, None, None),  # Feb 30
    ("SO240229-999902", True, "SO240229-999902", SO),  # 2024 leap year
    ("SO250229-999902", False, None, None),  # 2025 not a leap year
    ("SO000229-999902", True, "SO000229-999902", SO),  # YY 00 = 2000, a leap year
    ("SO261231-999902", True, "SO261231-999902", SO),
    ("SO260101-999902", True, "SO260101-999902", SO),
    # wrong prefix or extra characters
    ("SX261017-999902", False, None, None),
    ("SO261017-999902-1", False, None, None),
    ("99-2026-999901\n1", False, None, None),
    # dashed format checks the year is 4 digits only, not a range
    ("99-0000-999901", True, "99-0000-999901", DASHED),
]


@pytest.mark.parametrize(("raw", "valid", "value", "fmt"), CASE_NUMBERS)
def test_case_numbers(records: Config, raw: str, valid: bool, value: str | None, fmt: str | None) -> None:
    result = validate_field(records, "identifier", raw)
    assert result.valid is valid, result.message
    if valid:
        assert result.value == value
        assert result.format == fmt
        assert result.message == f"Matches {fmt}."
    elif "\n" in raw:  # a line break is refused as a hidden character first (D-123)
        assert result.message.startswith("Case number: Contains a hidden character (U+000A, line break)")
    else:
        assert result.format is None
        assert result.message.startswith("Case number must match one of:")


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ("SO261017 999902", "character 9: expected '-', found a space"),
        ("SO261017-99990O", "character 15: expected a digit, found 'O'"),
        ("SO250229-999902", "2025 isn't a leap year"),
        ("SO261317-999902", "month 13 isn't a real month"),
        ("SO261032-999902", "day 32 isn't a real day"),
        ("SO260431-999902", "April has only 30 days"),
        ("99-2026-99990", "too short"),
        ("SO261017-9999021", "too long"),
    ],
)
def test_failure_messages_point_at_the_problem(records: Config, raw: str, fragment: str) -> None:
    assert fragment in validate_field(records, "identifier", raw).message


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_required_field_rejects_empty(records: Config, raw: str | None) -> None:
    result = validate_field(records, "identifier", raw)
    assert not result.valid
    assert result.message == "Case number is required."


def test_optional_field_accepts_empty(records: Config) -> None:
    result = validate_field(records, "doc_title", "   ")
    assert result.valid and result.value == ""


def test_optional_text_without_formats_keeps_value(records: Config) -> None:
    result = validate_field(records, "doc_title", "  Provider records, part 2 ")
    assert result.valid and result.value == "Provider records, part 2"


# ------------------------------------------------------------ choice fields
@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("Search warrant return", "Search warrant return"),
        ("search warrant return", "Search warrant return"),
        ("  COURT ORDER ", "Court order"),
    ],
)
def test_choice_is_case_insensitive_and_stores_canonical(records: Config, raw: str, stored: str) -> None:
    result = validate_field(records, "doc_type", raw)
    assert result.valid and result.value == stored


def test_choice_rejects_value_not_in_taxonomy(records: Config) -> None:
    result = validate_field(records, "doc_type", "Search warrant")
    assert not result.valid
    assert result.message.startswith("Document type must be one of: Search warrant return,")


# ------------------------------------------------------------ date fields
@pytest.mark.parametrize(
    ("raw", "valid", "value"),
    [
        ("10-17-2026", True, "10-17-2026"),
        (" 02-29-2024 ", True, "02-29-2024"),
        ("02-29-2025", False, None),
        ("2026-10-17", False, None),
        ("10/17/2026", False, None),
        ("13-01-2026", False, None),
    ],
)
def test_date_field_uses_date_format(records: Config, raw: str, valid: bool, value: str | None) -> None:
    result = validate_field(records, "received", raw)
    assert result.valid is valid
    if valid:
        assert result.value == value
    else:
        assert "MM-DD-YYYY" in result.message


# ------------------------------------------------------------ generic default
@pytest.mark.parametrize("raw", ["ABC-123", "anything at all", "SO261017-999902", "x"])
def test_default_identifier_accepts_any_non_empty_value(raw: str) -> None:
    cfg = load_config(DEFAULT)
    result = validate_field(cfg, "identifier", raw)
    assert result.valid
    assert result.value == normalize(raw, ["trim", "upper", "dashes"])


def test_default_identifier_still_required() -> None:
    assert not validate_field(load_config(DEFAULT), "identifier", " ").valid


def test_default_identifier_never_prefills_from_filename() -> None:
    # "*" would match any filename, so wildcard formats are skipped.
    assert value_from_filename(load_config(DEFAULT), "identifier", "SO261017-999902_x.pdf") is None


# ------------------------------------------------------------ normalizers
def test_normalizers_match_config_schema() -> None:
    from typing import get_args

    from stamper.core.config import Normalizer

    assert set(NORMALIZERS) == set(get_args(Normalizer))


def test_normalizers_apply_in_order() -> None:
    assert normalize("  so\u2013x ", ["trim", "upper", "dashes"]) == "SO-X"
    assert normalize(" a ", []) == " a "


def test_fold_dashes_covers_lookalikes() -> None:
    lookalikes = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"
    assert fold_dashes(lookalikes) == "-" * len(lookalikes)
    assert fold_dashes("_~") == "_~"


# ------------------------------------------------------------ filename pre-fill
@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        # the two Phase 0 corpus files
        ("provider_return_99-2026-999901.pdf", "99-2026-999901"),
        ("SO261017-999902_provider_return.pdf", "SO261017-999902"),
        # full paths, lowercase, dash look-alikes
        (r"D:\Returns\in\so261017-999902 return.pdf", "SO261017-999902"),
        ("return SO261017\u2013999902.pdf", "SO261017-999902"),
        # the same number twice is one distinct value
        ("SO261017-999902_copy_of_SO261017-999902.pdf", "SO261017-999902"),
        # two different numbers: don't guess
        ("99-2026-999901_and_SO261017-999902.pdf", None),
        ("SO261017-999902_SO261018-999903.pdf", None),
        # embedded in a longer digit run: no match
        ("1299-2026-9999015.pdf", None),
        ("scan_0099-2026-999901.pdf", None),
        ("99-2026-9999012.pdf", None),
        # glued to letters: no match
        ("caseSO261017-999902.pdf", None),
        # invalid SO date
        ("SO261332-999902_return.pdf", None),
        ("SO250229-999902.pdf", None),
        # invalid date next to a valid number still yields the valid one
        ("SO250229-999902_99-2026-999901.pdf", "99-2026-999901"),
        # nothing there
        ("provider_return.pdf", None),
        ("", None),
    ],
)
def test_value_from_filename(records: Config, filename: str, expected: str | None) -> None:
    assert value_from_filename(records, "identifier", filename) == expected


def test_prefill_only_for_fields_that_opt_in(records: Config) -> None:
    assert value_from_filename(records, "doc_title", "SO261017-999902.pdf") is None


# ------------------------------------------------------------ encoded dates (O-003, D-069)
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("SO261017-999902", date(2026, 10, 17)),
        ("SO621017-999902", date(2062, 10, 17)),  # the future-date typo case
        ("SO000229-999902", date(2000, 2, 29)),
        ("99-2026-999901", None),  # YYYY alone isn't a full date
    ],
)
def test_validation_reports_encoded_date(records: Config, value: str, expected: date | None) -> None:
    r = validate_field(records, "identifier", value)
    assert r.valid
    assert r.date == expected


def test_invalid_value_has_no_date(records: Config) -> None:
    r = validate_field(records, "identifier", "SO261332-999902")
    assert not r.valid and r.date is None


def test_date_field_reports_its_date(records: Config) -> None:
    r = validate_field(records, "received", "10-17-2026")
    assert r.valid and r.date == date(2026, 10, 17)


def test_date_of_needs_year_month_and_day() -> None:
    from stamper.core.patterns import compile_pattern

    assert compile_pattern("MMDD-##").date_of("1017-12") is None
    assert compile_pattern("YYYYMMDD").date_of("20261017") == date(2026, 10, 17)
    assert compile_pattern("YYYYMMDD").date_of("20260230") is None
    assert compile_pattern("YYYYMMDD").date_of("2026101") is None


def test_year_zero_is_refused_not_a_crash() -> None:
    from stamper.core.patterns import compile_pattern

    pattern = compile_pattern("SOYYYYMMDD-##")
    assert not pattern.test("SO00000101-01").ok
    assert pattern.date_of("SO00000101-01") is None  # date(0, 1, 1) would raise
