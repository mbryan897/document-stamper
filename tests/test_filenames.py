"""Suggested filenames for stamped copies."""

from __future__ import annotations

from pathlib import Path

import pytest

from stamper.core.config import load_config
from stamper.core.filenames import (
    MAX_FILENAME_LENGTH,
    build_filename,
    clean_part,
    is_reserved_name,
    remove_value,
    suggest_filename,
)
from stamper.core.fields import prefill_fields

FIXTURE = Path(__file__).parent / "fixtures" / "records_sample.json"
ID = "SO261017-999902"


def utf16_len(name: str) -> int:
    return len(name.encode("utf-16-le")) // 2


def test_dev_plan_example() -> None:
    name = build_filename([ID, "Search warrant return"], separator="_", original_name="provider_return")
    assert name == f"{ID}_Search-warrant-return_provider_return.pdf"


def test_suggest_from_profile_defaults() -> None:
    prof = load_config(FIXTURE).default_profile
    vals = {"identifier": ID, "doc_type": "Search warrant return"}
    assert suggest_filename(prof, vals, r"D:\in\provider_return.pdf") == f"{ID}_Search-warrant-return_provider_return.pdf"


def test_suggest_with_ticked_fields_and_date() -> None:
    prof = load_config(FIXTURE).default_profile
    vals = {"identifier": ID, "doc_type": "Court order", "date": "10-17-2026"}
    name = suggest_filename(prof, vals, "scan.PDF", ticked=["identifier", "date"])
    assert name == f"{ID}_10-17-2026_scan.PDF"


def test_suggest_skips_empty_ticked_field() -> None:
    prof = load_config(FIXTURE).default_profile
    assert suggest_filename(prof, {"identifier": ID, "doc_type": ""}, "a.pdf") == f"{ID}_a.pdf"


@pytest.mark.parametrize(
    ("value", "cleaned"),
    [
        ('a<b>c:d"e/f\\g|h?i*j', "a-b-c-d-e-f-g-h-i-j"),
        ("tab\there\nnewline", "tab-here-newline"),
        ("bell\x07char\x7f", "bell-char"),
        ("  many   spaces  ", "many-spaces"),
        ("a - b", "a-b"),
        ("ends with dots...", "ends-with-dots"),
        ("under_score", "under-score"),  # the separator inside a value
        ("-.leading", "leading"),
        ("Cafe\u0301", "Café"),  # NFC
    ],
)
def test_clean_part(value: str, cleaned: str) -> None:
    assert clean_part(value, "_") == cleaned


def test_original_name_keeps_underscores() -> None:
    assert build_filename([ID], original_name="provider_return_v2") == f"{ID}_provider_return_v2.pdf"


@pytest.mark.parametrize(
    "stem",
    ["CON", "con", "PRN", "AUX", "NUL", "COM1", "com9", "LPT1", "LPT9", "COM0",
     "COM\u00b9", "LPT\u00b3", "CONIN$", "CONOUT$", "NUL.txt", "CON .x"],
)
def test_reserved_names_detected(stem: str) -> None:
    assert is_reserved_name(stem)


@pytest.mark.parametrize("stem", ["CONSOLE", "COM10", "LPT", "NULL", "AUXILIARY", "PRN1"])
def test_not_reserved(stem: str) -> None:
    assert not is_reserved_name(stem)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["CON"], "CON_.pdf"),
        (["nul"], "nul_.pdf"),
        (["COM1"], "COM1_.pdf"),
    ],
)
def test_reserved_names_fixed(values: list[str], expected: str) -> None:
    assert build_filename(values) == expected


def test_trailing_dots_and_spaces_removed() -> None:
    assert build_filename(["Report.  "], extension="") == "Report"
    assert build_filename(["Report. . ."]) == "Report.pdf"


def test_empty_result_falls_back_to_original() -> None:
    assert build_filename(["", "  ", "???"], original_name=None) == "document.pdf"
    assert build_filename([""], original_name="scan 01") == "scan-01.pdf"
    assert build_filename([""], fallback_name="scan 01") == "scan-01_stamped.pdf"


def test_empty_everything_uses_stamped_suffix() -> None:
    # Nothing ticked has a value and the original name isn't in the filename.
    prof = load_config(FIXTURE).default_profile
    no_orig = prof.output_name.model_copy(update={"include_original_name": False})
    prof2 = prof.model_copy(update={"output_name": no_orig})
    assert suggest_filename(prof2, {"identifier": "", "doc_type": ""}, "scan.pdf") == "scan_stamped.pdf"


def test_long_title_truncated_original_first() -> None:
    long_title = "Provider records " * 20
    name = build_filename([ID, long_title], original_name="provider_return_" + "x" * 80)
    assert utf16_len(name) <= MAX_FILENAME_LENGTH
    assert name.startswith(f"{ID}_Provider-records-")
    assert name.endswith(".pdf")
    assert "provider_return" not in name  # the original name went first


def test_identifier_kept_whole_when_others_can_shrink() -> None:
    name = build_filename([ID, "T" * 300, "U" * 300], original_name="O" * 300)
    assert name.startswith(f"{ID}_")
    assert utf16_len(name) <= MAX_FILENAME_LENGTH


def test_first_part_cut_only_as_last_resort() -> None:
    name = build_filename(["A" * 500])
    assert utf16_len(name) == MAX_FILENAME_LENGTH
    assert name == "A" * (MAX_FILENAME_LENGTH - 4) + ".pdf"


def test_length_counts_utf16_units() -> None:
    emoji = "\U0001f4c4"  # two UTF-16 units each
    name = build_filename([emoji * 100])
    assert utf16_len(name) <= MAX_FILENAME_LENGTH


def test_custom_max_length() -> None:
    assert utf16_len(build_filename([ID, "x" * 50], max_length=30)) <= 30


def test_truncation_never_leaves_ragged_edges() -> None:
    name = build_filename([ID, "word " * 40], original_name=None)
    stem = name.removesuffix(".pdf")
    assert not stem.endswith(("-", "_", ".", " "))


def test_output_identical_across_runs() -> None:
    args = ([ID, "Search warrant return", "x" * 200],)
    kwargs = {"separator": "_", "original_name": "provider_return"}
    assert len({build_filename(*args, **kwargs) for _ in range(5)}) == 1


def test_other_separator() -> None:
    name = build_filename([ID, "Court order"], separator=" - ", original_name=None)
    assert name == f"{ID} - Court-order.pdf"


# ------------------------------------------------------------ repeated identifier (O-008, D-067)
@pytest.mark.parametrize(
    ("original", "value", "expected"),
    [
        ("SO261017-999902_provider_return", ID, "provider_return"),
        ("provider_return_99-2026-999901", "99-2026-999901", "provider_return"),
        ("provider SO261017-999902 return", ID, "provider return"),
        ("so261017\u2013999902_return", ID, "return"),  # case and en dash folded
        ("SO261017-999902_copy_of_SO261017-999902", ID, "copy_of"),
        ("SO261017-999902", ID, ""),
        # whole values only (D-040 boundaries)
        ("XSO261017-999902_return", ID, "XSO261017-999902_return"),
        ("SO261017-9999021_return", ID, "SO261017-9999021_return"),
        # not there, or nothing to remove
        ("provider_return", ID, "provider_return"),
        ("provider_return", "", "provider_return"),
    ],
)
def test_remove_value(original: str, value: str, expected: str) -> None:
    assert remove_value(original, value) == expected


def _records():
    cfg = load_config(FIXTURE)
    return cfg, cfg.default_profile


@pytest.mark.parametrize(
    ("filename", "identifier", "expected"),
    [
        # both corpus case-number filenames
        ("SO261017-999902_provider_return.pdf", ID, f"{ID}_Search-warrant-return_provider_return.pdf"),
        ("provider_return_99-2026-999901.pdf", "99-2026-999901",
         "99-2026-999901_Search-warrant-return_provider_return.pdf"),
        # a different number than the filename's: the original name keeps its own
        ("provider_return_99-2026-999901.pdf", ID,
         f"{ID}_Search-warrant-return_provider_return_99-2026-999901.pdf"),
        # nothing left of the original name: that part is dropped
        ("SO261017-999902.pdf", ID, f"{ID}_Search-warrant-return.pdf"),
    ],
)
def test_suggest_drops_repeated_identifier(filename: str, identifier: str, expected: str) -> None:
    cfg, prof = _records()
    vals = {"identifier": identifier, "doc_type": "Search warrant return"}
    assert suggest_filename(prof, vals, filename, dedupe=prefill_fields(cfg, prof)) == expected


def test_dedupe_only_for_ticked_prefill_fields() -> None:
    cfg, prof = _records()
    vals = {"identifier": ID, "doc_type": "Search warrant return"}
    # identifier not ticked: it stays in the original name, so it isn't lost
    assert suggest_filename(prof, vals, f"{ID}_return.pdf", ticked=["doc_type"],
                            dedupe=prefill_fields(cfg, prof)) == f"Search-warrant-return_{ID}_return.pdf"
    # doc_type isn't a from_filename field: "Return" isn't removed from provider_return
    vals = {"identifier": ID, "doc_type": "Return"}
    assert suggest_filename(prof, vals, "provider_return.pdf",
                            dedupe=prefill_fields(cfg, prof)) == f"{ID}_Return_provider_return.pdf"
    # without dedupe the Phase 1 behaviour is unchanged
    assert suggest_filename(prof, vals, f"{ID}_x.pdf") == f"{ID}_Return_{ID}_x.pdf"


def test_prefill_fields() -> None:
    cfg, prof = _records()
    assert prefill_fields(cfg, prof) == ["identifier"]
