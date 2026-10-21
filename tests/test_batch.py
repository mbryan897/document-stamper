"""Batch stamping (D-097, D-107), the file-name pre-fill rule (D-106) and the command line (D-112)."""

from __future__ import annotations

import io
import signal
from pathlib import Path

import pymupdf
import pytest

from stamper.cli import ExitCode, batch_exit_code, main
from stamper.core.multi import agree_from_filenames, combined_original_name, disagreement_note
from stamper.handlers.batch import BatchItem, BatchOutcome, Status, check_batch, run_batch
from stamper.handlers.base import Cancelled, StampDoesNotFit
from stamper.handlers.pdf import PdfHandler
from tests.gui_support import copy_to
from tests.multi_support import make_tiny
from tests.stamping import DEFAULT_CONFIG, FAKE_ID, RECORDS_CONFIG, default_job, records_config, sha256
from tools import make_corpus

PW = make_corpus.USER_PASSWORD


def _five(corpus_dir: Path, folder: Path) -> list[BatchItem]:
    """Five files: one doesn't fit, one whose output name already exists."""
    locked = copy_to(corpus_dir / "encrypted_user_password.pdf", folder)
    restricted = copy_to(corpus_dir / "owner_restrictions.pdf", folder)
    plain = copy_to(corpus_dir / "rotate_90.pdf", folder)
    tiny = make_tiny(folder / "tiny.pdf")
    taken = copy_to(corpus_dir / "letter_portrait.pdf", folder)
    (folder / "taken_stamped.pdf").write_bytes(b"existing output - must not be replaced")
    return [
        BatchItem(locked, folder / "locked_stamped.pdf", PW),
        BatchItem(restricted, folder / "restricted_stamped.pdf"),
        BatchItem(plain, folder / "plain_stamped.pdf"),
        BatchItem(tiny, folder / "tiny_stamped.pdf"),
        BatchItem(taken, folder / "taken_stamped.pdf"),
    ]


def test_batch_saves_skips_and_fails(corpus_dir: Path, tmp_path: Path) -> None:
    items = _five(corpus_dir, tmp_path)
    before = {i.path: sha256(i.path) for i in items}
    existing = (tmp_path / "taken_stamped.pdf").read_bytes()
    handler = PdfHandler()
    checks = check_batch(handler, items, default_job())
    assert [c.status for c in checks] == [Status.OK, Status.OK, Status.OK, Status.FAILED, Status.SKIPPED]
    assert isinstance(checks[3].error, StampDoesNotFit)
    assert "already exists" in checks[4].message

    pages: list[tuple[int, int, int]] = []
    outcomes = run_batch(handler, checks, default_job(), accept_restrictions=True,
                         progress=lambda d, t, i: pages.append((d, t, i)))
    assert [o.status for o in outcomes] == [Status.SAVED, Status.SAVED, Status.SAVED, Status.FAILED, Status.SKIPPED]
    assert (tmp_path / "taken_stamped.pdf").read_bytes() == existing  # never overwritten (D-075)
    assert not (tmp_path / "tiny_stamped.pdf").exists()
    for path, digest in before.items():
        assert sha256(path) == digest
    # One progress count across all files.
    assert pages[-1][0] == pages[-1][1] == 2 + 2 + 3

    # Each copy keeps its original's protection (D-050).
    with pymupdf.open(tmp_path / "locked_stamped.pdf") as doc:
        assert doc.needs_pass and doc.authenticate(PW)
    with pymupdf.open(tmp_path / "restricted_stamped.pdf") as doc, \
            pymupdf.open(items[1].path) as orig:
        assert doc.metadata["encryption"] and doc.permissions == orig.permissions
    with pymupdf.open(tmp_path / "plain_stamped.pdf") as doc:
        assert not doc.metadata["encryption"]
        assert f"{FAKE_ID}  |  Report  |  Page 3 of 3" in doc[2].get_text()  # each file numbered on its own
    assert batch_exit_code(outcomes) == ExitCode.PARTIAL


def test_two_files_saving_to_one_name(corpus_dir: Path, tmp_path: Path) -> None:
    a = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    b = copy_to(corpus_dir / "rotate_90.pdf", tmp_path)
    same = tmp_path / "out.pdf"
    checks = check_batch(PdfHandler(), [BatchItem(a, same), BatchItem(b, same)], default_job())
    assert [c.status for c in checks] == [Status.OK, Status.SKIPPED]
    over = check_batch(PdfHandler(), [BatchItem(a, b), BatchItem(b, tmp_path / "x.pdf")], default_job())
    assert over[0].status == Status.FAILED and "one of the originals" in over[0].message


def test_a_failed_file_doesnt_take_its_name(corpus_dir: Path, tmp_path: Path) -> None:
    # The first file can't be stamped, so the second may use the name they share.
    tiny = make_tiny(tmp_path / "tiny.pdf")
    a = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    same = tmp_path / "out.pdf"
    checks = check_batch(PdfHandler(), [BatchItem(tiny, same), BatchItem(a, same)], default_job())
    assert [c.status for c in checks] == [Status.FAILED, Status.OK]


def test_cancel_mid_batch_keeps_saved_copies(corpus_dir: Path, tmp_path: Path) -> None:
    items = [BatchItem(copy_to(corpus_dir / n, tmp_path), tmp_path / f"out_{n}")
             for n in ("letter_portrait.pdf", "rotate_mixed.pdf", "mixed_sizes.pdf")]
    handler = PdfHandler()
    checks = check_batch(handler, items, default_job())
    state = {"stop": False}

    def progress(_d: int, _t: int, index: int) -> None:
        if index == 1:
            state["stop"] = True  # cancel while the second file is being stamped

    outcomes = run_batch(handler, checks, default_job(), progress=progress, cancel=lambda: state["stop"])
    assert [o.status for o in outcomes] == [Status.SAVED, Status.CANCELLED, Status.NOT_STARTED]
    assert (tmp_path / "out_letter_portrait.pdf").is_file()
    assert not (tmp_path / "out_rotate_mixed.pdf").exists()
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("~stamper")) == []
    with pytest.raises(Cancelled):
        check_batch(handler, items, default_job(), cancel=lambda: True)


def test_exit_code_rule() -> None:
    item = BatchItem(Path("a.pdf"), Path("b.pdf"))
    saved = BatchOutcome(item, Status.SAVED)
    skipped = BatchOutcome(item, Status.SKIPPED)
    fit = BatchOutcome(item, Status.FAILED, error=StampDoesNotFit("x", 1, 1))
    assert batch_exit_code([saved, saved]) == ExitCode.OK
    assert batch_exit_code([saved, fit]) == ExitCode.PARTIAL
    assert batch_exit_code([skipped, skipped]) == ExitCode.OUTPUT_REFUSED
    assert batch_exit_code([fit]) == ExitCode.DOES_NOT_FIT
    assert batch_exit_code([fit, skipped]) == ExitCode.ERROR


# ------------------------------------------------------------ identifier from file names (D-106)


def test_identifier_agreeing_names() -> None:
    cfg = records_config()
    a = agree_from_filenames(cfg, "identifier", ["return_99-2026-999901.pdf", "99-2026-999901 phone.pdf",
                                                 "cover letter.pdf"])
    assert a.value == "99-2026-999901" and not a.conflict
    assert disagreement_note("Case number", a) == ""


def test_identifier_disagreeing_names() -> None:
    cfg = records_config()
    a = agree_from_filenames(cfg, "identifier", ["return_99-2026-999901.pdf", "return_99-2026-999903.pdf"])
    assert a.value is None and a.conflict
    note = disagreement_note("Case number", a)
    assert "return_99-2026-999901.pdf → 99-2026-999901" in note
    assert "return_99-2026-999903.pdf → 99-2026-999903" in note


def test_identifier_no_names_have_one() -> None:
    a = agree_from_filenames(records_config(), "identifier", ["a.pdf", "b.pdf"])
    assert a.value is None and not a.conflict and a.found == ()


def test_combined_original_name() -> None:
    assert combined_original_name(Path("D:/Returns/Return A.pdf")) == "Return A_combined.pdf"


# ------------------------------------------------------------ command line (D-112)


class Run:
    """One CLI run."""

    def __init__(self, argv: list[str]) -> None:
        self.out, self.err = io.StringIO(), io.StringIO()
        self.code = main(argv, stdout=self.out, stderr=self.err, stdin=io.StringIO(""))
        self.stdout, self.stderr = self.out.getvalue(), self.err.getvalue()


BASE = ["--config", str(DEFAULT_CONFIG), "--set", f"identifier={FAKE_ID}", "--set", "doc_type=Report"]


def test_cli_several_files_without_a_mode(corpus_dir: Path) -> None:
    r = Run([str(corpus_dir / "letter_portrait.pdf"), str(corpus_dir / "rotate_90.pdf"), *BASE])
    assert r.code == ExitCode.USAGE
    assert "--combine" in r.stderr and "--batch" in r.stderr


def test_cli_combine_in_argument_order(corpus_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    b = copy_to(corpus_dir / "rotate_90.pdf", tmp_path)
    a = copy_to(corpus_dir / "encrypted_user_password.pdf", tmp_path)
    out = tmp_path / "exhibit.pdf"
    monkeypatch.setenv("STAMPER_TEST_PW_A", PW)
    r = Run(["--combine", str(b), str(a), "--out", str(out),
             "--password-env-for", str(a), "STAMPER_TEST_PW_A", *BASE])
    assert r.code == ExitCode.OK, r.stderr
    assert "Protected files in it: encrypted_user_password.pdf" in r.stdout
    with pymupdf.open(out) as doc:
        assert [t[1] for t in doc.get_toc() if t[0] == 1] == ["rotate_90.pdf", "encrypted_user_password.pdf"]
        assert "Page 5 of 5" in doc[4].get_text()
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("~")] == []


def test_cli_combine_needs_password_and_confirmation(corpus_dir: Path, tmp_path: Path) -> None:
    from tests.stamping import make_signed_pdf

    a = copy_to(corpus_dir / "encrypted_user_password.pdf", tmp_path)
    b = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    r = Run(["--combine", str(a), str(b), *BASE])
    assert r.code == ExitCode.PASSWORD and "--password-env-for" in r.stderr and "--passwords-stdin" in r.stderr
    signed = make_signed_pdf(corpus_dir, tmp_path / "signed.pdf")
    r = Run(["--combine", str(b), str(signed), *BASE])
    assert r.code == ExitCode.CONFIRM and "signed.pdf" in r.stderr and "--accept-signature-loss" in r.stderr


def test_cli_batch_partial(corpus_dir: Path, tmp_path: Path) -> None:
    a = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    tiny = make_tiny(tmp_path / "tiny.pdf")
    outdir = tmp_path / "out"
    outdir.mkdir()
    r = Run(["--batch", str(a), str(tiny), "--out-dir", str(outdir), *BASE])
    assert r.code == ExitCode.PARTIAL, r.stderr
    assert "Failed: tiny.pdf" in r.stderr and "1 of 2 files saved." in r.stdout
    assert len(list(outdir.iterdir())) == 1
    again = Run(["--batch", str(a), str(tiny), "--out-dir", str(outdir), *BASE])
    assert again.code == ExitCode.ERROR  # one skipped (exists), one failed: different reasons
    assert "Skipped: letter_portrait.pdf" in again.stderr


def test_cli_batch_ctrl_c_reports_the_saved_copies(corpus_dir: Path, tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    files = [copy_to(corpus_dir / "letter_portrait.pdf", tmp_path, f"f{i}.pdf") for i in range(3)]
    outdir = tmp_path / "out"
    outdir.mkdir()
    real_apply = PdfHandler.apply

    def apply_then_ctrl_c(self: PdfHandler, *a: object, **kw: object) -> object:
        result = real_apply(self, *a, **kw)  # type: ignore[arg-type]
        signal.raise_signal(signal.SIGINT)  # the user presses Ctrl+C after the first copy is saved
        return result

    monkeypatch.setattr(PdfHandler, "apply", apply_then_ctrl_c)
    r = Run(["--batch", *map(str, files), "--out-dir", str(outdir), *BASE])
    assert r.code == ExitCode.PARTIAL, r.stderr
    assert "Nothing was saved" not in r.stderr
    assert "Saved: " in r.stdout and "1 of 3 files saved." in r.stdout
    assert r.stderr.count("Not started: ") == 2
    assert len(list(outdir.iterdir())) == 1
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler  # put back afterwards


def test_cli_batch_second_ctrl_c_stops_at_once(corpus_dir: Path, tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    files = [copy_to(corpus_dir / "letter_portrait.pdf", tmp_path, f"f{i}.pdf") for i in range(2)]
    outdir = tmp_path / "out"
    outdir.mkdir()
    real_apply = PdfHandler.apply

    def apply_then_two_ctrl_c(self: PdfHandler, *a: object, **kw: object) -> object:
        result = real_apply(self, *a, **kw)  # type: ignore[arg-type]
        signal.raise_signal(signal.SIGINT)
        signal.raise_signal(signal.SIGINT)  # pressed again: don't wait for the next page
        return result

    monkeypatch.setattr(PdfHandler, "apply", apply_then_two_ctrl_c)
    r = Run(["--batch", *map(str, files), "--out-dir", str(outdir), *BASE])
    assert r.code == ExitCode.INTERRUPTED, r.stderr
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


@pytest.mark.parametrize("which", [0, 1])
def test_cli_combine_never_saves_over_an_original(corpus_dir: Path, tmp_path: Path, which: int) -> None:
    a = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    b = copy_to(corpus_dir / "rotate_90.pdf", tmp_path)
    before = {p: sha256(p) for p in (a, b)}
    target = (a, b)[which]
    r = Run(["--combine", str(a), str(b), "--out", str(target), "--overwrite", *BASE])
    assert r.code == ExitCode.OUTPUT_REFUSED, r.stderr
    assert "one of the originals" in r.stderr
    assert {p: sha256(p) for p in (a, b)} == before


def test_cli_batch_refuses_out(corpus_dir: Path, tmp_path: Path) -> None:
    r = Run(["--batch", str(corpus_dir / "letter_portrait.pdf"), str(corpus_dir / "rotate_90.pdf"),
             "--out", str(tmp_path / "x.pdf"), *BASE])
    assert r.code == ExitCode.USAGE and "--out-dir" in r.stderr


def test_cli_identifier_conflict_named(tmp_path: Path, corpus_dir: Path) -> None:
    a = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path, "return_99-2026-999901.pdf")
    b = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path, "return_99-2026-999903.pdf")
    r = Run(["--batch", str(a), str(b), "--config", str(RECORDS_CONFIG), "--set", "doc_type=Court order",
             "--dry-run"])
    assert r.code == ExitCode.INVALID_FIELDS
    assert "return_99-2026-999903.pdf → 99-2026-999903" in r.stdout
    agree = Run(["--batch", str(a), str(a.with_name("return_99-2026-999901.pdf")), "--config", str(RECORDS_CONFIG),
                 "--set", "doc_type=Court order", "--dry-run"])
    assert agree.code == ExitCode.USAGE  # the same file twice
