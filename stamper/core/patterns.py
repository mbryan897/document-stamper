"""Simple validation patterns and date formats.

Users write rules like ``SOYYMMDD-######`` instead of regular expressions.
The same tokens describe the ``{date}`` format (``MM-DD-YYYY``).

Pattern syntax
--------------
=========  ===============================================================
``#``      one digit 0-9
``@``      one letter A-Z (either case)
``?``      one letter or digit
``*``      anything, one or more characters (at most two ``*`` per pattern)
``YYYY``   4-digit year
``YY``     2-digit year, always read as 2000 + YY
``MM``     month 01-12
``DD``     day 01-31; with ``MM`` the whole date must exist on the calendar
``\\x``    the character ``x`` literally (``\\#``, ``\\D``)
other      itself; letters compare exactly, so apply an ``upper`` normalizer
=========  ===============================================================

A single ``Y``, ``M`` or ``D`` is literal text. Runs of other lengths
(``YYY``, ``MMM``) are errors, because their meaning is ambiguous. Tokens are
uppercase only; ``yy`` is literal text.

Patterns compile to an ASCII-only regular expression, so fullwidth or
non-Latin digits never pass as ``#``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from datetime import date
from enum import Enum

__all__ = [
    "MAX_STARS",
    "CompiledPattern",
    "DateCheck",
    "DateFormat",
    "PatternError",
    "PatternTest",
    "TokenKind",
    "compile_pattern",
    "example",
    "explain",
    "parse_date_format",
    "regex_risk",
    "test_value",
]


MAX_STARS = 2  # ``*`` tokens allowed in one pattern (D-118)


class PatternError(ValueError):
    """A pattern or date format can't be understood.

    ``position`` is the 1-based character position in the pattern, or 0 when
    the problem isn't tied to one place.
    """

    def __init__(self, message: str, position: int = 0) -> None:
        self.position = position
        where = f"character {position}: " if position else ""
        super().__init__(f"{where}{message}")


class TokenKind(Enum):
    """The kinds of pattern token."""

    LITERAL = "literal"
    DIGIT = "digit"
    LETTER = "letter"
    ALNUM = "alnum"
    ANY = "any"
    YEAR4 = "year4"
    YEAR2 = "year2"
    MONTH = "month"
    DAY = "day"


@dataclass(frozen=True, slots=True)
class _Token:
    kind: TokenKind
    text: str  # the literal character, or the source text of the token
    position: int  # 1-based position in the pattern


_DATE_KINDS = {TokenKind.YEAR4, TokenKind.YEAR2, TokenKind.MONTH, TokenKind.DAY}

_REGEX: dict[TokenKind, str] = {
    TokenKind.DIGIT: "[0-9]",
    TokenKind.LETTER: "[A-Za-z]",
    TokenKind.ALNUM: "[A-Za-z0-9]",
    TokenKind.ANY: ".+",
    TokenKind.YEAR4: "(?P<yyyy>[0-9]{4})",
    TokenKind.YEAR2: "(?P<yy>[0-9]{2})",
    TokenKind.MONTH: "(?P<mm>[0-9]{2})",
    TokenKind.DAY: "(?P<dd>[0-9]{2})",
}

# Shorter descriptions used for "expected ..." messages.
_EXPECT: dict[TokenKind, str] = {
    TokenKind.DIGIT: "a digit",
    TokenKind.LETTER: "a letter",
    TokenKind.ALNUM: "a letter or digit",
    TokenKind.ANY: "at least one more character",
    TokenKind.YEAR4: "a 4-digit year",
    TokenKind.YEAR2: "a 2-digit year",
    TokenKind.MONTH: "a 2-digit month",
    TokenKind.DAY: "a 2-digit day",
}

_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)


def _parse(source: str) -> list[_Token]:
    """Split a pattern into tokens, raising PatternError on bad syntax."""
    if source == "":
        raise PatternError("the pattern is empty")
    tokens: list[_Token] = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        pos = i + 1
        if ch == "\\":
            if i + 1 >= n:
                raise PatternError(
                    "a backslash must be followed by the character it makes literal",
                    pos,
                )
            tokens.append(_Token(TokenKind.LITERAL, source[i + 1], pos))
            i += 2
            continue
        if ch in "YMD":
            j = i
            while j < n and source[j] == ch:
                j += 1
            run = j - i
            if run == 1:
                tokens.append(_Token(TokenKind.LITERAL, ch, pos))
            elif ch == "Y" and run in (2, 4):
                kind = TokenKind.YEAR4 if run == 4 else TokenKind.YEAR2
                tokens.append(_Token(kind, ch * run, pos))
            elif ch in "MD" and run == 2:
                kind = TokenKind.MONTH if ch == "M" else TokenKind.DAY
                tokens.append(_Token(kind, ch * run, pos))
            else:
                allowed = "YY or YYYY" if ch == "Y" else ch * 2
                raise PatternError(
                    f'"{ch * run}" isn\'t a date part (use {allowed}); '
                    f"to match a literal {ch}, write \\{ch}",
                    pos,
                )
            i = j
            continue
        kind = {
            "#": TokenKind.DIGIT,
            "@": TokenKind.LETTER,
            "?": TokenKind.ALNUM,
            "*": TokenKind.ANY,
        }.get(ch, TokenKind.LITERAL)
        tokens.append(_Token(kind, ch, pos))
        i += 1

    stars = [t for t in tokens if t.kind is TokenKind.ANY]
    if len(stars) > MAX_STARS:
        # Each * can match any run of text, so several of them make a failed
        # match slow: four took about 100 ms on 128 characters (D-118).
        raise PatternError(f"a rule can have at most {MAX_STARS} *, because more can take too long to check",
                           stars[MAX_STARS].position)

    seen: dict[str, _Token] = {}
    for tok in tokens:
        if tok.kind not in _DATE_KINDS:
            continue
        group = "year" if tok.kind in (TokenKind.YEAR2, TokenKind.YEAR4) else tok.kind.value
        if group in seen:
            raise PatternError(
                f"the {group} appears twice (first at character {seen[group].position})",
                tok.position,
            )
        seen[group] = tok
    return tokens


def _regex_for(tokens: list[_Token]) -> str:
    return "".join(
        re.escape(t.text) if t.kind is TokenKind.LITERAL else _REGEX[t.kind]
        for t in tokens
    )


@dataclass(frozen=True, slots=True)
class DateCheck:
    """Outcome of checking the date parts of a matched value."""

    ok: bool
    message: str = ""


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        return 29 if leap else 28
    return 30 if month in (4, 6, 9, 11) else 31


def _check_date(groups: dict[str, str | None]) -> DateCheck:
    """Check month, day and their combination from named regex groups."""
    yyyy, yy = groups.get("yyyy"), groups.get("yy")
    mm, dd = groups.get("mm"), groups.get("dd")
    year: int | None = int(yyyy) if yyyy is not None else (2000 + int(yy) if yy is not None else None)
    if year == 0 and mm is not None and dd is not None:  # a year alone is just 4 digits
        return DateCheck(False, "year 0000 isn't a real year")
    if mm is not None:
        month = int(mm)
        if not 1 <= month <= 12:
            return DateCheck(False, f"month {mm} isn't a real month (01-12)")
    if dd is not None:
        day = int(dd)
        if not 1 <= day <= 31:
            return DateCheck(False, f"day {dd} isn't a real day (01-31)")
        if mm is not None:
            month = int(mm)
            # Without a year, allow Feb 29 by checking against a leap year.
            limit = _days_in_month(year if year is not None else 2000, month)
            if day > limit:
                name = _MONTH_NAMES[month - 1]
                if month == 2 and day == 29 and year is not None:
                    return DateCheck(
                        False, f"{year} isn't a leap year, so February has 28 days"
                    )
                return DateCheck(False, f"{name} has only {limit} days")
    return DateCheck(True)


@dataclass(frozen=True, slots=True)
class PatternTest:
    """Result of testing one value against one pattern.

    ``matched`` is the number of characters at the start of the value that
    fit the pattern, so a caller can pick the closest of several formats.
    ``position`` is the 1-based character in the value where it went wrong,
    or 0 when the problem is the value as a whole (such as a bad date).
    """

    ok: bool
    message: str
    matched: int
    position: int = 0


def _describe_char(ch: str) -> str:
    if ch == " ":
        return "a space"
    if ch.isspace():
        return "a whitespace character"
    names = {"–": "an en dash (–)", "—": "an em dash (—)"}
    return names.get(ch, f"'{ch}'")


class CompiledPattern:
    """A parsed and compiled validation pattern."""

    def __init__(self, source: str) -> None:
        self.source = source
        self._tokens = _parse(source)
        self.body = _regex_for(self._tokens)
        self.regex = re.compile(self.body, re.ASCII)
        self.has_wildcard = any(t.kind is TokenKind.ANY for t in self._tokens)
        self._prefixes: list[re.Pattern[str]] | None = None

    def __repr__(self) -> str:
        return f"CompiledPattern({self.source!r})"

    def matches(self, value: str) -> bool:
        """True if the whole value fits the pattern and any date is real."""
        m = self.regex.fullmatch(value)
        return m is not None and _check_date(m.groupdict()).ok

    def check_date_parts(self, value: str) -> DateCheck | None:
        """Date check for a value that fits the shape, or None if it doesn't."""
        m = self.regex.fullmatch(value)
        return None if m is None else _check_date(m.groupdict())

    def date_of(self, value: str) -> date | None:
        """The calendar date a matching value encodes, or None.

        Only patterns with a year (``YYYY`` or ``YY``), ``MM`` and ``DD``
        encode a full date. None if the value doesn't match or the date isn't
        real. ``YY`` is 2000 + YY (D-035).
        """
        m = self.regex.fullmatch(value)
        if m is None or not _check_date(m.groupdict()).ok:
            return None
        g = m.groupdict()
        yyyy, yy, mm, dd = g.get("yyyy"), g.get("yy"), g.get("mm"), g.get("dd")
        if mm is None or dd is None or (yyyy is None and yy is None):
            return None
        year = int(yyyy) if yyyy is not None else 2000 + int(yy)  # type: ignore[arg-type]
        return date(year, int(mm), int(dd))

    def test(self, value: str) -> PatternTest:
        """Test a value and say where it first goes wrong."""
        m = self.regex.fullmatch(value)
        if m is not None:
            check = _check_date(m.groupdict())
            if check.ok:
                return PatternTest(True, f"Matches {self.source}", len(value))
            return PatternTest(False, f"Not a real date: {check.message}", len(value))

        if self._prefixes is None:
            self._prefixes = [
                re.compile(_regex_for(self._tokens[:k]), re.ASCII)
                for k in range(len(self._tokens) + 1)
            ]
        # Longest run of tokens that fits the start of the value.
        k_best, end_best = 0, 0
        for k, rx in enumerate(self._prefixes):
            pm = rx.match(value)
            if pm is None:
                break
            k_best, end_best = k, pm.end()
        if k_best == len(self._tokens):
            return PatternTest(
                False,
                f"Too long: the pattern ends after character {end_best}, "
                f"but {_describe_char(value[end_best])} follows",
                end_best,
                end_best + 1,
            )
        tok = self._tokens[k_best]
        expected = f"'{tok.text}'" if tok.kind is TokenKind.LITERAL else _EXPECT[tok.kind]
        if end_best >= len(value):
            return PatternTest(
                False, f"Too short: expected {expected} after character {end_best}",
                end_best, end_best + 1,
            )
        return PatternTest(
            False,
            f"Character {end_best + 1}: expected {expected}, "
            f"found {_describe_char(value[end_best])}",
            end_best,
            end_best + 1,
        )

    def example(self, today: date) -> str:
        """A value that fits the pattern, for previews and the test box.

        Digits count 1, 2, 3 …, letters A, B, C …, date parts are ``today``.
        """
        out: list[str] = []
        digit = letter = 0
        for t in self._tokens:
            match t.kind:
                case TokenKind.LITERAL:
                    out.append(t.text)
                case TokenKind.DIGIT:
                    out.append("1234567890"[digit % 10])
                    digit += 1
                case TokenKind.LETTER | TokenKind.ALNUM:
                    out.append("ABCDEFGHIJKLMNOPQRSTUVWXYZ"[letter % 26])
                    letter += 1
                case TokenKind.ANY:
                    out.append("Example")
                case TokenKind.YEAR4:
                    out.append(f"{today.year:04d}")
                case TokenKind.YEAR2:
                    out.append(f"{today.year % 100:02d}")
                case TokenKind.MONTH:
                    out.append(f"{today.month:02d}")
                case TokenKind.DAY:
                    out.append(f"{today.day:02d}")
        return "".join(out)

    def explain(self) -> str:
        """Plain-English description of the pattern, for the help screen."""
        parts: list[str] = []
        literal = ""
        i = 0
        toks = self._tokens
        while i < len(toks):
            t = toks[i]
            if t.kind is TokenKind.LITERAL:
                literal += t.text
                i += 1
                continue
            if literal:
                parts.append(f'"{literal}"')
                literal = ""
            if t.kind in (TokenKind.DIGIT, TokenKind.LETTER, TokenKind.ALNUM):
                j = i
                while j < len(toks) and toks[j].kind is t.kind:
                    j += 1
                count = j - i
                noun = {
                    TokenKind.DIGIT: ("digit", "digits"),
                    TokenKind.LETTER: ("letter", "letters"),
                    TokenKind.ALNUM: ("letter or digit", "letters or digits"),
                }[t.kind]
                parts.append(f"{count} {noun[0] if count == 1 else noun[1]}")
                i = j
                continue
            parts.append({
                TokenKind.ANY: "any text",
                TokenKind.YEAR4: "4-digit year",
                TokenKind.YEAR2: "2-digit year",
                TokenKind.MONTH: "month (01-12)",
                TokenKind.DAY: "day (01-31)",
            }[t.kind])
            i += 1
        if literal:
            parts.append(f'"{literal}"')
        text = ", then ".join(parts)
        kinds = {t.kind for t in toks}
        if TokenKind.MONTH in kinds and TokenKind.DAY in kinds:
            text += "; month and day must be a real date"
        return text[0].upper() + text[1:] + "."


@lru_cache(maxsize=512)
def compile_pattern(source: str) -> CompiledPattern:
    """Parse and compile a validation pattern; raises PatternError.

    Results are cached, so calling this on every keystroke is cheap.
    """
    return CompiledPattern(source)


def explain(source: str) -> str:
    """Describe a pattern in plain English; raises PatternError."""
    return compile_pattern(source).explain()


def example(source: str, today: date) -> str:
    """A value that fits a pattern (see ``CompiledPattern.example``); raises PatternError."""
    return compile_pattern(source).example(today)


def test_value(source: str, value: str) -> PatternTest:
    """Test one value against a pattern; raises PatternError for a bad pattern."""
    return compile_pattern(source).test(value)


test_value.__test__ = False  # type: ignore[attr-defined]  # not a pytest test


class DateFormat:
    """A date format written with the pattern tokens, e.g. ``MM-DD-YYYY``."""

    def __init__(self, source: str) -> None:
        tokens = _parse(source)
        for t in tokens:
            if t.kind in (TokenKind.DIGIT, TokenKind.LETTER, TokenKind.ALNUM, TokenKind.ANY):
                raise PatternError(
                    f"'{t.text}' can't be used in a date format; use YYYY or YY, MM and DD",
                    t.position,
                )
        kinds = {t.kind for t in tokens}
        missing = [
            name
            for name, ok in (
                ("a year (YYYY or YY)", kinds & {TokenKind.YEAR4, TokenKind.YEAR2}),
                ("a month (MM)", TokenKind.MONTH in kinds),
                ("a day (DD)", TokenKind.DAY in kinds),
            )
            if not ok
        ]
        if missing:
            raise PatternError("a date format needs " + ", ".join(missing))
        self.source = source
        self._tokens = tokens
        self._regex = re.compile(_regex_for(tokens), re.ASCII)

    def __repr__(self) -> str:
        return f"DateFormat({self.source!r})"

    def format(self, d: date) -> str:
        """Render a date in this format."""
        out: list[str] = []
        for t in self._tokens:
            match t.kind:
                case TokenKind.YEAR4:
                    out.append(f"{d.year:04d}")
                case TokenKind.YEAR2:
                    out.append(f"{d.year % 100:02d}")
                case TokenKind.MONTH:
                    out.append(f"{d.month:02d}")
                case TokenKind.DAY:
                    out.append(f"{d.day:02d}")
                case _:
                    out.append(t.text)
        return "".join(out)

    def parse(self, text: str) -> date | None:
        """Read a date written in this format, or None if it isn't one."""
        m = self._regex.fullmatch(text)
        if m is None:
            return None
        g = m.groupdict()
        if not _check_date(g).ok:
            return None
        year = int(g["yyyy"]) if g.get("yyyy") is not None else 2000 + int(g["yy"])
        if year < 1:
            return None
        return date(year, int(g["mm"]), int(g["dd"]))


@lru_cache(maxsize=64)
def parse_date_format(source: str) -> DateFormat:
    """Parse a date format such as ``MM-DD-YYYY``; raises PatternError (cached)."""
    return DateFormat(source)


# ------------------------------------------------------------------ advanced regex safety (D-118)

# Python's re has no match timeout, so a rule that can backtrack badly would
# freeze the window on every keystroke. Advanced rules are checked here, on
# the parse tree, before they are ever run; this check can't hang because it
# never matches anything. It walks ``re._parser``'s tree (private, stable since
# Python 3.11): anything it doesn't recognise is refused rather than passed,
# and a test fails loudly if the tree's shape changes on a Python upgrade.

import re._constants as _sre  # noqa: E402
import re._parser as _sre_parse  # noqa: E402

_SAMPLE = [chr(i) for i in range(128)] + ["\u00a0", "\u00e9", "\u0661", "\u3000"]
_DIGITS = frozenset("0123456789")
_WORD = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
_SPACE = frozenset(" \t\n\r\f\v")
_ALL = frozenset(_SAMPLE)
_UNBOUNDED = _sre.MAXREPEAT
_REPEATS = (_sre.MAX_REPEAT, _sre.MIN_REPEAT)

_NESTED = "a repeated part contains another open-ended repeat"
_ALTERNATION = "a repeated choice has options that can match the same text"
_ADJACENT = "two open-ended repeats next to each other can match the same characters"
_BACKREF = "it refers back to an earlier group"
_UNKNOWN = "it uses a construct this check can't assess"


class _Risky(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _category(cat: object) -> frozenset[str]:
    table = {
        _sre.CATEGORY_DIGIT: _DIGITS,
        _sre.CATEGORY_NOT_DIGIT: _ALL - _DIGITS,
        _sre.CATEGORY_WORD: _WORD,
        _sre.CATEGORY_NOT_WORD: _ALL - _WORD,
        _sre.CATEGORY_SPACE: _SPACE,
        _sre.CATEGORY_NOT_SPACE: _ALL - _SPACE,
    }
    if cat not in table:
        raise _Risky(_UNKNOWN)
    return table[cat]


def _fold(chars: frozenset[str], ignore_case: bool) -> frozenset[str]:
    if not ignore_case:
        return chars
    return frozenset(chars | {c.swapcase() for c in chars if c.isascii()})


def _class(items: list[tuple[object, object]], ignore_case: bool) -> frozenset[str]:
    chars: set[str] = set()
    negate = False
    for op, av in items:
        if op is _sre.NEGATE:
            negate = True
        elif op is _sre.LITERAL:
            chars.add(chr(av))  # type: ignore[arg-type]
        elif op is _sre.RANGE:
            lo, hi = av  # type: ignore[misc]
            chars.update(c for c in _SAMPLE if lo <= ord(c) <= hi)
        elif op is _sre.CATEGORY:
            chars.update(_category(av))
        else:
            raise _Risky(_UNKNOWN)
    result = _fold(frozenset(chars), ignore_case)
    return _ALL - result if negate else result


@dataclass(frozen=True, slots=True)
class _Info:
    """What a piece of a regex can do: be empty, start with, contain, and repeat without limit."""

    nullable: bool
    first: frozenset[str]
    chars: frozenset[str]
    unbounded: bool


_EMPTY = _Info(True, frozenset(), frozenset(), False)


def _seq(items: list[tuple[object, object]], ignore_case: bool) -> _Info:
    infos = [_node(op, av, ignore_case) for op, av in items]
    # Open-ended repeats separated only by parts that can be empty must not overlap.
    last_open: frozenset[str] | None = None
    for (op, av), info in zip(items, infos, strict=True):
        is_open = op in _REPEATS and av[1] is _UNBOUNDED  # type: ignore[index]
        if is_open:
            if last_open is not None and last_open & info.chars:
                raise _Risky(_ADJACENT)
            last_open = info.chars
        elif not info.nullable:
            last_open = None
    nullable = all(i.nullable for i in infos)
    first: set[str] = set()
    for info in infos:
        first |= info.first
        if not info.nullable:
            break
    chars = frozenset().union(*(i.chars for i in infos)) if infos else frozenset()
    return _Info(nullable, frozenset(first), chars, any(i.unbounded for i in infos))


def _node(op: object, av: object, ignore_case: bool) -> _Info:
    if op is _sre.LITERAL:
        c = _fold(frozenset({chr(av)}), ignore_case)  # type: ignore[arg-type]
        return _Info(False, c, c, False)
    if op is _sre.NOT_LITERAL:
        c = _ALL - _fold(frozenset({chr(av)}), ignore_case)  # type: ignore[arg-type]
        return _Info(False, c, c, False)
    if op is _sre.ANY:
        c = _ALL - {"\n"}
        return _Info(False, c, c, False)
    if op is _sre.IN:
        c = _class(av, ignore_case)  # type: ignore[arg-type]
        return _Info(False, c, c, False)
    if op is _sre.AT:
        return _EMPTY
    if op in (_sre.ASSERT, _sre.ASSERT_NOT):
        _seq(list(av[1]), ignore_case)  # type: ignore[index]  # checked, but matches nothing itself
        return _EMPTY
    if op is _sre.SUBPATTERN:
        _group, add, _del, body = av  # type: ignore[misc]
        return _seq(list(body), ignore_case or bool(add & _sre.SRE_FLAG_IGNORECASE))
    if op is _sre.ATOMIC_GROUP:
        inner = _seq(list(av), ignore_case)  # type: ignore[arg-type]
        return _Info(inner.nullable, inner.first, inner.chars, False)  # can't be backtracked into
    if op is _sre.BRANCH:
        _none, alternatives = av  # type: ignore[misc]
        infos = [_seq(list(a), ignore_case) for a in alternatives]
        return _Info(any(i.nullable for i in infos), frozenset().union(*(i.first for i in infos)),
                     frozenset().union(*(i.chars for i in infos)), any(i.unbounded for i in infos))
    if op is _sre.POSSESSIVE_REPEAT:
        lo, _hi, body = av  # type: ignore[misc]
        inner = _seq(list(body), ignore_case)
        return _Info(lo == 0 or inner.nullable, inner.first, inner.chars, False)  # gives nothing back
    if op in _REPEATS:
        lo, hi, body = av  # type: ignore[misc]
        inner = _seq(list(body), ignore_case)
        if hi is _UNBOUNDED or hi > 1:
            if inner.unbounded:
                raise _Risky(_NESTED)
            _check_repeated_choice(list(body), ignore_case)
        return _Info(lo == 0 or inner.nullable, inner.first, inner.chars, inner.unbounded or hi is _UNBOUNDED)
    if op in (_sre.GROUPREF, _sre.GROUPREF_EXISTS):
        raise _Risky(_BACKREF)
    raise _Risky(_UNKNOWN)


def _check_repeated_choice(body: list[tuple[object, object]], ignore_case: bool) -> None:
    """Inside a repeat, a choice's options must not overlap or be empty."""
    for op, av in body:
        if op is _sre.SUBPATTERN:
            _check_repeated_choice(list(av[3]), ignore_case)  # type: ignore[index]
        elif op is _sre.BRANCH:
            infos = [_seq(list(a), ignore_case) for a in av[1]]  # type: ignore[index]
            if any(i.nullable for i in infos):
                raise _Risky(_ALTERNATION)
            for i, a in enumerate(infos):
                for b in infos[i + 1:]:
                    if a.first & b.first:
                        raise _Risky(_ALTERNATION)


def regex_risk(regex: str) -> str | None:
    """Why an advanced rule could take a very long time to check, or None if it can't (D-118).

    Refused: an open-ended repeat inside another repeat (``(a+)+``,
    ``(\\w+\\s?)+``, ``(.*a){20}``); a repeated choice whose options can match
    the same text or nothing (``(a|aa)+``); two open-ended repeats with nothing
    required between them that can match the same characters (``\\d*\\d*``,
    ``.*.*``); backreferences; and anything the check doesn't recognise.
    Atomic groups and possessive repeats (``(?>...)``, ``a++``) are allowed.
    Raises ``re.error`` if the regex doesn't compile.
    """
    tree = _sre_parse.parse(regex, re.ASCII)
    ignore_case = bool(tree.state.flags & _sre.SRE_FLAG_IGNORECASE)
    try:
        _seq(list(tree.data), ignore_case)
    except _Risky as e:
        return e.reason
    except (TypeError, ValueError, AttributeError, IndexError):
        return _UNKNOWN
    return None
