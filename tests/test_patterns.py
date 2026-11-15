"""The simple pattern syntax and date formats."""

from __future__ import annotations

from datetime import date

import pytest

from stamper.core.patterns import PatternError, compile_pattern, explain, parse_date_format


@pytest.mark.parametrize(
    ("pattern", "value", "ok"),
    [
        ("###", "123", True),
        ("###", "12a", False),
        ("@@-##", "ab-12", True),
        ("@@-##", "AB-12", True),
        ("@@-##", "A1-12", False),
        ("??", "a1", True),
        ("??", "a-", False),
        ("*", "x", True),
        ("*", "anything at all", True),
        ("*", "", False),
        ("CASE-*", "CASE-7", True),
        ("CASE-*", "CASE-", False),
        ("SO", "SO", True),
        ("SO", "so", False),  # literals compare exactly; normalizers handle case
        (r"\#\@##", "#@12", True),
        (r"\D\D-##", "DD-12", True),
        ("D-##", "D-12", True),  # single D is literal
        ("M####", "M1234", True),  # single M is literal
        ("Y-##", "Y-12", True),
        ("yy##", "yy12", True),  # lowercase is literal
        ("A.B", "A.B", True),
        ("A.B", "AxB", False),  # regex characters are literal
        ("(##)", "(12)", True),
        ("YYYY", "2026", True),
        ("YYYY", "0000", True),  # a year alone is just 4 digits
        ("MM", "12", True),
        ("MM", "13", False),
        ("DD", "31", True),
        ("DD", "32", False),
        ("MMDD", "0229", True),  # no year: Feb 29 allowed
        ("MMDD", "0230", False),
        ("YYYYMMDD", "20240229", True),
        ("YYYYMMDD", "21000229", False),  # 2100 isn't a leap year
        ("YYYYMMDD", "20000229", True),  # 2000 is
        ("YYMMDD", "000229", True),  # YY 00 = 2000
        ("##", "１２", False),  # fullwidth digits
        ("##", "12\n", False),
    ],
)
def test_matches(pattern: str, value: str, ok: bool) -> None:
    assert compile_pattern(pattern).matches(value) is ok


@pytest.mark.parametrize(
    ("pattern", "position", "fragment"),
    [
        ("", 0, "empty"),
        ("YYY", 1, '"YYY" isn\'t a date part'),
        ("SO-YYYYY", 4, "YY or YYYY"),
        ("MMM-##", 1, "use MM"),
        ("DDD", 1, "use DD"),
        ("##\\", 3, "backslash"),
        ("MM-MM", 4, "month appears twice"),
        ("YY-YYYY", 4, "year appears twice"),
        ("DD/DD", 4, "day appears twice"),
    ],
)
def test_pattern_errors(pattern: str, position: int, fragment: str) -> None:
    with pytest.raises(PatternError) as exc:
        compile_pattern(pattern)
    assert exc.value.position == position
    assert fragment in str(exc.value)


@pytest.mark.parametrize(
    ("pattern", "text"),
    [
        (
            "SOYYMMDD-######",
            '"SO", then 2-digit year, then month (01-12), then day (01-31), then "-", '
            "then 6 digits; month and day must be a real date.",
        ),
        ("##-YYYY-######", '2 digits, then "-", then 4-digit year, then "-", then 6 digits.'),
        ("*", "Any text."),
        ("@?#", "1 letter, then 1 letter or digit, then 1 digit."),
        (r"\#@@", '"#", then 2 letters.'),
    ],
)
def test_explain(pattern: str, text: str) -> None:
    assert explain(pattern) == text


@pytest.mark.parametrize(
    ("pattern", "value", "ok", "position", "fragment"),
    [
        ("SOYYMMDD-######", "SO261017-999902", True, 0, "Matches SOYYMMDD-######"),
        ("SOYYMMDD-######", "SX261017-999902", False, 2, "expected 'O', found 'X'"),
        ("SOYYMMDD-######", "SO2610", False, 7, "Too short"),
        ("SOYYMMDD-######", "SO261017-9999021", False, 16, "Too long"),
        ("SOYYMMDD-######", "SO261017–999902", False, 9, "an en dash"),
        ("SOYYMMDD-######", "SO261317-999902", False, 0, "month 13"),
        ("##", "1 ", False, 2, "found a space"),
    ],
)
def test_test_value_reports_position(
    pattern: str, value: str, ok: bool, position: int, fragment: str
) -> None:
    result = compile_pattern(pattern).test(value)
    assert result.ok is ok
    assert result.position == position
    assert fragment in result.message


def test_matched_prefix_length_ranks_formats() -> None:
    # The format a value gets furthest into is the one to hint about.
    value = "SO261017-99990X"
    assert compile_pattern("SOYYMMDD-######").test(value).matched > compile_pattern("##-YYYY-######").test(value).matched


# ------------------------------------------------------------ date formats
@pytest.mark.parametrize(
    ("fmt", "d", "text"),
    [
        ("MM-DD-YYYY", date(2026, 10, 17), "10-17-2026"),
        ("YYYY-MM-DD", date(2026, 1, 5), "2026-01-05"),
        ("MM/DD/YY", date(2026, 1, 5), "01/05/26"),
        ("DD.MM.YYYY", date(2026, 1, 5), "05.01.2026"),
        (r"\Day DD MM YYYY", date(2026, 1, 5), "Day 05 01 2026"),
    ],
)
def test_date_format_round_trip(fmt: str, d: date, text: str) -> None:
    df = parse_date_format(fmt)
    assert df.format(d) == text
    assert df.parse(text) == d


@pytest.mark.parametrize("text", ["02-29-2025", "13-01-2026", "10-17-26", "", "10-17-2026 "])
def test_date_format_rejects(text: str) -> None:
    assert parse_date_format("MM-DD-YYYY").parse(text) is None


# UX-016: separators and one-digit parts as people type them; the order and year come from the format.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10/17/2026", date(2026, 10, 17)),
        ("10.17.2026", date(2026, 10, 17)),
        ("1-7-2026", date(2026, 1, 7)),
        ("1/7/2026", date(2026, 1, 7)),
        ("01.07.2026", date(2026, 1, 7)),
        ("10-7/2026", date(2026, 10, 7)),
    ],
)
def test_date_format_accepts_typed_dates(text: str, expected: date) -> None:
    df = parse_date_format("MM-DD-YYYY")
    assert df.parse(text) == expected
    assert df.format(expected) == expected.strftime("%m-%d-%Y")  # what is stored and stamped


@pytest.mark.parametrize(
    "text",
    [
        "13/01/2026",  # no day-first retry for a US format
        "02/30/2026",  # not a real date
        "10-17-26",  # two-digit year where the format says YYYY
        "2026-10-17",  # ISO isn't accepted for a US format
        "\uff11\uff10-\uff11\uff17-\uff12\uff10\uff12\uff16",  # fullwidth digits (D-033)
        "\u0661\u0660-17-2026",  # Arabic-Indic digits
        "10 17 2026",  # a space isn't one of the interchangeable separators
        "010-17-2026",
    ],
)
def test_date_format_still_refuses(text: str) -> None:
    assert parse_date_format("MM-DD-YYYY").parse(text) is None


def test_typed_dates_follow_the_configured_order() -> None:
    day_first = parse_date_format("DD.MM.YYYY")
    assert day_first.parse("7/1/2026") == date(2026, 1, 7)
    assert day_first.parse("10/17/2026") is None  # month 17: never retried month-first
    assert parse_date_format("MM/DD/YY").parse("1-7-26") == date(2026, 1, 7)
    assert parse_date_format("MM/DD/YY").parse("1-7-2026") is None


def test_one_digit_parts_need_a_separator() -> None:
    # Without separators a short part would make the split a guess.
    packed = parse_date_format("MMDDYYYY")
    assert packed.parse("10172026") == date(2026, 10, 17)
    assert packed.parse("1172026") is None


def test_identifier_patterns_stay_strict() -> None:
    # Date parts inside an identifier pattern don't get looser (UX-016 covers date fields only).
    so = compile_pattern("SOYYMMDD-######")
    assert not so.matches("SO261317-999902")
    assert not so.matches("SO26117-999902")
    assert not so.matches("SO26/10/17-999902")
    assert so.matches("SO261017-999902")
    dashed = compile_pattern("MM-DD-YYYY")
    assert not dashed.matches("1-7-2026") and not dashed.matches("10/17/2026")


@pytest.mark.parametrize(
    ("fmt", "fragment"),
    [
        ("MM-DD", "a year"),
        ("YYYY-MM", "a day"),
        ("YYYY", "a month (MM), a day (DD)"),
        ("MM-DD-YYYY-##", "can't be used in a date format"),
        ("MM-DD-*", "can't be used"),
    ],
)
def test_date_format_errors(fmt: str, fragment: str) -> None:
    with pytest.raises(PatternError, match=None) as exc:
        parse_date_format(fmt)
    assert fragment in str(exc.value)


# ------------------------------------------------------------ example values (D-084)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("##-YYYY-######", "12-2026-345678"),
        ("SOYYMMDD-######", "SO261018-123456"),
        ("@@?-#", "ABC-1"),
        ("*", "Example"),
        (r"\#\D-#", "#D-1"),
    ],
)
def test_example_values_fit(source: str, expected: str) -> None:
    from datetime import date

    value = compile_pattern(source).example(date(2026, 10, 18))
    assert value == expected
    assert compile_pattern(source).matches(value)
