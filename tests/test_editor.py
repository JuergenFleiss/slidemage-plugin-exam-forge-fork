# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The question editor: answers, footer state, linked slides, banner and palette."""
from dataclasses import replace

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QMessageBox
from slidemage.plugin_api import SlideSource
from test_ui import app, env, fill, source  # noqa: F401 - fixtures

from exam_forge.model import Item, Option, Origin
from exam_forge.topics import Topic, topic_color
from exam_forge.ui.editor import accent_color, red_color, reviewed_issues


def ticks(window):
    return [row.correct() for row in window.rows]


def test_tick_is_radio_for_revision_and_multi_for_elp(env):
    window = env[4]
    window.new_question()
    window.add_option()
    window.rows[0].tick.click()
    window.rows[1].tick.click()
    assert ticks(window) == [False, True, False]
    window.rows[1].tick.click()
    assert ticks(window) == [False, True, False]  # a radio stays ticked
    window.use.setCurrentData("elp")
    window.rows[0].tick.click()
    assert ticks(window) == [True, True, False, False, False]
    window.rows[1].tick.click()
    assert ticks(window)[:2] == [True, False]
    window.rows[1].tick.click()
    window.use.setCurrentData("revision")
    assert ticks(window)[:2] == [True, True]  # switching never unsets ticks
    assert "mark exactly one correct answer" in window.status_line.text()


def test_elp_pads_to_five_keeps_typed_answers_and_caps(env):
    window = env[4]
    window.new_question()
    ids = [row.option_id for row in window.rows]
    window.rows[0].text.setPlainText("typed")
    window.use.setCurrentData("elp")
    assert len(window.rows) == 5 and [r.option_id for r in window.rows[:2]] == ids
    assert not window.add_button.isEnabled() and "exactly five answers" in window.hint.text()
    window.use.setCurrentData("revision")
    for _ in range(3):
        window.add_option()
    assert len(window.rows) == 8 and not window.add_button.isEnabled()
    window.use.setCurrentData("elp")
    assert len(window.rows) == 8 and window.rows[0].text.toPlainText() == "typed"  # never deleted
    assert "have exactly five answers" in reviewed_issues(window.candidate())
    window.remove_option(window.rows[-1])
    assert len(window.rows) == 7


def test_issue_list_and_footer_buttons(env, monkeypatch):
    host, _, bank, _, window = env
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Discard)
    window.new_question()
    assert reviewed_issues(window.candidate()) == [
        "write the question", "fill in every answer", "mark exactly one correct answer", "link a slide"]
    assert window.status_line.text().startswith("Before marking reviewed: write the question · ")
    assert not window.approve_button.isEnabled() and window.save_button.isEnabled()
    assert window.retire_button.isHidden() and window.restore_button.isHidden()
    item = fill(window)
    assert window.status_line.text() == "Draft · saved"
    assert not window.save_button.isEnabled() and window.approve_button.isEnabled()
    window.stem.setPlainText("Changed")
    assert window.status_line.text() == "Unsaved edits · saving creates a new draft revision"
    assert window.save_button.isEnabled()
    window.stem.setPlainText(item.stem)
    assert not window.save_button.isEnabled()
    window.rows[0].text.setPlainText("")
    assert "fill in every answer" in window.status_line.text() and not window.approve_button.isEnabled()
    window.rows[0].text.setPlainText("4")
    window.approve_button.click()
    host.drain()
    assert window.status_line.text() == "Reviewed · ready to use"
    assert window.approve_button.text() == "Reviewed" and not window.approve_button.isEnabled()
    assert window.retire_button.isEnabled() and not window.retire_button.isHidden()
    window.objective.setText("edit")
    assert window.retire_button.isHidden() and window.approve_button.text() == "Mark reviewed"
    window.objective.setText(window.current.objective)
    window.retire_button.click()
    host.drain()
    assert window.status_line.text().startswith("Retired") and not window.restore_button.isHidden()
    assert not window.approve_button.isEnabled() and window.retire_button.isHidden()
    window.restore_button.click()
    host.drain()
    assert window.current.status == "draft"
    unlinked = bank.create(Item("bare", 1, "Q", window.current.options))
    window.populate(unlinked)
    assert "link a slide" in window.status_line.text()


def test_reviewed_edit_goes_through_save_then_review(env):
    host, _, bank, _, window = env
    fill(window)
    window.approve_button.click()
    host.drain()
    reviewed = window.current
    window.stem.setPlainText("Edited")
    window.approve_button.click()
    host.drain()
    assert window.current.status == "reviewed" and window.current.revision == reviewed.revision + 2
    assert bank.get(reviewed.id, reviewed.revision + 1).status == "draft"


def test_still_correct_keeps_status_and_clears_flag(env, monkeypatch):
    host, deck, bank, _, window = env
    ref = source(host, deck)
    item = fill(window)
    window.approve_button.click()
    host.drain()
    moved = replace(ref, position=7)
    host.slides[(deck.path, "instructor", "en")] = [moved]
    host.sources[moved] = SlideSource(text="Changed")
    window.check_sources()
    host.drain()
    assert window.current.source_changed and window.source_banner.isVisible()
    assert window.source_banner.text() == "Slide 7 changed after this question was written."
    shown = []
    window.review_changes = shown.append
    window.compare_button.click()
    assert shown == [window.current]
    # Unsaved edits: the button asks first, and Cancel changes nothing.
    window.stem.setPlainText("Typed")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Cancel)
    window.still_button.click()
    host.drain()
    assert window.stem.toPlainText() == "Typed" and window.current.source_changed
    window.stem.setPlainText(item.stem)
    revision = window.current.revision
    window.still_button.click()
    host.drain()
    current = bank.get(item.id)
    assert current.status == "reviewed" and not current.source_changed
    assert current.revision == revision + 1 and window.current == current
    assert current.sources[0].text == "Changed" and window.source_banner.isHidden()


def test_still_correct_error_keeps_fields(env):
    host, deck, bank, _, window = env
    item = fill(window)
    bank.set_source_changed(item.id, True)
    window.populate(bank.get(item.id))
    host.drain()
    window.stem.setPlainText("Typed")
    bank.save(replace(bank.get(item.id), stem="Their edit"))
    window.baseline = window.candidate()  # no unsaved changes, but the revision is stale
    window.still_button.click()
    host.drain()
    assert "stale revision" in window.notice.text() and window.stem.toPlainText() == "Typed"


def test_unsaved_dialog_kept_on_back(env, monkeypatch):
    window = env[4]
    fill(window)
    backs = []
    window.leave_editor = lambda: backs.append(1)
    window.set_back_label("‹ Slide 1 · Addition")
    assert window.back_button.text() == "‹ Slide 1 · Addition" and not window.back_button.isHidden()
    window.back_button.click()
    assert backs == [1]
    window.stem.setPlainText("Typed")
    answers = iter([QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save])
    monkeypatch.setattr(QMessageBox, "question", lambda *a: next(answers))
    window.back_button.click()
    assert backs == [1] and window.stem.toPlainText() == "Typed"
    window.back_button.click()
    assert backs == [1, 1] and window.bank.get(window.current.id).stem == "Typed"


def test_link_grid_uses_worker_and_last_context(env):
    host, deck, bank, _, window = env
    ref = source(host, deck)
    window.new_question()
    assert window.last_link_context == ("instructor", "en")
    window.link_button.click()
    assert window.busy and not window.link_grid_buttons
    host.drain()
    assert host.read_threads and all(t != host.ui_thread for t in host.read_threads)
    assert [b.text() for b in window.link_grid_buttons] == ["1 · Addition"]
    window.link_grid_buttons[0].click()
    host.drain()
    link, = window.links
    assert link.slide_id == "s1" and link.text == "Two plus two = four" and not window.link_grid.isVisible()
    assert window.link_box.count() == 1
    assert window.slide_label(link) == "Slide 1 · Addition"
    window.link_button.click()
    host.drain()
    assert not window.link_grid_buttons[0].isEnabled()  # already linked
    window.link_button.click()
    window.remove_source(0)
    assert window.links == () and "link a slide" in window.status_line.text()
    other = replace(ref, id="s2", id_slug="s2-EN-Other", position=2, title="Other", language_read="de")
    host.slides[(deck.path, "after-course", "de")] = [other]
    host.sources[other] = SlideSource(text="Anderer Text")
    window.last_link_context = ("after-course", "de")
    window.link_button.click()
    host.drain()
    window.link_grid_buttons[0].click()
    host.drain()
    assert window.links[0].view == "after-course" and window.links[0].language == "de"
    assert window.last_link_context == ("after-course", "de")


def test_topic_line_follows_first_link_and_leaves_item_topic(env):
    item = fill(env[4])
    window = env[4]
    window.topics = (Topic("t1", "Probability <b>", "blue", ("s1",)),)
    window.render_links()
    text = window.topic_label.text()
    assert "Topic from first slide:" in text and "Probability &lt;b&gt;" in text
    assert topic_color("blue", False) in text
    assert window.candidate().topic == "Arithmetic"
    window.remove_source(0)
    assert window.topic_label.text() == "No topic" and window.candidate().topic == "Arithmetic"
    window.links = item.sources
    window.render_links()
    assert "Probability" in window.topic_label.text()
    palette = window.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    window.setPalette(palette)
    assert topic_color("blue", True) in window.topic_label.text()


def test_dark_palette_recolours_text_only(env):
    _, _, bank, _, window = env
    window.new_question()
    assert red_color(window) == "#a8231a" and red_color(window) in window.status_line.styleSheet()
    bank.create(Item("ai", 1, "AI", (Option("a", "x", True), Option("b", "y")), origin=Origin("ai", "r", "m")))
    window.populate(bank.get("ai"))
    assert window.draft_tag.isVisible() and accent_color(window, True) in window.draft_tag.styleSheet()
    palette = window.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#eeeeee"))
    window.setPalette(palette)
    assert "#fbbf24" in window.draft_tag.styleSheet()
    assert "#c4b5fd" in window.source_banner.styleSheet()
    assert red_color(window) == "#fca5a5" and red_color(window) in window.status_line.styleSheet()
    for widget in (window.editor, window.stem, window.status_line):
        assert "background" not in widget.styleSheet()


def test_unknown_difficulty_is_preserved_and_not_dirty(env):
    _, _, bank, _, window = env
    item = fill(env[4])
    odd = bank.save(replace(item, difficulty="medium"))
    window.populate(odd)
    assert window.difficulty.currentData() == "medium" and not window.dirty()
    assert not any(b.isChecked() for b in window.difficulty.buttons.values())
    assert not window.save_button.isEnabled()
    window.stem.setPlainText("Edited")
    assert window.candidate().difficulty == "medium"
    window.difficulty.buttons["hard"].click()
    assert window.candidate().difficulty == "hard" and window.dirty()
    window.difficulty.buttons["hard"].click()
    assert window.difficulty.currentData() == ""


def test_option_ids_stay_stable_across_edits(env):
    window = env[4]
    item = fill(window)
    ids = [o.id for o in item.options]
    window.rows[0].text.setPlainText("Four")
    assert window.save()
    assert [o.id for o in window.current.options] == ids


def test_ai_chips(env):
    _, _, bank, _, window = env
    ai = bank.create(Item("ai", 1, "AI", (Option("a", "x", True), Option("b", "y")), origin=Origin("ai", "r", "m")))
    window.populate(ai)
    assert window.draft_tag.isVisible() and not window.ai_tag.isVisible()
    window.populate(bank.set_status("ai", 1, "reviewed"))
    assert window.ai_tag.isVisible() and not window.draft_tag.isVisible()
    assert window.ai_tag.toolTip() == "r · m"
    edited = bank.create(Item("edit", 1, "E", ai.options, ai_edit_route="route", ai_edit_model="mod"))
    window.populate(edited)
    assert window.ai_tag.isVisible() and window.ai_tag.toolTip() == "route · mod" and not window.draft_tag.isVisible()
