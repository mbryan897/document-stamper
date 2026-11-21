"""Loading config.json and its cross-checks."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from stamper.core.config import Config, ConfigError, load_config, parse_config

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "config" / "default.json"
FIXTURE = Path(__file__).parent / "fixtures" / "records_sample.json"


def minimal() -> dict[str, Any]:
    """Smallest valid config, for tests that break one thing at a time."""
    return {
        "schema_version": 1,
        "taxonomies": {"kinds": ["Alpha", "Beta"]},
        "fields": {
            "ident": {"label": "Identifier", "type": "text", "required": True,
                      "formats": [{"pattern": "##-####"}]},
            "kind": {"label": "Kind", "type": "choice", "taxonomy": "kinds"},
            "title": {"label": "Title"},
        },
        "profiles": [
            {
                "name": "Main",
                "default": True,
                "fields": ["ident", "kind", "title"],
                "stamps": [{"text": "{ident} | {kind} | Page {page} of {total}"}],
                "output_name": {"fields": ["ident"]},
                "metadata": {"Ident": "{ident}"},
            }
        ],
    }


def problems(data: dict[str, Any]) -> list[str]:
    with pytest.raises(ConfigError) as exc:
        parse_config(data)
    return exc.value.problems


def only_problem(data: dict[str, Any]) -> str:
    found = problems(data)
    assert len(found) == 1, found
    return found[0]


# ------------------------------------------------------------ files that load
def test_default_json_loads() -> None:
    cfg = load_config(DEFAULT)
    prof = cfg.default_profile
    assert prof.name == "Standard"
    assert cfg.fields["identifier"].label == "Identifier"
    assert cfg.fields["identifier"].formats[0].pattern == "*"
    assert cfg.settings.date_format == "MM-DD-YYYY"
    assert prof.output_name.fields[0] == "identifier"
    # 1.1.0: the shipped profile stamps the identifier and page number only; the
    # document type field and its list stay defined, for a profile to pick up.
    assert prof.fields == ["identifier", "doc_title"]
    assert [s.text for s in prof.stamps] == ["{identifier}  |  Page {page} of {total}"]
    assert prof.output_name.fields == ["identifier"]
    assert "doc_type" in cfg.fields and not cfg.fields["doc_type"].required


def test_default_json_has_no_agency_formats() -> None:
    text = DEFAULT.read_text(encoding="utf-8")
    assert "SOYYMMDD" not in text and "Case number" not in text


def test_fixture_loads() -> None:
    cfg = load_config(FIXTURE)
    assert [f.display_name for f in cfg.fields["identifier"].formats] == ["##-YYYY-######", "SOYYMMDD-######"]


def test_minimal_config_fills_defaults() -> None:
    cfg = parse_config(minimal())
    stamp = cfg.default_profile.stamps[0]
    assert (stamp.anchor, stamp.direction, stamp.margin_in, stamp.font_size_pt) == ("top-right", "along-top", 0.25, 9)
    assert stamp.background_box is True
    assert cfg.fields["title"].normalize == ["trim"]
    assert cfg.settings.date_format == "MM-DD-YYYY"


@pytest.mark.parametrize("path", [DEFAULT, FIXTURE])
def test_round_trip(path: Path, tmp_path: Path) -> None:
    cfg = load_config(path)
    out = tmp_path / "config.json"
    out.write_text(cfg.to_json(), encoding="utf-8")
    again = load_config(out)
    assert again == cfg
    assert again.to_json() == cfg.to_json()


def test_byte_order_mark_is_accepted(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps(minimal()).encode("utf-8"))
    assert load_config(p).default_profile.name == "Main"


def test_models_are_frozen() -> None:
    cfg = parse_config(minimal())
    with pytest.raises(ValidationError):
        cfg.default_profile.name = "Other"  # type: ignore[misc]


def test_profile_lookup_is_case_insensitive() -> None:
    cfg = parse_config(minimal())
    assert cfg.profile("main").name == "Main"
    with pytest.raises(KeyError):
        cfg.profile("nope")


# ------------------------------------------------------------ file-level errors
def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="can't find the config file"):
        load_config(tmp_path / "nope.json")


def test_bad_json_names_line_and_column(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text('{\n  "schema_version": 1,\n  "fields": {,}\n}', encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert exc.value.problems[0].startswith("line 3, column 14:")
    assert str(exc.value).startswith("config.json has 1 problem(s):")


def test_top_level_must_be_object(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ConfigError, match="one JSON object"):
        load_config(p)


@pytest.mark.parametrize("version", [0, -1])
def test_unknown_schema_version(version: int) -> None:
    data = minimal()
    data["schema_version"] = version
    assert only_problem(data) == f"schema_version: this app reads schema_version 1, but the file says {version}"


@pytest.mark.parametrize(
    ("mutate", "where"),
    [
        (lambda d: d.update(colour="x"), "colour: unknown key"),
        (lambda d: d["fields"]["ident"].update(lable="x"), "fields.ident.lable: unknown key"),
        (lambda d: d["profiles"][0]["stamps"][0].update(margn_in=1), 'profiles[0] "Main": stamps[0].margn_in: unknown key'),
        (lambda d: d["profiles"][0]["output_name"].update(seperator="-"), 'profiles[0] "Main": output_name.seperator: unknown key'),
        (lambda d: d.update(settings={"date_fmt": "MM"}), "settings.date_fmt: unknown key"),
        (lambda d: d["fields"]["ident"]["formats"][0].update(date_groups="YYMMDD"), "fields.ident.formats[0].date_groups: unknown key"),
    ],
)
def test_unknown_keys_are_rejected(mutate: Any, where: str) -> None:
    data = minimal()
    mutate(data)
    assert only_problem(data).startswith(where)


def test_missing_required_key() -> None:
    data = minimal()
    del data["profiles"][0]["fields"]
    assert only_problem(data) == 'profiles[0] "Main": fields: required, but missing'


def test_all_structural_problems_reported_together() -> None:
    data = minimal()
    data["profiles"][0]["stamps"][0].update(anchor="top", margin_in=0.1, color="blue")
    found = problems(data)
    assert len(found) == 3
    assert all(p.startswith('profiles[0] "Main": stamps[0].') for p in found)


# ------------------------------------------------------------ cross-checks
def test_stamp_token_not_a_field() -> None:
    data = minimal()
    data["profiles"][0]["stamps"][0]["text"] = "{case_num} | Page {page}"
    assert only_problem(data) == (
        'profiles[0] "Main": stamps[0] text uses {case_num}, which isn\'t one of this '
        "profile's fields. Available: ident, kind, title, page, total, original_name, date"
    )


def test_stamp_token_defined_but_not_in_profile() -> None:
    data = minimal()
    data["profiles"][0]["fields"] = ["ident", "kind"]
    parse_config(data)  # title is defined but unused, which is fine
    data["profiles"][0]["stamps"][0]["text"] = "{ident} {title}"
    assert "stamps[0] text uses {title}" in only_problem(data)


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("{ident", "never closed"),
        ("ident}", "has no matching {"),
        ("{ident name}", "isn't a valid token"),
        ("{}", "isn't a valid token"),
    ],
)
def test_bad_template_syntax(text: str, fragment: str) -> None:
    data = minimal()
    data["profiles"][0]["stamps"][0]["text"] = text
    msg = only_problem(data)
    assert msg.startswith('profiles[0] "Main": stamps[0] text:')
    assert fragment in msg


def test_profile_field_not_defined() -> None:
    data = minimal()
    data["profiles"][0]["fields"].append("case_num")
    assert only_problem(data) == (
        'profiles[0] "Main": fields lists case_num, which is not a field. Known fields: ident, kind, title'
    )


def test_profile_field_listed_twice() -> None:
    data = minimal()
    data["profiles"][0]["fields"].append("kind")
    assert only_problem(data) == 'profiles[0] "Main": fields lists kind twice'


def test_output_name_field_not_available() -> None:
    data = minimal()
    data["profiles"][0]["output_name"]["fields"] = ["ident", "page"]
    assert "output_name.fields lists page" in only_problem(data)


def test_output_name_original_name_hint() -> None:
    data = minimal()
    data["profiles"][0]["output_name"]["fields"] = ["original_name"]
    assert "include_original_name" in only_problem(data)


def test_output_name_may_use_date() -> None:
    data = minimal()
    data["profiles"][0]["output_name"]["fields"] = ["ident", "date"]
    parse_config(data)


@pytest.mark.parametrize("sep", ["", "/", ":", "|", "\t"])
def test_bad_separator(sep: str) -> None:
    data = minimal()
    data["profiles"][0]["output_name"]["separator"] = sep
    assert "output_name.separator" in only_problem(data)


def test_metadata_rejects_page_tokens() -> None:
    data = minimal()
    data["profiles"][0]["metadata"] = {"Pages": "{total}"}
    assert "changes from page to page" in only_problem(data)


def test_metadata_bad_key() -> None:
    data = minimal()
    data["profiles"][0]["metadata"] = {"Case Number": "{ident}"}
    assert 'metadata key "Case Number"' in only_problem(data)


def test_choice_field_unknown_taxonomy() -> None:
    data = minimal()
    data["fields"]["kind"]["taxonomy"] = "kindz"
    assert only_problem(data) == 'fields.kind: taxonomy "kindz" doesn\'t exist. Known taxonomies: kinds'


def test_choice_field_without_taxonomy() -> None:
    data = minimal()
    del data["fields"]["kind"]["taxonomy"]
    assert only_problem(data) == 'fields.kind: a "choice" field needs a "taxonomy"'


def test_formats_only_on_text_fields() -> None:
    data = minimal()
    data["fields"]["kind"]["formats"] = [{"pattern": "##"}]
    assert "only \"text\" fields take \"formats\"" in only_problem(data)


def test_empty_taxonomy() -> None:
    data = minimal()
    data["taxonomies"]["spare"] = []
    assert only_problem(data) == "taxonomies.spare: the list is empty; add at least one choice"


def test_duplicate_taxonomy_entry_case_insensitive() -> None:
    data = minimal()
    data["taxonomies"]["kinds"] = ["Alpha", "Beta", "alpha "]
    assert only_problem(data) == 'taxonomies.kinds: "alpha " is listed twice (matches "Alpha")'


@pytest.mark.parametrize(
    ("fmt", "fragment"),
    [
        ({"pattern": "YYY-##"}, 'pattern "YYY-##": character 1'),
        ({"pattern": "MM-MM"}, "month appears twice"),
        ({"pattern": ""}, "pattern is empty"),
        ({"regex": "([0-9]"}, "doesn't compile"),
        ({"pattern": "##", "regex": "[0-9]{2}"}, "not both"),
        ({"name": "Nothing"}, "not neither"),
    ],
)
def test_bad_format(fmt: dict[str, str], fragment: str) -> None:
    data = minimal()
    data["fields"]["ident"]["formats"] = [fmt]
    msg = only_problem(data)
    assert msg.startswith("fields.ident.formats[0]: ")
    assert fragment in msg


def test_regex_format_is_ascii_and_whole_value() -> None:
    data = minimal()
    data["fields"]["ident"]["formats"] = [{"name": "Two digits", "regex": r"\d\d"}]
    cfg = parse_config(data)
    from stamper.core.fields import validate_field

    assert validate_field(cfg, "ident", "12").format == "Two digits"
    assert not validate_field(cfg, "ident", "１２").valid
    assert not validate_field(cfg, "ident", "123").valid


@pytest.mark.parametrize("name", ["page", "date", "total", "original_name"])
def test_field_name_cannot_be_builtin(name: str) -> None:
    data = minimal()
    data["fields"][name] = {"label": "X"}
    assert f'"{name}" is a built-in token name' in only_problem(data)


def test_field_name_must_be_token_safe() -> None:
    data = minimal()
    data["fields"]["case number"] = {"label": "X"}
    assert "can't be used as a field name" in only_problem(data)


@pytest.mark.parametrize(
    ("key", "value", "fragment"),
    [
        ("anchor", "top", "must be one of 'top-left'"),
        ("direction", "along-left", "must be one of 'along-top' or 'along-right'"),
        ("margin_in", 0.2, "at least 0.25 inches"),
        ("font_size_pt", 5, "between 6 and 24"),
        ("font_size_pt", 30, "between 6 and 24"),
        ("color", "#2B3A8", "#RRGGBB"),
        ("color", "2B3A8F", "#RRGGBB"),
        ("color", "#GGGGGG", "#RRGGBB"),
        ("pages", "odd", "must be one of 'all'"),
    ],
)
def test_stamp_values(key: str, value: Any, fragment: str) -> None:
    data = minimal()
    data["profiles"][0]["stamps"][0][key] = value
    msg = only_problem(data)
    assert msg.startswith(f'profiles[0] "Main": stamps[0].{key}: ')
    assert fragment in msg


@pytest.mark.parametrize("anchor", [
    "top-left", "top-center", "top-right", "middle-left", "middle-center",
    "middle-right", "bottom-left", "bottom-center", "bottom-right",
])
def test_all_nine_anchors_accepted(anchor: str) -> None:
    data = minimal()
    data["profiles"][0]["stamps"][0]["anchor"] = anchor
    parse_config(data)


def test_lowercase_color_accepted() -> None:
    data = minimal()
    data["profiles"][0]["stamps"][0]["color"] = "#2b3a8f"
    parse_config(data)


def test_profile_needs_a_stamp() -> None:
    data = minimal()
    data["profiles"][0]["stamps"] = []
    assert only_problem(data) == 'profiles[0] "Main": stamps: needs at least one entry'


def _two_profiles() -> dict[str, Any]:
    data = minimal()
    second = copy.deepcopy(data["profiles"][0])
    second["name"] = "Second"
    second["default"] = False
    data["profiles"].append(second)
    return data


def test_no_default_profile() -> None:
    data = _two_profiles()
    data["profiles"][0]["default"] = False
    assert only_problem(data) == 'profiles: no profile has "default": true; mark exactly one'


def test_two_default_profiles() -> None:
    data = _two_profiles()
    data["profiles"][1]["default"] = True
    assert only_problem(data) == 'profiles: 2 profiles have "default": true ("Main", "Second"); mark exactly one'


def test_duplicate_profile_names() -> None:
    data = _two_profiles()
    data["profiles"][1]["name"] = "main"
    assert only_problem(data) == 'profiles[1] "main": the name is already used by profiles[0]'


def test_bad_date_format_setting() -> None:
    data = minimal()
    data["settings"] = {"date_format": "MM-DD"}
    assert only_problem(data) == 'settings.date_format: "MM-DD" isn\'t a usable date format: a date format needs a year (YYYY or YY)'


def test_cross_check_messages_name_location() -> None:
    data = minimal()
    data["profiles"][0]["fields"] = ["ident", "nope"]
    data["profiles"][0]["stamps"][0]["text"] = "{ident} {kind}"
    found = problems(data)
    assert len(found) == 2
    assert all(p.startswith('profiles[0] "Main": ') for p in found)


def test_config_object_is_returned() -> None:
    assert isinstance(parse_config(minimal()), Config)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_nan_or_infinity_refused_at_load(bad: float) -> None:
    # json.loads accepts NaN and Infinity; a margin of NaN passed "< 0.25" and failed every page later.
    data = minimal()
    data["profiles"][0]["stamps"][0]["margin_in"] = bad
    with pytest.raises(ConfigError, match="margin_in"):
        parse_config(json.loads(json.dumps(data)))
