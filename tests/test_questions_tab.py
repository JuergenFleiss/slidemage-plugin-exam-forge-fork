# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Questions tab and topic dialog through real offscreen widgets and the fake host's worker queue."""
from dataclasses import replace
from uuid import uuid4

from fake_host import KEY, add, current_key, keys, pick, row_text, texts
from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QLabel, QMessageBox, QToolButton
from slidemage.plugin_api import Focus

from exam_forge.bank import TopicStore
from exam_forge.model import Item, Option, Origin, SourceLink, assign_slide


def card(window, n):
    return texts(window.questions.cards.itemWidget(window.questions.cards.item(n)))


def test_topic_rows_count_slides_questions_and_attention(lab):
    _, _, _, _, window, _, _, topics = lab
    assert keys(window.questions.topic_list) == ["all", topics[0].id, topics[1].id, "none", "attention", "unlinked"]
    probability = row_text(window.questions.topic_list, topics[0].id)
    assert "2 slides" in probability and "5 questions" in probability
    assert probability.split(" | ")[-1] == "2"  # Badge: the changed q3 and the AI draft q4.
    assert "1 changed slide · 1 AI draft" in row_text(window.questions.topic_list, "attention")
    assert window.save_topics(assign_slide(window.topics, topics[1].id, "d"))
    window.questions.render()
    assert "none" not in keys(window.questions.topic_list)  # Only while some slides have no topic.


def test_slides_follow_deck_order_and_topic_selection(lab):
    host, deck, _, _, window, refs, _, topics = lab
    assert keys(window.questions.slide_list) == ["a", "b", "c", "d"]
    assert "Changed" in row_text(window.questions.slide_list, "b") and "1 AI draft" in row_text(window.questions.slide_list, "a")
    pick(window.questions.topic_list, topics[1].id)
    assert keys(window.questions.slide_list) == ["c"] and window.questions.sel_slide == "c"
    host.slides[(deck.path, "instructor", "en")] = [refs["c"], refs["a"], refs["d"], refs["b"]]
    window.load_slides()
    host.drain()
    pick(window.questions.topic_list, "all")
    assert keys(window.questions.slide_list) == ["c", "a", "d", "b"]
    assert host.read_threads and all(t != host.ui_thread for t in host.read_threads)


def test_cards_follow_deck_order_retired_last_and_show_other_slides(lab):
    host, _, bank, _, window, refs, link, _ = lab
    bank.create(Item("exam", 1, "Exam on a", tuple(Option(uuid4().hex, str(n), n < 3) for n in range(5)),
                     use="exam", sources=(link["a"],)))
    window.reload()
    assert keys(window.questions.cards) == ["q4", "q2", "exam", "q1", "q5"]  # By question text, as in the exports.
    assert window.questions.cards.itemWidget(window.questions.cards.item(4)).graphicsEffect() is not None
    assert "Also on slide 3" in card(window, 1) and "5 answers · 3 correct" in card(window, 2)
    pick(window.questions.slide_list, "b")
    assert window.questions.changed_label.text() == "This slide changed after 1 question was written."
    window.questions.open_button.click()
    assert host.opened_slides == [refs["b"]]
    pick(window.questions.slide_list, "d")
    assert window.questions.cards.isHidden() and not window.questions.empty_label.isHidden()


def test_unsaved_work_blocks_every_navigation(lab, monkeypatch):
    _, _, _, _, window, _, _, topics = lab
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: asked.append(1) or QMessageBox.StandardButton.Cancel)
    window.questions.card_opened(window.questions.cards.item(0))
    window.editor.stem.setPlainText("Typed but not saved")
    pick(window.questions.slide_list, "b")
    assert window.questions.sel_slide == "a" and current_key(window.questions.slide_list) == "a"
    pick(window.questions.topic_list, topics[1].id)
    assert current_key(window.questions.topic_list) == "all"
    window.editor.back_button.click()
    window.questions.focus_switch.buttons()[1].click()
    assert not window.open_question("q3") and window.questions.focus == "slides"
    assert window.questions.stack.currentWidget() is window.editor and window.editor.stem.toPlainText() == "Typed but not saved"
    assert len(asked) == 5
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Discard)
    pick(window.questions.slide_list, "b")
    assert window.questions.sel_slide == "b" and window.editor.current is None


def test_needs_attention_lists_changed_and_ai_drafts(lab):
    _, _, bank, _, window, _, link, _ = lab
    # A draft the AI edited needs attention too; a reviewed question that is merely AI-origin does not.
    bank.save(replace(bank.get("q2"), ai_edit=Origin("ai", "route", "model")))
    add(bank, "q9", "AI but reviewed", [link["a"]], "reviewed", origin=Origin("ai", "route", "model"))
    bank.set_changed_slides("q5", ("a",))  # Retired: never needs attention.
    window.reload()
    pick(window.questions.topic_list, "attention")
    assert keys(window.questions.slide_list) == ["q2", "q3", "q4"]
    window.questions.review_row_opened(window.questions.slide_list.item(1))
    assert window.editor.current.id == "q3" and window.editor.back_button.text() == "Needs attention"
    window.editor.back_button.click()
    assert window.questions.view == "attention" and window.questions.stack.currentWidget() is window.questions.placeholder


def test_unlinked_questions_are_reachable_and_stay_reviewed(lab):
    _, _, bank, _, window, _, link, _ = lab
    add(bank, "q10", "Reviewed nowhere", status="reviewed")
    window.reload()
    pick(window.questions.topic_list, "unlinked")
    assert keys(window.questions.slide_list) == ["q6", "q10", "q7", "q8"]  # Retired last.
    window.questions.review_row_opened(window.questions.slide_list.item(1))
    window.editor.back_button.click()
    assert bank.get("q10").status == "reviewed" and window.questions.view == "unlinked"
    for item in ("q6", "q7", "q8", "q10"):
        bank.save(replace(bank.get(item), status="draft", sources=(link["a"],)))
    window.reload()
    assert window.questions.view == "all" and "unlinked" not in keys(window.questions.topic_list)


def test_a_retired_question_whose_slide_left_the_deck_stays_reachable(env):
    _, _, bank, _, window = env
    bank.create(Item("gone", 1, stem="Old", sources=(SourceLink("removed-slide"),)))
    bank.set_status("gone", 1, "reviewed")
    bank.set_status("gone", 2, "retired")
    window.reload()
    window.slides = []
    assert [i.id for i in window.questions.unlinked()] == ["gone"]


def test_reload_keeps_selection_and_unsaved_editor_text(lab):
    _, _, bank, _, window, _, _, _ = lab
    pick(window.questions.slide_list, "c")
    window.questions.card_opened(window.questions.cards.item(0))
    window.editor.stem.setPlainText("Typing, not saved")
    add(bank, "q11", "Arrives meanwhile", [window.editor.links[0]])
    window.reload()
    assert window.questions.sel_slide == "c" and window.editor.stem.toPlainText() == "Typing, not saved" and window.editor.dirty()
    assert window.questions.stack.currentWidget() is window.editor


# -- topic dialog -------------------------------------------------------------------------------------------


def stored(window):
    return [(t.name, set(t.slides)) for t in TopicStore(window.bank.folder).load()]


def test_topic_dialog_creates_selects_and_invalidates_the_exam(lab):
    _, _, _, _, window, _, _, _ = lab
    window.exam_tab.selection = object()
    window.questions.edit_topic()
    window.questions.topic_dialog.name.setText("  Geometry ")
    window.questions.topic_dialog.buttons["d"].setChecked(True)
    window.questions.topic_dialog.done_button.click()
    assert stored(window)[-1] == ("Geometry", {"d"}) and window.exam_tab.selection is None
    assert window.questions.view == window.topics[-1].id and keys(window.questions.slide_list) == ["d"]


def test_topic_dialog_takes_a_slide_over_from_another_topic(lab):
    _, _, _, _, window, _, _, topics = lab
    window.questions.edit_topic(topics[1].id)
    window.questions.topic_dialog.buttons["a"].setChecked(True)
    window.questions.topic_dialog.done_button.click()
    assert stored(window) == [("Probability", {"b"}), ("Algebra", {"a", "c"})]


def test_topic_dialog_delete_confirms_and_frees_the_slides(lab, monkeypatch):
    _, _, _, _, window, _, _, topics = lab
    window.questions.edit_topic(topics[1].id)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    window.questions.topic_dialog.delete_button.click()
    assert window.questions.topic_dialog.isVisible() and len(stored(window)) == 2
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    window.questions.topic_dialog.delete_button.click()
    assert [name for name, _ in stored(window)] == ["Probability"] and "2 slides" in row_text(window.questions.topic_list, "none")


# -- drafting, writing, sidebar, review ---------------------------------------------------------------------


class SpyDialog(QDialog):
    made = []
    edit_requested = Signal(str)

    def __init__(self, *args, **kwargs):
        super().__init__(args[3] if len(args) > 3 else args[0])  # DraftDialog's parent is its fourth argument.
        self.args, self.kwargs, self.items = args, kwargs, ()
        SpyDialog.made.append(self)


def test_write_question_links_the_selected_slide(lab):
    host, _, bank, _, window, _, _, _ = lab
    pick(window.questions.slide_list, "b")
    window.questions.write_button.click()
    host.drain()
    assert window.editor.new_item and [l.slide_id for l in window.editor.links] == ["b"]
    window.editor.stem.setPlainText("Fresh question")
    assert window.editor.save() and bank.get(window.editor.current.id).sources[0].slide_id == "b"


def test_sidebar_writes_for_the_focused_slide_and_opens_exam_forge(lab):
    host, deck, bank, controller, window, refs, _, _ = lab
    sidebar = controller.sidebar(deck)
    buttons = {b.text(): b for b in sidebar.findChildren(type(window.questions.write_button))}
    write = buttons["Write a question for this slide…"]
    write.click()
    assert "No slide is in focus" in host.messages[-1][0]
    host.current_focus = Focus(deck.path, refs["c"].path, "slide")
    write.click()
    host.drain()
    assert window.questions.sel_slide == "c" and [l.slide_id for l in window.editor.links] == ["c"]
    window.editor.stem.setPlainText("From the slide")
    assert window.editor.save() and bank.get(window.editor.current.id).sources[0].view == "instructor"
    window.shell.close()
    buttons["Open Exam Forge"].click()
    host.drain()
    assert window.shell.isVisible()


def test_review_dialog_never_overwrites_a_dirty_editor(lab, monkeypatch):
    _, _, bank, _, window, _, _, _ = lab
    monkeypatch.setattr("exam_forge.ui.questions_tab.ReviewChangesDialog", SpyDialog)
    pick(window.questions.slide_list, "b")
    window.questions.review_button.click()
    spy = SpyDialog.made[-1]
    assert [i.id for i in spy.args[1]] == ["q3"] and spy.args[2] == "b"
    spy.edit_requested.emit("q3")
    spy.accept()
    bank.set_changed_slides("q3", ())  # The dialog resolved the question meanwhile.
    window.questions.review_changes(window.editor.current)
    SpyDialog.made[-1].accept()
    assert window.editor.current.source_changed is False  # A clean editor shows the stored version.
    window.editor.stem.setPlainText("Mine")
    window.questions.review_changes(window.editor.current)
    bank.set_changed_slides("q3", ("b",))
    SpyDialog.made[-1].accept()
    assert window.editor.stem.toPlainText() == "Mine" and window.editor.dirty()


# -- export, palette, window --------------------------------------------------------------------------------


def test_moodle_export_takes_every_reviewed_revision_question(lab, tmp_path, monkeypatch):
    host, _, bank, _, window, _, link, _ = lab
    add(bank, "exam", "Reviewed exam question", [link["a"]], "reviewed", use="exam")
    exported, asked = [], []
    monkeypatch.setattr("exam_forge.ui.export_menu.export_moodle_xml",
                        lambda items, bank, rules: exported.extend(items) or b"<quiz/>")
    monkeypatch.setattr("exam_forge.ui.export_menu.QFileDialog.getSaveFileName",
                        lambda *a: asked.append(a) or (str(tmp_path / "all"), ""))
    pick(window.questions.topic_list, "attention")  # No filter applies.
    window.export_button.click()
    window.export_menu.moodle_action.trigger()
    host.drain()
    assert asked[0][2] == str(window.bank.folder / "revision-quiz.xml")
    assert sorted(i.id for i in exported) == ["q1", "q3"] and (tmp_path / "all.xml").read_bytes() == b"<quiz/>"


def test_the_window_guards_unsaved_work(lab, monkeypatch):
    host, _, _, _, window, *_ = lab
    window.present()
    assert window.shell.windowTitle() == window.shell.windowTitle() == "Exam Forge · course"
    window.questions.write_button.click()
    host.drain()
    window.editor.stem.setPlainText("Unsaved")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    assert not window.shell.close() and window.shell.isVisible()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    assert window.shell.close() and not window.shell.isVisible()


def test_header_footer_and_columns_follow_the_design(lab):
    _, _, _, _, window, _, _, topics = lab
    header = window.findChild(QFrame, "efHeader")
    assert {"Exam Forge", "course"} <= {label.text() for label in header.findChildren(QLabel)}
    for part in (window.tabs.switch, window.check_button, window.export_button):
        assert header.isAncestorOf(part)
    assert not window.check_button.icon().isNull() and not window.export_button.icon().isNull()
    assert window.notice.parent().objectName() == "efFooter" and window.notice.text().startswith("Slides checked")
    sections = texts(window.questions.topic_list)
    assert "TOPICS" in sections and "REVIEW" in sections
    unlinked = len(window.questions.unlinked())
    assert unlinked and row_text(window.questions.topic_list, "unlinked").split(" | ")[-1] == str(unlinked)
    pick(window.questions.topic_list, topics[0].id)
    assert window.questions.list_title.text() == "Probability"
    assert window.questions.list_sub.text() == "2 slides in deck order"


def test_the_topic_pencil_is_faint_until_its_row_is_hovered(lab):
    _, _, _, _, window, _, _, topics = lab
    listw = window.questions.topic_list
    row = listw.itemWidget(next(listw.item(i) for i in range(listw.count()) if listw.item(i).data(KEY) == topics[0].id))
    pencil = row.findChild(QToolButton)
    assert pencil.accessibleName() == "Edit topic Probability" and pencil.property("faint")
    QApplication.sendEvent(row, QEvent(QEvent.Type.Enter))
    assert not pencil.property("faint")
    QApplication.sendEvent(row, QEvent(QEvent.Type.Leave))
    assert pencil.property("faint")
