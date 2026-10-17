"""Stamp text templates and the separator tidy rule."""

from __future__ import annotations

from datetime import date

import pytest

from stamper.core.patterns import parse_date_format
from stamper.core.template import (
    BUILTIN_TOKENS,
    Literal,
    TemplateError,
    Token,
    builtin_values,
    parse_template,
    render,
    template_tokens,
)

STAMP = "{identifier}  |  {doc_type}  |  Page {page} of {total}"
ID = "SO261017-999902"


def values(**overrides: str) -> dict[str, str]:
    base = builtin_values(
        page=1, total=3, original_name="provider_return",
        today=date(2026, 10, 17), date_format=parse_date_format("MM-DD-YYYY"),
    )
    base.update(identifier=ID, doc_type="Search warrant return", doc_title="")
    base.update(overrides)
    return base


def test_builtin_tokens_are_the_four_documented() -> None:
    assert BUILTIN_TOKENS == ("page", "total", "original_name", "date")


def test_every_builtin_token() -> None:
    out = render("{page}/{total} {original_name} {date}", values())
    assert out == "1/3 provider_return 10-17-2026"


def test_date_follows_date_format_setting() -> None:
    v = builtin_values(page=1, total=1, original_name="x", today=date(2026, 1, 5),
                       date_format=parse_date_format("YYYY-MM-DD"))
    assert v["date"] == "2026-01-05"


def test_full_stamp() -> None:
    assert render(STAMP, values()) == f"{ID}  |  Search warrant return  |  Page 1 of 3"


def test_literal_braces() -> None:
    assert render("{{{identifier}}} {{x}}", values()) == f"{{{ID}}} {{x}}"


@pytest.mark.parametrize(
    ("template", "empty", "expected"),
    [
        (STAMP, ["doc_type"], f"{ID}  |  Page 1 of 3"),
        (STAMP, ["identifier"], "Search warrant return  |  Page 1 of 3"),
        (STAMP, ["identifier", "doc_type"], "Page 1 of 3"),
        ("{identifier}  |  {doc_title}", ["doc_title"], ID),
        ("{identifier} | {doc_title} | {doc_type} | Page {page}", ["doc_title", "doc_type"], f"{ID} | Page 1"),
        ("{doc_title}", ["doc_title"], ""),
        ("  |  {doc_title}  |  Page {page}", ["doc_title"], "Page 1"),
        # labels with letters stay; only spaces and punctuation are removed
        ("Title: {doc_title}  |  Page {page}", ["doc_title"], "Title  |  Page 1"),
        # escaped braces are never eaten
        ("{{ {doc_title} }}", ["doc_title"], "{ }"),
        # punctuation inside a neighbouring value is never eaten
        ("{doc_type} | {doc_title}", ["doc_title"], "Report (part 1)."),
    ],
)
def test_empty_values_tidy_separators(template: str, empty: list[str], expected: str) -> None:
    v = values(doc_type="Report (part 1).") if template == "{doc_type} | {doc_title}" else values()
    for name in empty:
        v[name] = ""
    assert render(template, v) == expected


def test_whitespace_only_value_counts_as_empty() -> None:
    assert render(STAMP, values(doc_type="   ")) == f"{ID}  |  Page 1 of 3"


def test_never_leaves_double_separators() -> None:
    v = values(doc_type="")
    assert "|    |" not in render(STAMP, v)


def test_missing_value_is_a_programming_error() -> None:
    with pytest.raises(KeyError):
        render("{nope}", values())


def test_tokens_in_order_without_duplicates() -> None:
    assert template_tokens("{b} {a} {b} {{c}}") == ["b", "a"]


def test_parse_segments() -> None:
    assert parse_template("A{x}{{") == [Literal("A"), Token("x"), Literal("{", escaped=True)]


@pytest.mark.parametrize("bad", ["{x", "x}", "{x y}", "{}", "{1x}", "{{x}"])
def test_parse_errors(bad: str) -> None:
    with pytest.raises(TemplateError):
        parse_template(bad)


def test_unknown_token_rejected_at_config_load() -> None:
    from stamper.core.config import ConfigError, parse_config

    data = {
        "schema_version": 1,
        "fields": {"identifier": {"label": "Identifier"}},
        "profiles": [{"name": "P", "default": True, "fields": ["identifier"],
                      "stamps": [{"text": "{identifier} {pgae}"}]}],
    }
    with pytest.raises(ConfigError, match=r"uses \{pgae\}"):
        parse_config(data)
