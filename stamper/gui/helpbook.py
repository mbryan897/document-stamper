"""The help book: topics, contents and search (Phase 4.7, D-133).

The help is a set of Markdown files in ``assets/help`` (bundled with the
app; GitHub shows the same files as the online copy). ``index.json`` lists
the parts and their topics in order. Each topic file starts with a
``# Title`` line; its ``##`` and ``###`` headings are the sections that the
contents list and the search work with.

Links between topics are ordinary Markdown links to the other file, with an
optional heading: ``[Saving](saving.md#the-saved-screen)``. A heading's
anchor is its slug: lower case, every run of other characters turned into
``-``. Pictures are ``![what it shows](images/name.png)``.

Search is plain and predictable: every word typed must appear in a section
(its heading, its text or its topic's title), ignoring case. Results are
ranked by where the words were found, then listed in book order. Nothing
here imports Qt, so the book and the search are tested without a window.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core.locations import resource_dir

__all__ = [
    "HelpBook",
    "Hit",
    "Part",
    "Section",
    "Topic",
    "help_dir",
    "plain_text",
    "slugify",
    "split_link",
]

INDEX_NAME = "index.json"
SNIPPET_CHARS = 110
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_LINK_TARGET = re.compile(r"(?<!!)\[[^\]]+\]\(([^)\s]+)\)")
_IMAGE_TARGET = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
_FENCE = re.compile(r"^\s*```")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}")


def help_dir() -> Path:
    """Where the help files are: the repo's ``assets/help``, or a packaged build's resources."""
    return resource_dir() / "assets" / "help"


def slugify(heading: str) -> str:
    """A heading's anchor: ``"Before stamping"`` -> ``before-stamping``."""
    text = plain_text(heading).casefold()
    return re.sub(r"[^0-9a-z]+", "-", text).strip("-")


def plain_text(markdown: str) -> str:
    """Markdown with its markup taken out, for searching and snippets.

    Pictures are dropped, links keep their words, ``**``, ``*`` and
    backticks go, table rows become words separated by spaces.
    """
    lines: list[str] = []
    for raw in markdown.splitlines():
        if _FENCE.match(raw) or _TABLE_RULE.match(raw):
            continue
        line = _IMAGE.sub("", raw)
        line = _LINK.sub(r"\1", line)
        line = re.sub(r"^\s*#{1,6}\s+", "", line)
        line = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", line)
        line = re.sub(r"^\s*>\s?", "", line)
        line = line.replace("**", "").replace("`", "")
        line = re.sub(r"(?<![\w*])\*(?!\s)([^*]+?)\*(?!\w)", r"\1", line)
        line = line.replace("|", " ")
        lines.append(line)
    return re.sub(r"[ \t]+", " ", "\n".join(lines)).strip()


def split_link(link: str) -> tuple[str, str]:
    """``"saving.md#the-saved-screen"`` -> ``("saving", "the-saved-screen")``; ``"#x"`` -> ``("", "x")``."""
    target, _, anchor = link.partition("#")
    target = target.strip()
    if target.endswith(".md"):
        target = target[:-3]
    return Path(target).name if target else "", anchor.strip()


@dataclass(frozen=True, slots=True)
class Section:
    """One ``##`` or ``###`` part of a topic (the text before the first heading is the topic's intro)."""

    topic: str
    heading: str
    anchor: str
    level: int
    text: str  # plain text of the heading and its body


@dataclass(frozen=True, slots=True)
class Topic:
    """One help file."""

    id: str
    title: str
    part: str
    markdown: str
    sections: tuple[Section, ...]

    @property
    def anchors(self) -> set[str]:
        """The anchors a link into this topic may name."""
        return {s.anchor for s in self.sections if s.anchor}


@dataclass(frozen=True, slots=True)
class Part:
    """A heading in the contents, with its topics in order."""

    title: str
    topics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Hit:
    """A search result: where, how well it matched, and a line of text around the first match."""

    topic: Topic
    section: Section
    score: int
    snippet: str

    @property
    def link(self) -> str:
        """``topic#anchor`` for opening the result."""
        return f"{self.topic.id}#{self.section.anchor}" if self.section.anchor else self.topic.id


def _sections(topic_id: str, title: str, markdown: str) -> tuple[Section, ...]:
    out: list[Section] = []
    heading, level, body = title, 1, []
    in_fence = False

    def flush() -> None:
        text = plain_text("\n".join(body))
        anchor = "" if level == 1 else slugify(heading)
        out.append(Section(topic_id, plain_text(heading), anchor, level, f"{plain_text(heading)}\n{text}".strip()))

    for line in markdown.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(line)
        if m and len(m.group(1)) == 1:
            continue  # the title line
        if m:
            flush()
            heading, level, body = m.group(2), len(m.group(1)), []
        else:
            body.append(line)
    flush()
    return tuple(out)


def _terms(query: str) -> list[str]:
    words = [w.strip(".,;:!?\"'()").casefold() for w in query.split()]
    return [w for w in words if w]


@dataclass
class HelpBook:
    """The help files, read once."""

    folder: Path
    title: str = "Help"
    parts: tuple[Part, ...] = ()
    topics: dict[str, Topic] = field(default_factory=dict)

    @classmethod
    def load(cls, folder: Path | None = None, replacements: dict[str, str] | None = None) -> HelpBook:
        """Read ``index.json`` and every topic it lists. A missing topic file is an error.

        ``replacements`` are filled in wherever they appear in a topic, for
        example ``{"@VERSION@": "0.1.0"}`` in the About topic.
        """
        folder = Path(folder) if folder else help_dir()
        index = json.loads((folder / INDEX_NAME).read_text(encoding="utf-8"))
        parts: list[Part] = []
        topics: dict[str, Topic] = {}
        for p in index["parts"]:
            ids = tuple(p["topics"])
            parts.append(Part(p["title"], ids))
            for tid in ids:
                text = (folder / f"{tid}.md").read_text(encoding="utf-8")
                for marker, value in (replacements or {}).items():
                    text = text.replace(marker, value)
                first = text.lstrip().splitlines()[0] if text.strip() else ""
                m = _HEADING.match(first)
                if not m or len(m.group(1)) != 1:
                    raise ValueError(f"{tid}.md must start with a '# Title' line.")
                title = plain_text(m.group(2))
                topics[tid] = Topic(tid, title, p["title"], text, _sections(tid, title, text))
        return cls(folder, index.get("title", "Help"), tuple(parts), topics)

    # ---------------------------------------------------------- lookups

    def ordered(self) -> list[Topic]:
        """Every topic in contents order."""
        return [self.topics[t] for p in self.parts for t in p.topics]

    def resolve(self, link: str, current: str = "") -> tuple[str, str] | None:
        """``(topic, anchor)`` for a link or a context id, or None if it doesn't exist.

        ``link`` is ``topic``, ``topic#anchor``, ``topic.md#anchor`` or, inside
        ``current``, ``#anchor``. An anchor that doesn't exist in the topic is
        None too, so a stale context id is caught by the tests.
        """
        topic, anchor = split_link(link)
        topic = topic or current
        if topic not in self.topics:
            return None
        if anchor and anchor not in self.topics[topic].anchors:
            return None
        return topic, anchor

    def links(self, topic_id: str) -> list[str]:
        """The link targets in a topic (other topics and headings; not web addresses)."""
        md = self.topics[topic_id].markdown
        return [t for t in _LINK_TARGET.findall(md) if "://" not in t]

    def images(self, topic_id: str) -> list[str]:
        """The pictures a topic shows, as paths relative to the help folder."""
        return _IMAGE_TARGET.findall(self.topics[topic_id].markdown)

    # ---------------------------------------------------------- search

    def search(self, query: str, limit: int = 40) -> list[Hit]:
        """Sections containing every word of ``query`` (ignoring case), best first."""
        terms = _terms(query)
        if not terms:
            return []
        hits: list[tuple[int, int, Hit]] = []
        order = 0
        for topic in self.ordered():
            title = topic.title.casefold()
            for section in topic.sections:
                order += 1
                text = section.text.casefold()
                haystack = f"{title}\n{text}"
                if not all(t in haystack for t in terms):
                    continue
                heading = section.heading.casefold()
                score = 0
                for t in terms:
                    if t in heading:
                        score += 12
                    if t in title:
                        score += 6
                    score += min(text.count(t), 5)
                hits.append((-score, order, Hit(topic, section, score, _snippet(section, terms))))
        hits.sort(key=lambda h: (h[0], h[1]))
        return [h for _, _, h in hits[:limit]]


def _snippet(section: Section, terms: list[str]) -> str:
    """About a line of the section's text around the first word found (not the heading)."""
    body = section.text.split("\n", 1)[1] if "\n" in section.text else ""
    flat = re.sub(r"\s+", " ", body).strip()
    if not flat:
        return ""
    low = flat.casefold()
    at = min((low.find(t) for t in terms if t in low), default=0)
    start = max(0, at - SNIPPET_CHARS // 3)
    if start:
        space = flat.find(" ", start)
        start = space + 1 if 0 <= space < at else start
    end = min(len(flat), start + SNIPPET_CHARS)
    if end < len(flat):
        space = flat.rfind(" ", start, end)
        end = space if space > at else end
    return ("…" if start else "") + flat[start:end].strip() + ("…" if end < len(flat) else "")
