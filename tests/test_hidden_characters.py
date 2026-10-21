"""Hidden Unicode in file names and field values (D-123, amends D-043)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from stamper.cli import ExitCode, main
from stamper.core.config import load_config
from stamper.core.fields import validate_field
from stamper.core.filenames import build_filename, clean_part, hidden_characters, visible
from stamper.gui.main_window import MainWindow
from tests.gui_support import WAIT_MS, copy_to, fake, open_dialog, type_into, window  # noqa: F401 - fixtures
from tests.stamping import DEFAULT_CONFIG, FAKE_ID

HIDDEN = {
    "‮": "right-to-left override",
    "‭": "left-to-right override",
    "⁦": "left-to-right isolate",
    "⁧": "right-to-left isolate",
    "​": "zero width space",
    "‌": "zero width non-joiner",
    "‍": "zero width joiner",
    "⁠": "word joiner",
    "﻿": "zero width no-break space",
    "­": "soft hyphen",
}
SPOOF = "report‮fdp.exe.pdf"  # displays as "reportexe.pdf.pdf"-ish in many viewers


@pytest.mark.parametrize("ch", list(HIDDEN))
def test_format_characters_removed_from_file_names(ch: str) -> None:
    assert clean_part(f"99{ch}-2026{ch}-999901") == "99-2026-999901"
    name = build_filename([f"SO261017-{ch}999902"], original_name=f"pro{ch}vider return")
    assert not hidden_characters(name)
    assert name == "SO261017-999902_provider-return.pdf"


def test_spoofed_original_name_is_cleaned() -> None:
    assert build_filename(["99-2026-999901"], original_name=Path(SPOOF).stem) == "99-2026-999901_reportfdp.exe.pdf"


def test_line_and_paragraph_separators_become_dashes() -> None:
    # They're whitespace, so they become "-" like a space (D-043 unchanged).
    assert clean_part("a b c") == "a-b-c"


@pytest.mark.parametrize(("ch", "name"), list(HIDDEN.items()))
def test_hidden_character_in_a_value_is_refused(ch: str, name: str) -> None:
    config = load_config(DEFAULT_CONFIG)
    result = validate_field(config, "identifier", f"  99-2026{ch}-999901")
    assert not result.valid
    code = f"U+{ord(ch):04X}"
    assert f"({code}, {name}) at position 10" in result.message
    assert "Retype the value." in result.message


@pytest.mark.parametrize("ch", ["\x07", "\t", "\x1b", "\x85"])
def test_control_characters_inside_a_value_are_refused(ch: str) -> None:
    config = load_config(DEFAULT_CONFIG)
    result = validate_field(config, "identifier", f"99-2026{ch}-999901")
    assert not result.valid and "hidden character" in result.message


def test_several_hidden_characters_counted() -> None:
    config = load_config(DEFAULT_CONFIG)
    result = validate_field(config, "identifier", "99​-2026​-999901​")
    assert "at position 3 and 2 more" in result.message


def test_plain_values_unaffected() -> None:
    config = load_config(DEFAULT_CONFIG)
    assert validate_field(config, "identifier", f"  {FAKE_ID} ").valid


def test_visible_escapes_but_keeps_line_breaks() -> None:
    assert visible(SPOOF) == "report⟨U+202E⟩fdp.exe.pdf"
    assert visible("a​b\nc\td") == "a⟨U+200B⟩b\nc\td"
    assert visible("plain name.pdf") == "plain name.pdf"


def test_cli_shows_a_spoofed_name_escaped(corpus_dir: Path, tmp_path: Path) -> None:
    spoof = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    spoof = spoof.rename(tmp_path / SPOOF)
    other = copy_to(corpus_dir / "rotate_90.pdf", tmp_path)
    out, err = io.StringIO(), io.StringIO()
    code = main(["--combine", str(spoof), str(other), "--dry-run", "--config", str(DEFAULT_CONFIG),
                 "--set", f"identifier={FAKE_ID}", "--set", "doc_type=Report"],
                stdout=out, stderr=err, stdin=io.StringIO(""))
    assert code == ExitCode.OK, err.getvalue()
    text = out.getvalue()
    assert "report⟨U+202E⟩fdp.exe.pdf" in text
    assert "‮" not in text


def test_cli_refuses_a_hidden_character_in_set(corpus_dir: Path) -> None:
    out, err = io.StringIO(), io.StringIO()
    code = main([str(corpus_dir / "letter_portrait.pdf"), "--dry-run", "--config", str(DEFAULT_CONFIG),
                 "--set", "identifier=99-2026-​999901", "--set", "doc_type=Report"],
                stdout=out, stderr=err, stdin=io.StringIO(""))
    assert code == ExitCode.INVALID_FIELDS
    assert "U+200B" in err.getvalue() and "​" not in err.getvalue()


def test_files_list_shows_a_spoofed_name_escaped(qtbot: QtBot, window: MainWindow, corpus_dir: Path,
                                                 tmp_path: Path) -> None:
    spoof = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path).rename(tmp_path / SPOOF)
    other = copy_to(corpus_dir / "rotate_90.pdf", tmp_path)
    window.open_files([spoof, other])
    qtbot.waitUntil(lambda: window.stack.currentWidget() is window.files_screen, timeout=WAIT_MS)
    fs = window.files_screen
    qtbot.waitUntil(lambda: fs.list.count() == 2, timeout=WAIT_MS)
    texts = [fs.list.item(i).text() for i in range(fs.list.count())]
    assert any("report⟨U+202E⟩fdp.exe.pdf" in t for t in texts), texts
    assert not any("‮" in t for t in texts)
    assert fs.paths()[0] == spoof  # the real path is kept for opening the file


def test_dialog_suggested_name_drops_hidden_characters(qtbot: QtBot, window: MainWindow, corpus_dir: Path,
                                                       tmp_path: Path) -> None:
    # The name is built from the real file name, not its escaped display form.
    spoof = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path).rename(tmp_path / SPOOF)
    d = open_dialog(qtbot, window, spoof)
    type_into(qtbot, d, "identifier", FAKE_ID)
    name = d.suggested_name()
    assert name.endswith("_reportfdp.exe.pdf"), name
    assert "⟨" not in name and not hidden_characters(name)
