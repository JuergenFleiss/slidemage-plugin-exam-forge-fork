# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Drive every view with SlideMage's own widgets: the fake host cannot catch a mismatch with their API."""
from dataclasses import replace

import pytest
from fake_host import FakeHost, source

from exam_forge.model import Item, Option, Origin
from exam_forge.sources import link_slide
from exam_forge.ui.drafting import DraftDialog
from exam_forge.ui.review_changes import ReviewChangesDialog
from exam_forge.ui.topic_dialog import TopicDialog

kit = pytest.importorskip("slidemage.ui.kit.widgets")
plugin_ui = pytest.importorskip("slidemage.ui.plugin_ui")


@pytest.fixture
def real(request, monkeypatch):
    monkeypatch.setattr(FakeHost, "Page", lambda self: plugin_ui.PluginPage())
    monkeypatch.setattr(FakeHost, "Field", lambda self, label, widget: plugin_ui.Field(label, widget))
    monkeypatch.setattr(FakeHost, "Button", lambda self, text, kind="secondary", *, compact=False:
                        kit.Button(text, kind, compact=compact))
    monkeypatch.setattr(FakeHost, "Chip", lambda self, text, tone="neutral", *, icon=None, elide=False, pressable=False:
                        kit.Chip(text, tone, icon, pressable=pressable, elide=elide))
    return request.getfixturevalue("env")


def exam_options():
    return tuple(Option(str(i), f"Answer {i}", i == 0, "Why") for i in range(5))


def test_every_view_runs_on_the_hosts_widgets(real):
    host, deck, bank, _, window = real
    link = link_slide(host, source(host, deck), "instructor")
    item = bank.create(Item("plain", 1, stem="Q", difficulty="easy", use="exam", options=exam_options(), sources=(link,)))
    item = bank.set_status(item.id, 1, "reviewed")
    draft = bank.create(Item("draft", 1, stem="AI", difficulty="hard", options=exam_options()[:2], sources=(link,),
                             origin=Origin("ai", "route", "model")))
    bank.create(Item("loose", 1, stem="No slide"))
    bank.set_changed_slides(item.id, (link.slide_id,))
    window.load_topics()
    window.check_sources()
    host.drain()
    window.reload()
    window.load_slides()
    host.drain()
    assert window.questions.topic_list.count() and window.questions.slide_list.count()

    for question in (item.id, draft.id, "loose"):
        assert window.open_question(question)
        host.drain()
        assert window.questions.stack.currentWidget() is window.editor
        window.editor.back()
    assert window.questions.new_question()
    window.editor.update_header(window.editor.current)
    window.editor.populate(bank.get(item.id))
    window.editor.back()

    window.host.settings().set("ai_support", True)
    for dialog in (TopicDialog(window, None), ReviewChangesDialog(window, [bank.get(item.id)], link.slide_id),
                   DraftDialog(host, deck, bank, window, window.answer_rules, topics=window.topics,
                               preselect=link.slide_id)):
        host.drain()
        dialog.reject()

    exam = window.exam_tab
    window.tabs.select("Exam")
    exam.refresh()
    host.drain()
    bank.rebaseline(item.id, bank.get(item.id).revision, (replace(link),))
    window.reload()
    exam.render_counts()
    exam.issue.reset()
    exam.set_step(2)
    host.set_dark(True)
    window.tabs.select("Questions")
