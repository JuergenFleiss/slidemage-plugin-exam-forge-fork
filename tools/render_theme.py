# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render the adoption comparison with the real SlideMage stylesheet and widgets.

Run with the same PYTHONPATH as pytest: python tools/render_theme.py OUTPUT before|after.
"""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
import conftest
from fake_host import FakeHost, pick
from exam_forge.model import Item, Option
from exam_forge.exam import Blueprint
import exam_forge.ui.exam_tab as exam_tab

exam_tab.randbits = lambda bits: 42
from slidemage.ui import plugin_ui
from slidemage.ui.kit import widgets as kit
from slidemage.ui.kit.theme import ThemeManager
from slidemage.ui.kit.fonts import load_fonts
from PySide6.QtWidgets import QApplication

FakeHost.Page = lambda self: plugin_ui.PluginPage()
FakeHost.Field = lambda self, label, widget: plugin_ui.Field(label, widget)
FakeHost.Button = lambda self, text, kind='secondary', *, compact=False: kit.Button(text, kind, compact=compact)
FakeHost.Chip = lambda self, text, tone='neutral', *, icon=None, elide=False, pressable=False, mono=False: kit.Chip(text, tone, icon, elide=elide, pressable=pressable, mono=mono)
app = QApplication.instance() or QApplication([])
load_fonts()
out, phase = Path(sys.argv[1]), sys.argv[2]
out.mkdir(parents=True, exist_ok=True)
for dark in (False, True):
    ThemeManager(dark=dark).apply()
    with TemporaryDirectory() as root:
        fixture = conftest.env.__wrapped__(app, Path(root))
        env = next(fixture)
        host, deck, bank, controller, window, refs, links, topics = conftest.lab.__wrapped__(env)
        host.set_dark(dark)
        window.folder_label.setText('/private/course/questions')
        window.shell.resize(int(sys.argv[3]) if len(sys.argv) > 3 else 1180, 800)
        window.shell.show()
        def grab(view):
            host.drain()
            window.notice.setText('Slides checked · 1 changed')
            for _ in range(3):
                app.processEvents()
            window.shell.grab().save(str(out / f'{view}-{"dark" if dark else "light"}-{phase}.png'))
        pick(window.questions.topic_list, topics[0].id)
        pick(window.questions.slide_list, 'a')
        grab('questions-slides')
        window.questions.focus_changed('questions')
        grab('questions-list')
        window.open_question('q1')
        grab('editor')
        window.editor.back()
        pick(window.questions.topic_list, 'attention')
        grab('review')
        for n, difficulty in enumerate(('easy', 'hard')):
            item = bank.create(Item(f'exam{n}', 1, stem=f'Exam question: {difficulty}', use='exam',
                                    difficulty=difficulty, sources=(links['a'],),
                                    options=tuple(Option(str(i), f'Answer {i + 1}', i == 0) for i in range(5))))
            bank.set_status(item.id, 1, 'reviewed')
        window.reload()
        window.tabs.select('Exam')
        window.exam_tab.refresh()
        grab('exam')
        window.exam_tab.propose(Blueprint({('Probability', 'choice', 'easy'): 1,
                                          ('Probability', 'choice', 'hard'): 1}))
        grab('exam-review')
        try:
            next(fixture)
        except StopIteration:
            pass
