# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every question of the bank as one self-contained HTML page, without Qt, in the exam documents' style.

The page is a private key: it shows which answers are correct, the rationales, model answers and grading notes.
Questions follow the deck's slide order, as in the table export. Images are embedded from the bank after their hash
is checked; one that is missing, changed or of an unsupported type is named instead, so one bad file does not stop
the export. Math stays copyable source, as in the exam documents.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from .elp_document import (
    _page,
    _text,
    assistant_note,
    chips,
    figure,
    heading,
    image_mime,
    key_option,
    model_answer,
)
from .model import KINDS, USES, Item, Media, plural, slide_ids, slide_label
from .table_export import STATUS, deck_order
from .topics import Topic, topic_of


def _image(media: Media, read_media: Callable[[Media], bytes] | None) -> str:
    try:
        raw = read_media(media) if read_media is not None else None
    except Exception:  # noqa: BLE001 - any unreadable image is named, never fatal
        raw = None
    mime = image_mime(raw) if raw else None
    if mime is None:
        return f'<p class="meta">Image not available: {_text(media.name)} — {_text(media.alt)}</p>'
    return figure(raw, mime, media.alt)


def _question(item: Item, number: int, topic: str, slides: list[str], read_media) -> str:
    labels = [STATUS[item.status], USES[item.use], KINDS[item.kind], item.difficulty.title(), topic, *slides]
    parts = ['<section class="question">', heading(number, item.id, item.revision),
             chips(labels, item.status == "reviewed"), f'<div class="text stem">{_text(item.stem)}</div>',
             *(_image(media, read_media) for media in item.media)]
    if item.assistant_note:
        parts.append(assistant_note(item.assistant_note))
    if item.kind == "open":
        parts += model_answer(item.model_answer, item.grading_notes)
    else:
        parts += ['<ol>', *(key_option(o.text, o.correct, o.rationale) for o in item.options), '</ol>']
    if item.objective:
        parts.append(f'<p class="objective">Learning objective: {_text(item.objective)}</p>')
    parts.append('</section>')
    return "\n".join(parts)


def questions_html(items: Iterable[Item], topics: Iterable[Topic] = (),
                   slides: Mapping[str, tuple[int, str]] | None = None,
                   read_media: Callable[[Media], bytes] | None = None, language: str = "en") -> str:
    """`slides` maps a slide id to its position and title; `read_media` returns an image's verified bytes."""
    items, topics, slides = list(items), tuple(topics), dict(slides or {})
    items.sort(key=lambda item: deck_order(item, {slide: pos for slide, (pos, _) in slides.items()}))
    counts = {status: sum(item.status == status for item in items) for status in STATUS}
    body = [(f'<p class="lead">{plural(len(items), "question")}: {counts["reviewed"]} reviewed, '
             f'{plural(counts["draft"], "draft")}, {counts["retired"]} retired.</p>'),
            '<p class="private"><strong>This page holds the answers.</strong> '
            'Keep it as private as the question folder.</p>']
    for number, item in enumerate(items, 1):
        topic = topic_of(item, topics)
        names = []
        for slide in slide_ids(item):
            position, title = slides.get(slide, (None, ""))
            names += [slide_label(slide, position), title]     # chips() leaves out an empty title
        names = names or ["No slide linked"]
        body.append(_question(item, number, topic.name if topic else "", names, read_media))
    return _page("All questions with answers", body, language)
