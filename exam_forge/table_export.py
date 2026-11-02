# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Export the whole bank as a CSV table without Qt: one row per question, its answers side by side.

The table holds the answer keys and rationales, like the bank. It is UTF-8 with a byte order mark, so spreadsheet
programs read accents and mathematics correctly. A cell that a spreadsheet would run as a formula starts with an
apostrophe.
"""
from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Mapping

from .model import Item
from .topics import Topic, topic_of

STATUS = {"draft": "Draft", "reviewed": "Reviewed", "retired": "Retired"}
USES = {"revision": "Revision quiz", "elp": "ELP exam"}
FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: str) -> str:
    return "'" + value if value.startswith(FORMULA) else value


def _order(item: Item, positions: Mapping[str, int]) -> tuple:
    firsts = [positions[link.slide_id] for link in item.sources if link.slide_id in positions]
    return (not item.sources, min(firsts, default=len(positions) + 1), item.status == "retired", item.stem)


def questions_csv(items: Iterable[Item], topics: Iterable[Topic] = (),
                  slides: Mapping[str, tuple[int, str]] | None = None) -> bytes:
    """`slides` maps a slide id to its position and title in the deck; slides it lacks show by id."""
    items, topics, slides = list(items), tuple(topics), dict(slides or {})
    items.sort(key=lambda item: _order(item, {slide: pos for slide, (pos, _) in slides.items()}))
    answers = max((len(item.options) for item in items), default=0)
    head = ["ID", "Revision", "Status", "Used for", "Difficulty", "Topic", "Slides", "Learning objective", "Question"]
    for n in range(1, answers + 1):
        head += [f"Answer {n}", f"Answer {n} correct", f"Answer {n} rationale"]
    head += ["Note for teaching assistants", "Images", "Origin", "Slide changed"]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(head)
    for item in items:
        topic = topic_of(item, topics)
        names = []
        for slide in dict.fromkeys(link.slide_id for link in item.sources):
            names.append(f"Slide {slides[slide][0]} · {slides[slide][1]}" if slide in slides else f"Slide {slide}")
        origin = "AI" if item.origin.kind == "ai" else "AI-assisted" if item.ai_edit_route else "Author"
        row = [item.id, str(item.revision), STATUS[item.status], USES[item.use], item.difficulty.title(),
               topic.name if topic else "", "; ".join(names), item.objective, item.stem]
        for n in range(answers):
            option = item.options[n] if n < len(item.options) else None
            row += [option.text, "yes" if option.correct else "no", option.rationale] if option else ["", "", ""]
        row += [item.assistant_note, str(len(item.media)), origin, "yes" if item.source_changed else "no"]
        writer.writerow([_cell(value) for value in row])
    return ("﻿" + out.getvalue()).encode("utf-8")
