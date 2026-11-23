"""The working copy Profile settings edits (D-082).

A ``ConfigDraft`` holds the config as plain JSON data. Every edit changes
that data; ``validate()`` runs the same checks as loading a file
(``parse_config``), so Save is only possible for a config that would load.
Cancel throws the draft away.

The structural edits that could break references are refused here with a
sentence saying why (``DraftRefused``):

- a field still in a profile can't be deleted; ``identifier`` never can (D-031);
- a list a choice field uses can't be deleted; renaming a list updates the fields;
- the last profile and a profile's last stamp can't be deleted.

A field's key is made from its label when the field is added and never
changes after that, so templates keep working when a label is edited.

Nothing here imports Qt.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from ..core.config import Config, ConfigError, Profile, parse_config
from ..core.template import BUILTIN_TOKENS, TemplateError, template_tokens

__all__ = [
    "DraftRefused",
    "ConfigDraft",
    "NEW_STAMP_TEXT",
    "diff_summary",
    "make_key",
    "sample_values",
]

NEW_STAMP_TEXT = "{identifier}  |  Page {page} of {total}"
_KEY_CHARS = re.compile(r"[^a-z0-9]+")


class DraftRefused(Exception):
    """An edit that would break the config. ``str()`` says why, for the user."""


def make_key(label: str, taken: Sequence[str]) -> str:
    """A token-safe key from a label: ``Date received`` -> ``date_received``.

    Lowercase ASCII letters, digits and underscores; never a built-in token
    name or a key already taken (a number is added).
    """
    base = _KEY_CHARS.sub("_", label.strip().lower()).strip("_") or "field"
    if base[0].isdigit():
        base = f"f_{base}"
    used = set(taken) | set(BUILTIN_TOKENS)
    key, n = base, 2
    while key in used:
        key, n = f"{base}_{n}", n + 1
    return key


def _unique_name(base: str, taken: Sequence[str]) -> str:
    used = {t.casefold() for t in taken}
    if base.casefold() not in used:
        return base
    n = 2
    while f"{base} {n}".casefold() in used:
        n += 1
    return f"{base} {n}"


def _tokens(text: str) -> list[str]:
    try:
        return template_tokens(text)
    except TemplateError:
        return []


class ConfigDraft:
    """An editable copy of a config, as JSON data."""

    def __init__(self, config: Config | Mapping[str, Any]) -> None:
        data = config.model_dump(mode="json") if isinstance(config, Config) else config
        self.data: dict[str, Any] = copy.deepcopy(dict(data))
        self._saved = copy.deepcopy(self.data)
        self._undo: tuple[str, dict[str, Any]] | None = None

    # ------------------------------------------------------------ state

    @property
    def dirty(self) -> bool:
        """True when the draft differs from what was loaded or last saved."""
        return self.data != self._saved

    @property
    def baseline(self) -> dict[str, Any]:
        """A copy of the config as it was loaded or last saved (what Save compares against)."""
        return copy.deepcopy(self._saved)

    def mark_saved(self) -> None:
        """The draft was written; it's the new baseline."""
        self._saved = copy.deepcopy(self.data)

    def replace(self, data: Mapping[str, Any]) -> None:
        """Replace everything (Import). The baseline stays, so the draft is dirty."""
        self.data = copy.deepcopy(dict(data))
        self._undo = None

    # One level of undo for removals, not a stack. Later edits don't clear the
    # snapshot: undo() puts back the whole draft as it was, so they go too. The
    # screen shows Undo only until the next edit, so that can't happen by surprise.

    def snapshot(self, label: str) -> None:
        """Keep a copy of the draft to go back to, replacing any earlier one."""
        self._undo = (label, copy.deepcopy(self.data))

    @property
    def can_undo(self) -> bool:
        """True while there is a snapshot."""
        return self._undo is not None

    def undo(self) -> str | None:
        """Go back to the snapshot and forget it; returns its label, or None if there was none."""
        if self._undo is None:
            return None
        label, self.data = self._undo
        self._undo = None
        return label

    def validate(self) -> tuple[Config | None, list[str]]:
        """The config this draft would save, or None and the problems."""
        try:
            return parse_config(copy.deepcopy(self.data)), []
        except ConfigError as e:
            return None, e.problems

    # ------------------------------------------------------------ parts

    @property
    def profiles(self) -> list[dict[str, Any]]:
        """The profiles, in file order."""
        return self.data["profiles"]  # type: ignore[no-any-return]

    @property
    def fields(self) -> dict[str, dict[str, Any]]:
        """Field definitions by key."""
        return self.data["fields"]  # type: ignore[no-any-return]

    @property
    def taxonomies(self) -> dict[str, list[str]]:
        """Lists by name."""
        return self.data.setdefault("taxonomies", {})  # type: ignore[no-any-return]

    def label(self, key: str) -> str:
        """A field's label, or its key if it has none."""
        return str(self.fields.get(key, {}).get("label") or key)

    # ------------------------------------------------------------ profiles

    def add_profile(self) -> int:
        """Add a new profile with the identifier and one stamp; returns its index."""
        name = _unique_name("New profile", [p["name"] for p in self.profiles])
        self.profiles.append({
            "name": name,
            "default": False,
            "fields": ["identifier"] if "identifier" in self.fields else [],
            "stamps": [self._new_stamp()],
            "output_name": {"fields": ["identifier"] if "identifier" in self.fields else [],
                            "separator": "_", "include_original_name": True},
            "metadata": {},
        })
        return len(self.profiles) - 1

    def duplicate_profile(self, index: int) -> int:
        """Copy a profile (not as the default); returns the copy's index."""
        clone = copy.deepcopy(self.profiles[index])
        clone["name"] = _unique_name(f"{clone['name']} copy", [p["name"] for p in self.profiles])
        clone["default"] = False
        self.profiles.insert(index + 1, clone)
        return index + 1

    def delete_profile(self, index: int) -> None:
        """Delete a profile; refused for the last one. The first left becomes default if needed."""
        if len(self.profiles) <= 1:
            raise DraftRefused("A config needs at least one profile, so the last one can't be deleted.")
        was_default = bool(self.profiles[index].get("default"))
        del self.profiles[index]
        if was_default:
            self.profiles[0]["default"] = True

    def set_default(self, index: int) -> None:
        """Make one profile the default."""
        for i, p in enumerate(self.profiles):
            p["default"] = i == index

    def _new_stamp(self) -> dict[str, Any]:
        return {"text": NEW_STAMP_TEXT if "identifier" in self.fields else "Page {page} of {total}",
                "anchor": "top-right", "direction": "along-top", "margin_in": 0.25,
                "font_size_pt": 9.0, "color": "#2B3A8F", "background_box": True, "pages": "all"}

    def add_stamp(self, profile: int) -> int:
        """Add a stamp to a profile, at the bottom right; returns its index."""
        stamps = self.profiles[profile]["stamps"]
        stamp = copy.deepcopy(stamps[0]) if stamps else self._new_stamp()
        stamp.update(text="Page {page} of {total}", anchor="bottom-right", direction="along-top")
        stamps.append(stamp)
        return len(stamps) - 1

    def remove_stamp(self, profile: int, index: int) -> None:
        """Remove a stamp; refused for a profile's last stamp."""
        stamps = self.profiles[profile]["stamps"]
        if len(stamps) <= 1:
            raise DraftRefused("A profile needs at least one stamp, so the last one can't be removed.")
        del stamps[index]

    # ------------------------------------------------------------ fields

    def add_field(self, label: str = "New field") -> str:
        """Add a text field; returns its key (made from the label, never changed after)."""
        label = label.strip() or "New field"
        label = _unique_name(label, [f.get("label", "") for f in self.fields.values()])
        key = make_key(label, list(self.fields))
        self.fields[key] = {"label": label, "type": "text", "required": False, "normalize": ["trim"],
                            "formats": [], "taxonomy": None, "from_filename": False}
        return key

    def field_usage(self, key: str) -> list[str]:
        """Names of the profiles that use a field."""
        return [p["name"] for p in self.profiles if key in p.get("fields", [])]

    def delete_field(self, key: str) -> None:
        """Delete a field no profile uses; never ``identifier``."""
        if key == "identifier":
            raise DraftRefused(
                "The identifier field can't be deleted; every profile is built around it. "
                "Change its label instead.")
        used = self.field_usage(key)
        if used:
            names = ", ".join(f'"{n}"' for n in used)
            raise DraftRefused(
                f"{self.label(key)} is used by {names}. Remove it from "
                f"{'that profile' if len(used) == 1 else 'those profiles'} first.")
        del self.fields[key]

    def set_field_type(self, key: str, kind: str) -> None:
        """Change a field's type, dropping settings the new type doesn't take."""
        spec = self.fields[key]
        spec["type"] = kind
        if kind != "text":
            spec["formats"] = []
            spec["from_filename"] = False
        if kind == "choice":
            if not spec.get("taxonomy"):
                spec["taxonomy"] = next(iter(self.taxonomies), None)
        else:
            spec["taxonomy"] = None

    def template_usage(self, key: str) -> list[str]:
        """Where a field's token is used in a profile: stamps, file name, properties."""
        out: list[str] = []
        for p in self.profiles:
            for j, s in enumerate(p.get("stamps", [])):
                if key in _tokens(s.get("text", "")):
                    out.append(f'"{p["name"]}" stamp {j + 1}')
            if key in p.get("output_name", {}).get("fields", []):
                out.append(f'"{p["name"]}" file name')
            for mk, mv in p.get("metadata", {}).items():
                if key in _tokens(mv):
                    out.append(f'"{p["name"]}" document property {mk}')
        return out

    # ------------------------------------------------------------ lists

    def taxonomy_usage(self, name: str) -> list[str]:
        """Labels of the choice fields that use a list."""
        return [f.get("label", k) for k, f in self.fields.items()
                if f.get("type") == "choice" and f.get("taxonomy") == name]

    def add_taxonomy(self, name: str = "New list") -> str:
        """Add a list with one placeholder choice; returns its name."""
        name = _unique_name(name.strip() or "New list", list(self.taxonomies))
        self.taxonomies[name] = ["New choice"]
        return name

    def rename_taxonomy(self, old: str, new: str) -> str:
        """Rename a list, keeping its place and updating the fields that use it."""
        new = new.strip()
        if not new:
            raise DraftRefused("A list needs a name.")
        if new != old and new.casefold() in {t.casefold() for t in self.taxonomies if t != old}:
            raise DraftRefused(f'There is already a list called "{new}".')
        self.data["taxonomies"] = {(new if k == old else k): v for k, v in self.taxonomies.items()}
        for f in self.fields.values():
            if f.get("taxonomy") == old:
                f["taxonomy"] = new
        return new

    def delete_taxonomy(self, name: str) -> None:
        """Delete a list no field uses."""
        used = self.taxonomy_usage(name)
        if used:
            raise DraftRefused(
                f'The list "{name}" is used by {", ".join(used)}. '
                "Change those fields to another list first.")
        del self.taxonomies[name]


# ------------------------------------------------------------------ helpers


def sample_values(config: Config, profile: Profile, today: date) -> dict[str, str]:
    """Made-up values for the profile's fields, for the preview and the example line.

    Text fields with a simple pattern get an example that fits it; other text
    fields their label; choice fields the first item; date fields today.
    """
    out: dict[str, str] = {}
    for name in profile.fields:
        spec = config.fields[name]
        if spec.type == "choice":
            assert spec.taxonomy is not None
            out[name] = config.taxonomies[spec.taxonomy][0]
        elif spec.type == "date":
            out[name] = config.settings.date_formatter.format(today)
        else:
            compiled = next((f.compiled for f in spec.formats if f.compiled is not None), None)
            out[name] = compiled.example(today) if compiled is not None else spec.label
    return out


def diff_summary(old: Config | Mapping[str, Any], new: Config | Mapping[str, Any]) -> list[str]:
    """What changes between two configs, as short lines.

    Profiles, fields and lists added or removed; fields renamed; and, for the
    ones in both, a profile's stamp text, a field's rules, the date format and
    whether folders are remembered.
    """

    def data(cfg: Config | Mapping[str, Any]) -> Mapping[str, Any]:
        return cfg.model_dump(mode="json") if isinstance(cfg, Config) else cfg

    def parts(d: Mapping[str, Any]) -> tuple[list[str], dict[str, str], list[str]]:
        return ([p["name"] for p in d.get("profiles", [])],
                {k: v.get("label", k) for k, v in d.get("fields", {}).items()},
                list(d.get("taxonomies", {})))

    def stamp_texts(d: Mapping[str, Any]) -> dict[str, list[str]]:
        return {p["name"]: [st.get("text", "") for st in p.get("stamps", [])] for p in d.get("profiles", [])}

    def rules(d: Mapping[str, Any]) -> dict[str, list[tuple[Any, Any]]]:
        return {k: [(f.get("pattern"), f.get("regex")) for f in v.get("formats") or []]
                for k, v in d.get("fields", {}).items()}

    od, nd = data(old), data(new)
    (op, of, ot), (np_, nf, nt) = parts(od), parts(nd)
    lines: list[str] = []

    def report(kind: str, before: Sequence[str], after: Sequence[str]) -> None:
        added = [x for x in after if x not in before]
        removed = [x for x in before if x not in after]
        if added:
            lines.append(f"{kind} added: {', '.join(added)}")
        if removed:
            lines.append(f"{kind} removed: {', '.join(removed)}")

    report("Profiles", op, np_)
    ost, nst = stamp_texts(od), stamp_texts(nd)
    restamped = [n for n in nst if n in ost and ost[n] != nst[n]]
    if restamped:
        lines.append(f"Stamp text changed: {', '.join(restamped)}")
    added = [nf[k] for k in nf if k not in of]
    removed = [of[k] for k in of if k not in nf]
    if added:
        lines.append(f"Fields added: {', '.join(added)}")
    if removed:
        lines.append(f"Fields removed: {', '.join(removed)}")
    report("Lists", ot, nt)
    relabeled = [f"{of[k]} → {nf[k]}" for k in of if k in nf and of[k] != nf[k]]
    if relabeled:
        lines.append(f"Fields renamed: {', '.join(relabeled)}")
    orl, nrl = rules(od), rules(nd)
    reruled = [nf[k] for k in nrl if k in orl and orl[k] != nrl[k]]
    if reruled:
        lines.append(f"Rules changed: {', '.join(reruled)}")
    odf = od.get("settings", {}).get("date_format")
    ndf = nd.get("settings", {}).get("date_format")
    if odf and ndf and odf != ndf:
        lines.append(f"Date format changed: {odf} → {ndf}")
    orf = od.get("settings", {}).get("remember_folders", True) is not False
    nrf = nd.get("settings", {}).get("remember_folders", True) is not False
    if orf != nrf:
        lines.append(f"Remember folders turned {'on' if nrf else 'off'}")
    if not lines:
        lines.append("Same profiles, fields and lists; settings inside them may differ.")
    return lines
