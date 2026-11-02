# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Review changes dialog on real offscreen widgets and the real bank."""
import json
from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from slidemage.plugin_api import AiChatError, AiChatResult, Route, SlideSource
from test_drafting_ui import enable
from test_ui import app, env, source  # noqa: F401 - shared real-window fixtures

from exam_forge.model import Item, Option, Origin
from exam_forge.sources import link_slide
from exam_forge.ui.editor import accent_color
from exam_forge.ui.review_changes import NO_SNAPSHOT, ReviewChangesDialog

OLD, NEW = "Two plus two = four\nsecond <b>line</b> & more", "Two plus three = five\nsecond <b>line</b> & more"


def reply(**changes):
    row = {"stem": "What is two plus three?", "objective": "Addition", "difficulty": "easy", "options": [
        {"text": "5", "correct": True, "rationale": "Sum."}, {"text": "4", "correct": False, "rationale": "Off."}]}
    return AiChatResult(json.dumps({**row, **changes}), Route("upd-route", "chat", "upd-model"))


def changed_item(host, deck, bank, item_id="q1", *, text=OLD, **fields):
    """A question linked to the slide at `text`; the slide then changes to NEW."""
    ref = source(host, deck)
    host.sources[ref] = SlideSource(text)
    link = link_slide(host, ref, "instructor")
    host.sources[ref] = SlideSource(NEW)
    item = Item(item_id, 1, "What is two plus two?", (Option("a", "4", True, "yes"), Option("b", "3", False, "no")),
                sources=(link,), **fields)
    bank.create(item)
    return bank.set_source_changed(item_id, True)


def open_dialog(host, window, bank, slide_id=None):
    items = [i for i in bank.load()[0] if i.source_changed]
    dialog = ReviewChangesDialog(window, items, slide_id)
    host.drain()
    return dialog


def test_diff_marks_changes_with_escaped_text_and_no_backgrounds(env):
    host, deck, bank, _, window = env
    changed_item(host, deck, bank)
    dialog = open_dialog(host, window, bank)
    assert OLD in dialog.old_pane.toPlainText() and NEW in dialog.new_pane.toPlainText()
    old_html, new_html = dialog.old_pane.toHtml(), dialog.new_pane.toHtml()
    assert "line-through" in old_html and "underline" in new_html
    assert accent_color(dialog.new_pane) in new_html
    assert "<b>line" not in old_html + new_html and "&lt;b&gt;line" in new_html
    assert "background" not in dialog.html([("x", True)], "new") + dialog.html([("x", True)], "old")
    assert "background" not in (old_html + new_html).replace("background-color:transparent", "")
    dialog.reject()


def test_missing_snapshot_shows_note_and_unmarked_current_text(env):
    host, deck, bank, _, window = env
    item = changed_item(host, deck, bank)
    dialog = ReviewChangesDialog(window, [replace(item, sources=(replace(item.sources[0], text=""),))])
    host.drain()
    assert NO_SNAPSHOT in dialog.old_pane.toPlainText() and NEW in dialog.new_pane.toPlainText()
    assert "underline" not in dialog.new_pane.toHtml()
    dialog.reject()


def test_uses_link_for_slide_id_else_first_and_filters_retired(env):
    host, deck, bank, _, window = env
    item = changed_item(host, deck, bank)
    other = replace(item.sources[0], slide_id="gone", text="elsewhere")
    two = replace(item, sources=(other, item.sources[0]))
    retired = replace(item, id="r", status="retired")
    dialog = ReviewChangesDialog(window, [two, retired], "s1")
    host.drain()
    assert len(dialog.rows) == 1 and not dialog.rows[0].keep.isEnabled()  # the missing "gone" link blocks it
    assert dialog.new_pane.toPlainText().startswith("Slide s1") and "Slide gone" in dialog.new_pane.toPlainText()
    assert OLD in dialog.old_pane.toPlainText()
    dialog.reject()
    first =ReviewChangesDialog(window, [two])
    host.drain()
    assert first.new_pane.toPlainText().startswith("Slide gone")
    first.reject()


def test_missing_slide_disables_actions_but_not_edit(env):
    host, deck, bank, _, window = env
    item = changed_item(host, deck, bank)
    host.slides[(deck.path, "instructor", "en")] = []
    enable(host)
    dialog = ReviewChangesDialog(window, [item])
    host.drain()
    row = dialog.rows[0]
    assert "missing" in dialog.new_pane.toPlainText() and "missing" in dialog.old_pane.toPlainText()
    assert not row.keep.isEnabled() and not row.update.isEnabled() and row.edit.isEnabled()
    got = []
    dialog.edit_requested.connect(got.append)
    row.edit.click()
    assert got == ["q1"] and dialog.result() == dialog.DialogCode.Accepted


def test_still_correct_keeps_reviewed_status_and_clears_flag(env):
    host, deck, bank, _, window = env
    changed_item(host, deck, bank)
    bank.set_status("q1", 1, "reviewed")
    bank.set_source_changed("q1", True)
    dialog = open_dialog(host, window, bank)
    row = dialog.rows[0]
    row.keep.click()
    assert not row.keep.isEnabled() and not row.edit.isEnabled()
    host.drain()
    saved = bank.get("q1")
    assert saved.status == "reviewed" and not saved.source_changed and saved.sources[0].text == NEW
    assert row.chip.text() == "Kept · matches the current slide" and not row.keep.isEnabled()
    assert row.edit.isEnabled()
    dialog.reject()


def test_still_correct_on_changed_question_shows_conflict_and_writes_nothing(env):
    host, deck, bank, _, window = env
    item = changed_item(host, deck, bank)
    dialog = open_dialog(host, window, bank)
    bank.save(replace(item, stem="Edited elsewhere"))
    dialog.rows[0].keep.click()
    host.drain()
    assert "stale" in dialog.rows[0].note.text() and dialog.rows[0].chip is None
    assert bank.get("q1").sources[0].text == OLD
    dialog.reject()


def test_ai_update_of_author_question_keeps_origin_and_records_route(env):
    host, deck, bank, _, window = env
    changed_item(host, deck, bank)
    bank.set_status("q1", 1, "reviewed")
    bank.set_source_changed("q1", True)
    chat, _ = enable(host)
    dialog = open_dialog(host, window, bank)
    row = dialog.rows[0]
    assert not row.update.isHidden()
    row.update.click()
    host.drain()
    request, = chat.requests
    assert json.loads(request.text)["slide_text"] == NEW
    assert not row.keep.isEnabled() and not row.update.isEnabled()
    chat.done(reply(), None)
    host.drain()
    saved = bank.get("q1")
    assert saved.origin == Origin() and saved.status == "draft" and saved.stem == "What is two plus three?"
    assert (saved.ai_edit_route, saved.ai_edit_model) == ("upd-route", "upd-model")
    assert not saved.source_changed and saved.sources[0].text == NEW
    assert bank.get("q1", 2).status == "reviewed" and saved.revision == 3
    assert row.chip.text() == "Updated by AI · review the draft"
    assert len(bank.load()[0]) == 1
    dialog.reject()


@pytest.mark.parametrize("case", ["invalid", "provider"])
def test_refused_ai_reply_leaves_question_unchanged(env, case):
    host, deck, bank, _, window = env
    changed_item(host, deck, bank)
    chat, _ = enable(host)
    dialog = open_dialog(host, window, bank)
    dialog.rows[0].update.click()
    host.drain()
    if case == "provider":
        chat.done(None, AiChatError("Provider failed"))
    else:
        chat.done(replace(reply(), text='{"stem":"x"}'), None)
    host.drain()
    row = dialog.rows[0]
    assert ("Provider failed" if case == "provider" else "was not changed") in row.note.text()
    saved = bank.get("q1")
    assert saved.revision == 1 and saved.source_changed and row.chip is None
    assert row.update.isEnabled() and row.keep.isEnabled()
    dialog.reject()


def test_ai_off_hides_update_and_refuses_a_late_reply(env):
    host, deck, bank, _, window = env
    changed_item(host, deck, bank)
    dialog = open_dialog(host, window, bank)
    assert dialog.rows[0].update.isHidden()
    dialog.reject()
    chat, page = enable(host)
    dialog = open_dialog(host, window, bank)
    dialog.rows[0].update.click()
    host.drain()
    page.ai_checkbox.setChecked(False)
    chat.done(reply(), None)
    host.drain()
    assert "discarded" in dialog.rows[0].note.text() and bank.get("q1").revision == 1
    dialog.reject()


def test_close_during_request_cancels_and_drops_the_reply(env):
    host, deck, bank, _, window = env
    changed_item(host, deck, bank)
    chat, _ = enable(host)
    dialog = open_dialog(host, window, bank)
    dialog.rows[0].update.click()
    host.drain()
    dialog.reject()
    assert chat.cancelled == 1
    chat.done(reply(), None)
    host.drain()
    assert bank.get("q1").revision == 1


def test_decline_is_quiet_and_busy_rows_ignore_second_operation(env):
    host, deck, bank, _, window = env
    changed_item(host, deck, bank)
    chat, _ = enable(host)
    dialog = open_dialog(host, window, bank)
    row = dialog.rows[0]
    row.update.click()
    host.drain()
    row.keep.click()  # disabled while the request is open; a forced call is ignored too
    dialog.keep(row)
    assert not host.pending
    before = list(host.messages)
    chat.done(None, None)
    host.drain()
    assert row.note.text() == "" and row.update.isEnabled() and host.messages == before
    assert bank.get("q1").revision == 1
    dialog.reject()


def test_destroyed_dialog_drops_late_reply(env):
    host, deck, bank, _, window = env
    changed_item(host, deck, bank)
    chat, _ = enable(host)
    dialog = open_dialog(host, window, bank)
    dialog.rows[0].update.click()
    host.drain()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    chat.done(reply(), None)
    host.drain()
    assert bank.get("q1").revision == 1


def test_every_changed_link_is_shown_and_an_unreadable_link_blocks_actions(env):
    host, deck, bank, _, window = env
    item = changed_item(host, deck, bank)
    ref = host.slides[(deck.path, "instructor", "en")][0]
    second = replace(ref, id="s2", id_slug="s2", position=2, title="Second")
    host.slides[(deck.path, "instructor", "en")].append(second)
    host.sources[second] = SlideSource("Beta two")
    link2 = replace(item.sources[0], slide_id="s2", version=second.version or "", text="Alpha one")
    two = replace(item, sources=(item.sources[0], link2))
    enable(host)
    dialog = ReviewChangesDialog(window, [two], "s2")
    host.drain()
    old, new = dialog.old_pane.toPlainText(), dialog.new_pane.toPlainText()
    assert new.index("Slide s2 · Second") < new.index("Slide s1 · Addition")
    assert "Alpha one" in old and "Beta two" in new and OLD in old and NEW in new
    assert dialog.rows[0].keep.isEnabled() and dialog.rows[0].update.isEnabled()
    dialog.reject()
    host.slides[(deck.path, "instructor", "en")].remove(second)
    blocked = ReviewChangesDialog(window, [two], "s1")
    host.drain()
    assert "Slide s2" in blocked.new_pane.toPlainText() and "missing" in blocked.new_pane.toPlainText()
    assert not blocked.rows[0].keep.isEnabled() and not blocked.rows[0].update.isEnabled()
    assert blocked.rows[0].edit.isEnabled()
    blocked.reject()
