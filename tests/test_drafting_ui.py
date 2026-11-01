# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the real chooser, worker preparation, host callback and review UI."""
import json
from dataclasses import replace
from threading import get_ident

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QPlainTextEdit, QPushButton
from slidemage.plugin_api import AiChatError, MediaDescription, SlideSource

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
    host.chat = chat
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
    monkeypatch.setattr(host, "ai_chat", lambda: pytest.fail("AI off fetched chat"))
    window.draft_with_ai()
    assert not hasattr(window, "drafting_dialog") and bank.load() == ([], [])


def test_outgoing_request_excludes_bank_keys_rationales_and_student_data(env, tmp_path, monkeypatch):
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
    assert all(dialog.choices.item(i).checkState() == Qt.CheckState.Unchecked
               for i in range(dialog.choices.count()))
    dialog.choices.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.send.click()
    host.drain()
    request, = chat.requests
    assert json.loads(request.text) == {"slides": [{"slide": 1, "text": "SELECTED_TEACHING_TEXT"}]}
    for excluded in ("BANK_STEM", "BANK_KEY", "BANK_ANSWER", "BANK_RATIONALE", "STUDENT_NAME", "STUDENT_GRADE", "UNCHOSEN_SLIDE",
                     "IMAGE_PATH", "UNREVIEWED_IMAGE_DESCRIPTION"):
        assert excluded not in request.instructions + request.text
    chat.done(result(), None)
    host.drain()
    assert window.sel_slide == ref.id and window.cards.count() == 1 and window.current is None
    window.card_opened(window.cards.item(0))
    draft = window.current
    assert draft.status == "draft" and bank.get(draft.id).status == "draft"
    assert window.draft_tag.isVisible() and "color:" in window.draft_tag.styleSheet()
    assert draft.sources[0].slide_id == ref.id
    assert window.ai_tag.toolTip() == "captured-route · captured-model"
    exported = []
    monkeypatch.setattr("exam_forge.ui.questions_tab.export_moodle_xml",
                        lambda items, bank: exported.extend(items) or b"<quiz/>")
    monkeypatch.setattr("exam_forge.ui.questions_tab.QFileDialog.getSaveFileName", lambda *a: (str(tmp_path / "o.xml"), ""))
    window.export()
    host.drain()
    assert [item.id for item in exported] == ["old"]  # Drafts are not exportable.
    window.approve_button.click()
    host.drain()
    assert window.current.status == "reviewed" and window.draft_tag.isHidden()
    assert window.ai_tag.isVisible()
    window.stem.setPlainText("Author edit")
    window.save()
    assert window.current.status == "draft" and window.draft_tag.isVisible()
    assert bank.get(draft.id, 2).status == "reviewed"


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
    assert dialog.preview.toPlainText() == "Original"
    dialog.choices.item(0).setCheckState(Qt.CheckState.Checked)
    host.sources[ref] = SlideSource("Changed", descriptions=(MediaDescription("image", short="Not reviewed"),))
    dialog.send.click()
    host.drain()
    payload = json.loads(chat.requests[0].text)["slides"][0]
    assert payload["text"] == "Original"
    assert "image_descriptions" not in payload
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
    if case == "truncated":
        dialog.count.setValue(2)
    dialog.send.click()
    host.drain()
    if case == "provider":
        chat.done(None, AiChatError("Provider failed"))
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


def test_bulk_drafts_link_to_selected_slides(env):
    host, deck, bank, _, window = env
    first = source(host, deck)
    second = replace(first, id="second", id_slug="second", position=2)
    host.slides[(deck.path, "instructor", "en")].append(second)
    host.sources[second] = SlideSource("Second slide")
    chat, _ = enable(host)
    dialog = choose(host, window)
    assert (dialog.count.minimum(), dialog.count.maximum(), dialog.count.value()) == (1, 30, 1)
    for i in range(2):
        dialog.choices.item(i).setCheckState(Qt.CheckState.Checked)
    dialog.count.setValue(2)
    dialog.send.click()
    host.drain()
    assert len(chat.requests) == 1 and chat.requests[0].max_tokens == 8192
    from test_drafting import reply_row
    chat.done(result([reply_row(), {**reply_row(), "slide": 2}]), None)
    host.drain()
    drafts, errors = bank.load()
    assert not errors and len(drafts) == 2
    assert [item.sources[0].slide_id for item in drafts] == [first.id, second.id]
    assert [item.sources[0].text for item in drafts] == [host.sources[first].text, "Second slide"]
    assert all(item.topic == "" for item in drafts)
    assert all(item.status == "draft" and item.origin.kind == "ai" and
               (item.origin.route, item.origin.model) == ("captured-route", "captured-model")
               for item in drafts)
    assert window.sel_slide == first.id and "2 AI draft" in window.notice.text()
    assert host.messages[-1] == ("2 AI drafts added · review before use", False)


@pytest.mark.parametrize("rows", [[], [1], [1, 1, 1], [2, 1]])
def test_bulk_wrong_count_or_slide_refused(env, rows):
    from test_drafting import reply_row
    host, deck, bank, _, window = env
    source(host, deck)
    chat, _ = enable(host)
    dialog = choose(host, window)
    dialog.choices.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.count.setValue(2)
    dialog.send.click()
    host.drain()
    chat.done(result([{**reply_row(), "slide": n} for n in rows]), None)
    host.drain()
    assert bank.load() == ([], [])
    assert "No drafts were added" in dialog.message.text()
    if len(rows) != 2:
        assert f"requested 2 questions but received {len(rows)}" in dialog.message.text()
    dialog.reject()


def slides(host, deck, n=3, view="instructor"):
    first = source(host, deck)
    refs = [first] + [replace(first, id=f"s{i}", id_slug=f"s{i}", position=i) for i in range(2, n + 1)]
    host.slides[(deck.path, view, "en")] = refs
    for ref in refs[1:]:
        host.sources[ref] = SlideSource(f"Text of {ref.id}")
    return refs


def ticks(dialog):
    return [dialog.choices.item(i).checkState() == Qt.CheckState.Checked for i in range(dialog.choices.count())]


def scoped(host, deck, window, *slide_ids, preselect=None, topic=True):
    from exam_forge.topics import Topic
    from exam_forge.ui.drafting import DraftDialog
    topics = (Topic("t1", "Probability", "blue", tuple(slide_ids)),)
    dialog = DraftDialog(host, deck, window.bank, window, topics=topics,
                         topic_id="t1" if topic else None, preselect=preselect)
    host.drain()
    return dialog


def test_grid_scopes_to_topic_ticks_only_the_opening_slide_and_widens(env):
    host, deck, _, _, window = env
    slides(host, deck)
    dialog = scoped(host, deck, window, "s1", "s2", preselect="s2")
    assert dialog.choices.count() == 2 and ticks(dialog) == [False, True]
    dialog.show_all.setChecked(True)
    assert dialog.choices.count() == 3 and ticks(dialog) == [False, True, False]
    dialog.choices.item(2).setCheckState(Qt.CheckState.Checked)
    dialog.show_all.setChecked(False)
    assert ticks(dialog) == [False, True]
    dialog.show_all.setChecked(True)
    assert ticks(dialog) == [False, True, False]  # the hidden tick was dropped when the grid narrowed
    dialog.reject()
    outside = scoped(host, deck, window, "s1", preselect="s3")
    assert outside.show_all.isChecked() and outside.choices.count() == 3
    assert ticks(outside) == [False, False, True]
    outside.reject()
    everything = scoped(host, deck, window, topic=False)
    assert everything.choices.count() == 3 and not any(ticks(everything))
    everything.reject()
    empty = scoped(host, deck, window)
    assert empty.choices.count() == 3 and not any(ticks(empty))
    empty.reject()


def test_ticks_survive_reload_when_view_or_language_changes(env):
    host, deck, _, _, window = env
    refs = slides(host, deck)
    host.slides[(deck.path, "after-course", "en")] = refs[:2]
    host.slides[(deck.path, "instructor", "de")] = []
    dialog = scoped(host, deck, window, preselect="s2", topic=False)
    dialog.choices.item(2).setCheckState(Qt.CheckState.Checked)
    dialog.view.setCurrentIndex(dialog.view.findData("after-course"))
    host.drain()
    assert ticks(dialog) == [False, True]  # s3 is absent from this view and is no longer ticked
    dialog.view.setCurrentIndex(0)
    host.drain()
    assert ticks(dialog) == [False, True, False]
    dialog.language.setCurrentText("de")
    host.drain()
    assert dialog.choices.count() == 0 and not dialog.send.isEnabled()
    dialog.reject()


def test_button_and_note_follow_ticks_and_count(env):
    host, deck, _, _, window = env
    slides(host, deck)
    chat, _ = enable(host)
    dialog = scoped(host, deck, window, topic=False)
    assert dialog.send.text() == "Draft 1 question" and not dialog.send.isEnabled()
    assert "Only the text of the 0 ticked slides is sent" in dialog.note.text()
    dialog.choices.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.count.setValue(4)
    assert dialog.send.text() == "Draft 4 questions" and dialog.send.isEnabled()
    assert "Only the text of the 1 ticked slide is sent" in dialog.note.text()
    assert "Drafts arrive labelled AI draft and wait for your review." in dialog.note.text()
    assert dialog.language.currentText() == "en"
    dialog.choices.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert not dialog.send.isEnabled()
    dialog.choices.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.send.click()
    assert dialog.send.text() == "Drafting…" and not dialog.send.isEnabled()
    host.drain()
    chat.done(None, AiChatError("nope"))
    host.drain()
    assert dialog.send.text() == "Draft 4 questions" and dialog.send.isEnabled()
    dialog.reject()


def test_only_ticked_slide_text_is_sent_from_a_scoped_grid(env):
    host, deck, _, _, window = env
    slides(host, deck)
    chat, _ = enable(host)
    dialog = scoped(host, deck, window, "s1", "s2", preselect="s2")
    dialog.send.click()
    host.drain()
    request, = chat.requests
    assert json.loads(request.text) == {"slides": [{"slide": 1, "text": "Text of s2"}]}
    assert "Two plus two" not in request.text + request.instructions
    chat.done(result(), None)
    host.drain()
    assert dialog.result() == dialog.DialogCode.Accepted and dialog.items[0].sources[0].slide_id == "s2"


def test_tick_outside_the_shown_grid_is_never_sent(env):
    host, deck, _, _, window = env
    slides(host, deck)
    chat, _ = enable(host)
    dialog = scoped(host, deck, window, "s1", "s2", preselect="s2")
    dialog.show_all.setChecked(True)
    dialog.choices.item(2).setCheckState(Qt.CheckState.Checked)
    assert "2 ticked slides" in dialog.note.text()
    dialog.show_all.setChecked(False)
    assert "1 ticked slide " in dialog.note.text()
    dialog.send.click()
    host.drain()
    request, = chat.requests
    assert "Text of s3" not in request.text and "Text of s2" in request.text
    dialog.reject()


def test_same_slide_at_two_versions_is_ticked_per_row(env):
    host, deck, _, _, window = env
    first = source(host, deck)
    other = replace(first, version="2-0-0", position=2)
    host.slides[(deck.path, "instructor", "en")] = [first, other]
    host.sources[other] = SlideSource("Second version text")
    chat, _ = enable(host)
    dialog = scoped(host, deck, window, topic=False, preselect="s1")
    assert ticks(dialog) == [True, False]  # only the first ref with that id
    dialog.choices.item(0).setCheckState(Qt.CheckState.Unchecked)
    dialog.choices.item(1).setCheckState(Qt.CheckState.Checked)
    assert ticks(dialog) == [False, True] and "1 ticked slide " in dialog.note.text()
    dialog.send.click()
    host.drain()
    request, = chat.requests
    assert json.loads(request.text) == {"slides": [{"slide": 1, "text": "Second version text"}]}
    dialog.reject()
