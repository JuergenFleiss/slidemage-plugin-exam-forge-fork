# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The question editor: answers, footer state, linked slides, banner and palette."""
from dataclasses import replace

from fake_host import fill, revision, source
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QLabel, QMessageBox
from slidemage.plugin_api import SlideSource

from exam_forge.model import Item, Option, Origin
from exam_forge.ui.editor import reviewed_issues
from exam_forge.ui.kit import window_sheet


def ticks(window):
    return [row.correct() for row in window.editor.rows]


def test_ticks_are_a_radio_for_revision_and_multiple_for_exam_which_pads_to_five(env):
    window = env[4]
    window.questions.new_question()
    ids = [row.option_id for row in window.editor.rows]
    window.editor.rows[0].text.setPlainText("typed")
    window.editor.rows[0].tick.click()
    window.editor.rows[1].tick.click()
    assert ticks(window) == [False, True]
    window.editor.use.set_value("exam")
    window.editor.rows[0].tick.click()
    assert ticks(window) == [True, True, False, False, False] and [r.option_id for r in window.editor.rows[:2]] == ids
    assert not window.editor.add_button.isEnabled() and "exactly five answers" in window.editor.hint.text()
    window.editor.use.set_value("revision")
    assert ticks(window)[:2] == [True, True]  # Switching never unsets ticks.
    for _ in range(3):
        window.editor.add_option()
    assert len(window.editor.rows) == 8 and not window.editor.add_button.isEnabled()
    window.editor.use.set_value("exam")
    assert len(window.editor.rows) == 8 and window.editor.rows[0].text.toPlainText() == "typed"  # Never deleted.
    assert "have exactly five answers" in reviewed_issues(window.editor.candidate(), window.answer_rules)


def test_the_footer_follows_the_review_lifecycle(env, monkeypatch):
    host, _, _, _, window = env
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Discard)
    window.questions.new_question()
    assert reviewed_issues(window.editor.candidate(), window.answer_rules) == [
        "write the question", "fill in every answer", "mark exactly one correct answer", "link a slide"]
    assert not window.editor.approve_button.isEnabled() and window.editor.save_button.isEnabled()
    item = fill(window)
    assert window.editor.status_line.text() == "Draft · saved" and not window.editor.save_button.isEnabled()
    window.editor.stem.setPlainText("Changed")
    assert window.editor.status_line.text() == "Unsaved edits · saving creates a new draft revision"
    window.editor.stem.setPlainText(item.stem)
    window.editor.approve_button.click()
    host.drain()
    assert window.editor.status_line.text() == "Reviewed · ready to use" and not window.editor.approve_button.isEnabled()
    window.editor.objective.setText("edit")
    assert window.editor.retire_button.isHidden() and window.editor.approve_button.text() == "Mark reviewed"
    window.editor.objective.setText(window.editor.current.objective)
    window.editor.retire_button.click()
    host.drain()
    assert window.editor.status_line.text().startswith("Retired") and not window.editor.approve_button.isEnabled()
    window.editor.restore_button.click()
    host.drain()
    assert window.editor.current.status == "draft"


def test_reviewing_an_edited_question_saves_a_draft_first_and_keeps_option_ids(env):
    host, _, bank, _, window = env
    item = fill(window)
    window.editor.approve_button.click()
    host.drain()
    reviewed = window.editor.current
    window.editor.stem.setPlainText("Edited")
    window.editor.approve_button.click()
    host.drain()
    assert window.editor.current.status == "reviewed" and window.editor.current.revision == reviewed.revision + 2
    assert revision(bank, reviewed.id, reviewed.revision + 1).status == "draft"
    assert [o.id for o in window.editor.current.options] == [o.id for o in item.options]


def test_still_correct_keeps_the_status_and_takes_the_new_baseline(env, monkeypatch):
    host, deck, bank, _, window = env
    item = fill(window)
    window.editor.approve_button.click()
    host.drain()
    moved = replace(source(host, deck), position=7)
    host.slides[(deck.path, "instructor", "en")] = [moved]
    host.sources[moved] = SlideSource(text="Changed")
    window.check_sources()
    host.drain()
    assert window.editor.source_banner.text() == "Slide 7 changed after this question was written."
    window.editor.stem.setPlainText("Typed")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Cancel)
    window.editor.still_button.click()  # Unsaved edits: it asks first, and Cancel changes nothing.
    host.drain()
    assert window.editor.stem.toPlainText() == "Typed" and window.editor.current.source_changed
    window.editor.stem.setPlainText(item.stem)
    window.editor.still_button.click()
    host.drain()
    current = bank.get(item.id)
    assert current.status == "reviewed" and not current.source_changed and current.sources[0].text == "Changed"
    assert window.editor.current == current and window.editor.source_banner.isHidden()


def test_still_correct_on_a_stale_revision_keeps_the_fields(env):
    host, _, bank, _, window = env
    item = fill(window)
    window.editor.populate(bank.set_changed_slides(item.id, ("s1",)))
    window.editor.stem.setPlainText("Typed")
    bank.save(replace(bank.get(item.id), stem="Their edit"))
    window.editor.baseline = window.editor.candidate()  # No unsaved changes, but the revision is stale.
    window.editor.still_button.click()
    host.drain()
    assert "stale revision" in host.messages[-1][0] and window.editor.stem.toPlainText() == "Typed"


def test_back_asks_about_unsaved_edits(env, monkeypatch):
    window = env[4]
    item = fill(window)
    backs = []
    window.editor.closed.connect(lambda: backs.append(1))
    window.editor.stem.setPlainText("Typed")
    answers = iter([QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save])
    monkeypatch.setattr(QMessageBox, "question", lambda *a: next(answers))
    window.editor.back_button.click()
    assert backs == [] and window.editor.stem.toPlainText() == "Typed"
    window.editor.back_button.click()
    assert backs == [1] and window.bank.get(item.id).stem == "Typed"


def test_the_link_grid_lists_the_decks_slides_and_links_in_the_instructor_view(env):
    host, deck, _, _, window = env
    source(host, deck)
    window.load_slides()
    host.drain()
    window.questions.new_question()
    window.editor.link_button.click()
    assert [b.text() for b in window.editor.link_grid_buttons] == ["1 · Addition"]
    window.editor.link_grid_buttons[0].click()
    assert window.busy  # The slide is read on the worker.
    host.drain()
    link, = window.editor.links
    assert (link.slide_id, link.view, link.language, link.text) == ("s1", "instructor", "en", "Two plus two = four")
    assert window.editor.link_box.itemAt(0).widget().findChild(type(window.questions.write_button)).text() == "Slide 1 · Addition"
    window.editor.remove_source(0)
    assert "link a slide" in window.editor.status_line.text()


def text_color(widget):
    widget.ensurePolished()
    return widget.palette().color(QPalette.ColorRole.WindowText).name()


def test_ai_chips_and_the_dark_theme_take_the_hosts_colours(env):
    host, _, bank, _, window = env
    ai = bank.create(Item("ai", 1, "AI", (Option("a", "x", True), Option("b", "y")), origin=Origin("ai", "r", "m")))
    window.questions.open_card(ai.id)
    assert window.editor.draft_tag.isVisible() and not window.editor.ai_tag.isVisible()
    chip_text = window.editor.draft_tag.findChild(QLabel)
    assert text_color(chip_text) == host.theme().warning and text_color(window.editor.status_line) == host.theme().error
    host.set_dark(True)
    assert text_color(chip_text) == host.theme().warning and text_color(window.editor.status_line) == host.theme().error
    assert text_color(window.editor.source_banner) == host.theme().accents["violet"]
    # Surfaces come from the host's theme tokens: the sheet is rebuilt from the dark ones, with no light surface left.
    assert window.styleSheet() == window_sheet(host.theme()) and window_sheet(host.theme()) != window_sheet(
        replace(host.theme(), surface="#123456"))
    assert host.theme().surface in window.styleSheet() and "#ffffff" not in window.styleSheet()
    assert window.editor.draft_tag.styleSheet() == ""
    window.editor.populate(bank.set_status("ai", 1, "reviewed"))
    assert window.editor.ai_tag.isVisible() and not window.editor.draft_tag.isVisible() and window.editor.ai_tag.toolTip() == "r · m"
    edited = bank.create(Item("edit", 1, "E", ai.options, ai_edit=Origin("ai", "route", "mod")))
    window.editor.populate(edited)  # Updated by the AI: an AI draft until it is reviewed, like its card.
    assert window.editor.draft_tag.isVisible() and not window.editor.ai_tag.isVisible() and window.editor.draft_tag.toolTip() == "route · mod"
    window.editor.populate(bank.set_status("edit", 1, "reviewed"))
    assert window.editor.ai_tag.isVisible() and window.editor.ai_tag.toolTip() == "route · mod"


def test_answer_cards_tint_with_their_tick_and_name_their_parts(env):
    _, _, _, _, window = env
    window.questions.new_question()
    first, second = window.editor.rows
    first.tick.click()
    assert first.tick.isChecked() and first.property("correct") and not second.property("correct")
    second.tick.click()  # A revision quiz has one correct answer: the tick moves, and the tint with it.
    assert not first.property("correct") and second.property("correct") and second.correct()
    assert first.tick.accessibleName() == "Option 1 is correct" and second.remove.accessibleName() == "Remove answer 2"
    assert second.text.accessibleName() == "Answer 2" and second.why.accessibleName() == "Rationale for answer 2"
    window.editor.use.set_value("exam")
    assert len(window.editor.rows) == 5 and window.editor.add_button.isHidden()
    window.editor.setEnabled(False)
    assert not any(part.isEnabled() for part in (first.tick, first.text, first.why, first.remove))


def test_text_fields_grow_with_their_text(env):
    _, _, _, _, window = env
    window.questions.new_question()
    stem, answer = window.editor.stem, window.editor.rows[0].text
    short, line = stem.height(), answer.height()
    stem.setPlainText("\n".join("line" for _ in range(6)))
    answer.setPlainText("first\nsecond")
    assert stem.height() > short and answer.height() > line
    stem.setPlainText("\n".join("line" for _ in range(10)))
    tallest = stem.height()
    assert stem.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    stem.setPlainText("\n".join("line" for _ in range(30)))  # Beyond its ten lines it scrolls instead.
    assert stem.height() == tallest and stem.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
