# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Topics: named, coloured groups of slides. A question's topic comes from its first linked slide."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .bank import CorruptFileError, SchemaError, _atomic_write

TOPICS_SCHEMA_VERSION = 1
# key: (light, dark). Kept clear of the reserved violet and amber accents.
_COLORS = {"blue": ("#1d4ed8", "#60a5fa"), "green": ("#15803d", "#4ade80"),
           "red": ("#b91c1c", "#f87171"), "teal": ("#0f766e", "#2dd4bf"),
           "pink": ("#be185d", "#f472b6"), "slate": ("#475569", "#94a3b8")}
PALETTE = tuple(_COLORS)


def topic_color(key: str, dark: bool) -> str:
    return _COLORS[key][1 if dark else 0]


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
        if self.color not in PALETTE:
            raise ValueError("unknown topic colour")
        if any(not isinstance(slide, str) or not slide for slide in self.slides):
            raise ValueError("invalid slide id in topic")
        if len(set(self.slides)) != len(self.slides):
            raise ValueError("duplicate slide in topic")


def _validate(topics) -> None:
    names, slides = set(), set()
    for topic in topics:
        key = topic.name.strip().casefold()
        if key in names:
            raise ValueError(f"duplicate topic name: {topic.name}")
        names.add(key)
        if slides & set(topic.slides):
            raise ValueError("a slide can be in only one topic")
        slides |= set(topic.slides)


class TopicStore:
    def __init__(self, folder):
        self.path = Path(folder) / "topics.json"

    def load(self) -> tuple[Topic, ...]:
        if not self.path.exists():
            return ()
        try:
            with self.path.open(encoding="utf-8") as stream:
                data = json.load(stream)
            if not isinstance(data, dict) or type(data.get("schema_version")) is not int:
                raise ValueError("missing schema version")
            if data["schema_version"] > TOPICS_SCHEMA_VERSION:
                raise SchemaError(f"{self.path}: newer schema {data['schema_version']}")
            if data["schema_version"] != TOPICS_SCHEMA_VERSION:
                raise ValueError("unsupported schema version")
            rows = data["topics"]
            if type(rows) is not list:
                raise ValueError("invalid topics")
            topics = []
            for row in rows:
                if type(row) is not dict or type(row.get("slides")) is not list:
                    raise ValueError("invalid topic")
                topics.append(Topic(row["id"], row["name"], row["color"], tuple(row["slides"])))
            _validate(topics)
            return tuple(topics)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise CorruptFileError(f"{self.path}: {exc}") from exc

    def save(self, topics) -> None:
        topics = tuple(topics)
        _validate(topics)
        _atomic_write(self.path, {"schema_version": TOPICS_SCHEMA_VERSION, "topics": [
            {"id": t.id, "name": t.name, "color": t.color, "slides": list(t.slides)}
            for t in topics]})


def add_topic(topics, name: str) -> tuple[Topic, ...]:
    topics = tuple(topics)
    used = {t.color for t in topics}
    free = [c for c in PALETTE if c not in used]
    color = free[0] if free else PALETTE[len(topics) % len(PALETTE)]
    return topics + (Topic(uuid4().hex, name.strip(), color),)


def rename_topic(topics, topic_id: str, name: str) -> tuple[Topic, ...]:
    return tuple(Topic(t.id, name.strip(), t.color, t.slides) if t.id == topic_id else t
                 for t in topics)


def delete_topic(topics, topic_id: str) -> tuple[Topic, ...]:
    return tuple(t for t in topics if t.id != topic_id)


def assign_slide(topics, topic_id: str | None, slide_id: str) -> tuple[Topic, ...]:
    out = []
    for t in topics:
        slides = tuple(s for s in t.slides if s != slide_id)
        if t.id == topic_id:
            slides += (slide_id,)
        out.append(Topic(t.id, t.name, t.color, slides))
    return tuple(out)


def topic_of(item, topics) -> Topic | None:
    slide = item.sources[0].slide_id if item.sources else ""
    return next((t for t in topics if slide and slide in t.slides), None)


def topic_name(item, topics) -> str:
    topic = topic_of(item, topics)
    return topic.name if topic else ""

