# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Review changes dialog on real offscreen widgets and the real bank."""
import json
from dataclasses import replace

import pytest
from fake_host import enable_ai, source
from PySide6.QtCore import QCoreApplication, QEvent
from slidemage.plugin_api import AiChatError, AiChatResult, Route, SlideSource

from exam_forge.ai_support import NO_REPLY
from exam_forge.model import Item, Option, Origin
from exam_forge.sources import link_slide
from exam_forge.ui.editor import accent_color
from exam_forge.ui.review_changes import ReviewChangesDialog

OLD, NEW = "Two plus two = four\nsecond <b>line</b> & more", "Two plus three = five\nsecond <b>line</b> & more"


def reply():
    row = {"stem": "What is two plus three?", "objective": "Addition", "difficulty": "easy", "options": [
        {"text": "5", "correct": True, "rationale": "Sum."}, {"text": "4", "correct": False, "rationale": "Off."}]}
    return AiChatResult(json.dumps(row), Route("upd-route", "chat", "upd-model"))


@pytest.fixture
def review(env):
    """A reviewed question q1 whose slide changed from OLD to NEW, and a way to open the dialog on it."""
    host, deck, bank, _, window = env
    ref = source(host, deck)
    host.sources[ref] = SlideSource(OLD)
    link = link_slide(host, ref, "instructor")
    host.sources[ref] = SlideSource(NEW)
    bank.create(Item("q1", 1, "What is two plus two?", (Option("a", "4", True, "yes"), Option("b", "3", False, "no")),
                     sources=(link,)))
    bank.set_status("q1", 1, "reviewed")
    item = bank.set_source_changed("q1", True)

    def open_dialog(items=(item,), slide_id=None):
        dialog = ReviewChangesDialog(window, list(items), slide_id)
        host.drain()
        return dialog
    return host, bank, item, open_dialog


def test_the_diff_marks_changes_with_escaped_text_and_no_backgrounds(review):
    *_, open_dialog = review
    dialog = open_dialog()
    old_html, new_html = dialog.old_pane.toHtml(), dialog.new_pane.toHtml()
    assert OLD in dialog.old_pane.toPlainText() and NEW in dialog.new_pane.toPlainText()
    assert "line-through" in old_html and "underline" in new_html and accent_color(dialog.new_pane) in new_html
    assert "<b>line" not in old_html + new_html and "&lt;b&gt;line" in new_html
    assert "background" not in (old_html + new_html).replace("background-color:transparent", "")
    assert dialog.rows[0].update.isHidden()  # AI support is off.
    dialog.reject()


def test_a_missing_slide_blocks_keep_and_update_but_not_edit_and_retired_are_left_out(review):
    host, _, item, open_dialog = review
    enable_ai(host)
    gone = replace(item.sources[0], slide_id="gone", text="elsewhere")
    dialog = open_dialog([replace(item, sources=(gone, item.sources[0])), replace(item, id="r", status="retired")], "s1")
    row, = dialog.rows
    assert dialog.new_pane.toPlainText().startswith("Slide s1") and "Slide gone" in dialog.new_pane.toPlainText()
    assert not row.keep.isEnabled() and not row.update.isEnabled() and row.edit.isEnabled()
    got = []
    dialog.edit_requested.connect(got.append)
    row.edit.click()
    assert got == ["q1"] and dialog.result() == dialog.DialogCode.Accepted


def test_still_correct_keeps_reviewed_status_and_takes_the_new_baseline(review):
    host, bank, _, open_dialog = review
    dialog = open_dialog()
    row = dialog.rows[0]
    row.keep.click()
    assert not row.keep.isEnabled() and not row.edit.isEnabled()
    host.drain()
    saved = bank.get("q1")
    assert saved.status == "reviewed" and not saved.source_changed and saved.sources[0].text == NEW
    assert row.chip.text() == "Kept · matches the current slide" and row.edit.isEnabled()
    dialog.reject()


def test_still_correct_on_an_edited_question_writes_nothing(review):
    host, bank, item, open_dialog = review
    dialog = open_dialog()
    bank.save(replace(item, stem="Edited elsewhere", status="draft"))
    dialog.rows[0].keep.click()
    host.drain()
    assert "stale" in dialog.rows[0].note.text() and bank.get("q1").sources[0].text == OLD
    dialog.reject()


def test_an_ai_update_of_an_author_question_keeps_its_origin_and_records_the_route(review):
    host, bank, _, open_dialog = review
    chat, _ = enable_ai(host)
    dialog = open_dialog()
    row = dialog.rows[0]
    row.update.click()
    host.drain()
    assert json.loads(chat.requests[0].text)["slide_text"] == NEW
    assert not row.keep.isEnabled() and not row.update.isEnabled()
    chat.done(reply(), None)
    host.drain()
    saved = bank.get("q1")
    assert saved.origin == Origin() and saved.status == "draft" and saved.stem == "What is two plus three?"
    assert (saved.ai_edit_route, saved.ai_edit_model) == ("upd-route", "upd-model")
    assert not saved.source_changed and bank.get("q1", 2).status == "reviewed" and len(bank.load()[0]) == 1
    dialog.reject()


REPLIES = {"invalid": (replace(reply(), text='{"stem":"x"}'), None), "provider": (None, AiChatError("Provider failed")),
           "declined": (None, None)}
NOTES = {"invalid": "was not changed", "provider": "Provider failed", "declined": NO_REPLY, "ai_off": "discarded"}


@pytest.mark.parametrize("case", ["invalid", "provider", "declined", "ai_off", "rejected", "destroyed"])
def test_a_refused_or_late_ai_reply_leaves_the_question_unchanged(review, case):
    host, bank, _, open_dialog = review
    chat, page = enable_ai(host)
    dialog = open_dialog()
    row = dialog.rows[0]
    row.update.click()
    host.drain()
    dialog.keep(row)  # Busy rows ignore a second operation.
    assert not host.pending
    if case == "rejected":
        dialog.reject()
        assert chat.cancelled == 1
    elif case == "destroyed":
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    elif case == "ai_off":
        page.ai_checkbox.setChecked(False)
    chat.done(*REPLIES.get(case, (reply(), None)))
    host.drain()
    assert bank.get("q1").revision == 2 and bank.get("q1").source_changed
    if case in NOTES:
        assert NOTES[case] in row.note.text() and row.chip is None
    if case in REPLIES:
        assert row.update.isEnabled() and row.keep.isEnabled()
        dialog.reject()
