# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The question editor: answers, footer state, linked slides, banner and palette."""
from dataclasses import replace

from fake_host import fill, revision, source
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QMessageBox
from slidemage.plugin_api import SlideSource

from exam_forge.model import Item, Option, Origin
from exam_forge.ui.editor import accent_color, red_color, reviewed_issues


def ticks(window):
    return [row.correct() for row in window.rows]


def test_ticks_are_a_radio_for_revision_and_multiple_for_elp_which_pads_to_five(env):
    window = env[4]
    window.new_question()
    ids = [row.option_id for row in window.rows]
    window.rows[0].text.setPlainText("typed")
    window.rows[0].tick.click()
    window.rows[1].tick.click()
    assert ticks(window) == [False, True]
    window.use.setCurrentData("elp")
    window.rows[0].tick.click()
    assert ticks(window) == [True, True, False, False, False] and [r.option_id for r in window.rows[:2]] == ids
    assert not window.add_button.isEnabled() and "exactly five answers" in window.hint.text()
    window.use.setCurrentData("revision")
    assert ticks(window)[:2] == [True, True]  # Switching never unsets ticks.
    for _ in range(3):
        window.add_option()
    assert len(window.rows) == 8 and not window.add_button.isEnabled()
    window.use.setCurrentData("elp")
    assert len(window.rows) == 8 and window.rows[0].text.toPlainText() == "typed"  # Never deleted.
    assert "have exactly five answers" in reviewed_issues(window.candidate(), window.answer_rules)


def test_the_footer_follows_the_review_lifecycle(env, monkeypatch):
    host, _, _, _, window = env
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Discard)
    window.new_question()
    assert reviewed_issues(window.candidate(), window.answer_rules) == [
        "write the question", "fill in every answer", "mark exactly one correct answer", "link a slide"]
    assert not window.approve_button.isEnabled() and window.save_button.isEnabled()
    item = fill(window)
    assert window.status_line.text() == "Draft · saved" and not window.save_button.isEnabled()
    window.stem.setPlainText("Changed")
    assert window.status_line.text() == "Unsaved edits · saving creates a new draft revision"
    window.stem.setPlainText(item.stem)
    window.approve_button.click()
    host.drain()
    assert window.status_line.text() == "Reviewed · ready to use" and not window.approve_button.isEnabled()
    window.objective.setText("edit")
    assert window.retire_button.isHidden() and window.approve_button.text() == "Mark reviewed"
    window.objective.setText(window.current.objective)
    window.retire_button.click()
    host.drain()
    assert window.status_line.text().startswith("Retired") and not window.approve_button.isEnabled()
    window.restore_button.click()
    host.drain()
    assert window.current.status == "draft"


def test_reviewing_an_edited_question_saves_a_draft_first_and_keeps_option_ids(env):
    host, _, bank, _, window = env
    item = fill(window)
    window.approve_button.click()
    host.drain()
    reviewed = window.current
    window.stem.setPlainText("Edited")
    window.approve_button.click()
    host.drain()
    assert window.current.status == "reviewed" and window.current.revision == reviewed.revision + 2
    assert revision(bank, reviewed.id, reviewed.revision + 1).status == "draft"
    assert [o.id for o in window.current.options] == [o.id for o in item.options]


def test_still_correct_keeps_the_status_and_takes_the_new_baseline(env, monkeypatch):
    host, deck, bank, _, window = env
    item = fill(window)
    window.approve_button.click()
    host.drain()
    moved = replace(source(host, deck), position=7)
    host.slides[(deck.path, "instructor", "en")] = [moved]
    host.sources[moved] = SlideSource(text="Changed")
    window.check_sources()
    host.drain()
    assert window.source_banner.text() == "Slide 7 changed after this question was written."
    window.stem.setPlainText("Typed")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Cancel)
    window.still_button.click()  # Unsaved edits: it asks first, and Cancel changes nothing.
    host.drain()
    assert window.stem.toPlainText() == "Typed" and window.current.source_changed
    window.stem.setPlainText(item.stem)
    window.still_button.click()
    host.drain()
    current = bank.get(item.id)
    assert current.status == "reviewed" and not current.source_changed and current.sources[0].text == "Changed"
    assert window.current == current and window.source_banner.isHidden()


def test_still_correct_on_a_stale_revision_keeps_the_fields(env):
    host, _, bank, _, window = env
    item = fill(window)
    window.populate(bank.set_changed_slides(item.id, ("s1",)))
    window.stem.setPlainText("Typed")
    bank.save(replace(bank.get(item.id), stem="Their edit"))
    window.baseline = window.candidate()  # No unsaved changes, but the revision is stale.
    window.still_button.click()
    host.drain()
    assert "stale revision" in window.notice.text() and window.stem.toPlainText() == "Typed"


def test_back_asks_about_unsaved_edits(env, monkeypatch):
    window = env[4]
    fill(window)
    backs = []
    window.leave_editor = lambda: backs.append(1)
    window.stem.setPlainText("Typed")
    answers = iter([QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save])
    monkeypatch.setattr(QMessageBox, "question", lambda *a: next(answers))
    window.back_button.click()
    assert backs == [] and window.stem.toPlainText() == "Typed"
    window.back_button.click()
    assert backs == [1] and window.bank.get(window.current.id).stem == "Typed"


def test_the_link_grid_lists_the_decks_slides_and_links_in_the_instructor_view(env):
    host, deck, _, _, window = env
    source(host, deck)
    window.load_slides()
    host.drain()
    window.new_question()
    window.link_button.click()
    assert [b.text() for b in window.link_grid_buttons] == ["1 · Addition"]
    window.link_grid_buttons[0].click()
    assert window.busy  # The slide is read on the worker.
    host.drain()
    link, = window.links
    assert (link.slide_id, link.view, link.language, link.text) == ("s1", "instructor", "en", "Two plus two = four")
    assert window.link_box.itemAt(0).widget().findChild(type(window.write_button)).text() == "Slide 1 · Addition"
    window.remove_source(0)
    assert "link a slide" in window.status_line.text()


def test_ai_chips_and_the_dark_palette_recolour_text_only(env):
    _, _, bank, _, window = env
    ai = bank.create(Item("ai", 1, "AI", (Option("a", "x", True), Option("b", "y")), origin=Origin("ai", "r", "m")))
    window.populate(ai)
    assert window.draft_tag.isVisible() and not window.ai_tag.isVisible()
    assert red_color(window) == "#a8231a" and accent_color(window, True) in window.draft_tag.styleSheet()
    palette = window.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#eeeeee"))
    window.setPalette(palette)
    assert "#fbbf24" in window.draft_tag.styleSheet() and "#c4b5fd" in window.source_banner.styleSheet()
    assert red_color(window) == "#fca5a5" and red_color(window) in window.status_line.styleSheet()
    assert all("background" not in w.styleSheet() for w in (window.editor, window.stem, window.status_line))
    window.populate(bank.set_status("ai", 1, "reviewed"))
    assert window.ai_tag.isVisible() and not window.draft_tag.isVisible() and window.ai_tag.toolTip() == "r · m"
    edited = bank.create(Item("edit", 1, "E", ai.options, ai_edit=Origin("ai", "route", "mod")))
    window.populate(edited)
    assert window.ai_tag.isVisible() and window.ai_tag.toolTip() == "route · mod"
