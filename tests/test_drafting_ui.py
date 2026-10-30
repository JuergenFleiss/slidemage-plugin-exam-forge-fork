# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the real chooser, worker preparation, host callback and review UI."""
import json
from dataclasses import replace
from threading import get_ident

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QPlainTextEdit, QPushButton
from slidemage.plugin_api import MediaDescription, ServiceError, SlideSource

from exam_forge.model import Item, Option
from exam_forge.drafting import DEFAULT_GUIDANCE
from test_drafting import result
from test_ui import app, env, source  # noqa: F401 - shared real-window fixtures


class Chat:
    def __init__(self, host):
        self.host, self.requests, self.cancelled = host, [], 0

    def available(self):
        return True

    def request(self, request, done):
        assert get_ident() == self.host.ui_thread
        self.requests.append(request)
        self.done = done

        def cancel():
            assert get_ident() == self.host.ui_thread
            self.cancelled += 1
        return cancel


def enable(host):
    chat = Chat(host)
    host.offer("ai.chat", 1, chat)
    page = host.settings_tabs["Exam Forge"]()
    page.findChild(QCheckBox).setChecked(True)
    return chat, page


def choose(host, window):
    window.draft_button.click()
    dialog = window.drafting_dialog
    host.drain()
    return dialog


def test_settings_guidance_reaches_request_and_reset(env):
    host, deck, _, _, window = env
    source(host, deck)
    chat, page = enable(host)
    field = page.findChild(QPlainTextEdit)
    reset = next(button for button in page.findChildren(QPushButton) if button.text() == "Reset to default")
    assert field.toPlainText() == DEFAULT_GUIDANCE
    field.setPlainText("Ask a practical question.")
    assert host.settings().get("drafting_guidance") == "Ask a practical question."
    dialog = choose(host, window)
    dialog.choices.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.send.click()
    host.drain()
    assert chat.requests[0].instructions.startswith("Ask a practical question.")
    dialog.reject()
    reset.click()
    assert field.toPlainText() == DEFAULT_GUIDANCE
    assert host.settings().get("drafting_guidance") is None
    field.clear()
    assert host.settings().get("drafting_guidance") is None
    page.findChild(QCheckBox).setChecked(False)
    assert field.parentWidget().isHidden() and reset.isHidden()


def test_off_hides_entry_and_gates_programmatic_action(env, monkeypatch):
    host, _, bank, _, window = env
    assert window.draft_button.isHidden()
    monkeypatch.setattr(host, "service", lambda *args: pytest.fail("AI off fetched service"))
    window.draft_with_ai()
    assert not hasattr(window, "drafting_dialog") and bank.load() == ([], [])


def test_outgoing_request_excludes_bank_keys_rationales_and_student_data(env):
    host, deck, bank, _, window = env
    ref = source(host, deck)
    host.sources[ref] = SlideSource("SELECTED_TEACHING_TEXT", descriptions=(
        MediaDescription("IMAGE_PATH", short="UNREVIEWED_IMAGE_DESCRIPTION"),))
    other = replace(ref, id="other", id_slug="other", position=2)
    host.slides[(deck.path, "instructor", "en")].append(other)
    host.sources[other] = SlideSource("UNCHOSEN_SLIDE")
    bank.create(Item("old", 1, "BANK_STEM", (Option("BANK_KEY", "BANK_ANSWER", True, "BANK_RATIONALE"),)))
    bank.set_status("old", 1, "reviewed")
    host.student_data = {"name": "STUDENT_NAME", "grade": "STUDENT_GRADE"}
    chat, page = enable(host)
    assert not window.draft_button.isHidden()
    dialog = choose(host, window)
    assert len(dialog.refs) == 2 and dialog.choices.count() == 2
    assert not dialog.review.isChecked()
    assert all(dialog.choices.item(i).checkState() == Qt.CheckState.Unchecked
               for i in range(dialog.choices.count()))
    dialog.choices.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.send.click()
    host.drain()
    request, = chat.requests
    assert json.loads(request.text) == {"slides": [{"text": "SELECTED_TEACHING_TEXT"}]}
    for excluded in ("BANK_STEM", "BANK_KEY", "BANK_ANSWER", "BANK_RATIONALE", "STUDENT_NAME", "STUDENT_GRADE", "UNCHOSEN_SLIDE",
                     "IMAGE_PATH", "UNREVIEWED_IMAGE_DESCRIPTION"):
        assert excluded not in request.instructions + request.text
    chat.done(result(), None)
    host.drain()
    draft = window.current
    assert draft.status == "draft" and bank.get(draft.id).status == "draft"
    assert window.draft_tag.isVisible() and "color:" in window.draft_tag.styleSheet()
    assert draft.sources[0].slide_id == ref.id
    assert window.ai_tag.toolTip() == "captured-route · captured-model"
    window.search.setText(draft.stem)
    window.export()
    host.drain()
    assert "No reviewed" in window.notice.text()  # Drafts are not exportable.
    window.approve_button.click()
    host.drain()
    assert window.current.status == "reviewed" and window.draft_tag.isHidden()
    assert window.ai_tag.isVisible()
    window.stem.setPlainText("Author edit")
    window.save()
    assert window.current.status == "draft" and window.draft_tag.isVisible()
    assert bank.get(draft.id, 2).status == "reviewed"


def test_review_tick_preview_and_request_payload(env):
    host, deck, _, _, window = env
    ref = source(host, deck)
    host.sources[ref] = SlideSource("Slide", descriptions=(MediaDescription("PRIVATE_KEY", short="Image meaning"),))
    chat, page = enable(host)
    dialog = choose(host, window)
    dialog.choices.setCurrentRow(0)
    host.drain()
    assert "Image meaning" in dialog.preview.toPlainText()
    dialog.choices.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.review.setChecked(True)
    dialog.send.click()
    host.drain()
    assert json.loads(chat.requests[0].text)["slides"][0]["image_descriptions"][0]["short"] == "Image meaning"
    chat.done(None, None)
    again = choose(host, window)
    assert not again.review.isChecked()
    again.reject()


def test_preview_and_request_share_source_snapshot(env, monkeypatch):
    host, deck, _, _, window = env
    ref = source(host, deck)
    host.sources[ref] = SlideSource("Original", descriptions=(MediaDescription("image", short="Reviewed"),))
    read = host.slide_source
    calls = []

    def counted(ref):
        calls.append(ref)
        return read(ref)

    monkeypatch.setattr(host, "slide_source", counted)
    chat, page = enable(host)
    dialog = choose(host, window)
    dialog.choices.setCurrentRow(0)
    assert "Reviewed" in dialog.preview.toPlainText()
    dialog.choices.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.review.setChecked(True)
    host.sources[ref] = SlideSource("Changed", descriptions=(MediaDescription("image", short="Not reviewed"),))
    dialog.send.click()
    host.drain()
    payload = json.loads(chat.requests[0].text)["slides"][0]
    assert payload["text"] == "Original"
    assert payload["image_descriptions"][0]["short"] == "Reviewed"
    assert calls == [ref]
    dialog.reject()


def test_disable_during_request_discards_reply(env):
    host, deck, bank, _, window = env
    source(host, deck)
    chat, page = enable(host)
    dialog = choose(host, window)
    dialog.choices.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.send.click()
    host.drain()
    page.findChild(QCheckBox).setChecked(False)
    chat.done(result(), None)
    host.drain()
    assert bank.load() == ([], [])
    assert "discarded" in dialog.message.text()
    dialog.close()


def test_closing_dialog_cancels_request(env):
    host, deck, bank, _, window = env
    source(host, deck)
    chat, page = enable(host)
    dialog = choose(host, window)
    dialog.choices.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.send.click()
    host.drain()
    dialog.close()
    assert chat.cancelled == 1
    chat.done(result(), None)
    host.drain()
    assert bank.load() == ([], [])


@pytest.mark.parametrize("case", ["decline", "cancel", "before_ready", "destroyed"])
def test_decline_cancel_and_late_completions_are_quiet(env, case):
    host, deck, bank, _, window = env
    source(host, deck)
    chat, page = enable(host)
    dialog = choose(host, window)
    dialog.choices.item(0).setCheckState(Qt.CheckState.Checked)
    before = list(host.messages)
    dialog.send.click()
    if case == "before_ready":
        dialog.reject()
        host.drain()
        assert not chat.requests
    else:
        host.drain()
        if case == "decline":
            chat.done(None, None)
        elif case == "destroyed":
            # Real C++ destruction while the callback still retains its Python wrapper.
            from PySide6.QtCore import QCoreApplication, QEvent
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            chat.done(result(), None)
        else:
            dialog.reject()
            assert chat.cancelled == 1
            chat.done(result(), None)
    host.drain()
    assert bank.load() == ([], []) and host.messages == before


@pytest.mark.parametrize("case", ["truncated", "malformed", "provider"])
def test_bad_replies_and_provider_errors_add_nothing_and_allow_retry(env, case):
    host, deck, bank, _, window = env
    source(host, deck)
    chat, page = enable(host)
    dialog = choose(host, window)
    dialog.choices.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.send.click()
    host.drain()
    if case == "provider":
        chat.done(None, ServiceError("Provider failed"))
    else:
        reply = result(truncated=True) if case == "truncated" else replace(result(), text='{"items":[')
        chat.done(reply, None)
    host.drain()
    assert bank.load() == ([], []) and dialog.send.isEnabled()
    assert ("Provider failed" if case == "provider" else "No drafts were added") in dialog.message.text()
    dialog.reject()


def test_disable_while_preparing_cannot_request_and_updates_existing_window(env):
    host, deck, bank, _, window = env
    source(host, deck)
    chat, page = enable(host)
    dialog = choose(host, window)
    dialog.choices.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.send.click()
    page.findChild(QCheckBox).setChecked(False)
    assert window.draft_button.isHidden()
    host.drain()
    assert not chat.requests and bank.load() == ([], [])
