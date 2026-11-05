# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The question bank's exports, without Qt, one per entry of the Export menu.

Moodle quiz (XML): reviewed revision-quiz questions. Stems, options and rationales are plain text, not HTML. Write
inline TeX as \\( ... \\); it is passed through for Moodle's enabled MathJax filter. Images are appended to the stem
in the order attached, after their hash is checked. XML tests cannot verify a Moodle site's filters or import
behaviour.

Question table (CSV): every question as a row, its answers side by side; an open-answer question has its model answer
and grading notes instead. It is UTF-8 with a byte order mark, so spreadsheet programs read accents and mathematics
correctly. A cell that a spreadsheet would run as a formula starts with an apostrophe.

Question page (HTML): every question as one self-contained page in the exam documents' style. Images are embedded from
the bank after their hash is checked; one that is missing, changed or of an unsupported type is named instead, so one
bad file does not stop the export. Math stays copyable source, as in the exam documents.

The table and the page hold the answer keys and rationales, like the bank. Both follow the deck's slide order.
"""
from __future__ import annotations

import base64
import csv
import io
from collections.abc import Callable, Iterable, Mapping
from html import escape
from xml.etree import ElementTree as ET

from .bank import Bank
from .exam_document import assistant_note, chips, figure, heading, key_option, model_answer, page, text
from .model import (KINDS, STATUS, USES, Item, Media, Topic, deck_order, image_type, plural, slide_ids, slide_label,
                    topic_of)
from .rules import AnswerRules, question_issues


def _in_deck_order(items: Iterable[Item], slides: Mapping[str, tuple[int, str]] | None) -> list[Item]:
    """`slides` maps a slide id to its position and title in the deck; slides it lacks sort last."""
    positions = {slide: position for slide, (position, _) in (slides or {}).items()}
    return sorted(items, key=lambda item: deck_order(item, positions))


# -- Moodle quiz (XML) -----------------------------------------------------------------------------------------------

def _xml_text(value: str) -> str:
    # XML 1.0 does not permit arbitrary control characters, even when escaped.
    if any(not (c in '\t\n\r' or '\x20' <= c <= '\ud7ff'
               or '\ue000' <= c <= '\ufffd' or '\U00010000' <= c <= '\U0010ffff')
           for c in value):
        raise ValueError('text contains a character forbidden by XML 1.0')
    return value


def _xml_html(value: str) -> str:
    return escape(_xml_text(value), quote=True).replace('\n', '<br />\n')


def _fraction(share: int, sign: int = 1) -> str:
    """100% split into `share` equal parts, one of Moodle's grades for up to nine parts; 0 for no share."""
    if type(share) is not int or not 0 <= share <= 9:
        raise ValueError('answer fraction must split 100% into at most nine parts')
    if share == 0:
        return '0'
    return f'{sign * round(100 / share, 5):.5f}'.rstrip('0').rstrip('.')


def export_moodle_xml(items: Iterable[Item], *, rules: AnswerRules, bank: Bank | None = None) -> bytes:
    """Return UTF-8 XML. Only revision-quiz questions go to Moodle; an exam question is refused.

    The caller decides which items to export; status does not imply release or
    approval. Each selected item must follow the deck's revision-quiz rule; its
    images are read from `bank`. A rule that allows one correct answer makes single-answer
    questions; otherwise every question is multiple-response: the correct
    answers share 100% and the wrong ones -100%, so the format does not tell how
    many are correct. Only answer fractions encode the correctness flag.
    Rationales become per-answer feedback.
    """
    rule = rules.revision
    root = ET.Element('quiz')
    for item in items:
        if item.use != 'revision':
            raise ValueError(f'{item.id}: exam questions never go to Moodle')
        issues = question_issues(item, rules)
        if issues:
            raise ValueError(f'{item.id}: the question must {" and ".join(issues)}')
        question = ET.SubElement(root, 'question', {'type': 'multichoice'})
        ET.SubElement(ET.SubElement(question, 'name'), 'text').text = _xml_text(f'{item.id} r{item.revision}')
        questiontext = ET.SubElement(question, 'questiontext', {'format': 'html'})
        html = _xml_html(item.stem)
        if item.media and bank is None:
            raise ValueError(f'{item.id}: attached media requires its bank')
        for media in item.media:     # Media names are simple and unique within an item.
            html += f'<br /><img src="@@PLUGINFILE@@/{media.name}" alt="{escape(_xml_text(media.alt), quote=True)}" />'
            node = ET.SubElement(questiontext, 'file', {'name': media.name, 'path': '/', 'encoding': 'base64'})
            node.text = base64.b64encode(bank.read_media(media)).decode('ascii')
        ET.SubElement(questiontext, 'text').text = html
        ET.SubElement(question, 'defaultgrade').text = '1.0000000'
        ET.SubElement(question, 'single').text = 'true' if rule.max_correct == 1 else 'false'
        correct = sum(option.correct for option in item.options)
        wrong = 0 if rule.max_correct == 1 else len(item.options) - correct
        ET.SubElement(question, 'shuffleanswers').text = '1'
        ET.SubElement(question, 'answernumbering').text = 'abc'
        for option in item.options:
            fraction = _fraction(correct) if option.correct else _fraction(wrong, -1)
            answer = ET.SubElement(question, 'answer', {'fraction': fraction, 'format': 'html'})
            ET.SubElement(answer, 'text').text = _xml_html(option.text)
            feedback = ET.SubElement(answer, 'feedback', {'format': 'html'})
            ET.SubElement(feedback, 'text').text = _xml_html(option.rationale)
    ET.indent(root, space='  ')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n'


# -- Question table (CSV) ---------------------------------------------------------------------------------------------

FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: str) -> str:
    return "'" + value if value.startswith(FORMULA) else value


def questions_csv(items: Iterable[Item], topics: Iterable[Topic] = (),
                  slides: Mapping[str, tuple[int, str]] | None = None) -> bytes:
    """`slides` maps a slide id to its position and title in the deck; slides it lacks show by id."""
    items, topics, slides = _in_deck_order(items, slides), tuple(topics), dict(slides or {})
    answers = max((len(item.options) for item in items), default=0)
    head = ["ID", "Revision", "Status", "Used for", "Type", "Difficulty", "Topic", "Slides", "Learning objective",
            "Question", "Model answer", "Grading notes"]
    for n in range(1, answers + 1):
        head += [f"Answer {n}", f"Answer {n} correct", f"Answer {n} rationale"]
    head += ["Note for teaching assistants", "Images", "Origin", "Slide changed"]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(head)
    for item in items:
        topic = topic_of(item, topics)
        names = [slide_label(slide, *slides.get(slide, ())) for slide in slide_ids(item)]
        origin = "AI" if item.origin.kind == "ai" else "AI-assisted" if item.ai_edit.kind == "ai" else "Author"
        row = [item.id, str(item.revision), STATUS[item.status][0], USES[item.use], KINDS[item.kind],
               item.difficulty.title(), topic.name if topic else "", "; ".join(names), item.objective, item.stem,
               item.model_answer, item.grading_notes]
        for n in range(answers):
            option = item.options[n] if n < len(item.options) else None
            row += [option.text, "yes" if option.correct else "no", option.rationale] if option else ["", "", ""]
        row += [item.assistant_note, str(len(item.media)), origin, "yes" if item.source_changed else "no"]
        writer.writerow([_cell(value) for value in row])
    return ("\ufeff" + out.getvalue()).encode("utf-8")


# -- Question page (HTML) --------------------------------------------------------------------------------------------

def _page_image(media: Media, read_media: Callable[[Media], bytes] | None) -> str:
    try:
        raw = read_media(media) if read_media is not None else None
    except Exception:  # noqa: BLE001 - any unreadable image is named, never fatal
        raw = None
    mime = image_type(raw) if raw else None
    if mime is None:
        return f'<p class="meta">Image not available: {text(media.name)} — {text(media.alt)}</p>'
    return figure(raw, mime, media.alt)


def _page_question(item: Item, number: int, topic: str, slides: list[str], read_media) -> str:
    labels = [STATUS[item.status][0], USES[item.use], KINDS[item.kind], item.difficulty.title(), topic, *slides]
    parts = ['<section class="question">', heading(number, item.id, item.revision),
             chips(labels, item.status == "reviewed"), f'<div class="text stem">{text(item.stem)}</div>',
             *(_page_image(media, read_media) for media in item.media)]
    if item.assistant_note:
        parts.append(assistant_note(item.assistant_note))
    if item.kind == "open":
        parts += model_answer(item.model_answer, item.grading_notes)
    else:
        parts += ['<ol>', *(key_option(o.text, o.correct, o.rationale) for o in item.options), '</ol>']
    if item.objective:
        parts.append(f'<p class="objective">Learning objective: {text(item.objective)}</p>')
    parts.append('</section>')
    return "\n".join(parts)


def questions_html(items: Iterable[Item], topics: Iterable[Topic] = (),
                   slides: Mapping[str, tuple[int, str]] | None = None,
                   read_media: Callable[[Media], bytes] | None = None, language: str = "en") -> str:
    """`slides` maps a slide id to its position and title; `read_media` returns an image's verified bytes."""
    items, topics, slides = _in_deck_order(items, slides), tuple(topics), dict(slides or {})
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
        body.append(_page_question(item, number, topic.name if topic else "", names, read_media))
    return page("All questions with answers", body, language)
