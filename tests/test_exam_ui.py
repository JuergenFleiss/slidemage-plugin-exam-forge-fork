# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Exam tab end to end, through real widgets and the fake host's worker queue."""
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import get_ident

import pytest
from fake_host import FakeDeck, GuiHost, activate, source
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox
from slidemage.plugin_api import SlideSource

from exam_forge.assembly import Blueprint
from exam_forge.folder import open_chosen_folder
from exam_forge.manifest import Manifest, read_corrections
from exam_forge.model import add_topic, assign_slide
from exam_forge.sources import link_slide
from exam_forge.ui.issue import _SHEETS


class ExamHost(GuiHost):
    def slide_source(self, ref):
        assert get_ident() != self.ui_thread
        return super().slide_source(ref)


@pytest.fixture
def issuing(app, tmp_path, monkeypatch):
    """One reviewed easy ELP question on slide s1 in topic Arithmetic, and an Exam tab asking for it."""
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]) or QMessageBox.StandardButton.Yes)
    root = tmp_path / "course"
    root.mkdir()
    host = ExamHost(root)
    deck = FakeDeck("Course", root / "course.tex")
    deck.path.write_text("Fresh deck text")
    (tmp_path / "bank").mkdir()
    bank = open_chosen_folder(host, deck, tmp_path / "bank")
    window = activate(host).open(deck)
    host.drain()
    ref = source(host, deck)
    window.new_question()
    window.editor.stem.setPlainText("Two plus two")
    window.editor.assistant_note.setPlainText("Shared stem with next item; keep option order; exclude together.")
    window.editor.use.set_value("elp")
    for number, row in enumerate(window.editor.rows, 1):
        row.text.setPlainText(str(number))
    window.editor.rows[0].tick.click()
    window.editor.difficulty.set_value("easy")
    with ThreadPoolExecutor() as executor:
        window.editor.links = (executor.submit(link_slide, host, ref, "instructor").result(),)
    window.editor.save()
    window.editor.approve_button.click()
    host.drain()
    assert window.editor.current.status == "reviewed"
    topics = add_topic((), "Arithmetic")
    assert window.save_topics(assign_slide(topics, topics[0].id, "s1"))
    window.reload()
    window.exam_tab.refresh()
    counts(window.exam_tab, 1, 0)
    paths = iter([str(root / "exam.html"), str(root / "key.html")])
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (next(paths), ""))
    yield host, deck, bank, window, ref, warnings
    for sheet in _SHEETS.values():
        sheet.hide()
        sheet.deleteLater()
    _SHEETS.clear()
    window.hide()
    window.deleteLater()
    QApplication.processEvents()


def counts(exam, easy, hard, topic="Arithmetic"):
    exam.cells[topic]["easy"].setValue(easy)
    exam.cells[topic]["hard"].setValue(hard)


def propose(host, window):
    exam = window.exam_tab
    window.tabs.setCurrentWidget(exam)
    exam.propose()
    host.drain()
    assert exam.selection and exam.selection.feasible, exam.reason.text()
    return exam


def approve(host, window):
    exam = propose(host, window)
    exam.approve_button.click()
    host.drain()
    assert exam.stack.currentWidget() is exam.issue and exam.issue.manifest_path.exists()
    return exam.issue


def add_reviewed(bank, window, item_id, **fields):
    item = bank.create(replace(window.editor.current, id=item_id, revision=1, status="draft", **fields))
    bank.set_status(item.id, item.revision, "reviewed")
    window.reload()
    window.exam_tab.refresh()
    return item


def no_manifest(bank):
    return not list(bank.folder.glob("*.manifest.json"))


def test_swap_freeze_and_documents_keep_the_key_private(issuing, monkeypatch):
    host, _, bank, window, _, warnings = issuing
    add_reviewed(bank, window, "replacement")
    exam = propose(host, window)
    outgoing = exam.selection.items[0]
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a: (a[3][0], True))
    exam.swap_item(outgoing.id)
    incoming = exam.selection.items[0]
    assert incoming.id != outgoing.id and incoming.id in exam.rows and outgoing.id not in exam.rows
    seed = exam.selection.seed
    exam.approve_button.click()
    host.drain()
    view = exam.issue
    frozen = view.manifest.data()
    assert frozen["blueprint"]["seed"] == seed and frozen["items"][0]["id"] == incoming.id
    assert [i["topic"] for i in frozen["items"]] == ["Arithmetic"]  # Resolved from the slide, not the item.
    view.documents(0)
    host.drain()
    assert not warnings  # Only the private key carries the warning.
    view.documents(1)
    host.drain()
    exam_html = (host.root / "exam.html").read_text()
    assert window.editor.current.assistant_note in exam_html and "Correct —" not in exam_html
    assert "Correct" in (host.root / "key.html").read_text() and any("holds answers" in w for w in warnings)


def test_an_infeasible_blueprint_is_reported_without_relaxing(issuing):
    host, _, _, window, _, _ = issuing
    exam = window.exam_tab
    exam.propose(Blueprint({"Arithmetic": {"easy": 2}}))  # Bypasses the disabled button.
    host.drain()
    assert exam.selection is None and exam.stack.currentIndex() == 0
    assert "needs 2 easy questions; only 1 is eligible" in exam.reason.text()


def test_a_new_pick_changes_the_seed_and_a_topic_save_discards_the_proposal(issuing):
    host, _, _, window, _, _ = issuing
    exam = propose(host, window)
    seed, blueprint = exam.selection.seed, exam.selection.blueprint
    exam.again_button.click()
    host.drain()
    assert exam.selection.seed != seed and exam.selection.blueprint == blueprint
    assert window.save_topics(add_topic(window.topics, "Geometry"))
    assert exam.selection is None and exam.stack.currentIndex() == 0
    assert exam.cells["Arithmetic"]["easy"].value() == 1


def test_a_changed_source_blocks_approval_without_a_way_around(issuing):
    host, _, bank, window, _, _ = issuing
    exam = propose(host, window)
    bank.set_changed_slides(window.editor.current.id, ("s1",), expected_sources=window.editor.current.sources)
    window.reload()  # As after any check: the tab reads the window's questions.
    host.drain()
    assert exam.blocked.text() == "1 picked question is linked to a changed slide. Review or swap before freezing."
    assert not exam.approve_button.isEnabled()
    exam.approve()  # Even called directly, nothing is frozen.
    host.drain()
    assert exam.stack.currentIndex() == 1 and no_manifest(bank)


@pytest.mark.parametrize("when", ["approve", "freeze"])
def test_a_fresh_check_finding_a_change_blocks_the_freeze(issuing, when):
    host, deck, bank, window, ref, _ = issuing
    exam = propose(host, window)
    host.sources[ref] = SlideSource(text="Overwritten source")
    deck.path.write_text("Reread this fresh text before approval")
    if when == "approve":
        exam.approve_button.click()
    else:
        exam.issue.freeze(exam.selection)
    host.drain()
    assert host.last_deck_text == "Reread this fresh text before approval"
    assert exam.stack.currentIndex() == 1 and no_manifest(bank) and not window.busy
    assert exam.blocked.text().startswith("1 picked question is linked to a changed slide.")


def test_a_conflict_at_approval_stays_on_review(issuing, monkeypatch):
    host, _, bank, window, _, _ = issuing
    exam = propose(host, window)
    bank.set_status(window.editor.current.id, window.editor.current.revision, "retired")
    errors = []
    monkeypatch.setattr(window, "error", errors.append)
    exam.approve_button.click()
    host.drain()
    assert errors and exam.stack.currentIndex() == 1 and exam.issue.manifest is None


def test_editing_after_issue_leaves_the_manifest_and_documents_alone(issuing):
    host, _, bank, window, _, _ = issuing
    view = approve(host, window)
    view.documents(0)
    host.drain()
    before, exam_before = view.manifest_path.read_bytes(), (host.root / "exam.html").read_bytes()
    window.editor.stem.setPlainText("Changed after issue")
    assert window.editor.save()
    assert view.manifest_path.read_bytes() == before and (host.root / "exam.html").read_bytes() == exam_before


def test_opening_a_manifest_lands_on_the_frozen_step(issuing, monkeypatch):
    host, _, bank, window, _, _ = issuing
    path = approve(host, window).manifest_path
    exam = window.exam_tab
    exam.reset()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(path), ""))
    exam.open_issued()
    host.drain()
    assert exam.stack.currentWidget() is exam.issue and exam.issue.manifest_path == path
    assert exam.issue.summary.text().startswith("1 question · 12 points · 2.5 min")


def test_corrections_go_to_a_separate_journal_and_a_failed_save_keeps_the_input(issuing, monkeypatch):
    host, _, _, window, _, _ = issuing
    view = approve(host, window)
    before = view.manifest_path.read_bytes()
    view.corrections()
    host.drain()
    sheet = _SHEETS[view.journal_path.resolve()]
    sheet.change.setPlainText("Accept option B too")
    sheet.reason.setPlainText("Ambiguous wording")
    sheet.append.click()
    assert sheet.busy and not sheet.change.isEnabled()
    QTest.keyClicks(sheet.change, "New text while saving")
    assert sheet.change.toPlainText() == "Accept option B too"
    view.corrections()  # Reopening cannot create a concurrent writer.
    assert list(_SHEETS.values()) == [sheet]
    host.drain()
    assert [row["change"] for row in read_corrections(view.journal_path)] == ["Accept option B too"]
    assert view.manifest_path.read_bytes() == before and sheet.change.toPlainText() == ""
    sheet.change.setPlainText("Second")
    sheet.reason.setPlainText("Reason")

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr("exam_forge.ui.issue.append_correction", fail)
    sheet.record()
    host.drain()
    assert sheet.change.isEnabled() and sheet.change.toPlainText() == "Second" and "disk full" in sheet.message.text()


def test_unreadable_history_is_reported_and_valid_history_still_applies(issuing):
    host, _, bank, window, _, _ = issuing
    question = window.editor.current
    replacement = add_reviewed(bank, window, "replacement")
    Manifest(json.dumps({"schema_version": 1, "bank_id": bank.id,
                         "items": [{"id": question.id}]}).encode()).write(bank.folder / "valid.manifest.json")
    (bank.folder / "corrupt.manifest.json").write_text("invalid JSON")
    exam = window.exam_tab
    exam.avoid.setValue(1)
    host.drain()
    assert "corrupt.manifest.json" in exam.history_note.text()
    exam.propose()
    host.drain()
    assert [i.id for i in exam.selection.items] == [replacement.id]


def test_exam_and_key_cannot_share_a_file(issuing, monkeypatch):
    host, _, _, window, _, _ = issuing
    view = approve(host, window)
    errors = []
    monkeypatch.setattr(window, "error", errors.append)
    shared = host.root / "shared.html"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(shared), ""))
    view.documents(0)
    host.drain()
    written = shared.read_bytes()
    view.documents(1)
    host.drain()
    assert errors == ["Exam and private key need separate files."] and shared.read_bytes() == written
