"""Command line: ``stamper INPUT.pdf --set identifier=... [options]``.

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
from collections.abc import Sequence
from datetime import date
from enum import IntEnum
from pathlib import Path
from typing import TextIO

from .core.config import Config, ConfigError, Profile, load_config
from .core.fields import prefill_fields, validate_field, value_from_filename
from .core.filenames import suggest_filename
from .core.locations import builtin_config_path, find_config
from .core.patterns import parse_date_format
from .handlers.base import (
    Cancelled,
    ConfirmationRequired,
    MissingGlyph,
    OutputRefused,
    PasswordRequired,
    StampDoesNotFit,
    StampError,
    StampJob,
    UnsupportedDocument,
    WrongPassword,
)
from .handlers.pdf import PdfHandler

__all__ = [
    "ExitCode", "build_parser", "default_config_path", "get_version", "located_config_path", "main",
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
            "4 field values invalid, 5 file can't be stamped, 6 password missing or wrong, "
            "7 confirmation flag needed, 8 output refused, 9 stamp doesn't fit."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("input", type=Path, help="the PDF to stamp")
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


def _run(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> ExitCode:
    say = (lambda msg: None) if args.quiet else (lambda msg: out.write(msg + "\n"))

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

    src: Path = args.input
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
    )
    for w in result.warnings:
        say(f"Note: {w}")
    say(f"Saved: {result.output_path}")
    return ExitCode.OK


_ERROR_CODES: tuple[tuple[type[StampError], ExitCode], ...] = (
    (PasswordRequired, ExitCode.PASSWORD),
    (WrongPassword, ExitCode.PASSWORD),
    (ConfirmationRequired, ExitCode.CONFIRM),
    (OutputRefused, ExitCode.OUTPUT_REFUSED),
    (StampDoesNotFit, ExitCode.DOES_NOT_FIT),
    (MissingGlyph, ExitCode.DOES_NOT_FIT),
    (UnsupportedDocument, ExitCode.UNSUPPORTED),
    (Cancelled, ExitCode.INTERRUPTED),
)

_CONFIRM_FLAGS = {"signature": "--accept-signature-loss", "restrictions": "--accept-restrictions"}


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    stdin: TextIO | None = None,
) -> int:
    """Run the command line; returns the exit code."""
    out, err, inp = stdout or sys.stdout, stderr or sys.stderr, stdin or sys.stdin
    args = build_parser().parse_args(argv)
    try:
        return int(_run(args, out, inp))
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
