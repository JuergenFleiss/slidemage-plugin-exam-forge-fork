# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Export phase 1 items to Moodle XML without Qt. Bank attachments are verified before export.

Stems, options and rationales are plain text, not HTML. Write inline TeX as
\\( ... \\); it is passed through for Moodle's enabled MathJax filter. Images
are appended to the stem in the order supplied. Moodle must render the sample
at gate J2; XML tests cannot verify the site's filters or import behaviour.
"""
from __future__ import annotations

import base64
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from html import escape
from xml.etree import ElementTree as ET

from .bank import Bank
from .model import REVISION_ANSWERS, AnswerRule, Item, answer_issues


@dataclass(frozen=True)
class Image:
    """An image to append to a stem, with a simple filename and alt text."""

    name: str
    data: bytes
    alt: str


def _text(value: str) -> str:
    # XML 1.0 does not permit arbitrary control characters, even when escaped.
    if any(not (c in '\t\n\r' or '\x20' <= c <= '\ud7ff'
               or '\ue000' <= c <= '\ufffd' or '\U00010000' <= c <= '\U0010ffff')
           for c in value):
        raise ValueError('text contains a character forbidden by XML 1.0')
    return value


def _html(value: str) -> str:
    return escape(_text(value), quote=True).replace('\n', '<br />\n')


def _fraction(share: int, sign: int = 1) -> str:
    """100% split into `share` equal parts, one of Moodle's grades for up to nine parts; 0 for no share."""
    if type(share) is not int or not 0 <= share <= 9:
        raise ValueError('answer fraction must split 100% into at most nine parts')
    if share == 0:
        return '0'
    return f'{sign * round(100 / share, 5):.5f}'.rstrip('0').rstrip('.')


def export_moodle_xml(
    items: Iterable[Item], *,
    released_elp: Iterable[tuple[str, int]] = (),
    images: Mapping[tuple[str, int], Iterable[Image]] | None = None,
    bank: Bank | None = None,
    rule: AnswerRule = REVISION_ANSWERS,
) -> bytes:
    """Return UTF-8 XML. ELP release is explicit and specific to a revision.

    Images are keyed by (item id, revision). The caller decides which items to
    export; status does not imply release or approval. Each selected item must
    follow `rule`. A rule that allows one correct answer makes single-answer
    questions; otherwise every question is multiple-response: the correct
    answers share 100% and the wrong ones -100%, so the format does not tell how
    many are correct. Only answer fractions encode the correctness flag.
    Rationales become per-answer feedback.
    """
    released = set(released_elp)
    image_map = images if images is not None else {}
    root = ET.Element('quiz')
    for item in items:
        key = (item.id, item.revision)
        if item.use == 'elp' and key not in released:
            continue
        issues = answer_issues(item, rule)
        if issues:
            raise ValueError(f'{item.id}: the question must {" and ".join(issues)}')
        question = ET.SubElement(root, 'question', {'type': 'multichoice'})
        ET.SubElement(ET.SubElement(question, 'name'), 'text').text = _text(f'{item.id} r{item.revision}')
        questiontext = ET.SubElement(question, 'questiontext', {'format': 'html'})
        html = _html(item.stem)
        filenames: set[str] = set()
        if item.media and bank is None:
            raise ValueError(f'{item.id}: attached media requires its bank')
        attached = [Image(media.name, bank.read_media(media), media.alt) for media in item.media]
        for image in (*attached, *image_map.get(key, ())):
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', image.name) or image.name in filenames:
                raise ValueError(f'{item.id}: image filename must be simple and unique')
            if not isinstance(image.data, bytes) or not image.data:
                raise ValueError(f'{item.id}: image data must be nonempty bytes')
            filenames.add(image.name)
            html += f'<br /><img src="@@PLUGINFILE@@/{image.name}" alt="{escape(_text(image.alt), quote=True)}" />'
            node = ET.SubElement(questiontext, 'file', {'name': image.name, 'path': '/', 'encoding': 'base64'})
            node.text = base64.b64encode(image.data).decode('ascii')
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
            ET.SubElement(answer, 'text').text = _html(option.text)
            feedback = ET.SubElement(answer, 'feedback', {'format': 'html'})
            ET.SubElement(feedback, 'text').text = _html(option.rationale)
    ET.indent(root, space='  ')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n'
