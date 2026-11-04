# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The bank exports: Moodle quiz, question table and question page."""
import base64
import csv
import io
import struct
import zlib
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from exam_forge.bank import Bank
from exam_forge.exports import _fraction, export_moodle_xml, questions_csv, questions_html
from exam_forge.model import Item, Media, Option, Origin, SourceLink, add_topic, assign_slide
from exam_forge.rules import AnswerRules


def sample(tmp_path):
    """A question with one bank image, and its bank."""
    # A small, self-contained PNG: blue rectangle with a gold square.
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    rows = b''.join(b'\0' + b''.join(
        bytes((245, 180, 45) if 30 <= x < 66 and 14 <= y < 50 else (35, 90, 150))
        for x in range(96)) for y in range(64))
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 96, 64, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))
    bank = Bank(tmp_path / 'bank', create=True)
    image = replace(bank.store_media('sample.png', png), alt='A gold square on a blue background')
    item = Item('sample-math-image', 1, r'In the image, what is \( 2^3 \)?', (
        Option('a', '8', True, 'Correct: 2 × 2 × 2 = 8.'),
        Option('b', '6', False, 'Multiply, rather than add.'),
        Option('c', '9', False, 'Recalculate the power.'),
    ), media=(image,))
    return item, bank, png


class ExtractHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.data = ''
        self.images = []

    def handle_data(self, data):
        self.data += data

    def handle_starttag(self, tag, attrs):
        if tag == 'img':
            self.images.append(dict(attrs))


def test_sample_matches_fixed_structure_and_fixture(tmp_path):
    item, bank, png = sample(tmp_path)
    image = item.media[0]
    xml = export_moodle_xml([item], rules=AnswerRules(), bank=bank)
    assert xml == (Path(__file__).parent / 'fixtures/sample_moodle.xml').read_bytes()
    # The fixture is what Moodle imports; these say why it is right.
    q = ET.fromstring(xml).find('question')
    assert q.findtext('name/text') == 'sample-math-image r1' and q.findtext('single') == 'true'
    html = ExtractHTML()
    html.feed(q.findtext('questiontext/text'))
    assert html.images == [{'src': f'@@PLUGINFILE@@/{image.name}', 'alt': image.alt}]
    assert base64.b64decode(q.find('questiontext/file').text, validate=True) == png
    assert [a.attrib['fraction'] for a in q.findall('answer')] == ['100', '0', '0']
    assert [a.findtext('feedback/text') for a in q.findall('answer')] == [o.rationale for o in item.options]


def test_escaping_html_xml_and_math():
    item = Item('id<&', 1, 'Less < & >\n' + r'\( x < y \)', (
        Option('a', '<b> &', True, 'why < &'), Option('b', 'no', False)))
    q = ET.fromstring(export_moodle_xml([item], rules=AnswerRules()))[0]
    assert q.findtext('name/text') == 'id<& r1'
    html = ExtractHTML()
    html.feed(q.findtext('questiontext/text'))
    assert html.data == 'Less < & >\n' + r'\( x < y \)'
    assert q.findtext('answer/text') == '&lt;b&gt; &amp;'
    assert q.findtext('answer/feedback/text') == 'why &lt; &amp;'


def test_invalid_inputs_fail_closed(tmp_path):
    opts = (Option('a', 'a', True), Option('b', 'b', True))
    for bad in (Item('bad', 1, options=opts), Item('bad', 1, options=(opts[0],)),
                Item('bad', 1, '\x01', (opts[0], Option('b', 'b'))),
                Item('secret', 1, 'SECRET STEM', (opts[0], Option('b', 'b')), use='elp')):
        with pytest.raises(ValueError):
            export_moodle_xml([bad], rules=AnswerRules())
    item, bank, _ = sample(tmp_path)
    with pytest.raises(ValueError, match='requires its bank'):
        export_moodle_xml([item], rules=AnswerRules())
    assert _fraction(0) == '0' and _fraction(1) == '100' and _fraction(3) == '33.33333'
    assert _fraction(2, -1) == '-50' and _fraction(0, -1) == '0'
    for value in (-1, 33.33333, 10, True):
        with pytest.raises(ValueError):
            _fraction(value)


def rows(data):
    assert data.startswith("﻿".encode())
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


def item(item_id, stem, slides=(), n=2, **fields):
    options = tuple(Option(f"{item_id}{k}", f"Answer {k} of {item_id}", k == 0, f"Why {k}") for k in range(n))
    return Item(item_id, 1, stem, options, sources=tuple(SourceLink(slide_id=s) for s in slides), **fields)


def test_one_row_per_question_in_deck_order_with_answers_side_by_side():
    topics = add_topic((), "Probability")
    topics = assign_slide(topics, topics[0].id, "b")
    items = [item("q1", "Nowhere"), item("q2", "On c and b", ["c", "b"], 3, difficulty="hard", objective="Bayes"),
             item("q3", "On b", ["b"], use="elp", status="reviewed", assistant_note="Fixed order"),
             item("q4", "Retired on a", ["a"], status="retired"),
             item("q5", "AI on gone", ["gone"], origin=Origin("ai", "r", "m"), changed_slides=("gone",),
                  media=(Media("i.png", "0" * 64, "An image"),))]
    table = rows(questions_csv(items, topics, {"a": (1, "Title a"), "b": (2, "Title b"), "c": (3, "Title c")}))
    head, body = table[0], {row[0]: dict(zip(table[0], row)) for row in table[1:]}
    assert head[-4:] == ["Note for teaching assistants", "Images", "Origin", "Slide changed"]
    assert "Answer 3 rationale" in head and "Answer 4" not in head
    assert [row[0] for row in table[1:]] == ["q4", "q3", "q2", "q5", "q1"]
    assert body["q2"]["Slides"] == "Slide 3 · Title c; Slide 2 · Title b" and body["q2"]["Topic"] == ""
    assert body["q3"]["Topic"] == "Probability" and body["q3"]["Used for"] == "Exam"
    assert body["q2"]["Answer 1 correct"] == "yes" and body["q2"]["Answer 3"] == "Answer 2 of q2"
    assert body["q2"]["Answer 2 rationale"] == "Why 1" and body["q3"]["Answer 3"] == ""
    assert body["q2"]["Difficulty"] == "Hard" and body["q2"]["Learning objective"] == "Bayes"
    assert body["q3"]["Note for teaching assistants"] == "Fixed order" and body["q3"]["Status"] == "Reviewed"
    assert body["q5"]["Slides"] == "Slide gone" and body["q5"]["Origin"] == "AI"
    assert body["q5"]["Images"] == "1" and body["q5"]["Slide changed"] == "yes"


def test_formulas_are_not_run_and_text_survives():
    table = rows(questions_csv([item("q", '=HYPERLINK("x")\nsecond line, "quoted" \\( \\frac{1}{2} \\) Größe')]))
    assert table[1][table[0].index("Question")] == '\'=HYPERLINK("x")\nsecond line, "quoted" \\( \\frac{1}{2} \\) Größe'


def test_an_empty_bank_has_only_the_header():
    table = rows(questions_csv([]))
    assert len(table) == 1 and "Answer 1" not in table[0]


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\0\0\0\0")) + chunk(b"IEND", b""))


def test_every_question_with_its_answers_in_deck_order(tmp_path):
    bank = Bank(tmp_path / "bank", create=True)
    image = replace(bank.store_media("cells.png", png()), alt="Two blue cells")
    gone = replace(bank.store_media("gone.png", png() + b"x"), alt="Lost image")
    (bank.folder / "media" / gone.name).unlink()
    topics = add_topic((), "Probability")
    topics = assign_slide(topics, topics[0].id, "b")
    items = [
        Item("late", 1, "On <c>", (Option("x", "Yes & no", True, "Because"), Option("y", "Never", False, "")),
             sources=(SourceLink(slide_id="c"),), media=(image, gone), objective="Read fractions"),
        Item("early", 1, "Explain Bayes.", use="elp", kind="open", difficulty="hard", status="reviewed",
             model_answer="Posterior ∝ likelihood × prior", grading_notes="Two points", assistant_note="Shared stem",
             sources=(SourceLink(slide_id="b"),)),
        Item("loose", 1, "Nowhere", status="retired"),
    ]
    html = questions_html(items, topics, {"b": (2, "Bayes"), "c": (3, "Cells")}, bank.read_media, "de")
    assert html.index("Explain Bayes.") < html.index("On &lt;c&gt;") < html.index("Nowhere")
    assert '<html lang="de">' in html and "3 questions: 1 reviewed, 1 draft, 1 retired." in html
    chips = ['Reviewed', 'Exam', 'Open answer', 'Hard', 'Probability', 'Slide 2', 'Bayes']
    assert "".join(f'<span class="chip{" reviewed" if n == 0 else ""}">{c}</span>' for n, c in enumerate(chips)) in html
    assert "Posterior ∝ likelihood × prior" in html and "Two points" in html and "Shared stem" in html
    assert '<li class="ok"><span class="mk" aria-label="Correct">✓</span><div><div class="text option">Yes &amp; no' in html
    assert '<li class="no"><span class="mk" aria-label="Incorrect">✗</span><div><div class="text option">Never' in html
    assert "Because" in html and "Learning objective: Read fractions" in html
    assert html.count("data:image/png;base64,") == 1 and f"Image not available: {gone.name} — Lost image" in html
    assert "No slide linked" in html and "<script" not in html


def test_each_answer_has_one_marker_that_matches_it_and_the_heading_is_short():
    import re
    items = [Item("0123456789abcdef", 1, "Q", (Option("x", "A", True), Option("y", "B", False), Option("z", "C", False)))]
    html = questions_html(items)
    marks = re.findall(r'<li class="(ok|no)"><span class="mk" aria-label="(Correct|Incorrect)">', html)
    assert marks == [("ok", "Correct"), ("no", "Incorrect"), ("no", "Incorrect")]
    assert html.count('class="mk"') == 3 and "Correct</p>" not in html
    heading = re.search(r"<h2>.*?</h2>", html).group(0)
    assert "01234567 · r1" in heading and "0123456789abcdef" not in re.sub(r'title="[^"]*"', "", heading)
