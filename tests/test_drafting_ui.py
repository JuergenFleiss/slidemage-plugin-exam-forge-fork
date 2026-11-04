# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Drafting with AI through the real chooser, worker preparation and host callback."""
import json
from dataclasses import replace

import pytest
from fake_host import enable_ai, source
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from slidemage.plugin_api import AiChatError, MediaDescription, SlideSource
from test_drafting import reply_row, result

from exam_forge.ai_support import NO_REPLY
from exam_forge.model import Item, Option, Topic
from exam_forge.ui.drafting import DraftDialog


def send_first(host, window):
    """Open the dialog from the window, tick the first slide and send."""
    window.draft_button.click()
    dialog = window.drafting_dialog
    host.drain()
    dialog.choices.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.send.click()
    return dialog


def send_one(host, window):
    chat, page = enable_ai(host)
    return chat, page, send_first(host, window)


def test_ai_off_hides_the_entry_and_gates_the_action(env, monkeypatch):
    host, _, bank, _, window = env
    assert window.draft_button.isHidden()
    monkeypatch.setattr(host, "ai_chat", lambda: pytest.fail("AI off fetched chat"))
    window.draft_with_ai()
    assert not hasattr(window, "drafting_dialog") and bank.load() == ([], [])


def test_the_request_carries_only_the_ticked_slide_text_and_drafts_arrive_as_ai_drafts(env):
    host, deck, bank, _, window = env
    ref = source(host, deck)
    host.sources[ref] = SlideSource("SELECTED_TEACHING_TEXT", descriptions=(
        MediaDescription("IMAGE_PATH", short="UNREVIEWED_IMAGE_DESCRIPTION"),))
    other = replace(ref, id="other", id_slug="other", position=2)
    host.slides[(deck.path, "instructor", "en")].append(other)
    host.sources[other] = SlideSource("UNCHOSEN_SLIDE")
    bank.create(Item("old", 1, "BANK_STEM", (Option("BANK_KEY", "BANK_ANSWER", True, "BANK_RATIONALE"),
                                            Option("BANK_KEY2", "BANK_ANSWER", False, "BANK_RATIONALE"))))
    chat, _, dialog = send_one(host, window)
    host.drain()
    request, = chat.requests
    assert json.loads(request.text) == {"slides": [{"slide": 1, "text": "SELECTED_TEACHING_TEXT"}]}
    for excluded in ("BANK_STEM", "BANK_KEY", "BANK_ANSWER", "BANK_RATIONALE", "UNCHOSEN_SLIDE",
                     "IMAGE_PATH", "UNREVIEWED_IMAGE_DESCRIPTION"):
        assert excluded not in request.instructions + request.text
    chat.done(result(), None)
    host.drain()
    window.card_opened(window.cards.item(0))
    draft = window.current
    assert draft.status == "draft" and draft.sources[0].slide_id == ref.id and window.draft_tag.isVisible()
    assert window.ai_tag.toolTip() == "captured-route · captured-model"


def test_preview_and_request_share_one_source_read(env, monkeypatch):
    host, deck, _, _, window = env
    ref = source(host, deck)
    host.sources[ref] = SlideSource("Original")
    read, calls = host.slide_source, []
    monkeypatch.setattr(host, "slide_source", lambda ref: calls.append(ref) or read(ref))
    chat, _ = enable_ai(host)
    window.draft_button.click()
    dialog = window.drafting_dialog
    host.drain()
    dialog.choices.setCurrentRow(0)
    assert dialog.preview.toPlainText() == "Original"
    dialog.choices.item(0).setCheckState(Qt.CheckState.Checked)
    host.sources[ref] = SlideSource("Changed")
    dialog.send.click()
    host.drain()
    assert json.loads(chat.requests[0].text)["slides"][0]["text"] == "Original" and calls == [ref]
    dialog.reject()


@pytest.mark.parametrize("case", ["decline", "cancel", "before_ready", "destroyed", "ai_off", "ai_off_preparing"])
def test_declined_cancelled_and_late_replies_add_nothing(env, case):
    host, deck, bank, _, window = env
    source(host, deck)
    before = list(host.messages)
    chat, page, dialog = send_one(host, window)
    if case == "ai_off_preparing":
        page.ai_checkbox.setChecked(False)
        assert window.draft_button.isHidden()
    if case == "before_ready":
        dialog.reject()
    host.drain()
    if case in ("ai_off_preparing", "before_ready"):
        assert not chat.requests
    elif case == "decline":
        chat.done(None, None)
        assert dialog.isVisible() and dialog.message.text() == NO_REPLY and dialog.send.isEnabled()
    else:
        if case == "cancel":
            dialog.reject()
            assert chat.cancelled == 1
        elif case == "destroyed":
            # Real C++ destruction while the callback still retains its Python wrapper.
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        else:
            page.ai_checkbox.setChecked(False)
        chat.done(result(), None)
        if case == "ai_off":
            assert "discarded" in dialog.message.text()
    host.drain()
    assert bank.load() == ([], []) and host.messages == before


@pytest.mark.parametrize("case", ["malformed", "provider"])
def test_bad_replies_and_provider_errors_add_nothing_and_allow_retry(env, case):
    host, deck, bank, _, window = env
    source(host, deck)
    chat, _, dialog = send_one(host, window)
    host.drain()
    if case == "provider":
        chat.done(None, AiChatError("Provider failed"))
    else:
        chat.done(replace(result(), text='{"items":['), None)
    host.drain()
    assert bank.load() == ([], []) and dialog.send.isEnabled()
    assert ("Provider failed" if case == "provider" else "No drafts were added") in dialog.message.text()
    dialog.reject()


def slides(host, deck, n=3):
    first = source(host, deck)
    refs = [first] + [replace(first, id=f"s{i}", id_slug=f"s{i}", position=i) for i in range(2, n + 1)]
    host.slides[(deck.path, "instructor", "en")] = refs
    for ref in refs[1:]:
        host.sources[ref] = SlideSource(f"Text of {ref.id}")
    return refs


def scoped(host, deck, window, *slide_ids, preselect=None):
    topics = (Topic("t1", "Probability", "blue", tuple(slide_ids)),)
    dialog = DraftDialog(host, deck, window.bank, window, window.answer_rules, topics=topics, topic_id="t1",
                         preselect=preselect)
    host.drain()
    return dialog


def ticks(dialog):
    return [dialog.choices.item(i).checkState() == Qt.CheckState.Checked for i in range(dialog.choices.count())]


def test_bulk_drafts_link_to_their_slides(env):
    host, deck, bank, _, window = env
    first, second, _ = slides(host, deck)
    chat, _ = enable_ai(host)
    window.draft_button.click()
    dialog = window.drafting_dialog
    host.drain()
    for i in range(2):
        dialog.choices.item(i).setCheckState(Qt.CheckState.Checked)
    dialog.count.setValue(2)
    assert dialog.send.text() == "Draft 2 questions" and "Only the text of the 2 ticked slides is sent" in dialog.note.text()
    dialog.send.click()
    host.drain()
    chat.done(result([reply_row(), {**reply_row(), "slide": 2}]), None)
    host.drain()
    drafts, errors = bank.load()
    assert [item.sources[0].slide_id for item in drafts] == [first.id, second.id] and not errors
    assert all(item.origin.kind == "ai" for item in drafts)
    assert host.toasts == ["2 AI drafts added · review before use"] and not host.messages  # Reported once.


def test_the_grid_scopes_to_the_topic_and_hidden_ticks_are_never_sent(env):
    host, deck, _, _, window = env
    slides(host, deck)
    chat, _ = enable_ai(host)
    dialog = scoped(host, deck, window, "s1", "s2", preselect="s2")
    assert dialog.choices.count() == 2 and ticks(dialog) == [False, True]
    dialog.show_all.setChecked(True)
    dialog.choices.item(2).setCheckState(Qt.CheckState.Checked)
    dialog.show_all.setChecked(False)
    dialog.send.click()
    host.drain()
    request, = chat.requests
    assert json.loads(request.text) == {"slides": [{"slide": 1, "text": "Text of s2"}]}
    chat.done(result(), None)
    host.drain()
    assert dialog.result() == dialog.DialogCode.Accepted and dialog.items[0].sources[0].slide_id == "s2"
    outside = scoped(host, deck, window, "s1", preselect="s3")
    assert outside.show_all.isChecked() and ticks(outside) == [False, False, True]
    outside.reject()
