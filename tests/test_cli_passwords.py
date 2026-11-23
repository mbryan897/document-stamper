"""Passwords for several files never come from the command line (D-126, amends D-112)."""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
import pytest

from stamper.cli import REMOVED_PASSWORD_FOR, ExitCode, main
from tests.gui_support import copy_to
from tests.stamping import DEFAULT_CONFIG, FAKE_ID
from tools import make_corpus

PW = make_corpus.USER_PASSWORD
SECRET = "do-not-echo-this-9f3"
BASE = ["--config", str(DEFAULT_CONFIG), "--set", f"identifier={FAKE_ID}", "--set", "doc_type=Report"]


class Run:
    """One command-line run with captured output."""

    def __init__(self, argv: list[str], stdin: str = "") -> None:
        out, err = io.StringIO(), io.StringIO()
        self.code = main(argv, stdout=out, stderr=err, stdin=io.StringIO(stdin))
        self.stdout, self.stderr = out.getvalue(), err.getvalue()


@pytest.fixture
def two(corpus_dir: Path, tmp_path: Path) -> tuple[Path, Path]:
    """A password-protected file and a plain one, in a temp folder."""
    return (copy_to(corpus_dir / "encrypted_user_password.pdf", tmp_path),
            copy_to(corpus_dir / "letter_portrait.pdf", tmp_path))


@pytest.mark.parametrize("form", ["separate", "equals"])
def test_password_for_is_refused_without_echoing_the_password(two: tuple[Path, Path], form: str) -> None:
    a, b = two
    flag = ["--password-for", str(a), SECRET] if form == "separate" else [f"--password-for={a}", SECRET]
    r = Run(["--combine", str(a), str(b), *flag, *BASE])
    assert r.code == ExitCode.USAGE
    assert REMOVED_PASSWORD_FOR in r.stderr
    assert "--password-env-for" in r.stderr and "--passwords-stdin" in r.stderr
    assert SECRET not in r.stderr and SECRET not in r.stdout
    assert not list(a.parent.glob("*_stamped*")) and not list(a.parent.glob("~*"))


def test_password_env_for_combine(two: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = two
    out = a.parent / "exhibit.pdf"
    monkeypatch.setenv("STAMPER_TEST_PW", PW)
    r = Run(["--combine", str(b), str(a), "--out", str(out), "--password-env-for", str(a), "STAMPER_TEST_PW", *BASE])
    assert r.code == ExitCode.OK, r.stderr
    with pymupdf.open(out) as doc:
        assert doc.page_count == 5


def test_password_env_for_bare_name_batch(two: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = two
    outdir = a.parent / "out"
    outdir.mkdir()
    monkeypatch.setenv("STAMPER_TEST_PW", PW)
    r = Run(["--batch", str(a), str(b), "--out-dir", str(outdir),
             "--password-env-for", a.name, "STAMPER_TEST_PW", *BASE])
    assert r.code == ExitCode.OK, r.stderr
    assert len(list(outdir.iterdir())) == 2


def test_password_env_for_unset_variable(two: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = two
    monkeypatch.delenv("STAMPER_TEST_UNSET", raising=False)
    r = Run(["--combine", str(a), str(b), "--password-env-for", str(a), "STAMPER_TEST_UNSET", *BASE])
    assert r.code == ExitCode.PASSWORD
    assert "STAMPER_TEST_UNSET" in r.stderr and a.name in r.stderr


def test_password_env_for_unknown_file(two: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = two
    monkeypatch.setenv("STAMPER_TEST_PW", PW)
    r = Run(["--combine", str(a), str(b), "--password-env-for", "other.pdf", "STAMPER_TEST_PW", *BASE])
    assert r.code == ExitCode.USAGE and "isn't one of the inputs" in r.stderr


def test_password_env_for_named_twice(two: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = two
    monkeypatch.setenv("STAMPER_TEST_PW", PW)
    r = Run(["--combine", str(a), str(b), "--password-env-for", str(a), "STAMPER_TEST_PW",
             "--password-env-for", a.name, "STAMPER_TEST_PW", *BASE])
    assert r.code == ExitCode.USAGE and "more than once" in r.stderr


def test_bare_name_matching_two_inputs_is_refused(corpus_dir: Path, tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    one, two_ = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two_.mkdir()
    a = copy_to(corpus_dir / "encrypted_user_password.pdf", one)
    b = copy_to(corpus_dir / "encrypted_user_password.pdf", two_)
    monkeypatch.setenv("STAMPER_TEST_PW", PW)
    r = Run(["--combine", str(a), str(b), "--password-env-for", a.name, "STAMPER_TEST_PW", *BASE])
    assert r.code == ExitCode.USAGE
    assert "2 of the files are named encrypted_user_password.pdf" in r.stderr


def test_passwords_stdin_one_line_per_file(two: tuple[Path, Path]) -> None:
    a, b = two
    out = a.parent / "exhibit.pdf"
    r = Run(["--combine", str(b), str(a), "--out", str(out), "--passwords-stdin", *BASE], stdin=f"\n{PW}\n")
    assert r.code == ExitCode.OK, r.stderr
    assert out.is_file()


def test_passwords_stdin_short_input_names_the_file(two: tuple[Path, Path]) -> None:
    a, b = two
    r = Run(["--combine", str(b), str(a), "--passwords-stdin", *BASE], stdin="\n")
    assert r.code == ExitCode.PASSWORD and a.name in r.stderr


def test_passwords_stdin_wrong_password(two: tuple[Path, Path]) -> None:
    a, b = two
    r = Run(["--batch", str(a), str(b), "--passwords-stdin", *BASE], stdin="wrong\n\n")
    assert r.code == ExitCode.PASSWORD


def test_passwords_stdin_and_env_for_together_refused(two: tuple[Path, Path],
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = two
    monkeypatch.setenv("STAMPER_TEST_PW", PW)
    r = Run(["--combine", str(a), str(b), "--passwords-stdin",
             "--password-env-for", str(a), "STAMPER_TEST_PW", *BASE], stdin=f"{PW}\n\n")
    assert r.code == ExitCode.USAGE and "not both" in r.stderr


@pytest.mark.parametrize("flag", [["--passwords-stdin"], ["--password-env-for", "x.pdf", "VAR"]])
def test_multi_password_flags_refused_for_one_file(corpus_dir: Path, flag: list[str]) -> None:
    r = Run([str(corpus_dir / "letter_portrait.pdf"), *flag, *BASE])
    assert r.code == ExitCode.USAGE and "--combine and --batch" in r.stderr


def test_help_no_longer_offers_password_for() -> None:
    from stamper.cli import build_parser

    text = build_parser().format_help()
    assert "--password-for" not in text
    assert "--password-env-for" in text and "--passwords-stdin" in text


def test_an_original_with_a_short_password_opens_and_keeps_it(tmp_path: Path) -> None:
    """The 10-character minimum is for a new password only; an original's is whatever it has (F-01)."""
    src = tmp_path / "short_pw_original.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "Synthetic page")
        doc.save(src, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="abc", owner_pw="abc")
    out = tmp_path / "copy.pdf"
    r = Run([str(src), "--password-stdin", "--out", str(out), *BASE], "abc\n")
    assert r.code == ExitCode.OK, r.stderr
    with pymupdf.open(out) as doc:
        assert doc.needs_pass and doc.authenticate("abc") and FAKE_ID in doc[0].get_text()
