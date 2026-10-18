"""Command line: ``stamper INPUT.pdf --set identifier=... [options]``.

Several files need a mode (D-112): ``stamper --combine A.pdf B.pdf --out X.pdf``
makes one stamped file from them in the order given; ``stamper --batch A.pdf
B.pdf --out-dir D`` stamps each one separately. Without a mode, several files
are refused with exit code 2, as before.

Every argument works the same in PowerShell and cmd: no commas, braces or
``@`` in the syntax. Quote a whole ``--set`` argument when its value has
spaces: ``--set "doc_type=Search warrant return"``.

The command never waits for a yes/no answer. A signed or restricted file
stops with exit code 7 and names the flag that confirms it, so scripts can't
hang. The only prompt is a hidden password prompt, and only when a password
is needed, none was supplied and the command runs in a console.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Callable, Sequence
from datetime import date
from enum import IntEnum
from pathlib import Path
from typing import TextIO

from .core import pending
from .core.config import Config, ConfigError, Profile, load_config
from .core.fields import prefill_fields, validate_field, value_from_filename
from .core.filenames import suggest_filename, visible
from .core.locations import builtin_config_path, find_config
from .core.multi import agree_from_filenames, combined_original_name, disagreement_note
from .core.patterns import parse_date_format
from .handlers.base import (
    Cancelled,
    ConfirmationRequired,
    DocumentInfo,
    MissingGlyph,
    OutputRefused,
    PasswordRequired,
    SourceChanged,
    SourceFingerprint,
    StampDoesNotFit,
    StampError,
    StampJob,
    UnsupportedDocument,
    WrongPassword,
)
from .handlers.batch import BatchItem, BatchOutcome, Status, check_batch, run_batch
from .handlers.combine import CombineInput, combine_pdfs
from .handlers.pdf import PdfHandler, same_file

__all__ = [
    "ExitCode", "batch_exit_code", "build_parser", "default_config_path", "get_version",
    "located_config_path", "main", "several_files_usage", "REMOVED_PASSWORD_FOR",
]

DIST_NAME = "document-stamper"


class ExitCode(IntEnum):
    """Process exit codes."""

    OK = 0
    ERROR = 1
    USAGE = 2
    CONFIG = 3
    INVALID_FIELDS = 4
    UNSUPPORTED = 5
    PASSWORD = 6
    CONFIRM = 7
    OUTPUT_REFUSED = 8
    DOES_NOT_FIT = 9
    PARTIAL = 10  # a batch saved some files but not all (D-112)
    INTERRUPTED = 130


def get_version() -> str:
    """The installed package version, or a marker if not installed."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(DIST_NAME)
    except PackageNotFoundError:
        return "0+unknown (not installed; run: python -m pip install -e .)"


def default_config_path() -> Path:
    """The shipped generic config (``config/default.json``), the last step of the lookup."""
    return builtin_config_path()


def located_config_path() -> Path:
    """The config used when ``--config`` isn't given: next to the app, then ``%APPDATA%``, then the default (D-080)."""
    return find_config().path


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        sys.stderr.write(f"Error: {message}\n")
        sys.exit(ExitCode.USAGE)


def build_parser() -> argparse.ArgumentParser:
    """The argument parser (also used for the no-argument usage message)."""
    p = _Parser(
        prog="stamper",
        description="Stamp every page of a PDF and save a new copy. The original is never changed.",
        epilog=(
            "Example:\n"
            '  stamper "D:\\Returns\\provider return.pdf" --set identifier=SO261017-999902 '
            '--set "doc_type=Search warrant return"\n\n'
            "Exit codes: 0 saved, 1 unexpected error, 2 bad arguments, 3 config problem, "
            "4 field values invalid, 5 file can't be stamped (or changed while open), 6 password missing or wrong, "
            "7 confirmation flag needed, 8 output refused, 9 stamp doesn't fit, "
            "10 a batch saved some files but not all.\n\n"
            "Several files:\n"
            "  stamper --combine A.pdf B.pdf C.pdf --out exhibit.pdf --set identifier=...\n"
            "    one stamped file, pages in the order the files are given\n"
            "  stamper --batch A.pdf B.pdf --out-dir D:\\Stamped --set identifier=...\n"
            "    one stamped copy of each file\n"
            "  --password-env-for FILE VAR reads one file's password from an environment variable\n"
            "    (repeat for each file); --passwords-stdin reads one line per file, in the order\n"
            "    the files are given (an empty line for a file without a password).\n"
            "  Passwords are never taken on the command line itself."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("input", type=Path, nargs="+", help="the PDF to stamp (several with --combine or --batch)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--combine", action="store_true",
                      help="combine several PDFs, in the order given, into one stamped file")
    mode.add_argument("--batch", action="store_true",
                      help="stamp each of several PDFs, saving one stamped copy of each")
    p.add_argument("--set", dest="sets", action="append", default=[], metavar="NAME=VALUE",
                   help="a field value, e.g. identifier=99-2026-999901 (repeat for each field)")
    p.add_argument("--profile", help="profile name (default: the config's default profile)")
    p.add_argument("--config", type=Path,
                   help="config.json to use (default: next to the app, then %%APPDATA%%\\DocumentStamper, then the built-in one)")
    out = p.add_mutually_exclusive_group()
    out.add_argument("--out", type=Path, help="full path of the stamped copy")
    out.add_argument("--out-dir", type=Path,
                     help="folder for the stamped copy, with the suggested filename (default: the input's folder)")
    p.add_argument("--name-field", dest="name_fields", action="append", metavar="FIELD",
                   help="field to put in the suggested filename, in order (repeat; default from the profile)")
    pw = p.add_mutually_exclusive_group()
    pw.add_argument("--password-env", metavar="VAR",
                    help="read the PDF password from this environment variable")
    pw.add_argument("--password-stdin", action="store_true",
                    help="read the PDF password from the first line of standard input")
    pw.add_argument("--passwords-stdin", action="store_true",
                    help="with --combine or --batch: read one password per line from standard input, "
                         "in the order the files are given (empty line: no password)")
    p.add_argument("--password-env-for", dest="password_env_for", nargs=2, action="append", default=[],
                   metavar=("FILE", "VAR"),
                   help="with --combine or --batch: read one file's password from this environment "
                        "variable (repeat for each file)")
    p.add_argument("--no-box", action="store_true", help="don't draw the white box behind the stamp")
    p.add_argument("--accept-signature-loss", action="store_true",
                   help="stamp a digitally signed file; the copy's signature won't validate")
    p.add_argument("--accept-restrictions", action="store_true",
                   help="stamp a file whose owner restrictions don't allow changes")
    p.add_argument("--remove-encryption", action="store_true",
                   help="save the copy without the original's password and permissions")
    p.add_argument("--overwrite", action="store_true", help="replace the output file if it exists")
    p.add_argument("--dry-run", action="store_true",
                   help="check everything and show the output name without saving")
    p.add_argument("--quiet", action="store_true", help="print only errors")
    p.add_argument("--version", action="version", version=f"Document Stamper {get_version()}")
    return p


Say = Callable[[str], None]


class _Fail(Exception):
    def __init__(self, code: ExitCode, message: str) -> None:
        self.code = code
        super().__init__(message)


def _parse_sets(sets: Sequence[str], profile: Profile, config: Config) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in sets:
        name, sep, value = item.partition("=")
        name = name.strip()
        if not sep or not name:
            raise _Fail(ExitCode.USAGE, f'--set needs NAME=VALUE, not "{item}".')
        if name not in profile.fields:
            known = ", ".join(f"{f} ({config.fields[f].label})" for f in profile.fields)
            raise _Fail(ExitCode.USAGE,
                        f'"{name}" isn\'t a field in profile "{profile.name}". Fields: {known}.')
        if name in values:
            raise _Fail(ExitCode.USAGE, f"{name} is set more than once.")
        values[name] = value
    return values


def _get_password(args: argparse.Namespace, stdin: TextIO) -> str | None:
    if args.password_env:
        value = os.environ.get(args.password_env)
        if value is None:
            raise _Fail(ExitCode.PASSWORD,
                        f"The environment variable {args.password_env} isn't set.")
        return value
    if args.password_stdin:
        return stdin.readline().rstrip("\r\n")
    return None


def _run(args: argparse.Namespace, out: TextIO, stdin: TextIO, err: TextIO | None = None) -> ExitCode:
    say = (lambda msg: None) if args.quiet else (lambda msg: out.write(msg + "\n"))

    # Partial copies from a stopped run (D-124): told, never deleted from here.
    left = pending.leftovers()
    if left:
        what = "it" if len(left) == 1 else "them"
        say(f"Note: {pending.describe(left)} Open the Document Stamper window to delete or keep {what}.")

    # Config and profile
    config_path = args.config or located_config_path()
    try:
        config = load_config(config_path)
    except ConfigError as e:
        raise _Fail(ExitCode.CONFIG, str(e)) from None
    if args.profile:
        try:
            profile = config.profile(args.profile)
        except KeyError:
            names = ", ".join(f'"{p.name}"' for p in config.profiles)
            raise _Fail(ExitCode.USAGE, f'No profile named "{args.profile}". Profiles: {names}.') from None
    else:
        profile = config.default_profile

    raw = _parse_sets(args.sets, profile, config)

    if args.combine or args.batch:
        return _run_multi(args, config, profile, raw, say, stdin, err or sys.stderr)
    if len(args.input) > 1:
        raise _Fail(ExitCode.USAGE, several_files_usage(len(args.input)))
    if args.password_env_for or args.passwords_stdin:
        flag = "--password-env-for" if args.password_env_for else "--passwords-stdin"
        raise _Fail(ExitCode.USAGE,
                    f"{flag} is for --combine and --batch. For one file use --password-env or --password-stdin.")

    src: Path = args.input[0]
    handler = PdfHandler()
    if not src.is_file():
        raise _Fail(ExitCode.UNSUPPORTED, f"Can't find the file {src}.")
    if not handler.can_open(src):
        raise _Fail(ExitCode.UNSUPPORTED, f"{src.name} isn't a PDF file.")

    # Pre-fill from the filename
    for name in profile.fields:
        if name not in raw:
            found = value_from_filename(config, name, src.name)
            if found is not None:
                raw[name] = found
                say(f"{config.fields[name].label} taken from the filename: {found}")

    # Validate every field, report every failure
    values: dict[str, str] = {}
    failures: list[str] = []
    for name in profile.fields:
        result = validate_field(config, name, raw.get(name))
        if result.valid:
            values[name] = result.value
        else:
            hint = f" (use --set {name}=...)" if name not in raw else ""
            failures.append(f"{result.message}{hint}")
    if failures:
        raise _Fail(ExitCode.INVALID_FIELDS,
                    "Can't stamp until these are fixed:\n" + "\n".join(f"  - {f}" for f in failures))

    # Password
    password = _get_password(args, stdin)
    try:
        info = handler.inspect(src, password)
    except PasswordRequired as e:
        if password is None and stdin.isatty():
            password = getpass.getpass(f"Password for {src.name}: ")
            info = handler.inspect(src, password)
        else:
            raise _Fail(ExitCode.PASSWORD,
                        f"{e} Give it with --password-env VAR or --password-stdin.") from None

    # Output path
    today = date.today()
    ticked = args.name_fields if args.name_fields else None
    if ticked:
        allowed = [*profile.fields, "date"]
        bad = [f for f in ticked if f not in allowed]
        if bad:
            raise _Fail(ExitCode.USAGE,
                        f"--name-field {bad[0]} isn't available. Choose from: {', '.join(allowed)}.")
    if args.out:
        out_path: Path = args.out
    else:
        name_values = {**values, "date": parse_date_format(config.settings.date_format).format(today)}
        folder: Path = args.out_dir or src.parent
        out_path = folder / suggest_filename(
            profile, name_values, src.name, ticked=ticked, dedupe=prefill_fields(config, profile))

    job = StampJob.from_profile(
        config, profile, values,
        background_box=False if args.no_box else None,
        today=today,
    )

    if args.dry_run:
        say(f"Would save: {out_path}")
        if info.signed and not args.accept_signature_loss:
            say("Needs --accept-signature-loss: the file is digitally signed.")
        if info.restricted and not args.accept_restrictions:
            say("Needs --accept-restrictions: the file's owner restrictions don't allow changes.")
        if out_path.exists() and not args.overwrite:
            say("Needs --overwrite or another name: the output file already exists.")
        return ExitCode.OK

    result = handler.apply(
        src, job, out_path,
        password=password,
        accept_signature_loss=args.accept_signature_loss,
        accept_restrictions=args.accept_restrictions,
        remove_encryption=args.remove_encryption,
        overwrite=args.overwrite,
        expect=info.fingerprint,
    )
    for w in result.warnings:
        say(f"Note: {w}")
    say(f"Saved: {result.output_path}")
    return ExitCode.OK


# ------------------------------------------------------------------ several files (D-112)


def several_files_usage(count: int) -> str:
    """The refusal for several files without a mode (D-063, now naming the two modes)."""
    return (f"{count} files were given. Add --combine to make one stamped file from them "
            "(pages in the order given) or --batch to stamp each one separately.")


def _validate_all(config: Config, profile: Profile, raw: dict[str, str]) -> dict[str, str]:
    values: dict[str, str] = {}
    failures: list[str] = []
    for name in profile.fields:
        result = validate_field(config, name, raw.get(name))
        if result.valid:
            values[name] = result.value
        else:
            hint = f" (use --set {name}=...)" if name not in raw else ""
            failures.append(f"{result.message}{hint}")
    if failures:
        raise _Fail(ExitCode.INVALID_FIELDS,
                    "Can't stamp until these are fixed:\n" + "\n".join(f"  - {f}" for f in failures))
    return values


def _multi_inputs(paths: Sequence[Path], handler: PdfHandler) -> list[Path]:
    if len(paths) < 2:
        raise _Fail(ExitCode.USAGE, "--combine and --batch need at least two files.")
    for p in paths:
        if not p.is_file():
            raise _Fail(ExitCode.UNSUPPORTED, f"Can't find the file {p}.")
        if not handler.can_open(p):
            raise _Fail(ExitCode.UNSUPPORTED, f"{p.name} isn't a PDF file.")
    for i, p in enumerate(paths):
        for q in paths[:i]:
            if same_file(p, q):
                raise _Fail(ExitCode.USAGE, f"{p.name} is given more than once.")
    return list(paths)


def _match_input(given: Path, paths: Sequence[Path], flag: str) -> Path:
    """The one input ``given`` names: the same file, or a bare name matching exactly one input (D-126)."""
    same = [p for p in paths if same_file(given, p)]
    if len(same) == 1:
        return same[0]
    if given.parent == Path("."):
        named = [p for p in paths if p.name.casefold() == given.name.casefold()]
        if len(named) == 1:
            return named[0]
        if len(named) > 1:
            raise _Fail(ExitCode.USAGE,
                        f"{flag} {given}: {len(named)} of the files are named {given.name}. "
                        "Give the full path of the one you mean.")
    raise _Fail(ExitCode.USAGE, f"{flag} {given}: that file isn't one of the inputs.")


def _given_passwords(args: argparse.Namespace, paths: Sequence[Path], stdin: TextIO) -> dict[Path, str | None]:
    """Per-file passwords from --password-env-for or --passwords-stdin (D-126); never from argv."""
    given: dict[Path, str | None] = {}
    if args.passwords_stdin:
        if args.password_env_for:
            raise _Fail(ExitCode.USAGE, "Use either --passwords-stdin or --password-env-for, not both.")
        for p in paths:
            line = stdin.readline().rstrip("\r\n")
            given[p] = line or None
        return given
    for f, var in args.password_env_for:
        target = _match_input(Path(f), paths, "--password-env-for")
        if target in given:
            raise _Fail(ExitCode.USAGE, f"--password-env-for names {target.name} more than once.")
        value = os.environ.get(var)
        if value is None:
            raise _Fail(ExitCode.PASSWORD, f"The environment variable {var} (for {target.name}) isn't set.")
        given[target] = value
    return given


def _multi_passwords(
    args: argparse.Namespace, paths: Sequence[Path], handler: PdfHandler, stdin: TextIO,
) -> tuple[dict[Path, str | None], dict[Path, DocumentInfo]]:
    """Each file's password and DocumentInfo; asks in a console, else names the flags."""
    given = _given_passwords(args, paths, stdin)
    shared = _get_password(args, stdin)
    passwords: dict[Path, str | None] = {}
    infos: dict[Path, DocumentInfo] = {}
    for p in paths:
        pw = given[p] if p in given else shared
        try:
            infos[p] = handler.inspect(p, pw)
        except PasswordRequired:
            if pw is None and stdin.isatty() and not args.passwords_stdin:
                pw = getpass.getpass(f"Password for {p.name}: ")
                infos[p] = handler.inspect(p, pw)
            else:
                raise _Fail(ExitCode.PASSWORD,
                            f"{p.name} needs a password to open. Give it with --password-env-for "
                            f'"{p}" VAR or --passwords-stdin.') from None
        passwords[p] = pw
    return passwords, infos


def batch_exit_code(outcomes: Sequence[BatchOutcome]) -> ExitCode:
    """0 when every file was saved, 10 when some were, else the shared reason's code (or 1)."""
    saved = sum(1 for o in outcomes if o.status == Status.SAVED)
    if saved == len(outcomes):
        return ExitCode.OK
    if saved:
        return ExitCode.PARTIAL
    codes: set[ExitCode] = set()
    for o in outcomes:
        if o.status == Status.SKIPPED:
            codes.add(ExitCode.OUTPUT_REFUSED)
        elif o.status in (Status.CANCELLED, Status.NOT_STARTED):
            codes.add(ExitCode.INTERRUPTED)
        else:
            code = next((c for kind, c in _ERROR_CODES if isinstance(o.error, kind)), ExitCode.ERROR)
            codes.add(code)
    return codes.pop() if len(codes) == 1 else ExitCode.ERROR


def _run_multi(
    args: argparse.Namespace, config: Config, profile: Profile, raw: dict[str, str],
    say: Say, stdin: TextIO, err: TextIO,
) -> ExitCode:
    handler = PdfHandler()
    paths = _multi_inputs(args.input, handler)
    if args.batch and args.out:
        raise _Fail(ExitCode.USAGE, "--batch saves one copy per file: use --out-dir for the folder, not --out.")

    # Pre-fill: only when every file name that has a value agrees (D-106).
    for name in profile.fields:
        if name in raw:
            continue
        agreement = agree_from_filenames(config, name, [p.name for p in paths])
        if agreement.value is not None:
            raw[name] = agreement.value
            say(f"{config.fields[name].label} taken from the file names: {agreement.value}")
        elif agreement.conflict:
            say(f"Note: {disagreement_note(config.fields[name].label, agreement)}")
    values = _validate_all(config, profile, raw)

    passwords, infos = _multi_passwords(args, paths, handler, stdin)
    for p in paths:
        if infos[p].xfa:
            raise _Fail(ExitCode.UNSUPPORTED,
                        f"{p.name} is an XFA form, which this tool can't stamp. "
                        "Print it to PDF first, then stamp the printed copy.")
    signed = [p.name for p in paths if infos[p].signed]
    restricted = [p.name for p in paths if infos[p].restricted]

    today = date.today()
    ticked = args.name_fields if args.name_fields else None
    if ticked:
        allowed = [*profile.fields, "date"]
        bad = [f for f in ticked if f not in allowed]
        if bad:
            raise _Fail(ExitCode.USAGE,
                        f"--name-field {bad[0]} isn't available. Choose from: {', '.join(allowed)}.")
    name_values = {**values, "date": parse_date_format(config.settings.date_format).format(today)}
    dedupe = prefill_fields(config, profile)
    job = StampJob.from_profile(config, profile, values,
                                background_box=False if args.no_box else None, today=today)

    seen = {p: infos[p].fingerprint for p in paths}
    if args.combine:
        return _run_combine(args, handler, paths, passwords, seen, job, name_values, ticked, dedupe,
                            profile, signed, restricted, say)
    return _run_batch(args, handler, paths, passwords, seen, job, name_values, ticked, dedupe,
                      profile, signed, restricted, say, err)


def _confirmations(args: argparse.Namespace, signed: list[str], restricted: list[str], what: str) -> None:
    if signed and not args.accept_signature_loss:
        raise _Fail(ExitCode.CONFIRM,
                    f"These files are digitally signed: {', '.join(signed)}. {what} won't carry a valid "
                    "signature; the originals keep theirs. Add --accept-signature-loss to go ahead.")
    if restricted and not args.accept_restrictions:
        raise _Fail(ExitCode.CONFIRM,
                    f"These files have owner restrictions that don't allow changes: {', '.join(restricted)}. "
                    "Add --accept-restrictions to go ahead.")


def _run_combine(
    args: argparse.Namespace, handler: PdfHandler, paths: list[Path], passwords: dict[Path, str | None],
    seen: dict[Path, SourceFingerprint | None], job: StampJob, name_values: dict[str, str], ticked: list[str] | None, dedupe: list[str],
    profile: Profile, signed: list[str], restricted: list[str], say: Say,
) -> ExitCode:
    first = paths[0]
    if args.out:
        out_path: Path = args.out
    else:
        folder: Path = args.out_dir or first.parent
        out_path = folder / suggest_filename(profile, name_values, combined_original_name(first),
                                             ticked=ticked, dedupe=dedupe)
    for p in paths:
        if same_file(p, out_path):
            raise _Fail(ExitCode.OUTPUT_REFUSED,
                        f"The combined file can't be saved over one of the originals ({p.name}). "
                        "Choose a different name or folder.")
    if args.dry_run:
        say(f"Would save: {out_path}")
        say("Pages in this order: " + ", ".join(p.name for p in paths))
        if signed and not args.accept_signature_loss:
            say("Needs --accept-signature-loss: " + ", ".join(signed))
        if restricted and not args.accept_restrictions:
            say("Needs --accept-restrictions: " + ", ".join(restricted))
        if out_path.exists() and not args.overwrite:
            say("Needs --overwrite or another name: the output file already exists.")
        return ExitCode.OK
    if out_path.exists() and not args.overwrite:
        raise _Fail(ExitCode.OUTPUT_REFUSED, f"{out_path} already exists. Choose another name, or allow overwriting.")
    if not out_path.parent.is_dir():
        raise _Fail(ExitCode.OUTPUT_REFUSED, f"The folder {out_path.parent} doesn't exist.")
    _confirmations(args, signed, restricted, "The combined file")

    combined = combine_pdfs(
        [CombineInput(p, passwords[p], seen[p]) for p in paths], out_path.parent,
        accept_signature_loss=args.accept_signature_loss,
        accept_restrictions=args.accept_restrictions,
    )
    try:
        result = handler.apply(combined.path, job, out_path,
                               accept_signature_loss=True,  # confirmed above for every signed source
                               overwrite=args.overwrite)
    finally:
        combined.cleanup()
    for note in combined.notes:
        say(f"Note: {note}")
    if combined.protection_warning():
        say(f"Note: {combined.protection_warning()}")
    for w in result.warnings:
        say(f"Note: {w}")
    say(f"Saved: {result.output_path} ({result.pages_stamped} pages from {len(paths)} files)")
    return ExitCode.OK


def _run_batch(
    args: argparse.Namespace, handler: PdfHandler, paths: list[Path], passwords: dict[Path, str | None],
    seen: dict[Path, SourceFingerprint | None], job: StampJob, name_values: dict[str, str], ticked: list[str] | None, dedupe: list[str],
    profile: Profile, signed: list[str], restricted: list[str], say: Say, err: TextIO,
) -> ExitCode:
    items = [
        BatchItem(p, (args.out_dir or p.parent) / suggest_filename(
            profile, name_values, p.name, ticked=ticked, dedupe=dedupe), passwords[p], seen[p])
        for p in paths
    ]
    if not args.dry_run:
        _confirmations(args, signed, restricted, "The stamped copies")
    checks = check_batch(handler, items, job, overwrite=args.overwrite)
    if args.dry_run:
        for c in checks:
            if c.ok:
                say(f"Would save: {c.item.out_path}")
            else:
                say(f"Would {'skip' if c.status == Status.SKIPPED else 'fail'}: {c.item.path.name}: {c.message}")
        if signed and not args.accept_signature_loss:
            say("Needs --accept-signature-loss: " + ", ".join(signed))
        if restricted and not args.accept_restrictions:
            say("Needs --accept-restrictions: " + ", ".join(restricted))
        return ExitCode.OK
    outcomes = run_batch(
        handler, checks, job,
        accept_signature_loss=args.accept_signature_loss,
        accept_restrictions=args.accept_restrictions,
        remove_encryption=args.remove_encryption,
        overwrite=args.overwrite,
    )
    for o in outcomes:
        if o.status == Status.SAVED and o.result is not None:
            for w in o.result.warnings:
                say(f"Note ({o.item.path.name}): {w}")
            say(f"Saved: {o.result.output_path}")
        else:
            err.write(f"{o.status.capitalize()}: {o.item.path.name}: {o.message}\n")
    saved = sum(1 for o in outcomes if o.status == Status.SAVED)
    say(f"{saved} of {len(outcomes)} files saved.")
    return batch_exit_code(outcomes)


_ERROR_CODES: tuple[tuple[type[StampError], ExitCode], ...] = (
    (PasswordRequired, ExitCode.PASSWORD),
    (WrongPassword, ExitCode.PASSWORD),
    (ConfirmationRequired, ExitCode.CONFIRM),
    (OutputRefused, ExitCode.OUTPUT_REFUSED),
    (StampDoesNotFit, ExitCode.DOES_NOT_FIT),
    (MissingGlyph, ExitCode.DOES_NOT_FIT),
    (UnsupportedDocument, ExitCode.UNSUPPORTED),
    (SourceChanged, ExitCode.UNSUPPORTED),
    (Cancelled, ExitCode.INTERRUPTED),
)

_CONFIRM_FLAGS = {"signature": "--accept-signature-loss", "restrictions": "--accept-restrictions"}

REMOVED_PASSWORD_FOR = (
    "--password-for was removed: a password typed on the command line is kept in PowerShell "
    "history, the process list and Windows event logs. Use --password-env-for FILE VAR "
    "(repeat for each file) or --passwords-stdin (one line per file, in the order given)."
)


def _removed_flag(argv: Sequence[str]) -> str | None:
    """The refusal for a removed flag, checked before parsing so the password isn't echoed (D-126)."""
    for a in argv:
        if a == "--":
            break
        if a == "--password-for" or a.startswith("--password-for="):
            return REMOVED_PASSWORD_FOR
    return None


class _VisibleWriter:
    """A text stream that shows hidden characters in file names as ``⟨U+202E⟩`` (D-123)."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def write(self, text: str) -> int:
        return self._stream.write(visible(text))

    def flush(self) -> None:
        self._stream.flush()

    def isatty(self) -> bool:
        return self._stream.isatty()


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    stdin: TextIO | None = None,
) -> int:
    """Run the command line; returns the exit code."""
    out = _VisibleWriter(stdout or sys.stdout)
    err = _VisibleWriter(stderr or sys.stderr)
    inp = stdin or sys.stdin
    removed = _removed_flag(sys.argv[1:] if argv is None else argv)
    if removed:
        err.write(f"Error: {removed}\n")
        return int(ExitCode.USAGE)
    args = build_parser().parse_intermixed_args(argv)
    try:
        return int(_run(args, out, inp, err))  # type: ignore[arg-type]
    except _Fail as e:
        err.write(f"{e}\n")
        return int(e.code)
    except ConfirmationRequired as e:
        err.write(f"{e} Add {_CONFIRM_FLAGS.get(e.reason, '')} to go ahead.\n")
        return int(ExitCode.CONFIRM)
    except StampError as e:
        err.write(f"{e}\n")
        for kind, code in _ERROR_CODES:
            if isinstance(e, kind):
                return int(code)
        return int(ExitCode.ERROR)
    except KeyboardInterrupt:
        err.write("Stopped. Nothing was saved.\n")
        return int(ExitCode.INTERRUPTED)
    except Exception as e:  # last resort: a sentence, not a traceback
        err.write(f"Unexpected error ({type(e).__name__}): {e}\nNothing was saved over the original.\n")
        return int(ExitCode.ERROR)
