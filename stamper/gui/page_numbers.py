"""The stamp dialog's Page numbers option (O-004, D-065).

"With the identifier" leaves the profile's stamps alone. "Bottom left /
center / right" moves the page-number part of a stamp into a stamp of its own
(D-013) by editing copies of the profile's stamps for this job only
(``StampJob.stamps``, D-061). The config isn't changed.

The page-number part of a template is found like this:

- It starts at the first ``{page}`` and runs to the ``{total}`` after it, if
  one follows with no other token in between. A different token between them
  means the parts can't be separated.
- To the left it takes in letters, digits, single spaces and ``. # :``,
  stopping at a token, a double space or any other character; to the right
  of its last token, letters, digits and single spaces. Edge spaces are
  trimmed. So ``Page {page} of {total}`` and ``p. {page}/{total}`` are each
  one part.
- An optional group (D-083) that holds exactly that part moves with it,
  brackets included: ``[Page {page} of {total}]``.

The first stamp keeps its template with that part removed and the separator
touching it trimmed, as D-041 tidies an empty value: the run on its left, or
the run on its right when nothing precedes it. The new stamp gets the part's
text exactly, at the chosen bottom anchor, along the top, with the original
stamp's size, color, margin and box.

Nothing here imports Qt.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..core.config import StampSpec
from ..core.template import (
    GroupEnd,
    GroupStart,
    Literal,
    Segment,
    TemplateError,
    Token,
    parse_template,
    template_tokens,
)

__all__ = [
    "BOTTOM_CHOICES",
    "CHOICE_LABELS",
    "PROFILE",
    "PageNumberOption",
    "Split",
    "WITH_IDENTIFIER",
    "apply_page_numbers",
    "main_stamp_index",
    "page_number_option",
    "split_page_numbers",
]

WITH_IDENTIFIER = "with-identifier"
PROFILE = "profile"  # page numbers are already their own stamp, not at the bottom
BOTTOM_CHOICES = ("bottom-left", "bottom-center", "bottom-right")
CHOICE_LABELS = {
    WITH_IDENTIFIER: "With the identifier",
    PROFILE: "As in the profile",
    "bottom-left": "Bottom left",
    "bottom-center": "Bottom center",
    "bottom-right": "Bottom right",
}

_LEFT_EXTRA = ".#:"


@dataclass(frozen=True, slots=True)
class Split:
    """A template split into what the first stamp keeps and the page-number part."""

    kept: str
    moved: str


@dataclass(frozen=True, slots=True)
class PageNumberOption:
    """How the Page numbers control looks for a set of stamps.

    ``choices`` are offered in order; ``enabled`` says which can be picked;
    ``current`` is what the profile does; ``note`` explains a disabled
    control or choice. ``stamp`` is the index of the stamp holding ``{page}``.
    """

    choices: tuple[str, ...]
    enabled: tuple[bool, ...]
    current: str
    note: str
    stamp: int | None


def _raw_segments(text: str) -> list[tuple[int, int, Segment]]:
    """Template segments with their (start, end) in the raw template text."""
    out = []
    pos = 0
    for seg in parse_template(text):
        if isinstance(seg, Token):
            width = len(seg.name) + 2
        elif isinstance(seg, (GroupStart, GroupEnd)):
            width = 1
        elif seg.escaped:
            width = 2
        else:
            width = len(seg.text)
        out.append((pos, pos + width, seg))
        pos += width
    return out


def _segment_at(raw_index: int, segs: list[tuple[int, int, Segment]]) -> Segment | None:
    for start, end, seg in segs:
        if start <= raw_index < end:
            return seg
    return None


def _is_plain(raw_index: int, segs: list[tuple[int, int, Segment]]) -> bool:
    for start, end, seg in segs:
        if start <= raw_index < end:
            return isinstance(seg, Literal) and not seg.escaped
    return False


def _tidy_separator_chars(ch: str) -> bool:
    """Characters D-041 would remove next to an empty value (not braces)."""
    return not ch.isalnum() and ch not in "{}"


def split_page_numbers(text: str) -> Split | None:
    """Split a template into kept text and the page-number part.

    None if the template has no ``{page}``, can't be read, or has another
    token between ``{page}`` and the ``{total}`` after it.
    """
    try:
        segs = _raw_segments(text)
    except TemplateError:
        return None
    tokens = [(i, s) for i, s in enumerate(segs) if isinstance(s[2], Token)]
    page_at = next((i for i, (_, _, seg) in enumerate(segs)
                    if isinstance(seg, Token) and seg.name == "page"), None)
    if page_at is None:
        return None

    left, right = segs[page_at][0], segs[page_at][1]
    later = [(i, s) for i, s in tokens if i > page_at]
    total_at = next((i for i, (_, _, seg) in later if seg.name == "total"), None)  # type: ignore[union-attr]
    if total_at is not None:
        between = [seg for i, (_, _, seg) in later if i < total_at]
        if between:
            return None
        right = segs[total_at][1]

    def ok_left(k: int) -> bool:
        ch = text[k]
        if not _is_plain(k, segs):
            return False
        if ch == " ":
            return not (k > 0 and text[k - 1] == " ")
        return ch.isalnum() or ch in _LEFT_EXTRA

    while left > 0 and ok_left(left - 1):
        left -= 1
    while left < len(text) and text[left] == " ":
        left += 1

    def ok_right(k: int) -> bool:
        ch = text[k]
        if not _is_plain(k, segs):
            return False
        if ch == " ":
            return not (k + 1 < len(text) and text[k + 1] == " ")
        return ch.isalnum()

    while right < len(text) and ok_right(right):
        right += 1
    while right > left and text[right - 1] == " ":
        right -= 1
    # An optional group holding exactly the page-number part moves with its brackets (D-083).
    if (left > 0 and isinstance(_segment_at(left - 1, segs), GroupStart)
            and isinstance(_segment_at(right, segs), GroupEnd)):
        left, right = left - 1, right + 1

    moved = text[left:right]
    before, after = text[:left], text[right:]
    if before.strip():
        k = len(before)
        while k > 0 and _tidy_separator_chars(before[k - 1]) and _is_plain(k - 1, segs):
            k -= 1
        kept = before[:k] + after
    else:
        k = 0
        while k < len(after) and _tidy_separator_chars(after[k]) and _is_plain(right + k, segs):
            k += 1
        kept = after[k:]
    kept = kept.strip()
    if not any(ch.isalnum() for ch in kept):
        kept = ""  # only separators left (tokens always contain letters)
    return Split(kept=kept, moved=moved)


def _tokens(text: str) -> list[str]:
    try:
        return template_tokens(text)
    except TemplateError:
        return []


def main_stamp_index(stamps: Sequence[StampSpec]) -> int:
    """The stamp the placement controls edit: the first with ``{identifier}``, else the first."""
    return next((i for i, s in enumerate(stamps) if "identifier" in _tokens(s.text)), 0)


def page_number_option(stamps: Sequence[StampSpec]) -> PageNumberOption:
    """What the Page numbers control offers for these stamps."""
    idx = next((i for i, s in enumerate(stamps) if "page" in _tokens(s.text)), None)
    if idx is None:
        return PageNumberOption((WITH_IDENTIFIER,), (False,), WITH_IDENTIFIER,
                                "This profile's stamp has no page number.", None)
    split = split_page_numbers(stamps[idx].text)
    if split is None:
        return PageNumberOption((WITH_IDENTIFIER,), (False,), WITH_IDENTIFIER,
                                "The page number can't be separated from the rest of this stamp.", idx)

    others = {s.anchor for i, s in enumerate(stamps) if i != idx}
    if not split.kept:
        # Page numbers are already a stamp of their own: offer to move it.
        anchor = stamps[idx].anchor
        current = anchor if anchor in BOTTOM_CHOICES else PROFILE
        choices = ((PROFILE,) if current == PROFILE else ()) + BOTTOM_CHOICES
        enabled = tuple(c == PROFILE or c == current or c not in others for c in choices)
        return PageNumberOption(choices, enabled, current,
                                "Page numbers are already a separate stamp in this profile.", idx)

    taken = others | {stamps[idx].anchor}
    choices = (WITH_IDENTIFIER, *BOTTOM_CHOICES)
    enabled = (True, *(c not in taken for c in BOTTOM_CHOICES))
    note = "" if all(enabled) else "Positions another stamp already uses can't be chosen."
    return PageNumberOption(choices, enabled, WITH_IDENTIFIER, note, idx)


def apply_page_numbers(stamps: Sequence[StampSpec], choice: str) -> tuple[StampSpec, ...]:
    """The stamps to draw for a Page numbers choice; the input isn't changed."""
    result = list(stamps)
    if choice not in BOTTOM_CHOICES:
        return tuple(result)
    option = page_number_option(stamps)
    if option.stamp is None:
        return tuple(result)
    idx = option.stamp
    split = split_page_numbers(stamps[idx].text)
    if split is None:
        return tuple(result)
    source = stamps[idx]
    if not split.kept:
        result[idx] = source.model_copy(update={"anchor": choice})
        return tuple(result)
    result[idx] = source.model_copy(update={"text": split.kept})
    result.append(source.model_copy(update={
        "text": split.moved, "anchor": choice, "direction": "along-top",
    }))
    return tuple(result)
