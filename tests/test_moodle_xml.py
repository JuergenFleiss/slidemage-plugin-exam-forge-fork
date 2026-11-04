# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import struct
import zlib
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from exam_forge.model import Item, Option
from exam_forge.moodle_xml import Image, _fraction, export_moodle_xml


def sample():
    # A small, self-contained PNG: blue rectangle with a gold square.
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    rows = b''.join(b'\0' + b''.join(
        bytes((245, 180, 45) if 30 <= x < 66 and 14 <= y < 50 else (35, 90, 150))
        for x in range(96)) for y in range(64))
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 96, 64, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))
    item = Item('sample-math-image', 1, r'In the image, what is \( 2^3 \)?', (
        Option('a', '8', True, 'Correct: 2 × 2 × 2 = 8.'),
        Option('b', '6', False, 'Multiply, rather than add.'),
        Option('c', '9', False, 'Recalculate the power.'),
    ))
    return item, Image('sample.png', png, 'A gold square on a blue background')


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


def test_sample_matches_fixed_structure_and_fixture():
    item, image = sample()
    xml = export_moodle_xml([item], images={(item.id, item.revision): [image]})
    assert xml == (Path(__file__).parent / 'fixtures/sample_moodle.xml').read_bytes()
    # The fixture is what Moodle imports; these say why it is right.
    q = ET.fromstring(xml).find('question')
    assert q.findtext('name/text') == 'sample-math-image r1' and q.findtext('single') == 'true'
    html = ExtractHTML()
    html.feed(q.findtext('questiontext/text'))
    assert html.images == [{'src': '@@PLUGINFILE@@/sample.png', 'alt': image.alt}]
    assert base64.b64decode(q.find('questiontext/file').text, validate=True) == image.data
    assert [a.attrib['fraction'] for a in q.findall('answer')] == ['100', '0', '0']
    assert [a.findtext('feedback/text') for a in q.findall('answer')] == [o.rationale for o in item.options]


def test_escaping_html_xml_and_math():
    item = Item('id<&', 1, 'Less < & >\n' + r'\( x < y \)', (
        Option('a', '<b> &', True, 'why < &'), Option('b', 'no', False)))
    q = ET.fromstring(export_moodle_xml([item]))[0]
    assert q.findtext('name/text') == 'id<& r1'
    html = ExtractHTML()
    html.feed(q.findtext('questiontext/text'))
    assert html.data == 'Less < & >\n' + r'\( x < y \)'
    assert q.findtext('answer/text') == '&lt;b&gt; &amp;'
    assert q.findtext('answer/feedback/text') == 'why &lt; &amp;'


def test_elp_requires_explicit_revision_release():
    options = (Option('a', 'yes', True), Option('b', 'no'))
    revision = Item('rev', 1, 'revision', options)
    elp = Item('secret', 2, 'SECRET STEM', options, use='elp')
    assert b'SECRET STEM' not in export_moodle_xml([revision, elp], released_elp={('secret', 1)})
    assert b'SECRET STEM' in export_moodle_xml([elp], released_elp={('secret', 2)})


def test_invalid_inputs_fail_closed():
    opts = (Option('a', 'a', True), Option('b', 'b', True))
    with pytest.raises(ValueError):
        export_moodle_xml([Item('bad', 1, options=opts)])
    with pytest.raises(ValueError):
        export_moodle_xml([Item('bad', 1, options=(opts[0],))])
    with pytest.raises(ValueError):
        export_moodle_xml([Item('bad', 1, '\x01', (opts[0], Option('b', 'b')))])
    item, image = sample()
    with pytest.raises(ValueError):
        export_moodle_xml([item], images={(item.id, 1): [image, image]})
    with pytest.raises(ValueError):
        export_moodle_xml([item], images={(item.id, 1): [Image('../x.png', b'x', '')]})
    assert _fraction(0) == '0' and _fraction(1) == '100' and _fraction(3) == '33.33333'
    assert _fraction(2, -1) == '-50' and _fraction(0, -1) == '0'
    for value in (-1, 33.33333, 10, True):
        with pytest.raises(ValueError):
            _fraction(value)
