# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import hashlib
import json
import struct
import subprocess
import sys
import zlib
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path

import pytest

from exam_forge.bank import Bank
from exam_forge.elp_document import export_elp_documents
from exam_forge.manifest import Manifest, freeze
from exam_forge.model import Item, Option
from exam_forge.rules import ExamRules


def sample(tmp_path):
    """A real frozen bank; this same input produces the fixture pair."""
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', zlib.crc32(kind + data)))

    rows = b''.join(b'\0' + b''.join(
        bytes((40, 100, 180) if x // 40 < 2 else (255, 255, 255))
        if 2 <= x % 40 < 38 and 2 <= y < 58 else b'\x20\x20\x20'
        for x in range(200)) for y in range(60))
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 200, 60, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))
    bank = Bank(tmp_path / 'bank', create=True)
    media = replace(bank.store_media('cells.png', png),
                    alt='Five equal cells; the first two are blue and the other three are white.')
    items = [
        Item('shaded-fraction', 1,
             r'Two of the five equal cells are shaded. Select all true statements about the shaded fraction \( p \).',
             (Option('decimal', r'\( p = 0.4 \)', True, 'Two divided by five is 0.4.'),
              Option('unshaded', r'\( p = \frac{3}{5} \)', False, 'This is the unshaded fraction.'),
              Option('whole', r'\( p + (1-p) = 1 \)', True, 'The two fractions cover the whole.'),
              Option('zero', r'\( p = 0 \)', False, 'Two cells are shaded.'),
              Option('large', r'\( p > 1 \)', False, 'A fraction of these cells cannot exceed one.')),
             use='elp', difficulty='easy', media=(media,)),
        Item('quadratic-function', 1, r'For \( f(x) = x^2 \), select all true statements.',
             (Option('at-zero', r'\( f(0) = 0 \)', True, 'Zero squared is zero.'),
              Option('at-three', r'\( f(3) = 9 \)', True, 'Three squared is nine.'),
              Option('negative', r'\( f(-3) = -9 \)', False, 'A negative number squared is positive.'),
              Option('derivative', r'\( f^{\prime}(x) = 2x \)', True, 'Apply the power rule.'),
              Option('linear', 'The function is linear.', False, 'It is quadratic.')),
             use='elp', difficulty='hard'),
    ]
    for item in items:
        bank.create(item)
        bank.set_status(item.id, 1, 'reviewed')
    # Reverse lexical ID order deliberately; the renderer must not sort either list.
    manifest = freeze(None, None, bank, [(i.id, 2) for i in items],
                      {'counts': {'fractions': 1, 'functions': 1}, 'minutes': 7.5}, ExamRules(), topic_of=lambda i: 'Numbers')
    return manifest, bank, png


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags, self.text, self.blocks, self.rows = [], [], [], []
        self.block = self.row = self.cell = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == 'div' and 'text' in attrs.get('class', '').split():
            self.block = [attrs['class'], '']
        if tag == 'tr':
            self.row = []
        if tag in ('td', 'th'):
            self.cell = ''

    def handle_data(self, data):
        self.text.append(data)
        if self.block is not None:
            self.block[1] += data
        if self.cell is not None:
            self.cell += data

    def handle_endtag(self, tag):
        if tag == 'div' and self.block is not None:
            self.blocks.append(tuple(self.block))
            self.block = None
        if tag in ('td', 'th'):
            self.row.append(self.cell)
            self.cell = None
        if tag == 'tr':
            self.rows.append(self.row)
            self.row = None


def altered(manifest, change):
    data = manifest.data()
    change(data)
    return Manifest(json.dumps(data).encode('utf-8'))


def test_samples_and_fidelity_after_bank_edits(tmp_path):
    manifest, bank, png = sample(tmp_path)
    before = manifest.encoded
    documents = export_elp_documents(manifest)
    for kind, html in zip(('exam', 'key'), documents):
        assert html == (Path(__file__).parent / f'fixtures/sample_elp_{kind}.html').read_text('utf-8')
        doc = Document(html)
        items = manifest.data()['items']
        assert [text for cls, text in doc.blocks if cls == 'text stem'] == [i['stem'] for i in items]
        assert [text for cls, text in doc.blocks if cls == 'text option'] == [o['text'] for i in items for o in i['options']]
        assert '2 questions; 36 points; 7.5 planned minutes.' in ''.join(doc.text)
        images = [attrs for tag, attrs in doc.tags if tag == 'img']
        assert len(images) == 1
        assert images[0]['alt'] == items[0]['media'][0]['alt']
        prefix, encoded = images[0]['src'].split(',', 1)
        assert prefix == 'data:image/png;base64'
        assert base64.b64decode(encoded, validate=True) == png
    bank.save(replace(bank.get('shaded-fraction'), stem='EDITED STEM', status='draft'))
    (bank.folder / 'media' / bank.get('shaded-fraction').media[0].name).unlink()
    assert export_elp_documents(manifest) == documents
    assert manifest.encoded == before


def test_offline_no_active_content_or_external_references_in_the_decks_language(tmp_path):
    manifest, _, _ = sample(tmp_path)
    assert all('<html lang="fr">' in html for html in export_elp_documents(manifest, "fr"))
    for html in export_elp_documents(manifest):
        assert '<html lang="en">' in html
        doc = Document(html)
        assert html.startswith('<!doctype html>') and html.endswith('</html>\n')
        assert not {'script', 'link', 'iframe', 'object', 'embed', 'base', 'svg'} & {tag for tag, _ in doc.tags}
        for tag, attrs in doc.tags:
            assert not any(name.startswith('on') for name in attrs)
            for attr in ('src', 'href', 'srcset', 'action', 'poster', 'data'):
                if attr in attrs:
                    assert tag == 'img' and attr == 'src' and attrs[attr].startswith('data:image/png;base64,')
        assert 'url(' not in html and '@import' not in html
        assert r'\( ... \)' in ''.join(doc.text)


def test_exam_contains_no_key_even_in_hidden_metadata(tmp_path):
    manifest, _, _ = sample(tmp_path)
    def change(data):
        data['blueprint']['private'] = 'BLUEPRINT SECRET'
        data['items'][0]['origin'] = {'kind': 'ai', 'route': 'PRIVATE ROUTE', 'model': 'PRIVATE MODEL'}
        for item in data['items']:
            for option in item['options']:
                option['correct'] = not option['correct']
                option['rationale'] = 'RATIONALE SECRET'
    exam, key = export_elp_documents(manifest)
    changed_exam, changed_key = export_elp_documents(altered(manifest, change))
    assert changed_exam == exam
    assert changed_key != key and 'RATIONALE SECRET' in changed_key
    for secret in ('BLUEPRINT SECRET', 'PRIVATE ROUTE', 'PRIVATE MODEL', 'RATIONALE SECRET'):
        assert secret not in changed_exam
    assert all(o['rationale'] not in exam for item in manifest.data()['items'] for o in item['options'])
    assert 'Scoring sheet' not in exam and 'Correct choices' not in exam
    assert 'class="mk"' not in exam and '✓' not in exam and '✗' not in exam and 'aria-label' not in exam


def test_frozen_rules_tables_floor_and_option_ids(tmp_path):
    manifest, _, _ = sample(tmp_path)
    exam, key = export_elp_documents(manifest)
    rows = Document(key).rows
    assert rows[1] == ['1 — shaded-fraction r2', 'A (decimal), C (whole)', '12', '+6', '-4']
    assert rows[2] == ['2 — quadratic-function r2', 'A (at-zero), B (at-three), D (derivative)', '24', '+8', '-12']
    for points, frozen in manifest.data()['scoring']['tables'].items():
        for row in frozen:
            assert [points, str(row['correct_options']), f'{row["correct_tick"]:+d}', f'{row["wrong_tick"]:+d}'] in rows
    def change(data):
        # Distinguish reading the frozen values from recomputing them with today's rules.
        data['scoring']['tables']['12'][1]['correct_tick'] = 17
        data['scoring']['floor'] = 2
    changed_exam, changed_key = export_elp_documents(altered(manifest, change))
    assert changed_exam == exam
    assert Document(changed_key).rows[1][-2:] == ['+17', '-4']
    assert 'Floor per question: 2 points.' in changed_key

    def minutes(data):
        data['scoring']['rules']['easy']['minutes'] = .1
        data['scoring']['rules']['hard']['minutes'] = .2
    assert '36 points; 0.3 planned minutes' in export_elp_documents(altered(manifest, minutes))[0]


def test_escaping_all_text_and_attributes_without_changing_math(tmp_path):
    manifest, _, _ = sample(tmp_path)
    hostile = '<script src="https://example.invalid/x">& "\'</script>\n' + r'\( x < y \)'
    def change(data):
        data['items'][0]['id'] = hostile
        data['items'][0]['stem'] = hostile
        data['items'][0]['media'][0]['alt'] = hostile
        option = data['items'][0]['options'][0]
        option.update(id=hostile, text=hostile, rationale=hostile)
    exam, key = export_elp_documents(altered(manifest, change))
    for html in (exam, key):
        doc = Document(html)
        assert ('text stem', hostile) in doc.blocks and ('text option', hostile) in doc.blocks
        assert [attrs['alt'] for tag, attrs in doc.tags if tag == 'img'] == [hostile]
        assert not any(tag == 'script' or 'onerror' in attrs for tag, attrs in doc.tags)
        assert '&lt;script src=&quot;' in html and '&amp;' in html and '&#x27;' in html
    assert ('text rationale', hostile) in Document(key).blocks
    assert ('text rationale', hostile) not in Document(exam).blocks


@pytest.mark.parametrize('problem', ['missing', 'hash', 'svg', 'difficulty', 'row', 'options'])
def test_unusable_snapshot_fails_instead_of_dropping_content(tmp_path, problem):
    manifest, _, _ = sample(tmp_path)
    def change(data):
        media = data['items'][0]['media'][0]
        if problem == 'missing':
            data['media_base64'].clear()
        elif problem == 'hash':
            media['sha256'] = '0' * 64
        elif problem == 'svg':
            svg = b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.invalid/x"/></svg>'
            data['media_base64'][media['name']] = base64.b64encode(svg).decode('ascii')
            media['sha256'] = hashlib.sha256(svg).hexdigest()
        elif problem == 'difficulty':
            data['items'][0]['difficulty'] = ''
        elif problem == 'row':
            data['scoring']['tables'].clear()
        else:
            data['items'][0]['options'].pop()
    with pytest.raises(ValueError):
        export_elp_documents(altered(manifest, change))


def test_import_needs_only_standard_library():
    # -S removes site packages: importing Manifest at runtime would pull in the host.
    script = ('import sys; sys.path.insert(0, sys.argv[1]); '
              'from exam_forge.elp_document import export_elp_documents; '
              'assert not any(n.startswith(("slidemage", "PySide6")) for n in sys.modules)')
    subprocess.run([sys.executable, '-I', '-S', '-c', script, str(Path(__file__).resolve().parents[1])], check=True)

