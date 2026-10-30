# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stored question types. Validation for a particular output belongs outside the model."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


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


@dataclass(frozen=True)
class Origin:
    kind: Literal["author", "ai"] = "author"
    route: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ("author", "ai"):
            raise ValueError("invalid origin")
        if self.kind == "ai" and (not self.route or not self.model):
            raise ValueError("AI origin requires a route and model")
        if self.kind == "author" and (self.route or self.model):
            raise ValueError("author origin cannot have a route or model")


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


@dataclass(frozen=True)
class Item:
    id: str
    revision: int
    stem: str = ""
    options: tuple[Option, ...] = ()
    objective: str = ""
    topic: str = ""
    difficulty: str = ""
    status: Literal["draft", "reviewed", "retired"] = "draft"
    use: Literal["elp", "revision"] = "revision"
    sources: tuple[SourceLink, ...] = ()
    source_changed: bool = False  # Current item-level check, not revision content.
    origin: Origin = field(default_factory=Origin)
    media: tuple[Media, ...] = ()
    assistant_note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", tuple(self.options))
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "media", tuple(self.media))
        if len({entry.name for entry in self.media}) != len(self.media):
            raise ValueError("media filenames must be unique")
        if not self.id or type(self.revision) is not int or self.revision < 1:
            raise ValueError("item needs an id and a positive revision")
        if self.status not in ("draft", "reviewed", "retired"):
            raise ValueError("invalid status")
        if self.use not in ("elp", "revision"):
            raise ValueError("invalid use")
        if len({option.id for option in self.options}) != len(self.options):
            raise ValueError("option IDs must be unique")


def check_phase1(item: Item) -> list[str]:
    """Return phase 1 answer errors; storing incomplete drafts is allowed."""
    if sum(option.correct for option in item.options) != 1:
        return ["A phase 1 item must have exactly one correct option."]
    return []
