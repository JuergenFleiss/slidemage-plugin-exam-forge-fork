# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every question of the bank as one self-contained HTML page, without Qt, in the exam documents' style.

The page is a private key: it shows which answers are correct, the rationales, model answers and grading notes.
Questions follow the deck's slide order, as in the table export. Images are embedded from the bank after their hash
is checked; one that is missing, changed or of an unsupported type is named instead, so one bad file does not stop
the export. Math stays copyable source, as in the exam documents.
"""
from __future__ import annotations

import base64
from collections.abc import Callable, Iterable, Mapping

from .elp_document import _page, _text, image_mime
from .model import KINDS, USES, Item, Media
from .table_export import STATUS, deck_order
from .topics import Topic, topic_of


def _image(media: Media, read_media: Callable[[Media], bytes] | None) -> str:
    alt = _text(media.alt)
    try:
        raw = read_media(media) if read_media is not None else None
    except Exception:  # noqa: BLE001 - any unreadable image is named, never fatal
        raw = None
    mime = image_mime(raw) if raw else None
    if mime is None:
        return f'<p class="meta">Image not available: {_text(media.name)} — {alt}</p>'
    encoded = base64.b64encode(raw).decode("ascii")
    return f'<figure><img src="data:{mime};base64,{encoded}" alt="{alt}"><figcaption>{alt}</figcaption></figure>'


def _question(item: Item, number: int, topic: str, slides: str, read_media) -> str:
    meta = " · ".join(part for part in (STATUS[item.status], USES[item.use], KINDS[item.kind],
                                        item.difficulty.title(), topic, slides) if part)
    parts = ['<section class="question">', f'<h2>Question {number} — {_text(item.id)} r{item.revision}</h2>',
             f'<p class="meta">{_text(meta)}</p>', f'<div class="text stem">{_text(item.stem)}</div>',
             *(_image(media, read_media) for media in item.media)]
    if item.assistant_note:
        parts.append('<aside class="text assistant-note"><strong>Teaching assistant note</strong><br>'
                     + _text(item.assistant_note) + '</aside>')
    if item.kind == "open":
        parts += ['<h3>Model answer</h3>', f'<div class="text model-answer">{_text(item.model_answer)}</div>']
        if item.grading_notes:
            parts += ['<h3>Grading notes</h3>', f'<div class="text grading-notes">{_text(item.grading_notes)}</div>']
    else:
        parts.append('<ol type="A">')
        for option in item.options:
            parts.append(f'<li><div class="text option">{_text(option.text)}</div>'
                         f'<p>{"Correct" if option.correct else "Incorrect"}</p>')
            if option.rationale:
                parts.append(f'<div class="text rationale">{_text(option.rationale)}</div>')
            parts.append('</li>')
        parts.append('</ol>')
    if item.objective:
        parts.append(f'<p class="meta">Learning objective: {_text(item.objective)}</p>')
    parts.append('</section>')
    return "\n".join(parts)


def questions_html(items: Iterable[Item], topics: Iterable[Topic] = (),
                   slides: Mapping[str, tuple[int, str]] | None = None,
                   read_media: Callable[[Media], bytes] | None = None, language: str = "en") -> str:
    """`slides` maps a slide id to its position and title; `read_media` returns an image's verified bytes."""
    items, topics, slides = list(items), tuple(topics), dict(slides or {})
    items.sort(key=lambda item: deck_order(item, {slide: pos for slide, (pos, _) in slides.items()}))
    counts = {status: sum(item.status == status for item in items) for status in STATUS}
    drafts = f'{counts["draft"]} draft{"" if counts["draft"] == 1 else "s"}'
    body = [(f'<p>{len(items)} question{"" if len(items) == 1 else "s"}: {counts["reviewed"]} reviewed, {drafts}, '
             f'{counts["retired"]} retired.</p>'),
            '<p><strong>This page holds the answers. Keep it as private as the question folder.</strong></p>']
    for number, item in enumerate(items, 1):
        topic = topic_of(item, topics)
        names = "; ".join(f"Slide {slides[s][0]} · {slides[s][1]}" if s in slides else f"Slide {s}"
                          for s in dict.fromkeys(link.slide_id for link in item.sources)) or "No slide linked"
        body.append(_question(item, number, topic.name if topic else "", names, read_media))
    return _page("All questions with answers", body, language)
