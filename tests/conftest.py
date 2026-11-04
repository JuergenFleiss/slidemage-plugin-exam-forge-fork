# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared fixtures: an offscreen Qt app, an open Exam Forge window, and a mixed bank on four slides."""
import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from fake_host import FakeDeck, GuiHost, activate, add, make_slides
from PySide6.QtWidgets import QApplication

from exam_forge.folder import open_chosen_folder
from exam_forge.model import Origin
from exam_forge.sources import link_slide
from exam_forge.topics import add_topic, assign_slide


@pytest.fixture(autouse=True)
def _free_widgets_on_the_ui_thread():
    """A test's widgets are freed here, not whenever a collection happens to run on a worker thread: destroying a Qt
    widget off the UI thread hangs. The real plugin keeps its windows for as long as SlideMage runs."""
    yield
    gc.collect()


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def env(app, tmp_path):
    """The window of deck "course" on an empty bank."""
    host = GuiHost(tmp_path / "project")
    host.root.mkdir()
    deck = FakeDeck("course", host.root / "course.tex")
    deck.path.write_text("old deck")
    (tmp_path / "bank").mkdir()
    bank = open_chosen_folder(host, deck, tmp_path / "bank")
    controller = activate(host)
    window = controller.open(deck)
    assert window.busy
    host.drain()
    yield host, deck, bank, controller, window
    host.drain()
    for window in controller.windows.values():
        window.hide()
        window.deleteLater()
    QApplication.processEvents()


@pytest.fixture
def lab(env):
    """Four slides a-d; topics Probability (a, b) and Algebra (c); d has no topic; a mixed bank."""
    host, deck, bank, controller, window = env
    refs = make_slides(host, deck)
    link = {i: link_slide(host, ref, "instructor") for i, ref in refs.items()}
    topics = add_topic(add_topic((), "Probability"), "Algebra")
    topics = assign_slide(assign_slide(assign_slide(topics, topics[0].id, "a"), topics[0].id, "b"), topics[1].id, "c")
    assert window.save_topics(topics)
    add(bank, "q1", "Reviewed on a", [link["a"]], "reviewed", difficulty="easy")
    add(bank, "q2", "Draft on a and c", [link["a"], link["c"]])
    add(bank, "q3", "Reviewed on b", [link["b"]], "reviewed")
    bank.set_source_changed("q3", True)
    add(bank, "q4", "AI draft on a", [link["a"]], origin=Origin("ai", "route", "model"))
    add(bank, "q5", "Retired on a", [link["a"]], "retired")
    add(bank, "q6", "Nowhere")
    add(bank, "q7", "Unlinked ELP", use="elp")
    add(bank, "q8", "Retired nowhere", status="retired")
    window.reload()
    window.load_slides()
    host.drain()
    return host, deck, bank, controller, window, refs, link, topics
