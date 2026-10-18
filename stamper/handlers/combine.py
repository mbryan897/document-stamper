"""Combine several PDFs into one temporary PDF, ready for the unchanged single-file pipeline (D-095).

The window and the command line call ``combine_pdfs`` with the files in the
order the user chose. It writes one unencrypted PDF inside a new hidden
folder, ``~stamper-combine-<random>``, in the folder given (normally the
output folder). The temporary file is named after the first source file, so
``{original_name}`` in a stamp or a document property means the first file's
name (D-104) without any change to the handler. Then ``plan``,
``render_preview`` and ``apply`` run on that file exactly as on a single
file, and ``CombinedFile.cleanup`` removes the folder.

Evidentiary-integrity notes (for manual review)
-----------------------------------------------
- Every source is opened read-only through ``PdfHandler``'s own opener and
  is never written. Field renaming happens on the in-memory copy only.
- Pages are copied with PyMuPDF's ``insert_pdf`` (content, rotation, boxes,
  links, annotations, form fields). Page content is not changed here; the
  stamp is added later by ``apply``.
- What ``insert_pdf`` drops is rebuilt here (D-101): bookmarks (one per
  source, with its own bookmarks nested under it), the layer list (hidden
  layers stay hidden) and document-level attached files. Page labels and
  accessibility tags are not carried over; the result says so.
- The combined file has no password or restrictions (D-096). It exists only
  inside the temporary folder, which is removed on save, cancel, failure and
  close (D-055), with an ``atexit`` fallback and a start-up sweep for
  folders left by a crash.
- No hashing (D-007). The source file names and page ranges are recorded in
  the ``StamperSources`` document property (names only, never folders).
"""

from __future__ import annotations

import atexit
import re
import secrets
import shutil
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .base import (
    CancelCheck,
    Cancelled,
    ConfirmationRequired,
    StampError,
    UnsupportedDocument,
)
from .pdf import PdfHandler

__all__ = [
    "SOURCES_PROPERTY",
    "TEMP_PREFIX",
    "CombineInput",
    "CombineProgress",
    "CombinedFile",
    "SourceRange",
    "combine_pdfs",
    "field_suffix",
    "remove_stale_temp",
    "rename_clashing_fields",
]

TEMP_PREFIX = "~stamper-combine-"
SOURCES_PROPERTY = "StamperSources"
STALE_AFTER_S = 24 * 60 * 60
_REF = re.compile(r"(\d+)\s+0\s+R")
_MARK = "StamperOCG"  # private key used while copying layers, removed afterwards

CombineProgress = Callable[[int, int, str], None]
"""Called as ``progress(done, total, file_name)`` after each source file."""

_LIVE: set[Path] = set()  # temporary folders still in use; removed at exit if left


def _cleanup_at_exit() -> None:
    for folder in list(_LIVE):
        shutil.rmtree(folder, ignore_errors=True)
        _LIVE.discard(folder)


atexit.register(_cleanup_at_exit)


# ------------------------------------------------------------------ data


@dataclass(frozen=True, slots=True)
class CombineInput:
    """One source file and the password that opens it (None if it needs none)."""

    path: Path
    password: str | None = None


@dataclass(frozen=True, slots=True)
class SourceRange:
    """Where one source file's pages are in the combined file (1-based, inclusive)."""

    path: Path
    first: int
    last: int

    @property
    def page_count(self) -> int:
        """Number of pages from this source."""
        return self.last - self.first + 1


@dataclass(slots=True)
class CombinedFile:
    """The temporary combined PDF and what the user must be told about it.

    ``protected`` names sources that had a password or restrictions (the
    combined file has neither, D-096). ``signed`` names signed sources.
    ``renamed_fields`` maps a source file name to its ``(old, new)`` field
    names (D-098). ``notes`` are sentences for the stamp dialog and the
    saved screen.
    """

    path: Path
    folder: Path
    sources: tuple[SourceRange, ...]
    protected: tuple[str, ...] = ()
    signed: tuple[str, ...] = ()
    renamed_fields: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def page_count(self) -> int:
        """Total pages in the combined file."""
        return self.sources[-1].last if self.sources else 0

    def source_for(self, page: int) -> tuple[SourceRange, int]:
        """The source a combined page (1-based) came from, and its page number there."""
        for src in self.sources:
            if src.first <= page <= src.last:
                return src, page - src.first + 1
        raise ValueError(f"page {page} is outside 1-{self.page_count}")

    def protection_warning(self) -> str:
        """The D-096 warning, or an empty string when no source was protected."""
        if not self.protected:
            return ""
        return ("The combined file is saved without a password or restrictions. "
                f"Protected files in it: {', '.join(self.protected)}.")

    def cleanup(self) -> None:
        """Remove the temporary folder. Safe to call more than once."""
        shutil.rmtree(self.folder, ignore_errors=True)
        _LIVE.discard(self.folder)

    def __enter__(self) -> CombinedFile:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.cleanup()


# ------------------------------------------------------------------ temporary folders


def _hide(folder: Path) -> None:
    """Mark a folder hidden in Explorer (Windows only; best effort)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetFileAttributesW(str(folder), 0x2)  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass


def _make_temp_folder(parent: Path) -> Path:
    if not parent.is_dir():
        raise StampError(f"The folder {parent} doesn't exist.")
    for _ in range(20):
        folder = parent / f"{TEMP_PREFIX}{secrets.token_hex(4)}"
        try:
            folder.mkdir()
        except FileExistsError:
            continue
        except OSError as e:
            raise StampError(f"Can't write to the folder {parent} ({e.strerror or e}).") from None
        _hide(folder)
        _LIVE.add(folder)
        return folder
    raise StampError(f"Can't create a temporary folder in {parent}.")


def remove_stale_temp(parent: Path, *, older_than_s: float = STALE_AFTER_S) -> list[Path]:
    """Remove ``~stamper-combine-*`` folders older than ``older_than_s`` in ``parent``.

    Folders left by a crash or a power cut; a folder still in use by this
    process is never removed. Returns the folders removed.
    """
    removed: list[Path] = []
    try:
        entries = list(Path(parent).iterdir())
    except OSError:
        return removed
    now = time.time()
    for entry in entries:
        if not entry.name.startswith(TEMP_PREFIX) or entry in _LIVE:
            continue
        try:
            if not entry.is_dir() or now - entry.stat().st_mtime < older_than_s:
                continue
        except OSError:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        if not entry.exists():
            removed.append(entry)
    return removed


# ------------------------------------------------------------------ form fields (D-098, D-103)


def field_suffix(position: int, attempt: int = 1) -> str:
    """The suffix for a clashing field name from the source at ``position`` (1-based)."""
    return f"_f{position}" if attempt <= 1 else f"_f{position}_{attempt}"


def _root_fields(doc: pymupdf.Document) -> list[int]:
    """Top-level field xrefs: the AcroForm Fields array, plus widget roots missing from it."""
    roots: list[int] = []
    kind, val = doc.xref_get_key(doc.pdf_catalog(), "AcroForm/Fields")
    if kind == "array":
        roots.extend(int(m.group(1)) for m in _REF.finditer(val))
    seen = set(roots)
    for page in doc:
        for w in page.widgets():
            x = w.xref
            for _ in range(32):  # climb to the root; guards against loops
                pk, pv = doc.xref_get_key(x, "Parent")
                if pk != "xref":
                    break
                x = int(pv.split()[0])
            if x not in seen:
                seen.add(x)
                roots.append(x)
    return roots


def _field_name(doc: pymupdf.Document, xref: int) -> str | None:
    kind, val = doc.xref_get_key(xref, "T")
    return val if kind == "string" else None


def rename_clashing_fields(
    doc: pymupdf.Document, position: int, used: set[str]
) -> list[tuple[str, str]]:
    """Rename this document's top-level fields that clash with ``used`` (in memory only, D-103).

    The first file in the combine order to use a name keeps it; a later file's
    clashing field becomes ``<name>_f<position>`` (``_f<position>_2`` and so
    on if that is taken too). Only top-level names change: kids keep their
    partial names, so a radio group or ``person.first`` / ``person.last``
    moves as one unit and stays one field. ``used`` is updated with this
    file's final names. Returns the ``(old, new)`` pairs.
    """
    roots = _root_fields(doc)
    own = {n for n in (_field_name(doc, x) for x in roots) if n is not None}
    renamed: list[tuple[str, str]] = []
    final: set[str] = set()
    done: dict[str, str] = {}  # one root name used twice in the same file stays one name
    for xref in roots:
        name = _field_name(doc, xref)
        if name is None:
            continue
        if name in done:
            new = done[name]
        elif name in used:
            attempt = 1
            new = name + field_suffix(position, attempt)
            while new in used or new in own or new in final:
                attempt += 1
                new = name + field_suffix(position, attempt)
            renamed.append((name, new))
            done[name] = new
        else:
            new = name
            done[name] = new
        if new != name:
            doc.xref_set_key(xref, "T", pymupdf.get_pdf_str(new))
        final.add(new)
    used.update(final)
    return renamed


def _has_field_script(doc: pymupdf.Document) -> bool:
    """True if any form field carries JavaScript actions (calculations, formatting)."""
    for page in doc:
        for w in page.widgets():
            for key in ("AA", "Parent/AA"):
                if doc.xref_get_key(w.xref, key)[0] != "null":
                    return True
    return False


# ------------------------------------------------------------------ layers


def _refs(doc: pymupdf.Document, xref: int, key: str) -> list[int]:
    kind, val = doc.xref_get_key(xref, key)
    if kind != "array":
        return []
    return [int(m.group(1)) for m in _REF.finditer(val)]


@dataclass
class _Layers:
    """Layer state of one source, by source xref, before copying."""

    label: str
    order: list[int]
    off: set[int]


def _mark_layers(doc: pymupdf.Document, position: int, label: str) -> _Layers | None:
    """Tag each layer (OCG) object with a private key so it can be found after copying."""
    cat = doc.pdf_catalog()
    ocgs = _refs(doc, cat, "OCProperties/OCGs")
    if not ocgs:
        return None
    on = set(_refs(doc, cat, "OCProperties/D/ON"))
    off = set(_refs(doc, cat, "OCProperties/D/OFF"))
    kind, base = doc.xref_get_key(cat, "OCProperties/D/BaseState")
    if kind == "name" and base.lstrip("/") == "OFF":
        off |= {x for x in ocgs if x not in on}
    for x in ocgs:
        doc.xref_set_key(x, _MARK, pymupdf.get_pdf_str(f"{position}:{x}"))
    return _Layers(label=label, order=ocgs, off=off)


def _rebuild_layers(out: pymupdf.Document, layers: dict[int, _Layers]) -> None:
    """Write one layer list for the combined file from every source's layers.

    Hidden layers stay hidden; the layers panel groups them under each source
    file's name. Alternate configurations and radio-button layer groups are
    not carried over.
    """
    if not layers:
        return
    found: dict[str, int] = {}
    for x in range(1, out.xref_length()):
        try:
            kind, val = out.xref_get_key(x, _MARK)
        except Exception:  # free or damaged entries
            continue
        if kind == "string":
            found[val] = x
            out.xref_set_key(x, _MARK, "null")
    all_refs: list[int] = []
    off_refs: list[int] = []
    order_parts: list[str] = []
    for position in sorted(layers):
        info = layers[position]
        mapped = [found[f"{position}:{x}"] for x in info.order if f"{position}:{x}" in found]
        if not mapped:
            continue
        all_refs.extend(mapped)
        off_refs.extend(found[f"{position}:{x}"] for x in info.order
                        if x in info.off and f"{position}:{x}" in found)
        order_parts.append(
            "[" + pymupdf.get_pdf_str(info.label) + " " + " ".join(f"{r} 0 R" for r in mapped) + "]")
    if not all_refs:
        return
    refs = " ".join(f"{r} 0 R" for r in all_refs)
    off = " ".join(f"{r} 0 R" for r in off_refs)
    props = out.get_new_xref()
    out.update_object(
        props,
        f"<</OCGs[{refs}]/D<</Name(Combined)/BaseState/ON/OFF[{off}]/Order[{' '.join(order_parts)}]>>>>",
    )
    out.xref_set_key(out.pdf_catalog(), "OCProperties", f"{props} 0 R")


# ------------------------------------------------------------------ the combine step


def _label(path: Path) -> str:
    return path.name


def _toc_for(doc: pymupdf.Document, label: str, offset: int) -> list[list[object]]:
    """The source's bookmark under one top-level bookmark named after it."""
    toc: list[list[object]] = [[1, label, offset + 1]]
    for entry in doc.get_toc(simple=True):
        level, title, page = int(entry[0]), str(entry[1]), int(entry[2])
        toc.append([level + 1, title, page + offset if page > 0 else offset + 1])
    return toc


def _copy_attachments(src: pymupdf.Document, out: pymupdf.Document, label: str) -> int:
    count = 0
    for name in src.embfile_names():
        try:
            data = src.embfile_get(name)
            info = src.embfile_info(name)
        except Exception:  # an unreadable attachment is reported, not fatal
            continue
        new = f"{Path(label).stem} - {name}"
        existing = set(out.embfile_names())
        n = 2
        base = new
        while new in existing:
            new = f"{base} ({n})"
            n += 1
        out.embfile_add(new, data, filename=info.get("filename") or name, desc=info.get("description") or "")
        count += 1
    return count


def combine_pdfs(
    inputs: Sequence[CombineInput],
    folder: Path,
    *,
    accept_signature_loss: bool = False,
    accept_restrictions: bool = False,
    progress: CombineProgress | None = None,
    cancel: CancelCheck | None = None,
) -> CombinedFile:
    """Combine ``inputs``, in order, into one temporary PDF inside ``folder``.

    Raises the handler's errors for a source (PasswordRequired, WrongPassword,
    UnsupportedDocument for a damaged or XFA file), ConfirmationRequired
    naming every signed (``"signature"``) or restricted (``"restrictions"``)
    source unless accepted, and Cancelled. On any error, nothing is left
    behind. The caller must call ``cleanup()`` on the result.
    """
    if len(inputs) < 2:
        raise StampError("Combining needs at least two files.")
    handler = PdfHandler()

    # Check every source before writing anything, so problems show up front.
    signed: list[str] = []
    restricted: list[str] = []
    protected: list[str] = []
    for item in inputs:
        info = handler.inspect(Path(item.path), item.password)
        if info.xfa:
            raise UnsupportedDocument(
                f"{info.path.name} is an XFA form, which this tool can't stamp. "
                "Print it to PDF first, then stamp the printed copy.")
        if info.signed:
            signed.append(info.path.name)
        if info.restricted:
            restricted.append(info.path.name)
        if info.encrypted:
            protected.append(info.path.name)
    if signed and not accept_signature_loss:
        raise ConfirmationRequired(
            "signature",
            f"These files are digitally signed: {', '.join(signed)}. The combined file won't "
            "carry a valid signature; the originals keep theirs. Confirm to continue.")
    if restricted and not accept_restrictions:
        raise ConfirmationRequired(
            "restrictions",
            f"These files have owner restrictions that don't allow changes: {', '.join(restricted)}. "
            "Confirm to combine them anyway; the combined file has no restrictions.")

    first = Path(inputs[0].path)
    temp = _make_temp_folder(Path(folder))
    target = temp / first.name if first.suffix.lower() == ".pdf" else temp / f"{first.stem}.pdf"
    out = pymupdf.open()
    try:
        used: set[str] = set()
        ranges: list[SourceRange] = []
        renamed: dict[str, list[tuple[str, str]]] = {}
        toc: list[list[object]] = []
        layers: dict[int, _Layers] = {}
        lines: list[str] = []
        notes: list[str] = []
        repaired: list[str] = []
        tagged: list[str] = []
        scripted: list[str] = []
        attachments = 0
        labelled = False
        for position, item in enumerate(inputs, start=1):
            if cancel is not None and cancel():
                raise Cancelled("Combining was cancelled. Nothing was saved.")
            src_path = Path(item.path)
            label = _label(src_path)
            with handler._open(src_path, item.password) as (doc, _needed):
                if doc.is_repaired:
                    repaired.append(label)
                if doc.xref_get_key(doc.pdf_catalog(), "StructTreeRoot")[0] != "null":
                    tagged.append(label)
                if doc.get_page_labels():
                    labelled = True
                pairs = rename_clashing_fields(doc, position, used)
                if pairs:
                    renamed[label] = pairs
                    if _has_field_script(doc):
                        scripted.append(label)
                mark = _mark_layers(doc, position, label)
                if mark is not None:
                    layers[position] = mark
                offset = out.page_count
                toc.extend(_toc_for(doc, label, offset))
                out.insert_pdf(doc, links=True, annots=True, widgets=True)
                attachments += _copy_attachments(doc, out, label)
                ranges.append(SourceRange(src_path, offset + 1, out.page_count))
                lines.append(f"{label}|{offset + 1}-{out.page_count}")
            if progress is not None:
                progress(position, len(inputs), label)

        _rebuild_layers(out, layers)
        out.set_toc(toc)
        out.set_metadata({})  # no source's document properties are carried over (D-102)
        info_xref = out.get_new_xref()
        out.update_object(info_xref, "<<>>")
        out.xref_set_key(info_xref, SOURCES_PROPERTY, pymupdf.get_pdf_str("\n".join(lines)))
        out.xref_set_key(-1, "Info", f"{info_xref} 0 R")

        if cancel is not None and cancel():
            raise Cancelled("Combining was cancelled. Nothing was saved.")

        notes.append(f"Bookmarks: one for each of the {len(inputs)} files, with each file's own "
                     "bookmarks under it.")
        for label, pairs in renamed.items():
            listed = ", ".join(f"{a} → {b}" for a, b in pairs[:4])
            more = f" and {len(pairs) - 4} more" if len(pairs) > 4 else ""
            notes.append(f"Form fields in {label} were renamed so they stay separate: {listed}{more}.")
        if scripted:
            notes.append(f"Form-field scripts in {', '.join(scripted)} may still refer to the old field "
                         "names; check calculated fields in the combined file.")
        if repaired:
            notes.append(f"{', '.join(repaired)} {'was' if len(repaired) == 1 else 'were'} damaged and "
                         "repaired when opened. Check the combined file carefully.")
        if tagged:
            notes.append(f"Accessibility tags in {', '.join(tagged)} weren't kept in the combined file.")
        if labelled:
            notes.append("Page labels from the originals weren't kept; pages are numbered across the "
                         "whole file.")
        if attachments:
            plural = attachments != 1
            notes.append(f"{attachments} attached file{'s' if plural else ''} from the originals "
                         f"{'were' if plural else 'was'} kept, named after the file each came from.")
        out.save(target, garbage=1, deflate=True)
    except BaseException:
        out.close()
        shutil.rmtree(temp, ignore_errors=True)
        _LIVE.discard(temp)
        raise
    out.close()
    return CombinedFile(
        path=target,
        folder=temp,
        sources=tuple(ranges),
        protected=tuple(protected),
        signed=tuple(signed),
        renamed_fields=renamed,
        notes=tuple(notes),
    )
