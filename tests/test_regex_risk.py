"""Rule checking can't freeze the app (D-118): risky regexes refused before they run, values capped."""

from __future__ import annotations

import re
import re._constants as sre
import re._parser as sre_parse
import time
from typing import Any

import pytest
from pytestqt.qtbot import QtBot

from stamper.core.config import load_config, parse_config
from stamper.core.fields import MAX_RAW_LENGTH, MAX_VALUE_LENGTH, validate_field
from stamper.core.locations import Locations
from stamper.core.patterns import MAX_STARS, PatternError, compile_pattern, regex_risk
from stamper.gui.main_window import MainWindow
from tests.gui_support import fake, open_dialog  # noqa: F401 - fixtures
from tests.test_config import minimal, problems
from tests.test_gui_settings import _settings, _type, locs, records_window  # noqa: F401 - fixtures

RISKY = {
    r"(a+)+$": "contains another open-ended repeat",
    r"(a*)*": "contains another open-ended repeat",
    r"^(\w+\s?)+$": "contains another open-ended repeat",
    r"(.*a){20}": "contains another open-ended repeat",
    r"^(?:[A-Z]+-)+\d+$": "contains another open-ended repeat",
    r"(a|aa)+": "options that can match the same text",
    r"(?:\d|\d-)+x": "options that can match the same text",
    r"\d*\d*x": "two open-ended repeats",
    r".*.*=": "two open-ended repeats",
    r"\d+\d+x": "two open-ended repeats",
    r"(x)\1": "refers back",
    r"(x)?(?(1)a|b)": "refers back",
}
SAFE = [
    r"^[A-Z]{2}-\d{6}$",
    r"^(?:\d{2}-){2}\d{4}$",
    r"^SO\d{6}-\d{6}$",
    r"\d\d",
    r"CASE-\d{4}",
    r"^[A-Z]+\d+$",
    r"^(?:SO|99)-\d+$",
    r"(?>a+)+",
    r"a++b",
    r"(?=\d)\d{4}",
    r"(?i)^so\d{6}$",
    r"[^-]+-[^-]+",
]


def _with_regex(regex: str) -> dict[str, Any]:
    data = minimal()
    data["fields"]["ident"]["formats"] = [{"name": "Rule", "regex": regex}]
    return data


@pytest.mark.parametrize(("regex", "reason"), list(RISKY.items()))
def test_risky_regex_refused_at_load(regex: str, reason: str) -> None:
    found = problems(_with_regex(regex))
    assert len(found) == 1, found
    assert found[0].startswith("fields.ident.formats[0]: ")
    assert "can take a very long time to check" in found[0] and reason in found[0]


@pytest.mark.parametrize("regex", SAFE)
def test_ordinary_regexes_load(regex: str) -> None:
    assert regex_risk(regex) is None
    parse_config(_with_regex(regex))


def test_the_check_itself_is_fast() -> None:
    # It never runs the regex, so the classic freezers cost nothing to check.
    start = time.perf_counter()
    for regex in RISKY:
        regex_risk(regex)
    assert time.perf_counter() - start < 0.5


def test_regex_that_doesnt_compile_still_says_so() -> None:
    assert "doesn't compile" in problems(_with_regex("([0-9]"))[0]


def test_parse_tree_shape_is_what_the_check_expects() -> None:
    """Fails loudly if a Python upgrade changes ``re._parser``'s tree (D-118)."""
    tree = list(sre_parse.parse(r"(a+)+$", re.ASCII).data)
    op, (lo, hi, body) = tree[0]
    assert op is sre.MAX_REPEAT and lo == 1 and hi is sre.MAXREPEAT
    sub_op, (_group, _add, _del, inner) = list(body)[0]
    assert sub_op is sre.SUBPATTERN
    assert list(inner)[0][0] is sre.MAX_REPEAT
    assert tree[1] == (sre.AT, sre.AT_END)
    in_op, items = list(sre_parse.parse(r"[^\d]", re.ASCII).data)[0]
    assert in_op is sre.IN and items[0][0] is sre.NEGATE and items[1] == (sre.CATEGORY, sre.CATEGORY_DIGIT)


def test_unrecognised_construct_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    import stamper.core.patterns as patterns

    class Fake:
        data = [(object(), None)]

        class state:  # noqa: N801 - mimics re._parser
            flags = 0

    monkeypatch.setattr(patterns._sre_parse, "parse", lambda *_a: Fake)
    assert regex_risk("x") == "it uses a construct this check can't assess"


# ------------------------------------------------------------ simple patterns


def test_more_than_two_stars_refused() -> None:
    compile_pattern("*-*")
    with pytest.raises(PatternError, match=f"at most {MAX_STARS} \\*") as e:
        compile_pattern("*-*-*")
    assert e.value.position == 5


def test_two_stars_stay_quick_at_the_length_cap() -> None:
    cp = compile_pattern("*-*")
    worst = 0.0
    for value in ["-" * 127 + "\n", "a-" * 64, "-" * 64 + "x" * 64]:
        value = value[:MAX_VALUE_LENGTH]
        t = time.perf_counter()
        cp.test(value)
        cp.matches(value)
        worst = max(worst, time.perf_counter() - t)
    assert worst < 0.05


# ------------------------------------------------------------ value length


def _title_config() -> Any:
    return parse_config(minimal())


def test_value_at_the_cap_is_accepted() -> None:
    assert validate_field(_title_config(), "title", "x" * MAX_VALUE_LENGTH).valid


def test_value_over_the_cap_is_refused() -> None:
    r = validate_field(_title_config(), "title", "x" * (MAX_VALUE_LENGTH + 1))
    assert not r.valid
    assert r.message == f"Title: Too long: at most {MAX_VALUE_LENGTH} characters (this is {MAX_VALUE_LENGTH + 1})."


def test_huge_input_refused_before_normalizers(monkeypatch: pytest.MonkeyPatch) -> None:
    import stamper.core.fields as fields

    def boom(*_a: object) -> str:
        raise AssertionError("normalizers ran")

    monkeypatch.setattr(fields, "normalize", boom)
    r = validate_field(_title_config(), "title", " " * (MAX_RAW_LENGTH + 1))
    assert not r.valid and "Too long" in r.message


def test_trimmed_value_counts_after_normalizing() -> None:
    assert validate_field(_title_config(), "title", "  " + "x" * MAX_VALUE_LENGTH + "  ").valid


def test_choice_values_are_not_capped() -> None:
    data = minimal()
    data["taxonomies"]["kinds"].append("K" * 200)
    assert validate_field(parse_config(data), "kind", "K" * 200).valid


# ------------------------------------------------------------ the window


def test_stamp_dialog_fields_are_capped(qtbot: QtBot, records_window: MainWindow, corpus_dir: Any) -> None:
    from PySide6.QtWidgets import QLineEdit

    d = open_dialog(qtbot, records_window, corpus_dir / "letter_portrait.pdf")
    ident = next(e for e in d.findChildren(QLineEdit) if e.objectName() == "identifier")
    assert ident.maxLength() == MAX_VALUE_LENGTH
    ident.setText("9" * 300)
    assert len(ident.text()) == MAX_VALUE_LENGTH


def test_rule_editor_refuses_a_risky_regex(qtbot: QtBot, records_window: MainWindow, locs: Locations) -> None:
    s = _settings(records_window)
    s.show_section(1)
    page = s.fields_page
    page.select_key("identifier")
    page._add_format()
    ed = page.format_editors[-1]
    ed.regex_box.setChecked(True)
    _type(qtbot, ed.rule, r"(\w+\s?)+")
    assert "can take a very long time to check" in ed.explanation.text()
    before = locs.app_config.read_bytes()
    s.save()
    assert locs.app_config.read_bytes() == before  # not saved while a rule is refused
    assert all(f.regex != r"(\w+\s?)+" for f in load_config(locs.app_config).fields["identifier"].formats)
