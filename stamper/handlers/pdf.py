"""PDF handler, built on PyMuPDF.

Evidentiary-integrity notes (for manual review)
-----------------------------------------------
- The input file is opened read-only and never written. Output goes to a
  temporary file in the destination folder and is renamed into place, so a
  failure never leaves a half-written file and never touches the original.
- The copy is a full save (D-009): the file structure is rebuilt, so a
  digital signature on the original does not validate on the copy.
- Stamps are added to each page's content stream above the existing content.
  Existing content isn't removed; the white box covers what is under it
  visually, and that content stays in the file.
- Encryption and permissions are kept exactly (``PDF_ENCRYPT_KEEP``) unless
  the caller asks to remove them (D-050, as amended: the window asks). With
  ``out_password``, the copy is saved with AES-256 and that password as both
  the open and the owner password, with no permission restrictions, in place
  of any protection the original had. The password
  is only passed to that save; it is never stored.
- Document properties: existing Info entries are kept; configured keys are
  added (or replaced, with a warning naming old and new values); ``ModDate``
  is set to the stamping time. XMP is never touched (D-051); when the original
  has XMP, a note says some programs may show its title and dates for the copy.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
import unicodedata
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import pymupdf

from ..core import pending
from ..core.config import MIN_FONT_PT, StampSpec
from ..core.filenames import visible
from ..core.geometry import (
    Placement,
    TextMetrics,
    insert_rotation,
    place,
    visible_rect_to_unrotated,
    visible_to_unrotated,
)
from ..core.locations import resource_dir
from ..core.paths import same_file, size_text
from ..core.patterns import parse_date_format
from ..core.template import builtin_values, render
from .base import (
    SourceFingerprint,
    check_unchanged,
    ApplyResult,
    CancelCheck,
    Cancelled,
    ConfirmationRequired,
    DocumentInfo,
    Handler,
    MissingGlyph,
    OutputRefused,
    PageInfo,
    PasswordRequired,
    PlanResult,
    ProgressCallback,
    StampDoesNotFit,
    StampError,
    StampJob,
    StampPlacement,
    UnsupportedDocument,
    WrongPassword,
)

__all__ = [
    "BOX_PADDING_PT",
    "FONT_FILE",
    "FONT_RESOURCE",
    "PdfHandler",
    "SHRINK_STEP_PT",
    "combined_no_password_note",
    "font_path",
    "needs_check",
    "output_password_problem",
    "stamp_font",
    "write_sample_page",
    "xfa_refusal",
]

FONT_FILE = "AtkinsonHyperlegibleMono-Regular.ttf"
FONT_RESOURCE = "StampMono"  # resource name inside each page
BOX_PADDING_PT = 2.5  # white space between the text and the box edge
SHRINK_STEP_PT = 0.5
PREVIEW_OUTLINE_RGB = (0.85, 0.55, 0.0)  # amber, the interface's warning color
MAX_PREVIEW_PIXELS = 20_000_000  # a preview never asks for more; huge pages render coarser (D-122)
SMALL_PAGE_IN = 1.0  # notes for pages outside these sizes (either side) and very large files
LARGE_PAGE_IN = 100.0  # the PDF maximum is 200 in; from about 30 in the preview is drawn coarser
LARGE_PAGE_COUNT = 2000
LARGE_FILE_BYTES = 500 * 1024 * 1024
_EPS = 1e-6


def font_path() -> Path:
    """Where the bundled stamp font lives (the repo, or a packaged build's resources, D-087)."""
    return resource_dir() / "assets" / "fonts" / FONT_FILE


def write_sample_page(path: Path, width: float = 612.0, height: float = 792.0) -> Path:
    """Write a one-page placeholder PDF (US Letter by default) for Profile settings' preview.

    Grey placeholder lines run to the top edge, so the white box is visible.
    Synthetic content only; returns ``path``.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    grey = (0.62, 0.66, 0.72)
    page.insert_text((72, 40), "SAMPLE PAGE - NOT A REAL DOCUMENT", fontsize=10, fontname="helv", color=grey)
    y = 90.0
    while y < height - 72:
        page.draw_line((72, y), (width - 72, y), color=grey, width=0.6)
        y += 18.0
    doc.save(path)
    doc.close()
    return path


@lru_cache(maxsize=1)
def _font_buffer() -> bytes:
    path = font_path()
    try:
        return path.read_bytes()
    except OSError as e:
        raise UnsupportedDocument(
            f"The stamp font is missing ({path}). Reinstall the app."
        ) from e


@lru_cache(maxsize=1)
def stamp_font() -> pymupdf.Font:
    """The bundled Atkinson Hyperlegible Mono, for measuring text."""
    return pymupdf.Font(fontbuffer=_font_buffer())


def _hex_to_rgb(color: str) -> tuple[float, float, float]:
    c = color.lstrip("#")
    return (int(c[0:2], 16) / 255, int(c[2:4], 16) / 255, int(c[4:6], 16) / 255)


def _inches(points: float) -> str:
    return f"{points / 72:.1f}"


def preview_zoom(rect: pymupdf.Rect, dpi: float) -> float:
    """The scale for a preview at ``dpi``, lowered so it never exceeds MAX_PREVIEW_PIXELS (D-122)."""
    w, h = max(rect.width, 1.0), max(rect.height, 1.0)
    zoom = min(dpi / 72, math.sqrt(MAX_PREVIEW_PIXELS / (w * h)))
    while math.ceil(w * zoom) * math.ceil(h * zoom) > MAX_PREVIEW_PIXELS:  # pixel counts round up
        zoom *= 0.999
    return zoom


def _page_list(numbers: list[int]) -> str:
    shown = ", ".join(str(n) for n in numbers[:5])
    more = f" and {len(numbers) - 5} more" if len(numbers) > 5 else ""
    return f"page{'s' if len(numbers) > 1 else ''} {shown}{more}"


_ACTION_KINDS = {
    "JavaScript": "JavaScript",
    "Launch": "an action that opens another file or program",
    "SubmitForm": "a form that sends its data somewhere",
    "ImportData": "an action that imports data",
    "GoToR": "links that open other PDF files",
    "GoToE": "links into attached files",
}
_ACTION_RE = re.compile(r"/S\s*/(JavaScript|Launch|SubmitForm|ImportData|GoToR|GoToE)\b")
_REF_RE = re.compile(r"(\d+)\s+0\s+R")


def _action_kinds(doc: pymupdf.Document, xref: int, key: str, found: set[str]) -> None:
    """Add the kinds of action under ``key`` of object ``xref``, following references a few levels."""
    kind, val = doc.xref_get_key(xref, key)
    if kind in ("null", "array") and key == "OpenAction":
        return  # a destination, not an action
    if kind == "null":
        return
    texts = [val]
    seen: set[int] = set()
    for _depth in range(3):
        refs = [int(m.group(1)) for t in texts for m in _REF_RE.finditer(t)]
        texts_next = []
        for r in refs:
            if r in seen or not 0 < r < doc.xref_length():
                continue
            seen.add(r)
            try:
                if doc.xref_is_stream(r):
                    continue  # script text itself; the /S that named it is in the parent
                texts_next.append(doc.xref_object(r, compressed=True))
            except Exception:  # an unreadable object is skipped, never fatal
                continue
        for t in texts:
            found.update(m.group(1) for m in _ACTION_RE.finditer(t))
        texts = texts_next
        if not texts:
            break
    for t in texts:
        found.update(m.group(1) for m in _ACTION_RE.finditer(t))


def active_content(doc: pymupdf.Document) -> tuple[str, ...]:
    """What the document carries that can act by itself, read without running anything (D-119)."""
    kinds: set[str] = set()
    cat = doc.pdf_catalog()
    if doc.xref_get_key(cat, "Names/JavaScript")[0] != "null":
        kinds.add("JavaScript")
    for key in ("OpenAction", "AA"):
        _action_kinds(doc, cat, key, kinds)
    attached_annots = 0
    media = False
    for page in doc:
        _action_kinds(doc, page.xref, "AA", kinds)
        # Every annotation, links and form fields included (page.annots() leaves links out).
        for xref, _type, _id in page.annot_xrefs():
            subtype = doc.xref_get_key(xref, "Subtype")[1].lstrip("/")
            if subtype == "FileAttachment":
                attached_annots += 1
            elif subtype in ("RichMedia", "3D", "Screen", "Movie", "Sound"):
                media = True
            for key in ("A", "AA", "Parent/AA"):
                _action_kinds(doc, xref, key, kinds)
    out = [_ACTION_KINDS[k] for k in _ACTION_KINDS if k in kinds]
    files = doc.embfile_count() + attached_annots
    if files:
        out.append(f"{files} attached file{'s' if files != 1 else ''}")
    if media:
        out.append("embedded media")
    return tuple(out)


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def active_content_notes(info: DocumentInfo) -> list[str]:
    """Plain notes for active content and earlier revisions (D-119); nothing is removed."""
    notes: list[str] = []
    if info.active_content:
        notes.append(f"This file contains {_join(list(info.active_content))}. "
                     "The stamped copy keeps them unchanged.")
    if info.revisions > 1:
        notes.append(f"This file was edited after it was first saved ({info.revisions} saved versions). "
                     "The stamped copy keeps only the latest version, as a viewer shows it.")
    return notes


PROPERTIES_NOTE_PREFIX = "Document properties in the copy:"
_STANDARD_PROPERTIES = ("Title", "Author", "Subject", "Keywords", "Creator", "Producer",
                        "CreationDate", "ModDate", "Trapped")
_PROPERTY_LABELS = {"Creator": "Made with", "Producer": "PDF made by", "CreationDate": "Created",
                    "ModDate": "Last changed"}
MAX_PROPERTY_CHARS = 60  # a value longer than this is cut, with "…"
MAX_CUSTOM_PROPERTIES = 8
_PDF_DATE_RE = re.compile(r"D:(\d{4})(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)(?:(Z)|([+-])(\d\d)'?(\d\d)'?)?")

# Notes the saved screens show first, under "Check the copy" (UX-019): the copy differs
# from the original in a way the sender should look at. Worded here, classified here.
SIGNATURE_LOST_NOTE = "The original is digitally signed; the stamped copy's signature won't validate."
KEEP_PROTECTION = ("To keep the same level of protection, encrypt this file or keep it only on encrypted "
                   "drives and shares.")
UNPROTECTED_NOTE = f"The copy isn't password-protected, unlike the original. {KEEP_PROTECTION}"
# D-096, as amended: for a combined file whose protected sources only had owner restrictions.
COMBINED_UNPROTECTED_NOTE = "The combined file is saved without a password or restrictions."
# ... and when at least one source needed a password to open and none was set for the combined file.
COMBINED_NO_PASSWORD_NOTE = ("This combined file has no password. {count} of its files {were} "
                             f"password-protected. {KEEP_PROTECTION}")
_NO_PASSWORD_START = COMBINED_NO_PASSWORD_NOTE.split(" {count}", 1)[0]
# Information on the saved screen when the user set a password for the combined file.
PASSWORD_SET_NOTE = "This file opens only with the password you set. Whoever receives it will need that password."
# What an output password may be: PyMuPDF 1.28.2 refuses more than 40 characters, and MuPDF hashes
# the text as typed, without the SASLprep normalisation the PDF format asks readers to apply, so
# anything outside printable ASCII may not open in another reader with the same keystrokes.
MAX_OUTPUT_PASSWORD = 40
OUTPUT_PASSWORD_RULE = ("The password can have up to 40 characters: unaccented letters, "
                        "digits, spaces and the symbols on a US keyboard.")
# Information, not "Check the copy" (UX-030): what a receiver may see, since XMP is never touched (D-051).
XMP_NOTE = ("The original has a second set of properties (XMP) that this copy keeps unchanged. "
            "Some programs may show the original's title and dates for this copy.")
_REPAIRED_NOTE_END = " was damaged and was repaired when opened. Check the stamped copy carefully."
_REPLACED_NOTE_START = "Document property "
# The page list comes first ("On pages 3, 7"), so the fixed part is matched at the end:
# "On page" alone would also match the dialog's grouped form-field note (D-066).
COVERS_TEXT_NOTE_END = (" the white box covers text that was already on the page. "
                        "The covered text is still in the file: the box is not redaction. "
                        "Choose another position under Placement, or turn off the white box.")


def pdf_now(now: datetime | None = None) -> str:
    """The time of stamping as a PDF date, ``D:YYYYMMDDHHMMSS-05'00'``, for the copy's ModDate.

    Local time with the UTC offset in force at that moment. PyMuPDF's own
    ``get_pdf_now`` always writes the daylight-saving offset, so in standard
    time its dates read an hour off; this asks the system for the offset.
    """
    when = now if now is not None and now.tzinfo is not None else (now or datetime.now()).astimezone()
    offset = when.utcoffset()
    minutes = round(offset.total_seconds() / 60) if offset is not None else 0
    if minutes == 0:
        return when.strftime("D:%Y%m%d%H%M%S") + "Z"
    sign, minutes = ("-" if minutes < 0 else "+"), abs(minutes)
    return when.strftime("D:%Y%m%d%H%M%S") + f"{sign}{minutes // 60:02d}'{minutes % 60:02d}'"


def covers_text_note(pages: list[int]) -> str:
    """The note for pages where a white box covers existing text (UX-004); page numbers only."""
    return f"On {_page_list(pages)}{COVERS_TEXT_NOTE_END}"


def combined_no_password_note(count: int) -> str:
    """The warning for a combined file saved without a password when ``count`` sources needed one."""
    return COMBINED_NO_PASSWORD_NOTE.format(count=count, were="was" if count == 1 else "were")


def output_password_problem(password: str) -> str:
    """Why ``password`` can't protect a saved copy, as a sentence; "" when it can."""
    if not password:
        return "The password can't be empty."
    if len(password) > MAX_OUTPUT_PASSWORD or not (password.isascii() and password.isprintable()):
        return OUTPUT_PASSWORD_RULE
    return ""


def needs_check(note: str) -> bool:
    """True for a note about something to check in the copy: repaired, signature lost,
    a document property replaced, protection removed, or a white box over existing text.
    Every other note is information."""
    return (note in (SIGNATURE_LOST_NOTE, UNPROTECTED_NOTE)
            or note.startswith((COMBINED_UNPROTECTED_NOTE, _NO_PASSWORD_START, _REPLACED_NOTE_START))
            or note.endswith((_REPAIRED_NOTE_END, COVERS_TEXT_NOTE_END)))


def xfa_refusal(name: str) -> str:
    """Why an XFA form isn't stamped, and what to do instead."""
    return (f"{name} is an XFA form, which this tool can't stamp. "
            "Print it to PDF first, then stamp the printed copy.")


def _info_dict(doc: pymupdf.Document) -> int:
    """The document Info dictionary's xref, or 0 when there is none."""
    kind, val = doc.xref_get_key(-1, "Info")
    return int(val.split()[0]) if kind == "xref" else 0


def _info_values(doc: pymupdf.Document) -> tuple[dict[str, str], str]:
    """The Info dictionary's entries as text, and its StamperSources text, without changing the document."""
    xref = _info_dict(doc)
    if not xref:
        return {}, ""
    values: dict[str, str] = {}
    for key in doc.xref_get_keys(xref):
        kind, val = doc.xref_get_key(xref, key)
        values[key] = val.removeprefix("/") if kind == "name" else val
    sources = doc.xref_get_key(xref, "StamperSources")
    return values, sources[1] if sources[0] == "string" else ""


def _shown_value(value: str) -> str:
    """A property value for the note: one line, at most MAX_PROPERTY_CHARS, hidden characters shown."""
    m = _PDF_DATE_RE.fullmatch(value)
    if m:
        y, mo, d, h, mi, sec, z, sign, oh, om = m.groups()
        zone = " UTC" if z else (f" {sign}{oh}:{om or '00'}" if sign else "")
        return f"{y}-{mo}-{d} {h}:{mi}:{sec}{zone}"
    flat = "".join(" " if unicodedata.category(c) in ("Cc", "Zl", "Zp") else c for c in value)
    if len(flat) > MAX_PROPERTY_CHARS:
        flat = flat[:MAX_PROPERTY_CHARS - 1].rstrip() + "…"
    return visible(flat)


def properties_note(values: Mapping[str, str | None], sources: str, has_xmp: bool) -> str:
    """What document properties the copy carries, with their values (D-125, UX-046).

    ``values`` maps each Info key to the value the copy carries; None means
    the value is set when the copy is saved (ModDate before saving).
    """
    def item(key: str) -> str:
        value = values[key]
        shown = "when saved" if value is None else f'"{_shown_value(value)}"'
        return f"{_PROPERTY_LABELS.get(key) or _shown_value(key)}: {shown}"

    standard = [item(k) for k in _STANDARD_PROPERTIES if k in values]
    custom = sorted(set(values) - set(_STANDARD_PROPERTIES))
    parts = [f"{PROPERTIES_NOTE_PREFIX} {', '.join(standard) if standard else 'none of the standard ones'}."]
    if custom:
        named = []
        for k in custom[:MAX_CUSTOM_PROPERTIES]:
            if k == "StamperSources":
                count = len([line for line in sources.splitlines() if line.strip()])
                named.append(f"StamperSources (the {count} source file names)")
            else:
                named.append(item(k))
        more = len(custom) - len(named)
        parts.append(f"Custom properties: {', '.join(named)}" + (f" and {more} more." if more else "."))
    parts.append("XMP metadata: the original's, kept unchanged; some programs may show its title and dates "
                 "for this copy." if has_xmp else "XMP metadata: none.")
    return " ".join(parts)


def size_notes(info: DocumentInfo) -> list[str]:
    """Soft notes for unusual page sizes and very large files (D-122); never a refusal."""
    notes: list[str] = []
    small = [p.number for p in info.pages if min(p.width, p.height) / 72 < SMALL_PAGE_IN]
    large = [p.number for p in info.pages if max(p.width, p.height) / 72 > LARGE_PAGE_IN]
    if small:
        notes.append(f"{_page_list(small).capitalize()} {'is' if len(small) == 1 else 'are'} under "
                     f"{SMALL_PAGE_IN:g} inch on a side. Check the stamp there carefully.")
    if large:
        notes.append(f"{_page_list(large).capitalize()} {'is' if len(large) == 1 else 'are'} over "
                     f"{LARGE_PAGE_IN:g} inches on a side, so the preview is drawn at a lower resolution.")
    if info.page_count > LARGE_PAGE_COUNT:
        notes.append(f"This file has {info.page_count:,} pages; checking and saving take longer.")
    try:
        size = info.path.stat().st_size
    except OSError:
        size = 0
    if size > LARGE_FILE_BYTES:
        notes.append(f"This file is {size_text(size)}; checking and saving take longer.")
    return notes


@dataclass(frozen=True, slots=True)
class _Planned:
    """One stamp fitted on one page: what ``_draw_page`` draws."""

    index: int  # 0-based stamp number
    spec: StampSpec
    text: str
    placement: Placement
    size: float
    box_on: bool
    rect: pymupdf.Rect  # box in unrotated page space


# ------------------------------------------------------------------ handler


class PdfHandler(Handler):
    """Stamps PDF files."""

    # ---------------------------------------------------------- opening

    def can_open(self, path: Path) -> bool:
        """True if the file starts with a PDF header (within the first 1 KB)."""
        try:
            with open(path, "rb") as f:
                return b"%PDF-" in f.read(1024)
        except OSError:
            return False

    @contextmanager
    def _open(self, path: Path, password: str | None) -> Iterator[tuple[pymupdf.Document, bool]]:
        """Open and authenticate; yields (document, needed_password).

        ``needs_pass`` is read exactly once, before ``authenticate``: reading
        it afterwards makes PyMuPDF 1.28.2 write garbled streams on a later
        ``PDF_ENCRYPT_KEEP`` save (D-056).
        """
        path = Path(path)
        if not path.is_file():
            raise UnsupportedDocument(f"Can't find the file {path}.")
        if not self.can_open(path):
            raise UnsupportedDocument(f"{path.name} isn't a PDF file.")
        try:
            doc = pymupdf.open(path, filetype="pdf")
        except Exception as e:  # PyMuPDF raises several types for broken files
            raise UnsupportedDocument(
                f"{path.name} is damaged and can't be repaired ({e})."
            ) from None
        try:
            needed_password = bool(doc.needs_pass)
            if needed_password:
                if not password:
                    raise PasswordRequired(f"{path.name} needs a password to open.")
                if not doc.authenticate(password):
                    raise WrongPassword(f"That password doesn't open {path.name}.")
            elif password and doc.metadata and doc.metadata.get("encryption"):
                # Owner restrictions only: an owner password lifts them.
                if not doc.authenticate(password):
                    raise WrongPassword(f"That password isn't the owner password for {path.name}.")
            if doc.page_count == 0:
                raise UnsupportedDocument(f"{path.name} has no pages.")
            yield doc, needed_password
        finally:
            doc.close()

    @staticmethod
    def _is_xfa(doc: pymupdf.Document) -> bool:
        cat = doc.pdf_catalog()
        kind, _ = doc.xref_get_key(cat, "AcroForm/XFA")
        return kind != "null"

    @staticmethod
    def _is_signed(doc: pymupdf.Document) -> bool:
        if doc.get_sigflags() < 1 and not doc.is_form_pdf:
            return False
        for page in doc:
            for w in page.widgets(types=[pymupdf.PDF_WIDGET_TYPE_SIGNATURE]):
                for key in ("V", "Parent/V"):
                    kind, _ = doc.xref_get_key(w.xref, key)
                    if kind != "null":
                        return True
        return False

    def _info(self, doc: pymupdf.Document, path: Path, needed_password: bool) -> DocumentInfo:
        encryption = doc.metadata.get("encryption") if doc.metadata else None
        encrypted = bool(encryption)
        restricted = encrypted and not (doc.permissions & pymupdf.PDF_PERM_MODIFY)
        pages = tuple(
            PageInfo(i + 1, p.rect.width, p.rect.height, p.rotation) for i, p in enumerate(doc)
        )
        xml = doc.get_xml_metadata() if doc.xref_xml_metadata() else ""
        return DocumentInfo(
            path=Path(path),
            page_count=doc.page_count,
            pages=pages,
            encrypted=encrypted,
            needs_password=needed_password,
            restricted=restricted,
            signed=self._is_signed(doc),
            xfa=self._is_xfa(doc),
            repaired=bool(doc.is_repaired),
            has_xmp=bool(xml.strip()),
            has_form_fields=bool(doc.is_form_pdf),
            encryption=encryption,
            active_content=active_content(doc),
            revisions=max(1, int(getattr(doc, "version_count", 1) or 1)),
        )

    def inspect(self, path: Path, password: str | None = None) -> DocumentInfo:
        """See Handler.inspect.

        For a file that needs a password, call without one first: the
        PasswordRequired error tells the caller to ask.
        """
        try:
            fingerprint = SourceFingerprint.of(Path(path))  # before reading: a change after this is caught
        except OSError:
            fingerprint = None  # _open reports the missing file
        with self._open(path, password) as (doc, needed):
            return replace(self._info(doc, path, needed), fingerprint=fingerprint)

    # ---------------------------------------------------------- stamping

    def _check_glyphs(self, job: StampJob, total: int, original_name: str, today: date) -> None:
        font = stamp_font()
        fmt = parse_date_format(job.date_format)
        texts = []
        for spec in job.stamps:
            for page in {1, total}:
                vals = {**job.values, **builtin_values(
                    page=page, total=total, original_name=original_name, today=today, date_format=fmt)}
                texts.append(render(spec.text, vals))
        for text in texts:
            for ch in text:
                if unicodedata.category(ch) in ("Cc", "Zl", "Zp"):  # other spaces, such as U+00A0, are drawn
                    raise MissingGlyph(
                        f"The stamp text contains a line break or tab (U+{ord(ch):04X}); "
                        "stamps are a single line.", ch)
                if ch != " " and not font.has_glyph(ord(ch)):
                    where = next(
                        (job.labels.get(k, k) for k, v in job.values.items() if ch in v),
                        "the original filename" if ch in original_name else "the stamp text",
                    )
                    raise MissingGlyph(
                        f"The stamp font has no character for '{ch}' (U+{ord(ch):04X}) in "
                        f"{where}. Remove or replace that character.",
                        ch,
                    )

    @staticmethod
    def _fit(
        spec: StampSpec, text: str, vis_w: float, vis_h: float, page_no: int, stamp_no: int
    ) -> tuple[Placement, float]:
        font = stamp_font()
        asc, desc = font.ascender, -font.descender
        size = spec.font_size_pt
        margin = spec.margin_in * 72
        while True:
            metrics = TextMetrics(font.text_length(text, size), asc * size, desc * size)
            pl = place(
                vis_w, vis_h,
                anchor=spec.anchor, direction=spec.direction,
                margin=margin, metrics=metrics, padding=BOX_PADDING_PT,
            )
            if pl.fits:
                return pl, size
            nxt = round(size - SHRINK_STEP_PT, 3)
            if nxt < MIN_FONT_PT - _EPS:
                edge = "top edge" if spec.direction == "along-top" else "right edge"
                if pl.needed > pl.available:
                    detail = (f"needs {_inches(pl.needed)} in along the {edge} at "
                              f"{size:g} pt; {_inches(pl.available)} in available")
                else:
                    detail = (f"needs {_inches(pl.cross_needed)} in across the {edge} at "
                              f"{size:g} pt; {_inches(pl.cross_available)} in available")
                raise StampDoesNotFit(
                    f'Page {page_no} ({_inches(vis_w)} × {_inches(vis_h)} in), stamp {stamp_no} '
                    f'"{text}": {detail}. Shorten the stamp text, use a smaller margin, '
                    "or run it along the other edge.",
                    page_no, stamp_no,
                )
            size = nxt

    def _plan_page(
        self,
        page: pymupdf.Page,
        job: StampJob,
        values: Mapping[str, str],
        warnings: list[str],
        covered: list[int] | None = None,
    ) -> list[_Planned]:
        """Fit every stamp on one page and check overlaps, without drawing.

        Appends the shrink and form-field warnings for the page. When
        ``covered`` is given, appends the page number if a white box would
        cover a word in the page's text layer (UX-004); the words are not kept.
        Raises StampDoesNotFit when a stamp can't fit or two stamps overlap.
        """
        page_no = page.number + 1
        vis_w, vis_h = page.rect.width, page.rect.height
        rot = page.rotation
        crop = page.cropbox
        cw, ch = crop.width, crop.height

        planned: list[_Planned] = []
        for j, spec in enumerate(job.stamps):
            text = render(spec.text, values)
            if not text:
                continue
            pl, size = self._fit(spec, text, vis_w, vis_h, page_no, j + 1)
            if size < spec.font_size_pt - _EPS:
                warnings.append(
                    f"Page {page_no}, stamp {j + 1}: font reduced from "
                    f"{spec.font_size_pt:g} pt to {size:g} pt to fit."
                )
            box_on = spec.background_box if job.background_box is None else job.background_box
            rect = pymupdf.Rect(*visible_rect_to_unrotated(pl.box, rot, cw, ch).as_tuple())
            planned.append(_Planned(j, spec, text, pl, size, box_on, rect))

        for a in range(len(planned)):
            for b in range(a + 1, len(planned)):
                if planned[a].placement.box.intersects(planned[b].placement.box):
                    ja, jb = planned[a].index + 1, planned[b].index + 1
                    raise StampDoesNotFit(
                        f"Page {page_no} ({_inches(vis_w)} × {_inches(vis_h)} in): "
                        f"stamps {ja} and {jb} overlap. Move one to another position.",
                        page_no, jb,
                    )

        if planned:
            annot_rects = [a.rect for a in page.annots()] + [w.rect for w in page.widgets()]
            for item in planned:
                if any(item.rect.intersects(r) for r in annot_rects):
                    warnings.append(
                        f"Page {page_no}, stamp {item.index + 1}: overlaps a form field or "
                        "annotation, which is drawn on top of the stamp."
                    )
            # Same rectangle _draw_page fills, in the same (unrotated, CropBox-relative) space.
            if covered is not None and any(
                w[4].strip() for p in planned if p.box_on for w in page.get_text("words", clip=p.rect)
            ):
                covered.append(page_no)
        return planned

    @staticmethod
    def _placements(page_no: int, planned: list[_Planned]) -> list[StampPlacement]:
        return [
            StampPlacement(
                page=page_no, stamp=p.index + 1, text=p.text, box=p.placement.box.as_tuple(),
                font_size=p.size, box_drawn=p.box_on, direction=p.spec.direction,
            )
            for p in planned
        ]

    @staticmethod
    def _draw_page(page: pymupdf.Page, planned: list[_Planned], *, outline: bool = False) -> None:
        """Draw planned stamps on a page, above its content."""
        if not planned:
            return
        rot = page.rotation
        crop = page.cropbox
        cw, ch = crop.width, crop.height
        page.insert_font(fontname=FONT_RESOURCE, fontbuffer=_font_buffer())
        for p in planned:
            if p.box_on:
                page.draw_rect(p.rect, color=None, fill=(1, 1, 1), width=0, overlay=True)
            origin = visible_to_unrotated(p.placement.origin, rot, cw, ch)
            page.insert_text(
                pymupdf.Point(*origin), p.text,
                fontsize=p.size, fontname=FONT_RESOURCE,
                color=_hex_to_rgb(p.spec.color),
                rotate=insert_rotation(rot, p.spec.direction),
                overlay=True,
            )
            if outline:
                page.draw_rect(p.rect, color=PREVIEW_OUTLINE_RGB, width=0.75,
                               dashes="[2 2] 0", overlay=True)

    def _stamp_page(
        self,
        page: pymupdf.Page,
        job: StampJob,
        values: Mapping[str, str],
        warnings: list[str],
        *,
        outline: bool = False,
        covered: list[int] | None = None,
    ) -> list[StampPlacement]:
        planned = self._plan_page(page, job, values, warnings, covered)
        self._draw_page(page, planned, outline=outline)
        return self._placements(page.number + 1, planned)

    def _page_values(
        self, job: StampJob, page_no: int, total: int, original_name: str, today: date
    ) -> dict[str, str]:
        return {**job.values, **builtin_values(
            page=page_no, total=total, original_name=original_name, today=today,
            date_format=parse_date_format(job.date_format))}

    # ---------------------------------------------------------- preview

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
        """See Handler.render_preview. ``page`` is 1-based."""
        path = Path(path)
        today = job.today or date.today()
        check_unchanged(path, expect)
        with self._open(path, password) as (doc, _):
            if not 1 <= page <= doc.page_count:
                raise ValueError(f"page {page} is outside 1-{doc.page_count}")
            self._check_glyphs(job, doc.page_count, path.stem, today)
            p = doc[page - 1]
            values = self._page_values(job, page, doc.page_count, path.stem, today)
            self._stamp_page(p, job, values, [], outline=outline_box)
            return p.get_pixmap(matrix=pymupdf.Matrix(*[preview_zoom(p.rect, dpi)] * 2), alpha=False).tobytes("png")

    # ---------------------------------------------------------- plan

    @staticmethod
    def _refuse_xfa(info: DocumentInfo) -> None:
        if info.xfa:
            raise UnsupportedDocument(xfa_refusal(info.path.name))

    @staticmethod
    def _document_notes(info: DocumentInfo) -> list[str]:
        notes = []
        if info.repaired:
            notes.append(f"{info.path.name}{_REPAIRED_NOTE_END}")
        if info.has_xmp:
            notes.append(XMP_NOTE)
        notes.extend(size_notes(info))
        notes.extend(active_content_notes(info))
        return notes

    def plan(
        self,
        path: Path,
        job: StampJob,
        *,
        password: str | None = None,
        cancel: CancelCheck | None = None,
        expect: SourceFingerprint | None = None,
    ) -> PlanResult:
        """See Handler.plan. Uses the same fitting and checks as ``apply``."""
        src = Path(path)
        today = job.today or date.today()
        warnings: list[str] = []
        check_unchanged(src, expect)
        with self._open(src, password) as (doc, needed):
            info = self._info(doc, src, needed)
            self._refuse_xfa(info)
            warnings.extend(self._document_notes(info))
            total = doc.page_count
            self._check_glyphs(job, total, src.stem, today)
            placements: list[StampPlacement] = []
            covered: list[int] = []
            for page in doc:
                if cancel is not None and cancel():
                    raise Cancelled("The check was cancelled.")
                values = self._page_values(job, page.number + 1, total, src.stem, today)
                planned = self._plan_page(page, job, values, warnings, covered)
                placements.extend(self._placements(page.number + 1, planned))
            if covered:
                warnings.append(covers_text_note(covered))
            written, replaced = self._metadata_plan(doc, job, src.stem, today, warnings)
            values, sources = _info_values(doc)
            planned_values: dict[str, str | None] = {**values, **written, "ModDate": None}
            warnings.append(properties_note(planned_values, sources, info.has_xmp))
        return PlanResult(
            page_count=total,
            warnings=tuple(warnings),
            placements=tuple(placements),
            metadata_written=written,
            metadata_replaced=replaced,
        )

    # ---------------------------------------------------------- apply

    @staticmethod
    def _check_output(src: Path, out: Path, overwrite: bool) -> None:
        if same_file(src, out):
            raise OutputRefused(
                f"The stamped copy can't be saved over the original ({src.name}). "
                "Choose a different name or folder."
            )
        if out.exists() and not overwrite:
            raise OutputRefused(f"{out} already exists. Choose another name, or allow overwriting.")
        if not out.parent.is_dir():
            raise OutputRefused(f"The folder {out.parent} doesn't exist.")

    @staticmethod
    def _info_xref(doc: pymupdf.Document) -> int:
        if xref := _info_dict(doc):
            return xref
        xref = doc.get_new_xref()
        doc.update_object(xref, "<<>>")
        doc.xref_set_key(-1, "Info", f"{xref} 0 R")
        return xref

    @staticmethod
    def _metadata_plan(
        doc: pymupdf.Document, job: StampJob, original_name: str, today: date,
        warnings: list[str],
    ) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
        """Document properties to write, and the existing ones they replace.

        Reads the Info dictionary without changing it; appends a warning per
        replaced value.
        """
        fmt = parse_date_format(job.date_format)
        base = {**job.values, "original_name": original_name, "date": fmt.format(today)}
        written: dict[str, str] = {}
        replaced: dict[str, tuple[str, str]] = {}
        info = _info_dict(doc)
        for key, template in job.metadata.items():
            value = render(template, base)
            if not value:
                continue
            kind, old = doc.xref_get_key(info, key) if info else ("null", "")
            if kind != "null" and old != value:
                replaced[key] = (old, value)
                warnings.append(
                    f'{_REPLACED_NOTE_START}{key} was "{old}" in the original; '
                    f'the copy has "{value}".'
                )
            written[key] = value
        return written, replaced

    def _write_metadata(
        self, doc: pymupdf.Document, job: StampJob, original_name: str, today: date,
        warnings: list[str],
    ) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
        written, replaced = self._metadata_plan(doc, job, original_name, today, warnings)
        info = self._info_xref(doc)
        for key, value in written.items():
            doc.xref_set_key(info, key, pymupdf.get_pdf_str(value))
        doc.xref_set_key(info, "ModDate", pymupdf.get_pdf_str(pdf_now()))
        return written, replaced

    @staticmethod
    def _save_atomic(doc: pymupdf.Document, out: Path, encryption: int, overwrite: bool,
                     source: Path | None = None, password: str | None = None) -> None:
        fd, tmp_name = tempfile.mkstemp(prefix=pending.TEMP_NAME_PREFIX, suffix=pending.TEMP_NAME_SUFFIX,
                                        dir=out.parent)
        os.close(fd)
        tmp = Path(tmp_name)
        entry = pending.record(tmp, source=source)  # so a crash before the rename can be cleaned up later (D-124)
        try:
            if password is None:
                doc.save(tmp, garbage=0, deflate=False, clean=False, encryption=encryption)
            else:  # AES-256, open and owner password the same, nothing restricted
                doc.save(tmp, garbage=0, deflate=False, clean=False, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                         user_pw=password, owner_pw=password, permissions=-1)
            if overwrite:
                os.replace(tmp, out)
            elif os.name == "nt":
                os.rename(tmp, out)  # fails if out exists: no silent overwrite
            else:
                os.link(tmp, out)  # same guarantee on POSIX (tests)
                tmp.unlink()
        except FileExistsError:
            raise OutputRefused(f"{out} already exists. Choose another name, or allow overwriting.") from None
        finally:
            if tmp.exists():
                tmp.unlink()
            if not tmp.exists():
                pending.forget(entry)

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
        out_password: str | None = None,
    ) -> ApplyResult:
        """See Handler.apply."""
        src, out = Path(path), Path(out_path)
        if out_password is not None and (problem := output_password_problem(out_password)):
            raise StampError(problem)
        self._check_output(src, out, overwrite)
        check_unchanged(src, expect)
        today = job.today or date.today()
        original_name = src.stem
        warnings: list[str] = []

        with self._open(src, password) as (doc, needed):
            info = self._info(doc, src, needed)
            self._refuse_xfa(info)
            if info.signed:
                if not accept_signature_loss:
                    raise ConfirmationRequired(
                        "signature",
                        f"{src.name} is digitally signed. The stamped copy won't carry a valid "
                        "signature; the original keeps it. Confirm to continue.",
                    )
                warnings.append(SIGNATURE_LOST_NOTE)
            if info.restricted:
                if not accept_restrictions:
                    raise ConfirmationRequired(
                        "restrictions",
                        f"{src.name} has owner restrictions that don't allow changes. "
                        "Confirm to stamp it anyway; the copy keeps the same restrictions.",
                    )
                warnings.append("The original's owner restrictions don't allow changes; it was stamped after confirmation.")
            warnings.extend(self._document_notes(info))

            total = doc.page_count
            self._check_glyphs(job, total, original_name, today)
            placements: list[StampPlacement] = []
            covered: list[int] = []
            for page in doc:
                if cancel is not None and cancel():
                    raise Cancelled("Stamping was cancelled. Nothing was saved.")
                values = self._page_values(job, page.number + 1, total, original_name, today)
                placements.extend(self._stamp_page(page, job, values, warnings, covered=covered))
                if progress is not None:
                    progress(page.number + 1, total)
            if covered:
                warnings.append(covers_text_note(covered))

            written, replaced = self._write_metadata(doc, job, original_name, today, warnings)
            if cancel is not None and cancel():
                raise Cancelled("Stamping was cancelled. Nothing was saved.")
            values, sources = _info_values(doc)
            warnings.append(properties_note(values, sources, info.has_xmp))

            keep = info.encrypted and not remove_encryption and out_password is None
            if info.encrypted and remove_encryption:
                warnings.append(UNPROTECTED_NOTE)
            self._check_output(src, out, overwrite)  # again, just before writing
            check_unchanged(src, expect)  # MuPDF reads pages lazily: still the file that was checked
            self._save_atomic(
                doc, out,
                pymupdf.PDF_ENCRYPT_KEEP if keep else pymupdf.PDF_ENCRYPT_NONE,
                overwrite,
                source=src,
                password=out_password,
            )

        return ApplyResult(
            output_path=out,
            pages_stamped=total,
            warnings=tuple(warnings),
            placements=tuple(placements),
            metadata_written=written,
            metadata_replaced=replaced,
            encryption_kept=keep,
        )
