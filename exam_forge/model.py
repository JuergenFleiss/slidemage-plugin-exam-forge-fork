# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stored types: questions, their slide links, images and provenance, and the topics that group slides. Validation for
a particular output belongs outside the model."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Literal
from uuid import uuid4


@dataclass(frozen=True)
class Option:
    id: str
    text: str
    correct: bool = False
    rationale: str = ""


@dataclass(frozen=True)
class SourceLink:
    slide_id: str = ""
    version: str = ""
    language: str = ""
    view: str = ""
    fingerprint: str = ""
    text: str = ""  # Snapshot for comparing; not part of the fingerprint.


@dataclass(frozen=True)
class Origin:
    kind: Literal["author", "ai"] = "author"
    route: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ("author", "ai"):
            raise ValueError("invalid origin")
        if self.kind == "ai" and not self.route:
            raise ValueError("AI origin requires a route")
        if self.kind == "author" and (self.route or self.model):
            raise ValueError("author origin cannot have a route or model")


# Supported images: media type -> (name, file suffixes with the stored one first, how the content starts).
IMAGE_TYPES = {
    "image/png": ("PNG", (".png",), rb"\x89PNG\r\n\x1a\n"),
    "image/jpeg": ("JPEG", (".jpg", ".jpeg"), rb"\xff\xd8\xff"),
    "image/gif": ("GIF", (".gif",), rb"GIF8[79]a"),
    "image/webp": ("WebP", (".webp",), rb"RIFF.{4}WEBP"),
}
_NAMES = [name for name, _, _ in IMAGE_TYPES.values()]
IMAGE_NAMES = ", ".join(_NAMES[:-1]) + " or " + _NAMES[-1]
IMAGE_SUFFIXES = tuple(suffix for _, suffixes, _ in IMAGE_TYPES.values() for suffix in suffixes)


def image_type(raw: bytes) -> str | None:
    """The media type of a supported image, judged by its content."""
    return next((mime for mime, (_, _, start) in IMAGE_TYPES.items() if re.match(start, raw, re.DOTALL)), None)


@dataclass(frozen=True)
class Media:
    """An immutable image in the bank's media folder."""

    name: str
    sha256: str
    alt: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.name):
            raise ValueError("media filename must be simple")
        if not re.fullmatch(r"[a-f0-9]{64}", self.sha256):
            raise ValueError("media needs a SHA-256 digest")


KINDS = {"choice": "Multiple choice", "open": "Open answer"}
USES = {"revision": "Revision quiz", "exam": "Exam"}
NEXT_STATUS = {"draft": "reviewed", "reviewed": "retired", "retired": "draft"}
STATUS = {"draft": ("Draft", "neutral"), "reviewed": ("Reviewed", "ok"), "retired": ("Retired", "muted")}  # name, tone


@dataclass(frozen=True)
class Item:
    id: str
    revision: int
    stem: str = ""
    options: tuple[Option, ...] = ()
    objective: str = ""
    difficulty: str = ""
    status: Literal["draft", "reviewed", "retired"] = "draft"
    use: Literal["exam", "revision"] = "revision"
    sources: tuple[SourceLink, ...] = ()
    changed_slides: tuple[str, ...] = ()  # The last check's changed or unreadable slides, not revision content.
    origin: Origin = field(default_factory=Origin)
    media: tuple[Media, ...] = ()
    assistant_note: str = ""
    ai_edit: Origin = field(default_factory=Origin)  # Per revision; an AI origin once an AI assisted an edit.
    # An open-answer question has no options: its model answer and grading notes go only into the private key.
    kind: Literal["choice", "open"] = "choice"
    model_answer: str = ""
    grading_notes: str = ""

    def __post_init__(self) -> None:
        for name in ("options", "sources", "changed_slides", "media"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if len({entry.name for entry in self.media}) != len(self.media):
            raise ValueError("media filenames must be unique")
        if not self.id or type(self.revision) is not int or self.revision < 1:
            raise ValueError("item needs an id and a positive revision")
        if self.status not in NEXT_STATUS:
            raise ValueError("invalid status")
        if self.use not in USES:
            raise ValueError("invalid use")
        if self.kind not in KINDS:
            raise ValueError("invalid question type")
        if len({option.id for option in self.options}) != len(self.options):
            raise ValueError("option IDs must be unique")
        if any(type(slide) is not str or not slide for slide in self.changed_slides):
            raise ValueError("invalid changed slide")

    @property
    def source_changed(self) -> bool:
        return bool(self.changed_slides)


def ai_draft(item: Item) -> bool:
    """A draft an AI wrote or updated: it waits for review, labelled as an AI draft."""
    return item.status == "draft" and "ai" in (item.origin.kind, item.ai_edit.kind)


def deck_order(item: Item, positions: Mapping[str, int]) -> tuple:
    """Sort key: by the first linked slide in the deck, unlinked last, retired last on a slide."""
    firsts = [positions[link.slide_id] for link in item.sources if link.slide_id in positions]
    return (not item.sources, min(firsts, default=len(positions) + 1), item.status == "retired", item.stem)


def plural(n: int, word: str) -> str:
    """'1 question' or '3 questions'."""
    return f"{n} {word}{'' if n == 1 else 's'}"


def agree(n: int, one: str, many: str) -> str:
    """The verb that agrees with a count: agree(n, "is", "are")."""
    return one if n == 1 else many


def slide_ids(item: Item) -> list[str]:
    """The item's linked slides, each once, in link order."""
    return list(dict.fromkeys(link.slide_id for link in item.sources))


def slide_label(slide_id: str, position: int | None = None, title: str = "") -> str:
    """'Slide 3 · Title', 'Slide 3' without a title, or 'Slide <id>' for a slide the deck does not place."""
    if not position:
        return f"Slide {slide_id}"
    return f"Slide {position} · {title}" if title else f"Slide {position}"


# -- topics: named, coloured groups of slides; a question's topic comes from its first linked slide -----------------

PALETTE = ("blue", "green", "teal", "pink", "slate")
LEGACY_COLORS = ("red",)  # Preserve saved names; the UI renders these topics in pink.


@dataclass(frozen=True)
class Topic:
    id: str
    name: str
    color: str
    slides: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("topic needs an id")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("topic needs a name")
        if self.color not in PALETTE + LEGACY_COLORS:
            raise ValueError("unknown topic colour")
        if any(not isinstance(slide, str) or not slide for slide in self.slides):
            raise ValueError("invalid slide id in topic")
        if len(set(self.slides)) != len(self.slides):
            raise ValueError("duplicate slide in topic")


def validate_topics(topics) -> None:
    """Topic names are unique regardless of case, and a slide is in at most one topic."""
    names, slides = set(), set()
    for topic in topics:
        key = topic.name.strip().casefold()
        if key in names:
            raise ValueError(f"duplicate topic name: {topic.name}")
        names.add(key)
        if slides & set(topic.slides):
            raise ValueError("a slide can be in only one topic")
        slides |= set(topic.slides)


def next_color(topics) -> str:
    """The colour a new topic gets: the first unused one, or round the palette again."""
    topics = tuple(topics)
    used = {t.color for t in topics}
    return next((c for c in PALETTE if c not in used), PALETTE[len(topics) % len(PALETTE)])


def add_topic(topics, name: str) -> tuple[Topic, ...]:
    return tuple(topics) + (Topic(uuid4().hex, name.strip(), next_color(topics)),)


def rename_topic(topics, topic_id: str, name: str) -> tuple[Topic, ...]:
    return tuple(replace(t, name=name.strip()) if t.id == topic_id else t for t in topics)


def delete_topic(topics, topic_id: str) -> tuple[Topic, ...]:
    return tuple(t for t in topics if t.id != topic_id)


def assign_slide(topics, topic_id: str | None, slide_id: str) -> tuple[Topic, ...]:
    """Move a slide into a topic, out of any other; None leaves it without one."""
    return tuple(replace(t, slides=tuple(s for s in t.slides if s != slide_id) + ((slide_id,) if t.id == topic_id else ()))
                 for t in topics)


def topic_of(item, topics) -> Topic | None:
    slide = item.sources[0].slide_id if item.sources else ""
    return next((t for t in topics if slide and slide in t.slides), None)
