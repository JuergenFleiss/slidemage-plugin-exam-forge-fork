# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Export revision-quiz questions to Moodle XML without Qt. Bank attachments are verified before export.

Stems, options and rationales are plain text, not HTML. Write inline TeX as
\\( ... \\); it is passed through for Moodle's enabled MathJax filter. Images
are appended to the stem in the order attached. XML tests cannot verify a Moodle
site's filters or import behaviour.
"""
from __future__ import annotations

import base64
from collections.abc import Iterable
from html import escape
from xml.etree import ElementTree as ET

from .bank import Bank
from .model import AnswerRule, Item, answer_issues


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


def export_moodle_xml(items: Iterable[Item], *, rule: AnswerRule, bank: Bank | None = None) -> bytes:
    """Return UTF-8 XML. Only revision-quiz questions go to Moodle; an exam question is refused.

    The caller decides which items to export; status does not imply release or
    approval. Each selected item must follow `rule`; its images are read from `bank`. A rule that allows one correct answer makes single-answer
    questions; otherwise every question is multiple-response: the correct
    answers share 100% and the wrong ones -100%, so the format does not tell how
    many are correct. Only answer fractions encode the correctness flag.
    Rationales become per-answer feedback.
    """
    root = ET.Element('quiz')
    for item in items:
        if item.use != 'revision':
            raise ValueError(f'{item.id}: exam questions never go to Moodle')
        issues = answer_issues(item, rule)
        if issues:
            raise ValueError(f'{item.id}: the question must {" and ".join(issues)}')
        question = ET.SubElement(root, 'question', {'type': 'multichoice'})
        ET.SubElement(ET.SubElement(question, 'name'), 'text').text = _text(f'{item.id} r{item.revision}')
        questiontext = ET.SubElement(question, 'questiontext', {'format': 'html'})
        html = _html(item.stem)
        if item.media and bank is None:
            raise ValueError(f'{item.id}: attached media requires its bank')
        for media in item.media:     # Media names are simple and unique within an item.
            html += f'<br /><img src="@@PLUGINFILE@@/{media.name}" alt="{escape(_text(media.alt), quote=True)}" />'
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
            ET.SubElement(answer, 'text').text = _html(option.text)
            feedback = ET.SubElement(answer, 'feedback', {'format': 'html'})
            ET.SubElement(feedback, 'text').text = _html(option.rationale)
    ET.indent(root, space='  ')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n'
