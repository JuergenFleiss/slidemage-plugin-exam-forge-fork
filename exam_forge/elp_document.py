# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
r"""Offline entry copies for the teaching assistant, generated only from a Manifest.

Math decision (G6 / gate J4): preserve plain text exactly, including visible
\( ... \) and \[ ... \] source. The assistant must read and copy it into the
exam software, whose math input has not been verified. Escaped, selectable source
is more honest than a partial TeX conversion. There is no TeX compilation, math
parser, JavaScript, network access, or vendored renderer (zero added library
bytes or third-party licence obligations). Both documents explain this choice.
Needs a decision at J4: is source readable and transferable in the actual exam
software? If typesetting is required, choose a renderer only after reviewing its
bundled size, licence and copy/paste behaviour; none is silently included here.

Item, option and media order is preserved; the exam software handles shuffling.
Author-written assistant notes mark shared stems, exclusions and fixed option
order. Notes are public exam content; no marks are inferred from the key.
Images are appended to stems, as in Moodle XML. Only PNG, JPEG, GIF and WebP are
embedded, with verified snapshot hashes; SVG is refused rather than carrying
potential scripts or external references into an allegedly self-contained file.
Scoring values come from the frozen manifest, never current defaults. Only the
key contains correctness, rationales and the scoring sheet. No student scoring
is performed. The pure exporter returns two strings; the caller writes UTF-8.
"""
from __future__ import annotations

import base64
import hashlib
from decimal import Decimal
from html import escape
from typing import TYPE_CHECKING

from .model import number_word

if TYPE_CHECKING:
    # manifest imports host-facing source checks; rendering needs no host or Qt.
    from .manifest import Manifest


_STYLE = """body { font-family: sans-serif; line-height: 1.5; max-width: 56rem; margin: 2rem auto; padding: 0 1rem; }
.question { border-top: 1px solid #aaa; margin-top: 2rem; break-inside: avoid; }
.text { white-space: pre-wrap; overflow-wrap: anywhere; }
.meta, figcaption { color: #444; }
li { margin: .6rem 0; }
img { max-width: 100%; height: auto; }
figure { margin: 1rem 0; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
th, td { border: 1px solid #aaa; padding: .4rem; text-align: left; }
.answer-space { border: 1px solid #aaa; min-height: 14rem; margin: 1rem 0; }
@media print { body { max-width: none; margin: 0; } }
"""
_MATH_NOTE = (r'Math is shown as copyable source, including \( ... \) and \[ ... \] '
              'delimiters. It is not typeset. Check entry in the exam software at gate J4.')


def _text(value: str) -> str:
    return escape(value, quote=True)


def _page(title: str, body: list[str], language: str = "en") -> str:
    return '\n'.join([
        '<!doctype html>', f'<html lang="{_text(language)}">', '<head>', '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        ('<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
         'img-src data:; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'),
        f'<title>{_text(title)}</title>', f'<style>{_STYLE}</style>', '</head>', '<body>',
        f'<h1>{_text(title)}</h1>', f'<p>{_text(_MATH_NOTE)}</p>', *body, '</body>', '</html>', '',
    ])


def image_mime(raw: bytes) -> str | None:
    """The media type of a supported image, judged by its content."""
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if raw.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if raw.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        return 'image/webp'
    return None


def _image(media: dict, attachments: dict) -> str:
    name = media['name']
    try:
        raw = base64.b64decode(attachments[name], validate=True)
    except (KeyError, ValueError) as exc:
        raise ValueError(f'Missing or invalid snapshot image: {name}') from exc
    if hashlib.sha256(raw).hexdigest() != media['sha256']:
        raise ValueError(f'Snapshot image hash mismatch: {name}')
    mime = image_mime(raw)
    if mime is None:
        raise ValueError(f'Unsupported image {name}: use PNG, JPEG, GIF or WebP')
    encoded = base64.b64encode(raw).decode('ascii')
    alt = _text(media['alt'])
    return (f'<figure><img src="data:{mime};base64,{encoded}" alt="{alt}">'
            f'<figcaption>{alt}</figcaption></figure>')


def _question(item: dict, number: int, rule: dict, images: list[str], *, key: bool) -> str:
    parts = [
        '<section class="question">',
        f'<h2>Question {number} — {_text(item["id"])} r{item["revision"]}</h2>',
        (f'<p class="meta">{rule["points"]} points; {rule["minutes"]} planned minutes; '
         f'{_text(item["difficulty"])}</p>'),
        f'<div class="text stem">{_text(item["stem"])}</div>', *images, '<ol type="A">',
    ]
    if item.get('assistant_note'):
        parts.insert(-1, '<aside class="text assistant-note"><strong>Teaching assistant note</strong><br>'
                     + _text(item['assistant_note']) + '</aside>')
    if item.get('kind') == 'open':
        parts.pop()     # no answers to list: room to write, or the model answer in the key
        if key:
            parts.append('<h3>Model answer</h3>')
            parts.append(f'<div class="text model-answer">{_text(item["model_answer"])}</div>')
            if item.get('grading_notes'):
                parts.append('<h3>Grading notes</h3>')
                parts.append(f'<div class="text grading-notes">{_text(item["grading_notes"])}</div>')
        else:
            parts.append('<div class="answer-space"></div>')
        parts.append('</section>')
        return '\n'.join(parts)
    for option in item['options']:
        parts.append(f'<li><div class="text option">{_text(option["text"])}</div>')
        if key:
            mark = 'Correct' if option['correct'] else 'Incorrect'
            parts.append(f'<p>{mark} — option {_text(option["id"])}</p>')
            if option['rationale']:
                parts.append(f'<div class="text rationale">{_text(option["rationale"])}</div>')
        parts.append('</li>')
    parts.extend(['</ol>', '</section>'])
    return '\n'.join(parts)


def export_elp_documents(manifest: Manifest, language: str = "en") -> tuple[str, str]:
    """Return (exam_html, key_html), to write as UTF-8; no I/O or live bank reads.

    Expects a G5 schema-1 or schema-2 snapshot; unknown item keys are ignored. Unknown difficulty classes, missing frozen
    scoring rows and unavailable/unsupported media fail rather than being omitted.
    Neither the blueprint nor private provenance is copied into the documents.
    """
    data = manifest.data()
    scoring = data['scoring']
    # Exams issued before answer rules could change have no `answers`: five per question.
    total = scoring['rules'].get('answers', {}).get('max_options', 5)
    exam, key, sheet = [], [], []
    points, minutes = 0, Decimal(0)
    for number, item in enumerate(data['items'], 1):
        difficulty = item['difficulty']
        if difficulty not in ('easy', 'hard'):
            raise ValueError(f'{item["id"]}: unsupported difficulty {difficulty!r}')
        rule = scoring['rules'][difficulty]
        images = [_image(media, data['media_base64']) for media in item['media']]
        exam.append(_question(item, number, rule, images, key=False))
        key.append(_question(item, number, rule, images, key=True))
        name = f'<td>{number} — {_text(item["id"])} r{item["revision"]}</td>'
        if item.get('kind') == 'open':
            if not item.get('model_answer', '').strip():
                raise ValueError(f'{item["id"]}: an open-answer question needs a model answer')
            sheet.append(f'<tr>{name}<td>Open answer: graded by hand</td><td>{rule["points"]}</td>'
                         '<td>—</td><td>—</td></tr>')
        else:
            options = item['options']
            count = sum(option['correct'] for option in options)
            if len(options) != total or not 1 <= count < total:
                raise ValueError(f'{item["id"]}: an exam question needs {total} options, at least one correct and '
                                 'one wrong')
            rows = scoring['tables'].get(str(rule['points']), [])
            row = next((r for r in rows if r['correct_options'] == count), None)
            if row is None:
                raise ValueError(f'{item["id"]}: missing frozen scoring row')
            answers = ', '.join(f'{chr(65 + i)} ({option["id"]})'
                                for i, option in enumerate(options) if option['correct'])
            sheet.append(f'<tr>{name}<td>{_text(answers)}</td><td>{rule["points"]}</td>'
                         f'<td>{row["correct_tick"]:+d}</td><td>{row["wrong_tick"]:+d}</td></tr>')
        points += rule['points']
        minutes += Decimal(str(rule['minutes']))
    summary = f'<p>{len(exam)} questions; {points} points; {minutes} planned minutes.</p>'
    exam.insert(0, summary)
    exam.insert(1, '<p>Entry copy: item and option order follows the manifest. '
                'The exam software handles shuffling.</p>')
    tables = []
    for p, rows in scoring['tables'].items():
        for row in rows:
            tables.append(f'<tr><td>{_text(p)}</td><td>{row["correct_options"]}</td>'
                          f'<td>{row["correct_tick"]:+d}</td><td>{row["wrong_tick"]:+d}</td></tr>')
    head = [summary, '<h2>Scoring sheet</h2>',
            ('<p>Add the frozen per-option values for ticked choices; unticked choices contribute 0. '
             f'Floor per question: {_text(str(scoring["floor"]))} points. '
             'This is a reference sheet; Exam Forge does not score students.</p>'),
            ('<table><thead><tr><th>Question / revision</th><th>Correct choices / option IDs</th>'
             '<th>Maximum points</th><th>Correct tick</th><th>Wrong tick</th></tr></thead><tbody>'),
            *sheet, '</tbody></table>']
    if tables:      # an exam of open-answer questions only has no ticks to score
        head += ['<h2>Frozen scoring tables</h2>',
                (f'<p>P = question points; n = number of correct options; {number_word(total)} options per '
                 'question. '
                 f'Correct tick: +P/n; wrong tick: −P/({total}−n).</p>'),
                ('<table><thead><tr><th>P</th><th>n</th><th>Correct tick</th><th>Wrong tick</th>'
                 '</tr></thead><tbody>'), *tables, '</tbody></table>']
    key[:0] = head
    # `language` is the deck's: the questions are written in it, so a screen reader reads the page in it.
    return _page('Exam — entry copy', exam, language), _page('Exam key and scoring sheet', key, language)
