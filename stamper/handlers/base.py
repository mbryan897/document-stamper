"""The handler interface every file format implements (D-018).

A handler knows one file format. The window, the command line and the tests
all go through this interface, so nothing above it changes when a new format
is added.

Nothing here imports Qt. Progress and cancellation are plain callables so a
GUI can wire them to its own thread without the engine knowing.
"""

from __future__ import annotations

import abc
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..core.config import Config, Profile, StampSpec

__all__ = [
    "ApplyResult",
    "Cancelled",
    "ConfirmationRequired",
    "DocumentInfo",
    "Handler",
    "MissingGlyph",
    "OutputRefused",
    "PageInfo",
    "PasswordRequired",
    "PlanResult",
    "ProgressCallback",
    "CancelCheck",
    "SourceChanged",
    "SourceFingerprint",
    "check_unchanged",
    "StampDoesNotFit",
    "StampError",
    "StampJob",
    "StampPlacement",
    "UnsupportedDocument",
    "WrongPassword",
]

ProgressCallback = Callable[[int, int], None]
"""Called as ``progress(done, total)`` after each page."""

CancelCheck = Callable[[], bool]
"""Called between pages; returning True stops the job with ``Cancelled``."""


# ------------------------------------------------------------------ errors


class StampError(Exception):
    """Base class for problems the user can understand and act on.

    ``str(error)`` is a complete sentence written for the end user.
    """


class UnsupportedDocument(StampError):
    """The file can't be stamped: not this format, XFA, or damaged beyond repair."""


class PasswordRequired(StampError):
    """The file needs a password to open and none was given."""


class WrongPassword(StampError):
    """The password given doesn't open the file."""


class ConfirmationRequired(StampError):
    """The caller must confirm before stamping.

    ``reason`` is ``"signature"`` (the copy loses the digital signature) or
    ``"restrictions"`` (the original forbids changes).
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


class OutputRefused(StampError):
    """The output path is the input file, or exists and overwriting wasn't allowed."""


class StampDoesNotFit(StampError):
    """A stamp doesn't fit on a page even at the smallest font size, or two stamps overlap."""

    def __init__(self, message: str, page: int, stamp: int) -> None:
        self.page = page  # 1-based
        self.stamp = stamp  # 1-based
        super().__init__(message)


class MissingGlyph(StampError):
    """The stamp font has no character for something in the stamp text."""

    def __init__(self, message: str, char: str) -> None:
        self.char = char
        super().__init__(message)


class Cancelled(StampError):
    """The caller cancelled the job. Nothing was written."""


class SourceChanged(StampError):
    """The original changed on disk after it was opened (D-125). Nothing was written."""


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    """Size, modification time and file ID of a source when it was opened (D-125).

    Not a hash (D-007): it catches a file replaced or re-saved while the
    window was open, not deliberate tampering (a modification time can be set
    back). ``dev`` and ``ino`` are the volume serial number and NTFS file ID
    on Windows; a file system without IDs reports 0 and only size and time
    are compared.
    """

    size: int
    mtime_ns: int
    dev: int
    ino: int

    @classmethod
    def of(cls, path: Path) -> SourceFingerprint:
        """The fingerprint of ``path`` now. Raises OSError if it can't be read."""
        st = os.stat(path)
        return cls(st.st_size, st.st_mtime_ns, st.st_dev, st.st_ino)

    def matches(self, other: SourceFingerprint) -> bool:
        """True if ``other`` looks like the same, unchanged file."""
        if (self.size, self.mtime_ns) != (other.size, other.mtime_ns):
            return False
        if self.ino and other.ino and (self.dev, self.ino) != (other.dev, other.ino):
            return False
        return True


def check_unchanged(path: Path, expect: SourceFingerprint | None) -> None:
    """Raise SourceChanged if ``path`` no longer matches ``expect`` (no-op when None)."""
    if expect is None:
        return
    try:
        now = SourceFingerprint.of(path)
    except OSError:
        raise SourceChanged(f"{Path(path).name} can't be found any more. Open it again.") from None
    if not now.matches(expect):
        raise SourceChanged(
            f"The original, {Path(path).name}, changed after you opened it. "
            "Open it again so the copy matches what you checked."
        )


# ------------------------------------------------------------------ data


@dataclass(frozen=True, slots=True)
class PageInfo:
    """One page as a viewer shows it. Sizes are visible width and height in points."""

    number: int  # 1-based
    width: float
    height: float
    rotation: int


@dataclass(frozen=True, slots=True)
class DocumentInfo:
    """What ``inspect`` learns about a file before stamping.

    ``restricted`` means the file is encrypted and its permissions don't
    allow changes for the password used to open it. ``signed`` means at
    least one signature field carries a signature. ``repaired`` means the
    file was damaged and was repaired when it was opened.
    """

    path: Path
    page_count: int
    pages: tuple[PageInfo, ...]
    encrypted: bool
    needs_password: bool
    restricted: bool
    signed: bool
    xfa: bool
    repaired: bool
    has_xmp: bool
    has_form_fields: bool
    encryption: str | None = None
    fingerprint: SourceFingerprint | None = None
    active_content: tuple[str, ...] = ()  # e.g. "JavaScript", "2 attached files" (D-119); never removed
    revisions: int = 1  # saved versions in the file: 2 or more means it was edited incrementally


@dataclass(frozen=True, slots=True)
class StampJob:
    """Everything to draw, independent of the file.

    ``stamps`` are the stamp layouts to draw on every page, usually a
    profile's, but the stamp dialog may pass edited copies (O-004).
    ``values`` holds validated field values keyed by field name.
    ``metadata`` maps document property names to templates.
    ``background_box`` overrides every stamp's own setting when not None.
    ``labels`` maps field names to user-facing labels for error messages.
    ``today`` is the date for ``{date}``; None means the local date at stamping.
    """

    stamps: tuple[StampSpec, ...]
    values: Mapping[str, str]
    metadata: Mapping[str, str] = field(default_factory=dict)
    date_format: str = "MM-DD-YYYY"
    background_box: bool | None = None
    labels: Mapping[str, str] = field(default_factory=dict)
    today: date | None = None

    @classmethod
    def from_profile(
        cls,
        config: Config,
        profile: Profile,
        values: Mapping[str, str],
        *,
        background_box: bool | None = None,
        stamps: Sequence[StampSpec] | None = None,
        today: date | None = None,
    ) -> StampJob:
        """Build a job from a profile; ``stamps`` replaces the profile's if given."""
        all_values = {f: values.get(f, "") for f in profile.fields}
        return cls(
            stamps=tuple(stamps if stamps is not None else profile.stamps),
            values=all_values,
            metadata=dict(profile.metadata),
            date_format=config.settings.date_format,
            background_box=background_box,
            labels={f: config.fields[f].label for f in profile.fields if f in config.fields},
            today=today,
        )


@dataclass(frozen=True, slots=True)
class StampPlacement:
    """Where one stamp landed on one page, in visible coordinates.

    ``box`` is the area covered by the white box (when ``box_drawn``) or the
    text's padded extent (when not). ``font_size`` may be below the
    configured size if the stamp was shrunk to fit.
    """

    page: int  # 1-based
    stamp: int  # 1-based
    text: str
    box: tuple[float, float, float, float]
    font_size: float
    box_drawn: bool
    direction: str


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """What ``apply`` did."""

    output_path: Path
    pages_stamped: int
    warnings: tuple[str, ...]
    placements: tuple[StampPlacement, ...]
    metadata_written: Mapping[str, str]
    metadata_replaced: Mapping[str, tuple[str, str]]
    encryption_kept: bool


@dataclass(frozen=True, slots=True)
class PlanResult:
    """What ``plan`` found: what ``apply`` would do, without writing anything.

    ``warnings`` are the page and document warnings ``apply`` would give
    (shrunk text, form fields over a stamp, a repaired file, XMP, replaced
    document properties), but not the confirmations, which the caller asks
    for from ``DocumentInfo``.
    """

    page_count: int
    warnings: tuple[str, ...]
    placements: tuple[StampPlacement, ...]
    metadata_written: Mapping[str, str]
    metadata_replaced: Mapping[str, tuple[str, str]]


# ------------------------------------------------------------------ interface


class Handler(abc.ABC):
    """One file format."""

    #: File extensions this handler takes, lowercase with the dot.
    extensions: tuple[str, ...] = ()

    @abc.abstractmethod
    def can_open(self, path: Path) -> bool:
        """True if this handler takes the file (checks content, not just the name)."""

    @abc.abstractmethod
    def inspect(self, path: Path, password: str | None = None) -> DocumentInfo:
        """Read page sizes, rotation, protection and signature flags.

        Raises PasswordRequired, WrongPassword or UnsupportedDocument.
        """

    @abc.abstractmethod
    def render_preview(
        self,
        path: Path,
        page: int,
        job: StampJob,
        *,
        password: str | None = None,
        dpi: int = 96,
        outline_box: bool = True,
        expect: SourceFingerprint | None = None,
    ) -> bytes:
        """PNG of one page (1-based) with the stamps drawn, as the copy will look.

        With ``outline_box`` the area each stamp covers is outlined, so the
        user can see exactly what the white box hides. The file isn't changed.
        """

    @abc.abstractmethod
    def plan(
        self,
        path: Path,
        job: StampJob,
        *,
        password: str | None = None,
        cancel: CancelCheck | None = None,
        expect: SourceFingerprint | None = None,
    ) -> PlanResult:
        """Check the whole document as ``apply`` would, without writing anything.

        Raises the same errors as ``apply`` for the file and the stamps
        (UnsupportedDocument, StampDoesNotFit for any page, MissingGlyph,
        password errors), but never ConfirmationRequired or OutputRefused.
        ``cancel`` is checked between pages (``Cancelled``). The file isn't
        changed.
        """

    @abc.abstractmethod
    def apply(
        self,
        path: Path,
        job: StampJob,
        out_path: Path,
        *,
        password: str | None = None,
        accept_signature_loss: bool = False,
        accept_restrictions: bool = False,
        remove_encryption: bool = False,
        overwrite: bool = False,
        progress: ProgressCallback | None = None,
        cancel: CancelCheck | None = None,
        expect: SourceFingerprint | None = None,
    ) -> ApplyResult:
        """Write a stamped copy to ``out_path``. The input is never changed.

        Raises a StampError subclass for anything the user must deal with.
        With ``expect`` (from ``DocumentInfo.fingerprint``), a source that
        changed since it was inspected raises SourceChanged, before reading
        and again just before writing (D-125). ``plan`` and ``render_preview``
        check before reading.
        """
