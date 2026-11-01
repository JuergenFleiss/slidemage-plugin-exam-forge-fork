# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Drive every view with SlideMage's own widgets: the fake host cannot catch a mismatch with their API."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace

import pytest
from fake_host import FakeHost
from test_ui import app, env, source  # noqa: F401 - shared real-window fixtures

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
    monkeypatch.setattr(FakeHost, "Chip", lambda self, text, tone="neutral": kit.Chip(text, tone))
    return request.getfixturevalue("env")


def elp_options():
    return tuple(Option(str(i), f"Answer {i}", i == 0, "Why") for i in range(5))


def test_every_view_runs_on_the_hosts_widgets(real):
    host, deck, bank, _, window = real
    link = link_slide(host, source(host, deck), "instructor")
    item = bank.create(Item("plain", 1, stem="Q", difficulty="easy", use="elp", options=elp_options(), sources=(link,)))
    item = bank.set_status(item.id, 1, "reviewed")
    draft = bank.create(Item("draft", 1, stem="AI", difficulty="hard", options=elp_options()[:2], sources=(link,),
                             origin=Origin("ai", "route", "model")))
    bank.create(Item("loose", 1, stem="No slide"))
    bank.set_source_changed(item.id, True)
    window.load_topics()
    window.check_sources()
    host.drain()
    window.reload()
    window.load_slides()
    host.drain()
    assert window.topic_list.count() and window.slide_list.count()

    for question in (item.id, draft.id, "loose"):
        assert window.open_question(question)
        host.drain()
        assert window.stack.currentWidget() is window.editor
        window.back()
    assert window.new_question()
    window.update_header(window.current)
    window.populate(bank.get(item.id))
    window.back()

    window.host.settings().set("ai_support", True)
    for dialog in (TopicDialog(window, None), ReviewChangesDialog(window, [bank.get(item.id)], link.slide_id),
                   DraftDialog(host, deck, bank, window, topics=window.topics, preselect=link.slide_id)):
        host.drain()
        dialog.reject()

    exam = window.exam_tab
    window.tabs.setCurrentWidget(exam)
    exam.refresh()
    host.drain()
    bank.rebaseline(item.id, bank.get(item.id).revision, (replace(link),))
    window.reload()
    exam.render_counts()
    exam.issue.reset()
    exam.set_step(2)
    window.setPalette(window.palette())
    window.tabs.setCurrentIndex(0)
