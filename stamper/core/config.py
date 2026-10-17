"""Load and validate config.json.

``load_config`` reads one file at an explicit path. Finding that path (next to
the exe, then ``%APPDATA%``) and remembering last-used values belong to a
later phase and wrap this function; nothing here reads the environment.

The models use the JSON key names directly and are frozen, so a loaded config
can be dumped back to the same JSON (``Config.to_json``) for Profile settings.

Problems are reported as plain sentences that name where in the file they
are, for example::

    profiles[0] "Records submission": stamps[0] text uses {case_num}, which
    isn't one of this profile's fields. Available: identifier, doc_type, ...
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .filenames import ILLEGAL_FILENAME_CHARS
from .patterns import CompiledPattern, DateFormat, PatternError, compile_pattern, parse_date_format
from .template import BUILTIN_TOKENS, TemplateError, template_tokens

__all__ = [
    "ANCHORS",
    "SCHEMA_VERSION",
    "Config",
    "ConfigError",
    "FieldSpec",
    "FormatSpec",
    "OutputName",
    "Profile",
    "Settings",
    "StampSpec",
    "load_config",
    "parse_config",
]

SCHEMA_VERSION = 1

ANCHORS = (
    "top-left", "top-center", "top-right",
    "middle-left", "middle-center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
)
Anchor = Literal[
    "top-left", "top-center", "top-right",
    "middle-left", "middle-center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
]
Direction = Literal["along-top", "along-right"]
Normalizer = Literal["trim", "upper", "dashes"]
FieldType = Literal["text", "choice", "date"]

MIN_MARGIN_IN = 0.25
MIN_FONT_PT = 6.0
MAX_FONT_PT = 24.0

_FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*", re.ASCII)
_METADATA_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]*", re.ASCII)
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}", re.ASCII)


class ConfigError(Exception):
    """config.json can't be used. ``problems`` holds one sentence per problem."""

    def __init__(self, problems: Sequence[str], source: str = "") -> None:
        self.problems = list(problems)
        self.source = source
        head = f"{source} has {len(self.problems)} problem(s):" if source else "Config problems:"
        super().__init__("\n".join([head, *(f"  - {p}" for p in self.problems)]))


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Settings(_Model):
    """Settings that apply to the whole config."""

    date_format: str = "MM-DD-YYYY"

    @field_validator("date_format")
    @classmethod
    def _check_date_format(cls, v: str) -> str:
        try:
            parse_date_format(v)
        except PatternError as e:
            raise ValueError(f'"{v}" isn\'t a usable date format: {e}') from None
        return v

    @property
    def date_formatter(self) -> DateFormat:
        """The compiled ``date_format``."""
        return parse_date_format(self.date_format)


class FormatSpec(_Model):
    """One accepted format for a text field: a simple ``pattern`` or a ``regex``."""

    name: str | None = None
    pattern: str | None = None
    regex: str | None = None

    @model_validator(mode="after")
    def _one_rule(self) -> FormatSpec:
        if (self.pattern is None) == (self.regex is None):
            raise ValueError('give either "pattern" or "regex", not both and not neither')
        if self.pattern is not None:
            try:
                compile_pattern(self.pattern)
            except PatternError as e:
                raise ValueError(f'pattern "{self.pattern}": {e}') from None
        else:
            assert self.regex is not None
            try:
                re.compile(self.regex, re.ASCII)
            except re.error as e:
                raise ValueError(f'regex "{self.regex}" doesn\'t compile: {e}') from None
        return self

    @property
    def display_name(self) -> str:
        """What the user sees for this format: its name, else the pattern."""
        return self.name or self.pattern or self.regex or ""

    @property
    def compiled(self) -> CompiledPattern | None:
        """The compiled simple pattern, or None for a regex format."""
        return compile_pattern(self.pattern) if self.pattern is not None else None

    @property
    def compiled_regex(self) -> re.Pattern[str] | None:
        """The compiled advanced regex, or None for a pattern format."""
        return re.compile(self.regex, re.ASCII) if self.regex is not None else None


class FieldSpec(_Model):
    """A field the user fills in, defined once and used by profiles."""

    label: str
    type: FieldType = "text"
    required: bool = False
    normalize: list[Normalizer] = Field(default_factory=lambda: ["trim"])
    formats: list[FormatSpec] = Field(default_factory=list)
    taxonomy: str | None = None
    from_filename: bool = False

    @field_validator("label")
    @classmethod
    def _label_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("the label can't be empty")
        return v

    @model_validator(mode="after")
    def _type_rules(self) -> FieldSpec:
        if self.type == "choice" and not self.taxonomy:
            raise ValueError('a "choice" field needs a "taxonomy"')
        if self.type != "choice" and self.taxonomy is not None:
            raise ValueError('only "choice" fields take a "taxonomy"')
        if self.type != "text" and self.formats:
            raise ValueError('only "text" fields take "formats"')
        if self.type != "text" and self.from_filename:
            raise ValueError('only "text" fields can use "from_filename"')
        return self


class StampSpec(_Model):
    """One stamp drawn on each page."""

    text: str
    anchor: Anchor = "top-right"
    direction: Direction = "along-top"
    margin_in: float = MIN_MARGIN_IN
    font_size_pt: float = 9.0
    color: str = "#2B3A8F"
    background_box: bool = True
    pages: Literal["all"] = "all"

    @field_validator("margin_in")
    @classmethod
    def _margin(cls, v: float) -> float:
        if v < MIN_MARGIN_IN:
            raise ValueError(
                f"is {v:g} inches; it must be at least {MIN_MARGIN_IN} inches "
                "so the stamp clears the printer's dead zone"
            )
        return v

    @field_validator("font_size_pt")
    @classmethod
    def _font(cls, v: float) -> float:
        if not MIN_FONT_PT <= v <= MAX_FONT_PT:
            raise ValueError(
                f"is {v:g}; it must be between {MIN_FONT_PT:g} and {MAX_FONT_PT:g}"
            )
        return v

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        if not _COLOR.fullmatch(v):
            raise ValueError(f'color "{v}" must be written as #RRGGBB, for example #2B3A8F')
        return v


class OutputName(_Model):
    """How the suggested filename is built."""

    fields: list[str] = Field(default_factory=list)
    separator: str = "_"
    include_original_name: bool = True


class Profile(_Model):
    """A named set of fields and stamps."""

    name: str
    default: bool = False
    fields: list[str]
    stamps: list[StampSpec] = Field(min_length=1)
    output_name: OutputName = Field(default_factory=OutputName)
    metadata: dict[str, str] = Field(default_factory=dict)


class Config(_Model):
    """The whole config.json."""

    schema_version: int
    settings: Settings = Field(default_factory=Settings)
    taxonomies: dict[str, list[str]] = Field(default_factory=dict)
    fields: dict[str, FieldSpec]
    profiles: list[Profile] = Field(min_length=1)

    @field_validator("schema_version")
    @classmethod
    def _version(cls, v: int) -> int:
        if v != SCHEMA_VERSION:
            raise ValueError(
                f"this app reads schema_version {SCHEMA_VERSION}, but the file says {v}"
            )
        return v

    @model_validator(mode="after")
    def _cross_checks(self) -> Config:
        problems = list(_cross_check(self))
        if problems:
            raise ValueError("\n".join(problems))
        return self

    @property
    def default_profile(self) -> Profile:
        """The profile marked ``"default": true``."""
        return next(p for p in self.profiles if p.default)

    def profile(self, name: str) -> Profile:
        """Find a profile by name (case-insensitive); raises KeyError."""
        for p in self.profiles:
            if p.name.casefold() == name.casefold():
                return p
        raise KeyError(name)

    def to_json(self) -> str:
        """Serialize back to config.json text, keys in schema order."""
        return json.dumps(self.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"


def _quoted(names: Iterable[str]) -> str:
    return ", ".join(names) or "(none)"


def _cross_check(cfg: Config) -> Iterable[str]:
    """Checks that span more than one part of the config."""
    for tax_name, items in cfg.taxonomies.items():
        where = f"taxonomies.{tax_name}"
        if not items:
            yield f"{where}: the list is empty; add at least one choice"
        seen: dict[str, str] = {}
        for item in items:
            if not item.strip():
                yield f"{where}: has an empty choice"
                continue
            key = item.strip().casefold()
            if key in seen:
                yield f'{where}: "{item}" is listed twice (matches "{seen[key]}")'
            else:
                seen[key] = item

    for fname, spec in cfg.fields.items():
        where = f"fields.{fname}"
        if not _FIELD_NAME.fullmatch(fname):
            yield (
                f'{where}: "{fname}" can\'t be used as a field name; use letters, '
                "digits and underscores, starting with a letter"
            )
        if fname in BUILTIN_TOKENS:
            yield f'{where}: "{fname}" is a built-in token name; pick another field name'
        if spec.type == "choice" and spec.taxonomy not in cfg.taxonomies:
            yield (
                f'{where}: taxonomy "{spec.taxonomy}" doesn\'t exist. '
                f"Known taxonomies: {_quoted(cfg.taxonomies)}"
            )

    names_seen: dict[str, int] = {}
    defaults = [i for i, p in enumerate(cfg.profiles) if p.default]
    if len(defaults) == 0:
        yield 'profiles: no profile has "default": true; mark exactly one'
    elif len(defaults) > 1:
        listed = ", ".join(f'"{cfg.profiles[i].name}"' for i in defaults)
        yield f'profiles: {len(defaults)} profiles have "default": true ({listed}); mark exactly one'

    for i, prof in enumerate(cfg.profiles):
        where = f'profiles[{i}] "{prof.name}"'
        if not prof.name.strip():
            yield f"profiles[{i}]: the profile name can't be empty"
        key = prof.name.strip().casefold()
        if key in names_seen:
            yield f"{where}: the name is already used by profiles[{names_seen[key]}]"
        else:
            names_seen[key] = i

        seen_fields: set[str] = set()
        for f in prof.fields:
            if f not in cfg.fields:
                yield f"{where}: fields lists {f}, which is not a field. Known fields: {_quoted(cfg.fields)}"
            if f in seen_fields:
                yield f"{where}: fields lists {f} twice"
            seen_fields.add(f)

        available = [f for f in prof.fields if f in cfg.fields]
        stamp_tokens = available + list(BUILTIN_TOKENS)
        for j, stamp in enumerate(prof.stamps):
            yield from _check_template(
                f"{where}: stamps[{j}] text", stamp.text, stamp_tokens
            )

        name_ok = available + ["date"]
        for f in prof.output_name.fields:
            if f not in name_ok:
                hint = (
                    " Use include_original_name for the original filename."
                    if f == "original_name"
                    else ""
                )
                yield (
                    f"{where}: output_name.fields lists {f}, which can't go in a filename "
                    f"for this profile. Available: {_quoted(name_ok)}.{hint}"
                )
        sep = prof.output_name.separator
        if sep == "":
            yield f"{where}: output_name.separator can't be empty"
        elif any(c in ILLEGAL_FILENAME_CHARS or ord(c) < 32 for c in sep):
            yield f'{where}: output_name.separator "{sep}" has a character Windows doesn\'t allow in filenames'

        meta_tokens = available + ["original_name", "date"]
        for key_name, value in prof.metadata.items():
            if not _METADATA_KEY.fullmatch(key_name):
                yield (
                    f'{where}: metadata key "{key_name}" must be letters, digits and '
                    "underscores, starting with a letter"
                )
            yield from _check_template(
                f'{where}: metadata "{key_name}"', value, meta_tokens,
                page_hint=True,
            )


def _check_template(
    where: str, text: str, allowed: Sequence[str], *, page_hint: bool = False
) -> Iterable[str]:
    try:
        tokens = template_tokens(text)
    except TemplateError as e:
        yield f"{where}: {e}"
        return
    for tok in tokens:
        if tok in allowed:
            continue
        if page_hint and tok in ("page", "total"):
            yield f"{where} uses {{{tok}}}, which changes from page to page and can't go in document properties"
        else:
            yield (
                f"{where} uses {{{tok}}}, which isn't one of this profile's fields. "
                f"Available: {_quoted(allowed)}"
            )


# ---------------------------------------------------------------- loading


def _loc_text(loc: tuple[int | str, ...], data: Any) -> str:
    """Turn a pydantic location into config-file wording.

    ``("profiles", 0, "stamps", 0, "margin_in")`` becomes
    ``profiles[0] "Records submission": stamps[0].margin_in``.
    """
    out = ""
    node: Any = data
    for depth, part in enumerate(loc):
        if isinstance(part, str) and part.startswith(("function-", "literal[")):
            continue  # pydantic's internal tags for validators and Literal types
        if isinstance(part, int):
            out += f"[{part}]"
            node = node[part] if isinstance(node, list) and 0 <= part < len(node) else None
            if depth == 1 and loc[0] == "profiles" and isinstance(node, dict):
                name = node.get("name")
                if isinstance(name, str):
                    out += f' "{name}":'
        else:
            if out:
                out += " " if out.endswith(":") else "."
            out += part
            node = node.get(part) if isinstance(node, dict) else None
    return out.rstrip(":")


def _friendly(err: dict[str, Any], data: Any) -> list[str]:
    loc = _loc_text(tuple(err.get("loc", ())), data)
    kind = err.get("type", "")
    msg: str = err.get("msg", "")
    prefix = f"{loc}: " if loc else ""
    match kind:
        case "extra_forbidden":
            return [f"{loc}: unknown key. Check the spelling, or remove it"]
        case "missing":
            return [f"{loc}: required, but missing"]
        case "literal_error":
            expected = err.get("ctx", {}).get("expected", "")
            return [f"{prefix}must be one of {expected} (found {err.get('input')!r})"]
        case "too_short":
            return [f"{prefix}needs at least one entry"]
        case "value_error":
            text = msg.removeprefix("Value error, ")
            return [f"{prefix}{line}" for line in text.split("\n")]
        case _:
            found = err.get("input")
            shown = "" if isinstance(found, (dict, list)) else f" (found {found!r})"
            return [f"{prefix}{msg[0].lower() + msg[1:] if msg else kind}{shown}"]


def parse_config(data: Any, source: str = "") -> Config:
    """Validate an already-parsed config dict; raises ConfigError."""
    try:
        return Config.model_validate(data)
    except ValidationError as e:
        problems: list[str] = []
        for err in e.errors(include_url=False):
            problems.extend(_friendly(dict(err), data))
        raise ConfigError(problems, source) from None


def load_config(path: Path) -> Config:
    """Read and validate a config.json file; raises ConfigError.

    Accepts files saved with or without a UTF-8 byte-order mark (Notepad adds
    one).
    """
    name = path.name
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ConfigError([f"can't find the config file at {path}"], name) from None
    except OSError as e:
        raise ConfigError([f"can't read {path}: {e.strerror or e}"], name) from None
    except UnicodeDecodeError:
        raise ConfigError(["the file isn't UTF-8 text; save it as UTF-8 and try again"], name) from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(
            [f"line {e.lineno}, column {e.colno}: this isn't valid JSON ({e.msg})"], name
        ) from None
    if not isinstance(data, dict):
        raise ConfigError(["the file must contain one JSON object { ... }"], name)
    return parse_config(data, name)
