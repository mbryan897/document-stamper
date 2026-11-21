"""Command line: arguments, messages and exit codes."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pymupdf
import pytest

from stamper.cli import ExitCode, main
from tests.stamping import BUILTIN_CONFIG, DEFAULT_CONFIG, FAKE_ID, RECORDS_CONFIG, REPO
from tools import make_corpus


class Run:
    """Result of one CLI run."""

    def __init__(self, argv: list[str], stdin: str = "") -> None:
        self.out, self.err = io.StringIO(), io.StringIO()
        self.code = main(argv, stdout=self.out, stderr=self.err, stdin=io.StringIO(stdin))
        self.stdout, self.stderr = self.out.getvalue(), self.err.getvalue()


def _copy(corpus_dir: Path, name: str, tmp_path: Path) -> Path:
    dst = tmp_path / name
    shutil.copyfile(corpus_dir / name, dst)
    return dst


@pytest.fixture(autouse=True)
def _three_field_config_next_to_the_app() -> None:
    """Runs without ``--config`` use the tests' three-field config, found next to the app (D-080)."""
    shutil.copyfile(DEFAULT_CONFIG, Path(os.environ["STAMPER_APP_DIR"]) / "config.json")


def test_success_with_out(corpus_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "stamped.pdf"
    r = Run([str(corpus_dir / "letter_portrait.pdf"), "--set", f"identifier={FAKE_ID}",
             "--set", "doc_type=Report", "--out", str(out)])
    assert r.code == ExitCode.OK, r.stderr
    assert f"Saved: {out}" in r.stdout
    with pymupdf.open(out) as d:
        assert f"{FAKE_ID}  |  Report  |  Page 1 of 3" in d[0].get_text()


def test_suggested_name_in_source_folder_and_prefill(corpus_dir: Path, tmp_path: Path) -> None:
    src = _copy(corpus_dir, f"{make_corpus.FAKE_CASE_SO}_provider_return.pdf", tmp_path)
    r = Run([str(src), "--config", str(RECORDS_CONFIG), "--set", "doc_type=search warrant return"])
    assert r.code == ExitCode.OK, r.stderr
    assert f"Case number taken from the filename: {make_corpus.FAKE_CASE_SO}" in r.stdout
    # O-008 (D-067): the identifier isn't repeated from the original name.
    expected = tmp_path / f"{make_corpus.FAKE_CASE_SO}_Search-warrant-return_provider_return.pdf"
    assert expected.is_file()


def test_set_overrides_prefill(corpus_dir: Path, tmp_path: Path) -> None:
    src = _copy(corpus_dir, f"provider_return_{make_corpus.FAKE_CASE_DASHED}.pdf", tmp_path)
    r = Run([str(src), "--config", str(RECORDS_CONFIG), "--set", "identifier=SO261017-999903",
             "--set", "doc_type=Court order", "--out-dir", str(tmp_path)])
    assert r.code == ExitCode.OK, r.stderr
    assert "taken from the filename" not in r.stdout
    assert (tmp_path / f"SO261017-999903_Court-order_{src.stem}.pdf").is_file()


def test_every_validation_failure_listed(corpus_dir: Path, tmp_path: Path) -> None:
    r = Run([str(corpus_dir / "letter_portrait.pdf"), "--config", str(RECORDS_CONFIG),
             "--set", "identifier=SO251301-999902", "--set", "doc_type=Memo",
             "--set", "received=02-30-2026", "--out-dir", str(tmp_path)])
    assert r.code == ExitCode.INVALID_FIELDS
    assert "Case number must match one of" in r.stderr
    assert "Document type must be one of" in r.stderr
    assert "Enter date received as a real date" in r.stderr
    assert list(tmp_path.iterdir()) == []


def test_missing_required_field_hint(corpus_dir: Path, tmp_path: Path) -> None:
    r = Run([str(corpus_dir / "letter_portrait.pdf"), "--set", "identifier=X1", "--out-dir", str(tmp_path)])
    assert r.code == ExitCode.INVALID_FIELDS
    assert "Document type is required. (use --set doc_type=...)" in r.stderr


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--set", "case=1"], 'isn\'t a field in profile "Standard"'),
        (["--set", "identifier"], "--set needs NAME=VALUE"),
        (["--set", "identifier=a", "--set", "identifier=b"], "set more than once"),
        (["--profile", "Nope"], 'No profile named "Nope"'),
        (["--set", "identifier=a", "--set", "doc_type=Report", "--name-field", "doc_title2"], "isn't available"),
    ],
)
def test_usage_errors(args: list[str], message: str, corpus_dir: Path) -> None:
    r = Run([str(corpus_dir / "letter_portrait.pdf"), *args, "--dry-run"])
    assert r.code == ExitCode.USAGE
    assert message in r.stderr


def test_argparse_errors_exit_2() -> None:
    with pytest.raises(SystemExit) as e:
        main([], stdout=io.StringIO(), stderr=io.StringIO())
    assert e.value.code == ExitCode.USAGE


def test_config_problem(corpus_dir: Path, tmp_path: Path) -> None:
    bad = tmp_path / "config.json"
    bad.write_text('{"schema_version": 1, ', encoding="utf-8")
    r = Run([str(corpus_dir / "letter_portrait.pdf"), "--config", str(bad)])
    assert r.code == ExitCode.CONFIG
    assert "isn't valid JSON" in r.stderr


def test_unsupported_inputs(tmp_path: Path) -> None:
    txt = tmp_path / "notes.pdf"
    txt.write_text("hello", encoding="utf-8")
    assert Run([str(txt)]).code == ExitCode.UNSUPPORTED
    r = Run([str(tmp_path / "missing.pdf")])
    assert r.code == ExitCode.UNSUPPORTED and "Can't find" in r.stderr


BASE = ["--set", f"identifier={FAKE_ID}", "--set", "doc_type=Report"]


def test_password_paths(corpus_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = str(corpus_dir / "encrypted_user_password.pdf")
    r = Run([src, *BASE, "--out", str(tmp_path / "a.pdf")])
    assert r.code == ExitCode.PASSWORD and "--password-env" in r.stderr

    monkeypatch.setenv("STAMPER_TEST_PW", make_corpus.USER_PASSWORD)
    assert Run([src, *BASE, "--password-env", "STAMPER_TEST_PW", "--out", str(tmp_path / "b.pdf")]).code == ExitCode.OK

    r = Run([src, *BASE, "--password-stdin", "--out", str(tmp_path / "c.pdf")], stdin=make_corpus.USER_PASSWORD + "\r\n")
    assert r.code == ExitCode.OK, r.stderr

    r = Run([src, *BASE, "--password-stdin", "--out", str(tmp_path / "d.pdf")], stdin="wrong\n")
    assert r.code == ExitCode.PASSWORD and "doesn't open" in r.stderr

    r = Run([src, *BASE, "--password-env", "STAMPER_UNSET_VAR", "--out", str(tmp_path / "e.pdf")])
    assert r.code == ExitCode.PASSWORD and "isn't set" in r.stderr
    assert not (tmp_path / "a.pdf").exists()


def test_password_never_in_output(corpus_dir: Path, tmp_path: Path) -> None:
    r = Run([str(corpus_dir / "encrypted_user_password.pdf"), *BASE, "--password-stdin",
             "--out", str(tmp_path / "x.pdf")], stdin=make_corpus.USER_PASSWORD)
    assert make_corpus.USER_PASSWORD not in r.stdout + r.stderr


def test_restrictions_need_flag(corpus_dir: Path, tmp_path: Path) -> None:
    src = str(corpus_dir / "owner_restrictions.pdf")
    r = Run([src, *BASE, "--out", str(tmp_path / "a.pdf")])
    assert r.code == ExitCode.CONFIRM and "--accept-restrictions" in r.stderr
    r = Run([src, *BASE, "--accept-restrictions", "--out", str(tmp_path / "a.pdf")])
    assert r.code == ExitCode.OK and "Note:" in r.stdout


def test_output_refusals(corpus_dir: Path, tmp_path: Path) -> None:
    src = _copy(corpus_dir, "letter_portrait.pdf", tmp_path)
    r = Run([str(src), *BASE, "--out", str(src), "--overwrite"])
    assert r.code == ExitCode.OUTPUT_REFUSED and "over the original" in r.stderr
    out = tmp_path / "out.pdf"
    assert Run([str(src), *BASE, "--out", str(out)]).code == ExitCode.OK
    r = Run([str(src), *BASE, "--out", str(out)])
    assert r.code == ExitCode.OUTPUT_REFUSED and "already exists" in r.stderr
    assert Run([str(src), *BASE, "--out", str(out), "--overwrite"]).code == ExitCode.OK


def test_does_not_fit(corpus_dir: Path, tmp_path: Path) -> None:
    r = Run([str(corpus_dir / "mixed_sizes.pdf"), "--config", str(RECORDS_CONFIG),
             "--set", f"identifier={FAKE_ID}", "--set", "doc_type=Search warrant return",
             "--out-dir", str(tmp_path)])
    assert r.code == ExitCode.DOES_NOT_FIT
    assert r.stderr.startswith("Page 4 ")
    assert list(tmp_path.iterdir()) == []


def test_missing_glyph_exit_code(corpus_dir: Path, tmp_path: Path) -> None:
    r = Run([str(corpus_dir / "letter_portrait.pdf"), "--set", "identifier=99-2026-999901案",
             "--set", "doc_type=Report", "--out-dir", str(tmp_path)])
    assert r.code == ExitCode.DOES_NOT_FIT and "no character" in r.stderr


def test_no_box_remove_encryption_and_quiet(corpus_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAMPER_TEST_PW", make_corpus.USER_PASSWORD)
    out = tmp_path / "plain.pdf"
    r = Run([str(corpus_dir / "encrypted_user_password.pdf"), *BASE, "--password-env", "STAMPER_TEST_PW",
             "--remove-encryption", "--no-box", "--quiet", "--out", str(out)])
    assert r.code == ExitCode.OK and r.stdout == ""
    with pymupdf.open(out) as d:
        assert not d.needs_pass and d.metadata["encryption"] is None


def test_dry_run_writes_nothing(corpus_dir: Path, tmp_path: Path) -> None:
    r = Run([str(corpus_dir / "owner_restrictions.pdf"), *BASE, "--out-dir", str(tmp_path), "--dry-run"])
    assert r.code == ExitCode.OK
    assert "Would save:" in r.stdout and "--accept-restrictions" in r.stdout
    assert list(tmp_path.iterdir()) == []


def test_name_fields_including_date(corpus_dir: Path, tmp_path: Path) -> None:
    r = Run([str(corpus_dir / "letter_portrait.pdf"), *BASE, "--name-field", "identifier",
             "--name-field", "date", "--out-dir", str(tmp_path)])
    assert r.code == ExitCode.OK, r.stderr
    today = date.today().strftime("%m-%d-%Y")
    assert (tmp_path / f"{FAKE_ID}_{today}_letter_portrait.pdf").is_file()


def test_default_config_is_the_shipped_one() -> None:
    from stamper.core.locations import builtin_config_path

    assert builtin_config_path() == BUILTIN_CONFIG


def _module(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "stamper", *args], cwd=REPO,
                          capture_output=True, text=True, timeout=60)


def test_module_version() -> None:
    p = _module("--version")
    assert p.returncode == 0
    assert p.stdout.startswith("Document Stamper ")


def test_module_help_prints_usage() -> None:
    # Replaces the Phase 2 no-arguments usage test: with no arguments the
    # module now opens the window (D-063, D-064; routing in test_gui_entry.py).
    p = _module("--help")
    assert p.returncode == 0
    assert "usage: stamper" in p.stdout


def test_module_runs_cli(corpus_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "m.pdf"
    p = _module(str(corpus_dir / "letter_portrait.pdf"), *BASE, "--out", str(out))
    assert p.returncode == 0, p.stderr
    assert out.is_file()
