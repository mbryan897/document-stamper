"""Stamp text templates.

A template is plain text with ``{token}`` placeholders. ``{{`` and ``}}``
write literal braces. Tokens are field names or the built-ins ``{page}``,
``{total}``, ``{original_name}`` and ``{date}``.

Optional groups (D-083, O-006)
------------------------------
Text in square brackets belongs to the tokens inside it: ``[Title: {doc_title}]``
is left out entirely when any token inside the brackets is empty, and counts
as one empty value for the separator rule below. A group with no tokens is
always printed (without the brackets). Groups can't be nested. ``[[`` and
``]]`` write literal square brackets.

Empty values and their separators
---------------------------------
When a token's value is empty, the token is removed together with the
separator touching it on the left: the run of spaces and punctuation between
it and the text before it. If there is no text before it (the token starts the
stamp), the separator on its right goes instead. Letters and digits in the
template are never removed, and neither are characters that came from another
value or from ``{{``/``}}``. So, with ``doc_type`` empty::

    "{identifier}  |  {doc_type}  |  Page {page} of {total}"
    -> "99-2026-999901  |  Page 1 of 3"

and with ``identifier`` empty as well::

    -> "Page 1 of 3"

Empty values are processed left to right, then the result is trimmed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from .patterns import DateFormat

__all__ = [
    "BUILTIN_TOKENS",
    "GroupEnd",
    "GroupStart",
    "Literal",
    "TemplateError",
    "Token",
    "builtin_values",
    "parse_template",
    "render",
    "template_tokens",
]

BUILTIN_TOKENS: tuple[str, ...] = ("page", "total", "original_name", "date")

_TOKEN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*", re.ASCII)


class TemplateError(ValueError):
    """A template's braces or token names can't be read."""


@dataclass(frozen=True, slots=True)
class Literal:
    """Template text; ``escaped`` marks a brace written as ``{{`` or ``}}``."""

    text: str
    escaped: bool = False


@dataclass(frozen=True, slots=True)
class Token:
    """A ``{name}`` placeholder."""

    name: str


@dataclass(frozen=True, slots=True)
class GroupStart:
    """``[``: the start of an optional group."""


@dataclass(frozen=True, slots=True)
class GroupEnd:
    """``]``: the end of an optional group."""


Segment = Literal | Token | GroupStart | GroupEnd


def parse_template(text: str) -> list[Segment]:
    """Split a template into literal text and tokens; raises TemplateError."""
    segments: list[Segment] = []
    buf: list[str] = []
    i, n = 0, len(text)
    group_at: int | None = None  # character index of the open "[", if any

    def flush() -> None:
        if buf:
            segments.append(Literal("".join(buf)))
            buf.clear()

    while i < n:
        ch = text[i]
        if ch == "{":
            if text.startswith("{{", i):
                flush()
                segments.append(Literal("{", escaped=True))
                i += 2
                continue
            end = text.find("}", i + 1)
            if end == -1:
                raise TemplateError(
                    f"the {{ at character {i + 1} is never closed; write {{{{ for a literal brace"
                )
            name = text[i + 1 : end]
            if not _TOKEN_NAME.fullmatch(name):
                raise TemplateError(
                    f'"{{{name}}}" at character {i + 1} isn\'t a valid token; '
                    "token names use letters, digits and underscores"
                )
            flush()
            segments.append(Token(name))
            i = end + 1
            continue
        if ch == "[":
            if text.startswith("[[", i):
                flush()
                segments.append(Literal("[", escaped=True))
                i += 2
                continue
            if group_at is not None:
                raise TemplateError(
                    f"the [ at character {i + 1} starts a group inside the group opened at "
                    f"character {group_at + 1}; groups can't be nested (write [[ for a literal bracket)"
                )
            flush()
            segments.append(GroupStart())
            group_at = i
            i += 1
            continue
        if ch == "]":
            if text.startswith("]]", i):
                flush()
                segments.append(Literal("]", escaped=True))
                i += 2
                continue
            if group_at is None:
                raise TemplateError(
                    f"the ] at character {i + 1} has no matching [; write ]] for a literal bracket"
                )
            flush()
            segments.append(GroupEnd())
            group_at = None
            i += 1
            continue
        if ch == "}":
            if text.startswith("}}", i):
                flush()
                segments.append(Literal("}", escaped=True))
                i += 2
                continue
            raise TemplateError(
                f"the }} at character {i + 1} has no matching {{; write }}}} for a literal brace"
            )
        buf.append(ch)
        i += 1
    flush()
    if group_at is not None:
        raise TemplateError(
            f"the [ at character {group_at + 1} is never closed; write [[ for a literal bracket"
        )
    return segments


def template_tokens(text: str) -> list[str]:
    """Token names used in a template, in order of first use."""
    seen: dict[str, None] = {}
    for seg in parse_template(text):
        if isinstance(seg, Token):
            seen.setdefault(seg.name, None)
    return list(seen)


_EMPTY = object()  # marks where an empty token was


def render(text: str, values: Mapping[str, str]) -> str:
    """Fill a template, tidying separators around empty values.

    ``values`` must hold every token in the template; a missing key raises
    KeyError, because config validation should have caught it. A value that
    is only whitespace counts as empty.
    """
    # Each item is (char, protected) or _EMPTY. Protected characters came
    # from a value or an escaped brace and are never removed. An optional
    # group is collected separately and becomes one _EMPTY if any of its
    # tokens is empty.
    items: list[tuple[str, bool] | object] = []
    group: list[tuple[str, bool]] | None = None
    group_empty = False
    for seg in parse_template(text):
        if isinstance(seg, GroupStart):
            group, group_empty = [], False
            continue
        if isinstance(seg, GroupEnd):
            assert group is not None
            if group_empty:
                items.append(_EMPTY)
            else:
                items.extend(group)
            group = None
            continue
        if isinstance(seg, Token):
            value = values[seg.name]
            if group is not None:
                if value.strip():
                    group.extend((c, True) for c in value)
                else:
                    group_empty = True
            elif value.strip():
                items.extend((c, True) for c in value)
            else:
                items.append(_EMPTY)
        else:
            chars = [(c, seg.escaped) for c in seg.text]
            (group if group is not None else items).extend(chars)  # type: ignore[arg-type]

    def removable(item: object) -> bool:
        if item is _EMPTY:
            return False
        ch, protected = item  # type: ignore[misc]
        return not protected and not ch.isalnum()

    while _EMPTY in items:
        i = items.index(_EMPTY)
        left = i
        while left > 0 and removable(items[left - 1]):
            left -= 1
        right = i + 1
        has_text_before = left > 0
        if not has_text_before:
            while right < len(items) and removable(items[right]):
                right += 1
        del items[left:right]

    return "".join(item[0] for item in items).strip()  # type: ignore[index]


def builtin_values(
    *,
    page: int,
    total: int,
    original_name: str,
    today: date,
    date_format: DateFormat,
) -> dict[str, str]:
    """Values for the built-in tokens on one page.

    ``original_name`` is the original file's name without its extension.
    ``today`` is the local date at stamping time.
    """
    return {
        "page": str(page),
        "total": str(total),
        "original_name": original_name,
        "date": date_format.format(today),
    }
