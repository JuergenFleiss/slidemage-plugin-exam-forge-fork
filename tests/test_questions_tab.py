# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Questions tab and topic dialog through real offscreen widgets and the fake host's worker queue."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re
from dataclasses import replace
from uuid import uuid4

import pytest
from fake_host import FakeDeck
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox
from slidemage.plugin_api import Focus, SlideRef, SlideSource
from test_ui import app, env  # noqa: F401 - shared real-window fixtures

from exam_forge.folder import open_chosen_folder
from exam_forge.model import Item, Option, Origin, SourceLink
from exam_forge.sources import link_slide
from exam_forge.topics import TopicStore, add_topic, assign_slide, topic_color

KEY = Qt.ItemDataRole.UserRole


def texts(widget):
    return " | ".join(label.text() for label in widget.findChildren(QLabel))


def live_labels(listw):
    """Labels of the rows now in the list; replaced rows are only deleted later."""
    return [label for i in range(listw.count()) if listw.itemWidget(listw.item(i))
            for label in listw.itemWidget(listw.item(i)).findChildren(QLabel)]


def keys(listw):
    return [listw.item(i).data(KEY) for i in range(listw.count()) if listw.item(i).data(KEY) is not None]


def row_text(listw, key):
    entry = next(listw.item(i) for i in range(listw.count()) if listw.item(i).data(KEY) == key)
    return texts(listw.itemWidget(entry))


def pick(listw, key):
    listw.setCurrentItem(next(listw.item(i) for i in range(listw.count()) if listw.item(i).data(KEY) == key))


def current_key(listw):
    return listw.currentItem().data(KEY) if listw.currentItem() else None


def make_slides(host, deck, ids=("a", "b", "c", "d")):
    refs = [SlideRef(id=i, id_slug=f"{i}-EN-Title", path=host.root / f"{i}.tex", language="en", language_read="en",
                     title=f"Title {i}", version="1-0-0", position=n) for n, i in enumerate(ids, 1)]
    host.slides[(deck.path, "instructor", "en")] = refs
    for ref in refs:
        host.sources[ref] = SlideSource(text=f"Text of {ref.id}")
    return {ref.id: ref for ref in refs}


def options():
    return (Option(uuid4().hex, "Right", True), Option(uuid4().hex, "Wrong"))


def add(bank, item_id, stem, links=(), status="draft", **fields):
    item = bank.create(Item(item_id, 1, stem, options(), sources=tuple(links), **fields))
    if status != "draft":
        item = bank.set_status(item_id, item.revision, "reviewed")
    if status == "retired":
        item = bank.set_status(item_id, item.revision, "retired")
    return item


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


def test_columns_topic_rows_counts_and_badges(lab):
    _, _, _, _, window, _, _, topics = lab
    assert keys(window.topic_list) == ["all", topics[0].id, topics[1].id, "none", "attention", "unlinked"]
    assert "4 slides" in row_text(window.topic_list, "all") and "8 questions" in row_text(window.topic_list, "all")
    probability = row_text(window.topic_list, topics[0].id)
    assert "Probability" in probability and "2 slides" in probability and "5 questions" in probability
    assert probability.split(" | ")[-1] == "2"  # Badge: the changed q3 and the AI draft q4.
    assert "Algebra" in row_text(window.topic_list, topics[1].id) and "1 slide · 0 questions" in row_text(window.topic_list, topics[1].id)
    assert "1 slide · 0 questions" in row_text(window.topic_list, "none") and "No topic" in row_text(window.topic_list, "none")
    attention = row_text(window.topic_list, "attention")
    assert "1 changed slide · 1 AI draft" in attention
    assert "3 questions" in row_text(window.topic_list, "unlinked")  # Retired ones count.
    assert any("Review" == texts(window.topic_list.itemWidget(window.topic_list.item(i)))
               for i in range(window.topic_list.count()))


def test_no_topic_row_appears_only_when_some_slides_have_none(lab):
    _, _, _, _, window, _, _, topics = lab
    assert window.save_topics(assign_slide(window.topics, topics[1].id, "d"))
    window.render()
    assert "none" not in keys(window.topic_list)


def test_slides_follow_deck_order_and_topic_selection(lab):
    host, deck, _, _, window, refs, _, topics = lab
    assert keys(window.slide_list) == ["a", "b", "c", "d"]
    assert "4 questions" in row_text(window.slide_list, "a") and "1 AI draft" in row_text(window.slide_list, "a")
    assert "Changed" in row_text(window.slide_list, "b") and "Changed" not in row_text(window.slide_list, "a")
    assert "No questions yet" in row_text(window.slide_list, "d")
    pick(window.topic_list, topics[1].id)
    assert keys(window.slide_list) == ["c"] and window.sel_slide == "c"
    pick(window.topic_list, "none")
    assert keys(window.slide_list) == ["d"]
    # Deck position, not id, decides the order; the worker reads the deck.
    reordered = [refs["c"], refs["a"], refs["d"], refs["b"]]
    host.slides[(deck.path, "instructor", "en")] = reordered
    window.load_slides()
    host.drain()
    pick(window.topic_list, "all")
    assert keys(window.slide_list) == ["c", "a", "d", "b"]
    assert host.read_threads and all(t != host.ui_thread for t in host.read_threads)


def test_cards_retired_last_dimmed_with_also_on_and_chips(lab):
    _, _, bank, _, window, _, link, _ = lab
    elp = bank.create(Item("elp", 1, "ELP on a", tuple(Option(uuid4().hex, str(n), n < 3) for n in range(5)),
                           use="elp", difficulty="hard", sources=(link["a"],)))
    window.reload()
    assert current_key(window.slide_list) == "a"
    assert keys(window.cards) == ["q1", "q2", "q4", "elp", "q5"]
    assert window.cards.itemWidget(window.cards.item(4)).graphicsEffect() is not None
    assert window.cards.itemWidget(window.cards.item(0)).graphicsEffect() is None
    first = texts(window.cards.itemWidget(window.cards.item(0)))
    assert "Reviewed on a" in first and "Reviewed" in first and "Revision quiz" in first and "Easy" in first
    assert "2 answers" in first
    assert "Also on slide 3" in texts(window.cards.itemWidget(window.cards.item(1)))
    assert "AI draft" in texts(window.cards.itemWidget(window.cards.item(2)))
    elp_text = texts(window.cards.itemWidget(window.cards.item(3)))
    assert "ELP exam" in elp_text and "Hard" in elp_text and "5 answers · 3 correct" in elp_text
    assert "Retired" in texts(window.cards.itemWidget(window.cards.item(4)))
    pick(window.slide_list, "c")
    assert keys(window.cards) == ["q2"] and "Also on slide 1" in texts(window.cards.itemWidget(window.cards.item(0)))
    assert window.crumb.text().count("Algebra") == 1 and "slide 3" in window.crumb.text()
    assert window.slide_title.text() == "Title c"
    pick(window.slide_list, "d")
    assert window.cards.isHidden() and not window.empty_label.isHidden()
    assert not window.write_button.isHidden()


def test_slide_banner_counts_changed_questions(lab):
    _, _, _, _, window, _, _, _ = lab
    pick(window.slide_list, "b")
    assert not window.changed_bar.isHidden()
    assert window.changed_label.text() == "This slide changed after 1 question was written."
    pick(window.slide_list, "a")
    assert window.changed_bar.isHidden()


def test_open_slide_button_uses_the_host(lab):
    host, _, _, _, window, refs, _, _ = lab
    pick(window.slide_list, "b")
    window.open_button.click()
    assert host.opened_slides == [refs["b"]]


def test_card_opens_editor_with_back_label_and_back_returns(lab):
    _, _, _, _, window, _, _, _ = lab
    pick(window.slide_list, "b")
    window.card_opened(window.cards.item(0))
    assert window.current.id == "q3" and window.stack.currentWidget() is window.editor
    assert window.back_button.text() == "‹ Slide 2 · Title b"
    window.back_button.click()
    assert window.stack.currentWidget() is window.slide_page and window.current is None
    assert window.sel_slide == "b" and keys(window.cards) == ["q3"]


def test_unsaved_work_blocks_every_navigation_and_cancel_restores_selection(lab, monkeypatch):
    _, _, _, _, window, _, _, topics = lab
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: asked.append(1) or QMessageBox.StandardButton.Cancel)
    window.card_opened(window.cards.item(0))
    window.stem.setPlainText("Typed but not saved")
    pick(window.slide_list, "b")
    assert window.sel_slide == "a" and current_key(window.slide_list) == "a" and len(asked) == 1
    pick(window.topic_list, topics[1].id)
    assert window.view == "all" and current_key(window.topic_list) == "all" and len(asked) == 2
    window.back_button.click()
    assert window.stack.currentWidget() is window.editor and len(asked) == 3
    window.write_button.click()
    window.card_opened(window.cards.item(1))
    assert window.current.id == "q1" and window.stem.toPlainText() == "Typed but not saved" and len(asked) == 5
    assert not window.open_question("q3") and window.current.id == "q1"
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Discard)
    pick(window.slide_list, "b")
    assert window.sel_slide == "b" and window.current is None


def test_needs_attention_lists_changed_and_ai_drafts_and_back_returns_to_it(lab):
    _, _, bank, _, window, _, link, _ = lab
    # A draft the AI edited needs attention too; a reviewed question that is merely AI-origin does not.
    bank.save(replace(bank.get("q2"), ai_edit_route="route", ai_edit_model="model"))
    add(bank, "q9", "AI but reviewed", [link["a"]], "reviewed", origin=Origin("ai", "route", "model"))
    bank.set_source_changed("q5", True)  # Retired: never needs attention.
    window.reload()
    pick(window.topic_list, "attention")
    assert keys(window.slide_list) == ["q2", "q3", "q4"]
    assert window.stack.currentWidget() is window.placeholder
    window.review_row_opened(window.slide_list.item(1))
    assert window.current.id == "q3" and window.back_button.text() == "‹ Needs attention"
    window.back_button.click()
    assert window.view == "attention" and window.stack.currentWidget() is window.placeholder
    assert "On slide 2" in texts(window.slide_list.itemWidget(window.slide_list.item(1)))


def test_unlinked_questions_are_listed_reachable_and_stay_reviewed(lab):
    _, _, bank, _, window, _, _, _ = lab
    add(bank, "q10", "Reviewed nowhere", status="reviewed")
    window.reload()
    pick(window.topic_list, "unlinked")
    assert keys(window.slide_list) == ["q6", "q7", "q10", "q8"]  # Retired last.
    assert window.slide_list.itemWidget(window.slide_list.item(3)).graphicsEffect() is not None
    window.review_row_opened(window.slide_list.item(2))
    assert window.current.status == "reviewed" and window.back_button.text() == "‹ Unlinked questions"
    window.back_button.click()
    assert bank.get("q10").status == "reviewed" and window.view == "unlinked"
    for item in ("q6", "q7", "q8", "q10"):
        bank.save(replace(bank.get(item), status="draft",
                          sources=(link_slide(window.host, window.slide_ref("a"), "instructor"),)))
    window.reload()
    assert window.view == "all" and "unlinked" not in keys(window.topic_list)


def test_unlinked_elp_notice_is_said_once_per_bank_open(lab):
    _, _, bank, _, window, _, _, _ = lab
    assert window.elp_told and "left out of exam counts" in window.notice.text()
    window.notice.setText("something else")
    window.reload()
    assert window.notice.text() == "something else"
    window.check_sources()  # The first check carries the sentence once more, the next ones do not.
    window.host.drain()
    assert "left out of exam counts" in window.notice.text()
    window.check_sources()
    window.host.drain()
    assert "left out of exam counts" not in window.notice.text()


def test_notice_survives_into_first_check(env):
    host, deck, bank, controller, window = env
    bank.create(Item("loose", 1, "Loose ELP", use="elp"))
    window.elp_told = False
    window.reload()
    window.check_sources()
    host.drain()
    assert window.notice.text().startswith("Source check complete")
    assert "left out of exam counts" in window.notice.text()
    window.check_sources()
    host.drain()
    assert "left out of exam counts" not in window.notice.text()


def test_reload_keeps_selection_and_unsaved_editor_text(lab):
    _, _, bank, _, window, _, _, _ = lab
    pick(window.slide_list, "c")
    window.card_opened(window.cards.item(0))
    window.stem.setPlainText("Typing, not saved")
    add(bank, "q11", "Arrives meanwhile", [window.links[0]])
    window.reload()
    assert window.sel_slide == "c" and window.stem.toPlainText() == "Typing, not saved" and window.dirty()
    assert window.stack.currentWidget() is window.editor and current_key(window.slide_list) == "c"
    assert window.back_button.text() == "‹ Slide 3 · Title c"


def test_reload_keeps_scroll_position(env):
    host, deck, bank, _, window = env
    make_slides(host, deck, tuple(f"s{n:02d}" for n in range(60)))
    window.load_slides()
    host.drain()
    window.resize(1100, 500)
    window.show()
    QApplication.processEvents()
    bar = window.slide_list.verticalScrollBar()
    assert bar.maximum() > 100
    bar.setValue(200)
    window.reload()
    assert bar.value() == 200
    window.hide()


def test_slide_load_follows_check_and_deck_switch(lab):
    host, deck, _, controller, window, refs, _, _ = lab
    host.slides[(deck.path, "instructor", "en")] = [refs["a"]]
    window.check_sources()
    host.drain()
    assert keys(window.slide_list) == ["a"]
    host.slides[(deck.path, "instructor", "en")] = [refs["b"], refs["a"]]
    controller.open(deck)
    host.drain()
    assert keys(window.slide_list) == ["b", "a"]


def test_footer_title_tabs_and_buttons(env, tmp_path):
    host, deck, bank, controller, window = env
    assert window.windowTitle() == "Exam Forge · course"
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == ["Questions", "Exam"]
    assert window.check_button.text() == "Check slides" and window.export_button.text() == "Export to Moodle"
    assert window.folder_label.text() == str(bank.folder)
    assert re.fullmatch(r"Slides checked \d\d:\d\d · 0 changed", window.checked_label.text())
    other = FakeDeck("other", host.root / "other.tex")
    other.path.write_text("")
    folder = tmp_path / "otherbank"
    folder.mkdir()
    open_chosen_folder(host, other, folder)
    second = controller.open(other)
    assert second.checked_label.text() == "Slides not checked yet"
    host.drain()
    assert second.checked_label.text().startswith("Slides checked")


def test_footer_counts_changed_slides_after_a_check(lab):
    host, _, _, _, window, refs, _, _ = lab
    host.sources[refs["a"]] = SlideSource(text="Changed text of a")
    window.check_button.click()
    host.drain()
    assert window.checked_label.text().endswith("· 1 changed")  # The stale flag on b is cleared by the check.
    assert "Changed" in row_text(window.slide_list, "a")


def test_switch_bank_resets_selection_and_topics(lab, tmp_path):
    host, deck, _, _, window, _, _, _ = lab
    pick(window.slide_list, "b")
    folder = tmp_path / "fresh"
    folder.mkdir()
    window.switch_bank(open_chosen_folder(host, deck, folder))
    host.drain()
    assert window.topics == () and window.view == "all" and window.items == []
    assert window.sel_slide == "a" and window.checked_label.text() == "Slides not checked yet"
    assert keys(window.topic_list) == ["all", "none", "attention"]


# -- topic dialog -------------------------------------------------------------------------------------------


def stored(window):
    return [(t.name, set(t.slides)) for t in TopicStore(window.bank.folder).load()]


def test_topic_dialog_creates_selects_and_invalidates_the_exam(lab):
    _, _, _, _, window, _, _, topics = lab
    window.exam_tab.selection = object()
    window.edit_topic()
    dialog = window.topic_dialog
    dialog.name.setText("  Geometry ")
    dialog.buttons["d"].setChecked(True)
    dialog.done_button.click()
    assert stored(window)[-1] == ("Geometry", {"d"})
    assert window.exam_tab.selection is None
    created = window.topics[-1]
    assert window.view == created.id and keys(window.slide_list) == ["d"]
    assert "Geometry" in row_text(window.topic_list, created.id) and "none" not in keys(window.topic_list)


def test_topic_dialog_renames_and_keeps_slides(lab):
    _, _, _, _, window, _, _, topics = lab
    window.edit_topic(topics[0].id)
    window.topic_dialog.name.setText("Chance")
    window.topic_dialog.done_button.click()
    assert stored(window)[0] == ("Chance", {"a", "b"})
    assert "Chance" in row_text(window.topic_list, topics[0].id)


def test_topic_dialog_shows_other_dot_and_takes_slide_over(lab):
    _, _, _, _, window, _, _, topics = lab
    window.edit_topic(topics[1].id)
    dialog = window.topic_dialog
    assert topic_color(topics[0].color, False) in dialog.dots["a"].text()
    assert topic_color(topics[1].color, False) in dialog.dots["c"].text() and dialog.dots["d"].text() == ""
    dialog.buttons["a"].setChecked(True)
    assert topic_color(topics[1].color, False) in dialog.dots["a"].text()
    dialog.done_button.click()
    assert stored(window) == [("Probability", {"b"}), ("Algebra", {"a", "c"})]
    assert window.owner("a").name == "Algebra"
    dialog = None
    window.edit_topic(topics[1].id)
    window.topic_dialog.buttons["c"].setChecked(False)
    window.topic_dialog.done_button.click()
    assert stored(window)[1] == ("Algebra", {"a"})


def test_topic_dialog_delete_confirms_and_frees_the_slides(lab, monkeypatch):
    _, _, _, _, window, _, _, topics = lab
    pick(window.topic_list, topics[1].id)
    window.edit_topic(topics[1].id)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    window.topic_dialog.delete_button.click()
    assert window.topic_dialog.isVisible() and len(stored(window)) == 2
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    window.topic_dialog.delete_button.click()
    assert [name for name, _ in stored(window)] == ["Probability"]
    assert window.view == "all" and "2 slides" in row_text(window.topic_list, "none") and window.sel_slide == "c"


def test_topic_dialog_refuses_empty_and_duplicate_names_without_closing(lab):
    _, _, _, _, window, _, _, topics = lab
    before = TopicStore(window.bank.folder).path.read_bytes()
    window.edit_topic()
    dialog = window.topic_dialog
    dialog.done_button.click()
    assert dialog.isVisible() and not dialog.problem.isHidden() and "name" in dialog.problem.text()
    dialog.name.setText(" probability ")
    dialog.done_button.click()
    assert dialog.isVisible() and "already" in dialog.problem.text()
    assert TopicStore(window.bank.folder).path.read_bytes() == before
    dialog.name.setText("Fresh")
    dialog.done_button.click()
    assert not dialog.isVisible() and stored(window)[-1][0] == "Fresh"
    window.edit_topic(topics[1].id)
    window.topic_dialog.name.setText("PROBABILITY")
    window.topic_dialog.done_button.click()
    assert window.topic_dialog.isVisible()
    window.topic_dialog.reject()
    # Keeping its own name is not a duplicate.
    window.edit_topic(topics[1].id)
    window.topic_dialog.done_button.click()
    assert not window.topic_dialog.isVisible()


def test_topic_dialog_cancel_and_close_write_nothing(lab):
    _, _, _, _, window, _, _, topics = lab
    window.exam_tab.selection = marker = object()
    path = TopicStore(window.bank.folder).path
    before, stamp = path.read_bytes(), path.stat().st_mtime_ns
    window.edit_topic(topics[0].id)
    window.topic_dialog.name.setText("Changed")
    window.topic_dialog.buttons["c"].setChecked(True)
    window.topic_dialog.reject()
    window.edit_topic()
    window.topic_dialog.name.setText("New")
    window.topic_dialog.close()
    assert path.read_bytes() == before and path.stat().st_mtime_ns == stamp
    assert window.exam_tab.selection is marker and window.topics == topics


def test_pencil_opens_the_topic_dialog(lab):
    _, _, _, _, window, _, _, topics = lab
    entry = next(window.topic_list.item(i) for i in range(window.topic_list.count()) if window.topic_list.item(i).data(KEY) == topics[0].id)
    pencil = next(b for b in window.topic_list.itemWidget(entry).findChildren(type(window.write_button)) if b.text() == "✎")
    pencil.click()
    assert window.topic_dialog.isVisible() and window.topic_dialog.name.text() == "Probability"
    window.topic_dialog.reject()
    new = next(b for b in window.topic_list.findChildren(type(window.write_button)) if b.text() == "+ New topic")
    new.click()
    assert window.topic_dialog.isVisible() and window.topic_dialog.name.text() == ""
    window.topic_dialog.reject()


# -- drafting, writing, sidebar -----------------------------------------------------------------------------


class SpyDialog(QDialog):
    made = []

    def __init__(self, host, deck, bank, parent, **kwargs):
        super().__init__(parent)
        self.kwargs, self.items = kwargs, ()
        SpyDialog.made.append(self)


def test_draft_dialog_receives_selected_topic_and_slide(lab, monkeypatch):
    host, _, _, _, window, _, _, topics = lab
    host.settings().set("ai_support", True)
    window.refresh_ai_support()
    assert not window.draft_button.isHidden()
    monkeypatch.setattr("exam_forge.ui.questions_tab.DraftDialog", type("D", (SpyDialog,), {"DialogCode": QDialog.DialogCode}))
    SpyDialog.made.clear()
    window.draft_button.click()
    assert SpyDialog.made[-1].kwargs == {"topics": topics, "topic_id": None, "preselect": "a",
                                         "rules": window.answer_rules}
    SpyDialog.made[-1].reject()
    pick(window.topic_list, topics[1].id)
    window.draft_with_ai()
    assert SpyDialog.made[-1].kwargs == {"topics": topics, "topic_id": topics[1].id, "preselect": "c",
                                         "rules": window.answer_rules}
    SpyDialog.made[-1].reject()
    assert not window.busy
    host.settings().set("ai_support", False)
    window.refresh_ai_support()
    assert window.draft_button.isHidden()


def test_write_question_links_the_selected_slide(lab):
    host, _, bank, _, window, _, _, _ = lab
    pick(window.slide_list, "b")
    window.write_button.click()
    host.drain()
    assert window.new_item and [l.slide_id for l in window.links] == ["b"]
    assert window.back_button.text() == "‹ Slide 2 · Title b" and window.last_link_context == ("instructor", "en")
    window.stem.setPlainText("Fresh question")
    window.rows[0].text.setPlainText("Yes")
    window.rows[0].tick.click()
    window.rows[1].text.setPlainText("No")
    assert window.save()
    assert bank.get(window.current.id).sources[0].slide_id == "b"
    created = window.current.id
    window.back_button.click()
    assert window.current is None and keys(window.cards) == ["q3", created]


def test_sidebar_opens_the_window_on_the_focused_slide_with_a_linked_question(lab):
    host, deck, bank, controller, window, refs, _, _ = lab
    sidebar = controller.sidebar(deck)
    write = next(b for b in sidebar.findChildren(type(window.write_button)) if b.text() == "Write a question for this slide…")
    assert "questions" in " ".join(l.text() for l in sidebar.findChildren(QLabel))
    host.current_focus = Focus(deck.path, refs["c"].path, "slide")
    write.click()
    host.drain()
    assert window.sel_slide == "c" and window.view == "all" and current_key(window.slide_list) == "c"
    assert window.new_item and [l.slide_id for l in window.links] == ["c"]
    assert window.stack.currentWidget() is window.editor and window.back_button.text() == "‹ Slide 3 · Title c"


def test_open_question_from_the_exam_tab(lab, monkeypatch):
    host, _, _, _, window, _, _, _ = lab
    window.tabs.setCurrentIndex(1)
    assert window.open_question("q3")
    assert window.tabs.currentIndex() == 0 and window.sel_slide == "b" and window.current.id == "q3"
    assert window.back_button.text() == "‹ Slide 2 · Title b"
    window.back_button.click()
    window.tabs.setCurrentIndex(1)
    assert window.open_question("q6") and window.view == "unlinked" and window.current.id == "q6"
    window.stem.setPlainText("dirty")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Cancel)
    window.tabs.setCurrentIndex(1)
    assert not window.open_question("q1") and window.tabs.currentIndex() == 1 and window.current.id == "q6"


class SpyReview(QDialog):
    made = []
    edit_requested = Signal(str)

    def __init__(self, window, items, slide_id=None):
        super().__init__(window)
        self.args = (window, items, slide_id)
        SpyReview.made.append(self)


def test_review_dialog_wiring(lab, monkeypatch):
    host, _, bank, _, window, _, _, _ = lab
    monkeypatch.setattr("exam_forge.ui.questions_tab.ReviewChangesDialog", SpyReview)
    pick(window.slide_list, "b")
    window.review_button.click()
    spy = SpyReview.made[-1]
    assert spy.args[0] is window and [i.id for i in spy.args[1]] == ["q3"] and spy.args[2] == "b"
    spy.edit_requested.emit("q1")
    assert window.current.id == "q1"
    spy.accept()
    window.back_button.click()
    pick(window.slide_list, "b")
    window.card_opened(window.cards.item(0))
    assert window.current.id == "q3"
    window.review_changes(window.current)
    spy = SpyReview.made[-1]
    assert [i.id for i in spy.args[1]] == ["q3"] and spy.args[2] == "b"
    # The dialog resolved the question meanwhile: a clean editor shows the stored version.
    bank.set_source_changed("q3", False)
    spy.accept()
    assert window.current.source_changed is False and window.source_bar.isHidden()
    # A dirty editor is never overwritten.
    window.stem.setPlainText("Mine")
    window.review_changes(window.current)
    bank.set_source_changed("q3", True)
    SpyReview.made[-1].accept()
    assert window.stem.toPlainText() == "Mine" and window.dirty()
    assert window.review_dialog is SpyReview.made[-1]


def test_compare_button_in_the_editor_opens_review_for_that_question(lab, monkeypatch):
    _, _, _, _, window, _, _, _ = lab
    monkeypatch.setattr("exam_forge.ui.questions_tab.ReviewChangesDialog", SpyReview)
    window.open_question("q3")
    window.compare_button.click()
    assert [i.id for i in SpyReview.made[-1].args[1]] == ["q3"] and SpyReview.made[-1].args[2] == "b"


def test_export_takes_every_reviewed_revision_question(lab, tmp_path, monkeypatch):
    host, _, bank, _, window, _, link, _ = lab
    add(bank, "elp", "Reviewed ELP", [link["a"]], "reviewed", use="elp")
    exported = []

    def fake(items, bank, rule):
        exported.extend(items)
        return b"<quiz/>"

    path = tmp_path / "all"
    monkeypatch.setattr("exam_forge.ui.questions_tab.export_moodle_xml", fake)
    monkeypatch.setattr("exam_forge.ui.questions_tab.QFileDialog.getSaveFileName", lambda *a: (str(path), ""))
    pick(window.topic_list, "attention")  # No filter applies.
    window.export_button.click()
    host.drain()
    assert sorted(i.id for i in exported) == ["q1", "q3"]
    assert path.with_suffix(".xml").read_bytes() == b"<quiz/>" and "Exported 2" in window.notice.text()


# -- palette ------------------------------------------------------------------------------------------------


def test_palette_change_rerenders_topic_dots_and_accents(lab):
    _, _, _, _, window, _, _, topics = lab
    def dots():
        entries = [window.topic_list.item(i) for i in range(window.topic_list.count())]
        return [l.text() for e in entries for l in window.topic_list.itemWidget(e).findChildren(QLabel) if "●" in l.text()]
    assert any(topic_color(topics[0].color, False) in d for d in dots())
    palette = window.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#eeeeee"))
    window.setPalette(palette)
    assert any(topic_color(topics[0].color, True) in d for d in dots())
    assert not any(topic_color(topics[0].color, False) in d for d in dots())
    pick(window.slide_list, "b")
    chip = next(l for l in live_labels(window.slide_list) if l.text() == "Changed")
    assert "#c4b5fd" in chip.parentWidget().styleSheet() and "#c4b5fd" in window.changed_label.styleSheet()
    pick(window.slide_list, "a")
    amber = next(l for l in live_labels(window.slide_list) if l.text() == "1 AI draft")
    assert "#fbbf24" in amber.parentWidget().styleSheet()
    sheets = [w.styleSheet() for w in window.findChildren(QLabel)]
    assert not any("background" in s for s in sheets) and "background" not in window.styleSheet()


def test_retired_question_whose_slide_left_the_deck_stays_reachable(env):
    _, _, bank, _, window = env
    item = bank.create(Item("gone", 1, stem="Old", sources=(SourceLink("removed-slide"),)))
    bank.set_status(item.id, 1, "reviewed")
    bank.set_status(item.id, 2, "retired")
    window.reload()
    window.slides = []
    assert [i.id for i in window.unlinked()] == ["gone"]
    window.slides = None
    assert window.unlinked() == []


# -- the host's widgets ------------------------------------------------------------------------------------------

def test_the_window_is_a_host_window_that_guards_unsaved_work(lab, monkeypatch):
    host, _, _, _, window, *_ = lab
    window.show()
    assert window.shell.objectName() == "kitToolWindow" and window.shell.isVisible()
    assert window.windowTitle() == window.shell.windowTitle()
    window.write_button.click()
    host.drain()
    window.stem.setPlainText("Unsaved")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    assert not window.shell.close() and window.shell.isVisible()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    assert window.shell.close() and not window.shell.isVisible()


def test_the_tab_switch_shows_its_page(lab):
    window = lab[4]
    window.tabs.switch.buttons()[1].click()
    assert window.tabs.currentWidget() is window.exam_tab
    window.tabs.setCurrentIndex(0)
    assert window.tabs.switch.current() == "Questions"


def test_the_selected_rows_are_marked_and_stay_marked_in_the_editor(lab):
    window = lab[4]
    pick(window.slide_list, "a")
    def selected(listw):
        return [listw.item(i).data(KEY) for i in range(listw.count())
                if getattr(listw.itemWidget(listw.item(i)), "selected", lambda: False)()]
    assert selected(window.topic_list) == ["all"] and selected(window.slide_list) == ["a"]
    window.card_opened(window.cards.item(0))
    assert selected(window.topic_list) == ["all"] and selected(window.slide_list) == ["a"]


def test_slide_titles_show_without_markup(env):
    host, deck, *_, window = env
    path = deck.path.with_name("t.tex")
    path.write_text("t")
    ref = SlideRef(id="t", id_slug="t-EN-x", path=path, language="en", title=r"A \\\\ B", title_text="A B", position=1,
                   language_read="en")
    host.sources[ref] = SlideSource(text="t")
    host.slides[(deck.path, "instructor", "en")] = [ref]
    window.load_slides()
    host.drain()
    assert "A B" in row_text(window.slide_list, "t") and "\\" not in row_text(window.slide_list, "t")


def test_the_sidebar_opens_exam_forge(lab):
    host, deck, _, controller, window, *_ = lab
    window.close()
    sidebar = controller.sidebar(deck)
    button = next(b for b in sidebar.findChildren(type(window.write_button)) if b.text() == "Open Exam Forge")
    button.click()
    host.drain()
    assert window.isVisible()



def test_questions_focus_lists_a_topics_questions_in_deck_order(lab):
    window, topics = lab[4], lab[7]
    assert window.focus_switch.isVisible() is not False and window.focus_switch.current() == "slides"
    window.focus_switch.buttons()[1].click()
    assert window.focus == "questions" and window.stack.currentWidget() is window.placeholder
    assert keys(window.slide_list) == ["q1", "q2", "q4", "q3", "q6", "q7", "q5", "q8"]  # Unlinked, then retired, last.
    assert "On slide 1, 3" in row_text(window.slide_list, "q2")
    pick(window.topic_list, topics[0].id)
    assert keys(window.slide_list) == ["q1", "q2", "q4", "q3", "q5"]  # As many as the topic row counts.
    pick(window.topic_list, topics[1].id)
    assert keys(window.slide_list) == [] and "No questions yet." in texts(window.slide_list)
    pick(window.topic_list, "attention")
    assert window.focus_switch.isHidden()
    pick(window.topic_list, "all")
    assert not window.focus_switch.isHidden() and window.focus == "questions"
    window.focus_switch.buttons()[0].click()
    assert keys(window.slide_list) == ["a", "b", "c", "d"] and window.stack.currentWidget() is window.slide_page


def test_a_listed_question_opens_marked_and_back_returns_to_the_list(lab):
    window, topics = lab[4], lab[7]
    window.focus_switch.buttons()[1].click()
    pick(window.topic_list, topics[0].id)
    window.review_row_opened(window.slide_list.item(3))
    assert window.current.id == "q3" and window.back_button.text() == "‹ Probability"
    assert current_key(window.slide_list) == "q3"
    window.back_button.click()
    assert window.stack.currentWidget() is window.placeholder and window.focus == "questions"
    assert current_key(window.slide_list) is None


def test_unsaved_work_keeps_the_focus(lab, monkeypatch):
    window = lab[4]
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Cancel)
    window.card_opened(window.cards.item(0))
    window.stem.setPlainText("Typed but not saved")
    window.focus_switch.buttons()[1].click()
    assert window.focus == "slides" and window.focus_switch.current() == "slides"
    assert window.stack.currentWidget() is window.editor


def test_export_table_writes_every_question(lab, tmp_path, monkeypatch):
    host, _, _, _, window, *_ = lab
    path = tmp_path / "all"
    monkeypatch.setattr("exam_forge.ui.questions_tab.QFileDialog.getSaveFileName", lambda *a: (str(path), ""))
    window.table_button.click()
    host.drain()
    text = path.with_suffix(".csv").read_bytes().decode("utf-8-sig")
    assert all(f"\r\nq{n}," in text for n in range(1, 9)) and "Slide 1 · Title a" in text
    assert "Exported 8 questions" in window.notice.text() and not window.busy
