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
  the caller asks to remove them (D-050).
- Document properties: existing Info entries are kept; configured keys are
  added (or replaced, with a warning naming old and new values); ``ModDate``
  is set to the stamping time. XMP is never touched (D-051).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import pymupdf

from ..core.config import MIN_FONT_PT, StampSpec
from ..core.geometry import (
    Placement,
    TextMetrics,
    insert_rotation,
    place,
    visible_rect_to_unrotated,
    visible_to_unrotated,
)
from ..core.locations import resource_dir
from ..core.patterns import parse_date_format
from ..core.template import builtin_values, render
from .base import (
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
    "font_path",
    "same_file",
    "stamp_font",
    "write_sample_page",
]

FONT_FILE = "AtkinsonHyperlegibleMono-Regular.ttf"
FONT_RESOURCE = "StampMono"  # resource name inside each page
BOX_PADDING_PT = 2.5  # white space between the text and the box edge
SHRINK_STEP_PT = 0.5
PREVIEW_OUTLINE_RGB = (0.85, 0.55, 0.0)  # amber, the interface's warning colour
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


def _strip_long_prefix(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _canonical(path: Path) -> str:
    raw = _strip_long_prefix(os.fspath(path))
    return os.path.normcase(os.path.realpath(os.path.abspath(raw)))


def same_file(a: Path, b: Path) -> bool:
    """True if two paths name the same file.

    Handles relative paths, letter case (on Windows), ``\\\\?\\`` prefixes,
    8.3 short names and links: canonical paths are compared first, then
    ``os.path.samefile`` when both exist.
    """
    if _canonical(a) == _canonical(b):
        return True
    try:
        return os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)
    except OSError:
        return False


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

    extensions = (".pdf",)

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
        )

    def inspect(self, path: Path, password: str | None = None) -> DocumentInfo:
        """See Handler.inspect.

        For a file that needs a password, call without one first: the
        PasswordRequired error tells the caller to ask.
        """
        with self._open(path, password) as (doc, needed):
            return self._info(doc, path, needed)

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
                if ch.isspace() and ch != " ":
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
    ) -> list[_Planned]:
        """Fit every stamp on one page and check overlaps, without drawing.

        Appends the shrink and form-field warnings for the page. Raises
        StampDoesNotFit when a stamp can't fit or two stamps overlap.
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
    ) -> list[StampPlacement]:
        planned = self._plan_page(page, job, values, warnings)
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
    ) -> bytes:
        """See Handler.render_preview. ``page`` is 1-based."""
        path = Path(path)
        today = job.today or date.today()
        with self._open(path, password) as (doc, _):
            if not 1 <= page <= doc.page_count:
                raise ValueError(f"page {page} is outside 1-{doc.page_count}")
            self._check_glyphs(job, doc.page_count, path.stem, today)
            p = doc[page - 1]
            values = self._page_values(job, page, doc.page_count, path.stem, today)
            self._stamp_page(p, job, values, [], outline=outline_box)
            return p.get_pixmap(dpi=dpi, alpha=False).tobytes("png")

    # ---------------------------------------------------------- plan

    @staticmethod
    def _refuse_xfa(info: DocumentInfo) -> None:
        if info.xfa:
            raise UnsupportedDocument(
                f"{info.path.name} is an XFA form, which this tool can't stamp. "
                "Print it to PDF first, then stamp the printed copy."
            )

    @staticmethod
    def _document_notes(info: DocumentInfo) -> list[str]:
        notes = []
        if info.repaired:
            notes.append(f"{info.path.name} was damaged and was repaired when opened. Check the stamped copy carefully.")
        if info.has_xmp:
            notes.append("The original has XMP metadata, which wasn't updated; only the document properties were.")
        return notes

    def plan(
        self,
        path: Path,
        job: StampJob,
        *,
        password: str | None = None,
        cancel: CancelCheck | None = None,
    ) -> PlanResult:
        """See Handler.plan. Uses the same fitting and checks as ``apply``."""
        src = Path(path)
        today = job.today or date.today()
        warnings: list[str] = []
        with self._open(src, password) as (doc, needed):
            info = self._info(doc, src, needed)
            self._refuse_xfa(info)
            warnings.extend(self._document_notes(info))
            total = doc.page_count
            self._check_glyphs(job, total, src.stem, today)
            placements: list[StampPlacement] = []
            for page in doc:
                if cancel is not None and cancel():
                    raise Cancelled("The check was cancelled.")
                values = self._page_values(job, page.number + 1, total, src.stem, today)
                planned = self._plan_page(page, job, values, warnings)
                placements.extend(self._placements(page.number + 1, planned))
            written, replaced = self._metadata_plan(doc, job, src.stem, today, warnings)
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
        kind, val = doc.xref_get_key(-1, "Info")
        if kind == "xref":
            return int(val.split()[0])
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
        kind, val = doc.xref_get_key(-1, "Info")
        info = int(val.split()[0]) if kind == "xref" else 0
        for key, template in job.metadata.items():
            value = render(template, base)
            if not value:
                continue
            kind, old = doc.xref_get_key(info, key) if info else ("null", "")
            if kind != "null" and old != value:
                replaced[key] = (old, value)
                warnings.append(
                    f'Document property {key} was "{old}" in the original; '
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
        doc.xref_set_key(info, "ModDate", pymupdf.get_pdf_str(pymupdf.get_pdf_now()))
        return written, replaced

    @staticmethod
    def _save_atomic(doc: pymupdf.Document, out: Path, encryption: int, overwrite: bool) -> None:
        fd, tmp_name = tempfile.mkstemp(prefix="~stamper-", suffix=".tmp", dir=out.parent)
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            doc.save(tmp, garbage=0, deflate=False, clean=False, encryption=encryption)
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
    ) -> ApplyResult:
        """See Handler.apply."""
        src, out = Path(path), Path(out_path)
        self._check_output(src, out, overwrite)
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
                warnings.append("The original is digitally signed; the stamped copy's signature won't validate.")
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
            for page in doc:
                if cancel is not None and cancel():
                    raise Cancelled("Stamping was cancelled. Nothing was saved.")
                values = self._page_values(job, page.number + 1, total, original_name, today)
                placements.extend(self._stamp_page(page, job, values, warnings))
                if progress is not None:
                    progress(page.number + 1, total)

            written, replaced = self._write_metadata(doc, job, original_name, today, warnings)
            if cancel is not None and cancel():
                raise Cancelled("Stamping was cancelled. Nothing was saved.")

            keep = info.encrypted and not remove_encryption
            if info.encrypted and remove_encryption:
                warnings.append("The copy isn't password-protected, unlike the original.")
            self._check_output(src, out, overwrite)  # again, just before writing
            self._save_atomic(
                doc, out,
                pymupdf.PDF_ENCRYPT_KEEP if keep else pymupdf.PDF_ENCRYPT_NONE,
                overwrite,
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
