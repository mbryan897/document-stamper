"""The Profile settings working copy (D-082). No Qt."""

from __future__ import annotations

from datetime import date

import pytest

from stamper.core.config import load_config
from stamper.gui.config_draft import ConfigDraft, DraftRefused, diff_summary, make_key, sample_values
from tests.stamping import DEFAULT_CONFIG, RECORDS_CONFIG

TODAY = date(2026, 10, 18)


@pytest.fixture
def draft() -> ConfigDraft:
    return ConfigDraft(load_config(RECORDS_CONFIG))


def test_round_trip_is_clean(draft: ConfigDraft) -> None:
    cfg, problems = draft.validate()
    assert problems == [] and cfg == load_config(RECORDS_CONFIG)
    assert not draft.dirty


def test_edits_make_it_dirty_and_mark_saved_clears(draft: ConfigDraft) -> None:
    draft.fields["doc_title"]["label"] = "Title"
    assert draft.dirty
    draft.mark_saved()
    assert not draft.dirty


def test_problems_come_from_the_loader(draft: ConfigDraft) -> None:
    draft.profiles[0]["stamps"][0]["margin_in"] = 0.1
    cfg, problems = draft.validate()
    assert cfg is None
    assert any("margin_in" in p and "at least 0.25" in p for p in problems)


# ------------------------------------------------------------ keys


@pytest.mark.parametrize(
    ("label", "taken", "key"),
    [
        ("Date received", [], "date_received"),
        ("  Agency / unit #  ", [], "agency_unit"),
        ("2nd reviewer", [], "f_2nd_reviewer"),
        ("Page", [], "page_2"),  # built-in token name
        ("Notes", ["notes"], "notes_2"),
        ("Notes", ["notes", "notes_2"], "notes_3"),
        ("!!!", [], "field"),
        ("Ünïcode", [], "n_code"),
    ],
)
def test_make_key(label: str, taken: list[str], key: str) -> None:
    assert make_key(label, taken) == key


def test_new_field_key_is_fixed_after_relabel(draft: ConfigDraft) -> None:
    key = draft.add_field("Reviewer")
    draft.fields[key]["label"] = "Reviewed by"
    assert key == "reviewer" and "reviewer" in draft.fields
    assert draft.validate()[1] == []


def test_new_field_labels_are_unique(draft: ConfigDraft) -> None:
    a, b = draft.add_field(), draft.add_field()
    assert draft.label(a) == "New field" and draft.label(b) == "New field 2"


# ------------------------------------------------------------ deleting fields


def test_identifier_cant_be_deleted(draft: ConfigDraft) -> None:
    with pytest.raises(DraftRefused, match="identifier field can't be deleted"):
        draft.delete_field("identifier")


def test_field_in_a_profile_cant_be_deleted(draft: ConfigDraft) -> None:
    with pytest.raises(DraftRefused, match='Document title is used by "Records submission"'):
        draft.delete_field("doc_title")


def test_unused_field_can_be_deleted(draft: ConfigDraft) -> None:
    draft.profiles[0]["fields"].remove("received")
    draft.delete_field("received")
    assert "received" not in draft.fields and draft.validate()[1] == []


def test_template_usage(draft: ConfigDraft) -> None:
    assert draft.template_usage("identifier") == [
        '"Records submission" stamp 1', '"Records submission" file name',
        '"Records submission" document property CaseNumber']
    assert draft.template_usage("received") == []


# ------------------------------------------------------------ field types


def test_text_to_choice_takes_the_first_list(draft: ConfigDraft) -> None:
    draft.set_field_type("doc_title", "choice")
    spec = draft.fields["doc_title"]
    assert spec["taxonomy"] == "doc_types" and spec["formats"] == []
    assert draft.validate()[1] == []


def test_choice_to_date_drops_the_list(draft: ConfigDraft) -> None:
    draft.set_field_type("doc_type", "date")
    assert draft.fields["doc_type"]["taxonomy"] is None
    assert draft.validate()[1] == []


def test_text_with_formats_to_date_drops_them(draft: ConfigDraft) -> None:
    draft.set_field_type("identifier", "date")
    assert draft.fields["identifier"]["formats"] == [] and not draft.fields["identifier"]["from_filename"]


# ------------------------------------------------------------ profiles and stamps


def test_add_profile_is_valid(draft: ConfigDraft) -> None:
    i = draft.add_profile()
    assert draft.profiles[i]["name"] == "New profile" and not draft.profiles[i]["default"]
    assert draft.validate()[1] == []


def test_duplicate_profile(draft: ConfigDraft) -> None:
    i = draft.duplicate_profile(0)
    assert draft.profiles[i]["name"] == "Records submission copy"
    assert not draft.profiles[i]["default"]
    assert draft.validate()[1] == []


def test_last_profile_cant_be_deleted(draft: ConfigDraft) -> None:
    with pytest.raises(DraftRefused, match="at least one profile"):
        draft.delete_profile(0)


def test_deleting_the_default_makes_another_default(draft: ConfigDraft) -> None:
    draft.add_profile()
    draft.delete_profile(0)
    assert draft.profiles[0]["default"] and draft.validate()[1] == []


def test_set_default(draft: ConfigDraft) -> None:
    i = draft.add_profile()
    draft.set_default(i)
    assert [p["default"] for p in draft.profiles] == [False, True]
    cfg, _ = draft.validate()
    assert cfg is not None and cfg.default_profile.name == "New profile"


def test_add_and_remove_stamp(draft: ConfigDraft) -> None:
    j = draft.add_stamp(0)
    assert draft.profiles[0]["stamps"][j]["anchor"] == "bottom-right"
    assert draft.validate()[1] == []
    draft.remove_stamp(0, j)
    with pytest.raises(DraftRefused, match="at least one stamp"):
        draft.remove_stamp(0, 0)


# ------------------------------------------------------------ lists


def test_list_in_use_cant_be_deleted(draft: ConfigDraft) -> None:
    with pytest.raises(DraftRefused, match="used by Document type"):
        draft.delete_taxonomy("doc_types")


def test_rename_list_updates_fields_and_keeps_order(draft: ConfigDraft) -> None:
    draft.add_taxonomy("units")
    draft.rename_taxonomy("doc_types", "Document types")
    assert list(draft.taxonomies) == ["Document types", "units"]
    assert draft.fields["doc_type"]["taxonomy"] == "Document types"
    assert draft.validate()[1] == []


def test_rename_list_refuses_a_duplicate(draft: ConfigDraft) -> None:
    draft.add_taxonomy("units")
    with pytest.raises(DraftRefused, match="already a list"):
        draft.rename_taxonomy("units", "DOC_TYPES")


def test_new_list_is_valid_and_deletable(draft: ConfigDraft) -> None:
    name = draft.add_taxonomy()
    assert draft.validate()[1] == []
    draft.delete_taxonomy(name)
    assert name not in draft.taxonomies


# ------------------------------------------------------------ helpers


def test_sample_values() -> None:
    cfg = load_config(RECORDS_CONFIG)
    vals = sample_values(cfg, cfg.default_profile, TODAY)
    assert vals == {"identifier": "12-2026-345678", "doc_type": "Search warrant return",
                    "doc_title": "Document title", "received": "10-18-2026"}


def test_sample_values_wildcard_pattern() -> None:
    cfg = load_config(DEFAULT_CONFIG)
    assert sample_values(cfg, cfg.default_profile, TODAY)["identifier"] == "Example"


def test_diff_summary() -> None:
    old, new = load_config(DEFAULT_CONFIG), load_config(RECORDS_CONFIG)
    lines = diff_summary(old, new)
    assert "Profiles added: Records submission" in lines
    assert "Profiles removed: Standard" in lines
    assert "Fields added: Date received" in lines
    assert "Fields renamed: Identifier → Case number" in lines


def test_diff_summary_same() -> None:
    cfg = load_config(RECORDS_CONFIG)
    assert diff_summary(cfg, cfg) == ["Same profiles, fields and lists; settings inside them may differ."]
