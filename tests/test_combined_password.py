"""An optional password on the combined file (F-01, amends D-096): engine, wording, command line.

Synthetic files only. The planted password below is made up for these tests.
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
import pytest

from stamper import cli
from stamper.handlers.base import StampError
from stamper.handlers.combine import CombineInput, combine_pdfs
from stamper.handlers.pdf import (
    COMBINED_UNPROTECTED_NOTE,
    KEEP_PROTECTION,
    OUTPUT_PASSWORD_RULE,
    PdfHandler,
    combined_no_password_note,
    needs_check,
    output_password_problem,
)
from tests.gui_support import copy_to
from tests.stamping import DEFAULT_CONFIG, FAKE_ID, default_job
from tools import make_corpus

PW = make_corpus.USER_PASSWORD  # opens encrypted_user_password.pdf
OUT_PW = "Zq7!planted-out-pw"  # distinctive, so it can be searched for
DECLINED = ("This combined file has no password. 1 of its files was password-protected. To keep the same "
            "level of protection, encrypt this file or keep it only on encrypted drives and shares.")


def _locked_and_open(corpus_dir: Path, folder: Path) -> list[CombineInput]:
    locked = copy_to(corpus_dir / "encrypted_user_password.pdf", folder)
    plain = copy_to(corpus_dir / "letter_portrait.pdf", folder)
    return [CombineInput(locked, PW), CombineInput(plain)]


def _source_texts(corpus_dir: Path) -> list[str]:
    """The text of every source page, in combined order."""
    texts: list[str] = []
    for name, pw in (("encrypted_user_password.pdf", PW), ("letter_portrait.pdf", None)):
        with pymupdf.open(corpus_dir / name) as doc:
            if pw:
                doc.authenticate(pw)
            texts.extend(p.get_text().strip() for p in doc)
    return texts


# ------------------------------------------------------------ the save


def test_password_set_saves_aes256_with_every_page_readable(corpus_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "combined.pdf"
    with combine_pdfs(_locked_and_open(corpus_dir, tmp_path), tmp_path) as combined:
        result = PdfHandler().apply(combined.path, default_job(), out, out_password=OUT_PW)
    assert not result.encryption_kept
    expected = _source_texts(corpus_dir)

    with pymupdf.open(out) as doc:
        assert doc.needs_pass
        assert not doc.authenticate("wrong-password")
    with pymupdf.open(out) as doc:
        assert doc.authenticate(OUT_PW)
        assert doc.metadata["encryption"] == "Standard V5 R6 256-bit AES"
        assert doc.permissions & 0xF3C == 0xF3C  # print, modify, copy, annotate, forms, accessibility, assemble, HQ print
        assert doc.page_count == len(expected) == 5
        for page, original in zip(doc, expected, strict=True):
            text = page.get_text()
            for line in original.splitlines():  # D-056: content written on an encrypted save stays readable
                assert line in text
            assert FAKE_ID in text  # and the stamp


def test_no_password_saves_exactly_as_before(corpus_dir: Path, tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    # The output isn't byte-reproducible (a new /ID and ModDate each save), so pin the save itself.
    calls: list[dict[str, object]] = []
    real = pymupdf.Document.save

    def spy(self: pymupdf.Document, filename: object, **kw: object) -> object:
        calls.append(kw)
        return real(self, filename, **kw)

    out = tmp_path / "combined.pdf"
    inputs = [CombineInput(copy_to(corpus_dir / n, tmp_path)) for n in ("letter_portrait.pdf", "rotate_90.pdf")]
    with combine_pdfs(inputs, tmp_path) as combined:
        monkeypatch.setattr(pymupdf.Document, "save", spy)
        PdfHandler().apply(combined.path, default_job(), out)
    assert calls == [{"garbage": 0, "deflate": False, "clean": False, "encryption": pymupdf.PDF_ENCRYPT_NONE}]
    with pymupdf.open(out) as doc:
        assert not doc.needs_pass and not doc.metadata["encryption"]


@pytest.mark.parametrize("bad", ["", "x" * 41, "pässwort", "tab\there", "line\nbreak", "😀😀😀"])
def test_out_of_range_passwords_are_refused_before_saving(corpus_dir: Path, tmp_path: Path, bad: str) -> None:
    assert output_password_problem(bad)
    out = tmp_path / "combined.pdf"
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    with pytest.raises(StampError):
        PdfHandler().apply(src, default_job(), out, out_password=bad)
    assert not out.exists()


def test_accepted_passwords() -> None:
    assert output_password_problem("x" * 40) == ""
    assert output_password_problem("Pass word ~!@#$%^&*()_+{}|:\"<>?") == ""
    assert output_password_problem("é") == OUTPUT_PASSWORD_RULE


# ------------------------------------------------------------ wording


def test_declined_warning_counts_only_sources_that_needed_a_password(corpus_dir: Path, tmp_path: Path) -> None:
    owner = copy_to(corpus_dir / "owner_restrictions.pdf", tmp_path)
    inputs = [*_locked_and_open(corpus_dir, tmp_path), CombineInput(owner)]
    with combine_pdfs(inputs, tmp_path, accept_restrictions=True) as combined:
        assert combined.locked == ("encrypted_user_password.pdf",)
        assert set(combined.protected) == {"encrypted_user_password.pdf", "owner_restrictions.pdf"}
        assert combined.protection_warning() == DECLINED
        assert combined.protection_warning(password_set=True) == ""
    assert needs_check(DECLINED)  # under "Check the copy"
    assert combined_no_password_note(2).startswith("This combined file has no password. 2 of its files were ")


def test_owner_restricted_only_keeps_the_d096_warning(corpus_dir: Path, tmp_path: Path) -> None:
    owner = copy_to(corpus_dir / "owner_restrictions.pdf", tmp_path)
    plain = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    with combine_pdfs([CombineInput(owner), CombineInput(plain)], tmp_path, accept_restrictions=True) as combined:
        assert combined.locked == ()
        assert combined.protection_warning() == (f"{COMBINED_UNPROTECTED_NOTE} "
                                                 "Protected files in it: owner_restrictions.pdf.")


def test_remove_encryption_warning_says_how_to_keep_protection(corpus_dir: Path, tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "encrypted_user_password.pdf", tmp_path)
    r = PdfHandler().apply(src, default_job(), tmp_path / "out.pdf", password=PW, remove_encryption=True)
    note = next(w for w in r.warnings if "isn't password-protected" in w)
    assert note == ("The copy isn't password-protected, unlike the original. To keep the same level of "
                    "protection, encrypt this file or keep it only on encrypted drives and shares.")
    assert note.endswith(KEEP_PROTECTION) and needs_check(note)


# ------------------------------------------------------------ command line


def _run(argv: list[str], stdin: str = "") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, stdout=out, stderr=err, stdin=io.StringIO(stdin))
    return code, out.getvalue(), err.getvalue()


def _combine_args(corpus_dir: Path, folder: Path, out: Path) -> list[str]:
    locked = copy_to(corpus_dir / "encrypted_user_password.pdf", folder)
    plain = copy_to(corpus_dir / "letter_portrait.pdf", folder)
    return ["--config", str(DEFAULT_CONFIG), "--combine", str(locked), str(plain), "--out", str(out),
            "--set", f"identifier={FAKE_ID}", "--set", "doc_type=Report",
            "--password-env-for", str(locked), "SRC_PW"]


def test_cli_out_password_env_protects_the_file(corpus_dir: Path, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRC_PW", PW)
    monkeypatch.setenv("OUT_PW", OUT_PW)
    out = tmp_path / "combined.pdf"
    code, stdout, err = _run([*_combine_args(corpus_dir, tmp_path, out), "--out-password-env", "OUT_PW"])
    assert code == 0, err
    assert "This file opens only with the password you set." in stdout
    assert "has no password" not in stdout and OUT_PW not in stdout + err
    with pymupdf.open(out) as doc:
        assert doc.needs_pass and not doc.authenticate("wrong-password")
    with pymupdf.open(out) as doc:
        assert doc.authenticate(OUT_PW) and doc.metadata["encryption"] == "Standard V5 R6 256-bit AES"
        assert all(FAKE_ID in p.get_text() for p in doc)


def test_cli_out_password_stdin(corpus_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRC_PW", PW)
    out = tmp_path / "combined.pdf"
    code, _, err = _run([*_combine_args(corpus_dir, tmp_path, out), "--out-password-stdin"], f"{OUT_PW}\n")
    assert code == 0, err
    with pymupdf.open(out) as doc:
        assert doc.authenticate(OUT_PW)


def test_cli_without_out_password_prints_the_declined_warning(corpus_dir: Path, tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRC_PW", PW)
    out = tmp_path / "combined.pdf"
    code, stdout, err = _run(_combine_args(corpus_dir, tmp_path, out))
    assert code == 0, err
    assert f"Note: {DECLINED}" in stdout
    with pymupdf.open(out) as doc:
        assert not doc.needs_pass


@pytest.mark.parametrize("argv", [["--out-password", OUT_PW], [f"--out-password={OUT_PW}"]])
def test_cli_refuses_a_password_on_the_command_line(tmp_path: Path, argv: list[str]) -> None:
    code, stdout, err = _run(["--combine", "a.pdf", "b.pdf", *argv])
    assert code == 2
    assert "--out-password-env VAR or --out-password-stdin" in err
    assert OUT_PW not in stdout + err  # not echoed


@pytest.mark.parametrize("other", ["--password-stdin", "--passwords-stdin"])
def test_cli_out_password_stdin_cant_share_stdin(corpus_dir: Path, tmp_path: Path, other: str) -> None:
    out = tmp_path / "combined.pdf"
    argv = [*_combine_args(corpus_dir, tmp_path, out)[:-3], "--out-password-stdin", other]
    code, _, err = _run(argv, f"{PW}\n{PW}\n{OUT_PW}\n")
    assert code == 2
    assert f"--out-password-stdin can't be used with {other}" in err
    assert not out.exists()


def test_cli_out_password_is_only_for_combine(corpus_dir: Path, tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUT_PW", OUT_PW)
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    for extra in (["--batch", str(src), str(copy_to(src, tmp_path, "b.pdf"))], [str(src)]):
        code, _, err = _run(["--config", str(DEFAULT_CONFIG), *extra, "--set", f"identifier={FAKE_ID}",
                             "--out-password-env", "OUT_PW"])
        assert code == 2 and "only with --combine" in err


def test_cli_refuses_an_out_of_range_password_before_combining(corpus_dir: Path, tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRC_PW", PW)
    monkeypatch.setenv("OUT_PW", "pässwort")
    out = tmp_path / "combined.pdf"
    code, _, err = _run([*_combine_args(corpus_dir, tmp_path, out), "--out-password-env", "OUT_PW"])
    assert code == 6
    assert OUTPUT_PASSWORD_RULE in err and not out.exists()
