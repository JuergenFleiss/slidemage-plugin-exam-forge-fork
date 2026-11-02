# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""G7 integration proofs through real widgets and the fake host's worker queue."""
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import get_ident

import pytest
from fake_host import FakeDeck
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QMessageBox,
)
from slidemage.plugin_api import SlideSource
from test_ui import GuiHost, source

import exam_forge
from exam_forge.assembly import Blueprint
from exam_forge.folder import open_chosen_folder
from exam_forge.manifest import Manifest, read_corrections
from exam_forge.sources import link_slide
from exam_forge.topics import add_topic, assign_slide
from exam_forge.ui.editor import accent_color
from exam_forge.ui.exam_tab import red_color
from exam_forge.ui.corrections import _SHEETS


class PhaseHost(GuiHost):
    def deck_slides(self, deck, view, language):
        self.last_deck_text = deck.text
        return super().deck_slides(deck, view, language)

    def ai_chat(self):
        assert get_ident() == self.ui_thread
        return super().ai_chat()

    def slide_source(self, ref):
        assert get_ident() != self.ui_thread
        return super().slide_source(ref)


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def phase(app, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]) or QMessageBox.StandardButton.Yes)
    windows = []

    def make():
        root = tmp_path / "course"
        root.mkdir()
        host = PhaseHost(root)
        deck = FakeDeck("Course", root / "course.tex")
        deck.path.write_text("Fresh deck text")
        folder = tmp_path / "bank"
        folder.mkdir()
        bank = open_chosen_folder(host, deck, folder)
        exam_forge.activate(host)
        host.finish_activation()
        controller = host.commands["exam-forge.open"].run.__self__
        window = controller.open(deck)
        windows.append(window)
        host.drain()
        ref = source(host, deck)
        host.slides[(deck.path, "during-course", "en")] = [ref]
        # Authoring, linking and review all use the actual bank editor.
        window.new_question()
        window.stem.setPlainText("Two plus two")
        window.assistant_note.setPlainText("Shared stem with next item; keep option order; exclude together.")
        window.use.setCurrentData("elp")
        for number, row in enumerate(window.rows, 1):
            row.text.setPlainText(str(number))
        window.rows[0].tick.click()
        window.difficulty.setCurrentData("easy")
        with ThreadPoolExecutor() as executor:
            window.links = (executor.submit(link_slide, host, ref, "instructor").result(),)
        window.save()
        window.approve_button.click()
        host.drain()
        assert window.current.status == "reviewed"
        topics = add_topic((), "Arithmetic")
        assert window.save_topics(assign_slide(topics, topics[0].id, "s1"))
        window.reload()
        window.exam_tab.refresh()
        counts(window.exam_tab, 1, 0)
        paths = iter([str(root / "exam.html"), str(root / "key.html")])
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (next(paths), ""))
        return host, deck, bank, window, ref, None, warnings

    yield make
    for sheet in _SHEETS.values():
        sheet.hide()
        sheet.deleteLater()
    _SHEETS.clear()
    for window in windows:
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
    assert exam.stack.currentIndex() == 1
    return exam


def propose_approve(host, window):
    exam = propose(host, window)
    exam.approve_button.click()
    host.drain()
    assert exam.stack.currentWidget() is exam.issue and exam.issue.manifest
    return exam.issue


def issue(host, window):
    view = propose_approve(host, window)
    assert view.manifest_path.exists()
    view.documents(0)
    host.drain()
    view.documents(1)
    host.drain()
    assert "Correct" in (host.root / "key.html").read_text()
    return view


def add_reviewed(bank, window, item_id, **fields):
    item = bank.create(replace(window.current, id=item_id, revision=1, status="draft", **fields))
    bank.set_status(item.id, item.revision, "reviewed")
    window.reload()
    window.exam_tab.refresh()
    return item


def test_bank_swap_and_elp_documents(phase, tmp_path, monkeypatch):
    host, deck, bank, window, _ref, _, warnings = phase()
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == ["Questions", "Exam"]
    current = window.current
    add_reviewed(bank, window, "replacement")
    exam = propose(host, window)
    outgoing = exam.selection.items[0]
    assert exam.rows[outgoing.id]["swap"].isEnabled()
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a: (a[3][0], True))
    exam.swap_item(outgoing.id)
    incoming = exam.selection.items[0]
    assert incoming.id != outgoing.id and incoming.difficulty == outgoing.difficulty
    assert exam.selection.topic_of(incoming) == exam.selection.topic_of(outgoing) == "Arithmetic"
    assert incoming.id in exam.rows and outgoing.id not in exam.rows
    seed = exam.selection.seed
    exam.approve_button.click()
    host.drain()
    view = exam.issue
    frozen = view.manifest.data()
    assert frozen["blueprint"]["seed"] == seed
    assert "by" not in frozen["blueprint"]
    assert frozen["items"][0]["id"] == incoming.id
    assert frozen["scoring"]["rules"]["easy"] == {"points": 12, "minutes": 2.5}
    assert frozen["scoring"]["rules"]["hard"] == {"points": 24, "minutes": 5}
    view.documents(0)
    host.drain()
    assert not warnings  # Only the private key carries the warning.
    view.documents(1)
    host.drain()
    exam_html = (host.root / "exam.html").read_text()
    assert "Teaching assistant note" in exam_html and current.assistant_note in exam_html
    assert "Correct —" not in exam_html and any("holds answers" in w for w in warnings)
    # UI reopen reads the issued snapshot, independent of the live bank.
    path = view.manifest_path
    exam.reset()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(path), ""))
    exam.open_issued()
    host.drain()
    assert exam.stack.currentWidget() is view
    assert view.manifest.data() == Manifest.read(path).data()


def test_frozen_manifest_stores_resolved_topic_names(phase):
    host, _, _, window, _, _, _ = phase()
    view = propose_approve(host, window)
    assert window.current.topic == ""
    assert [i["topic"] for i in view.manifest.data()["items"]] == ["Arithmetic"]
    assert view.file_name.text() == view.manifest_path.name


def test_summary_and_only_n_available_are_live(phase):
    host, _, bank, window, _, _, _ = phase()
    exam = window.exam_tab
    cell = exam.cells["Arithmetic"]
    assert cell["avail"].text() == "1 easy · 0 hard reviewed"
    assert [exam.totals[k].text() for k in ("questions", "points", "minutes")] == ["1", "12", "2.5"]
    assert exam.propose_button.isEnabled() and cell["easy_over"].text() == ""
    counts(exam, 3, 0)
    assert cell["easy_over"].text() == "Only 1 available" and not exam.propose_button.isEnabled()
    assert "Arithmetic (easy)" in exam.reason.text()
    counts(exam, 0, 0)
    assert exam.reason.text() == "Choose at least one question." and not exam.propose_button.isEnabled()
    add_reviewed(bank, window, "hard-one", difficulty="hard")
    cell = exam.cells["Arithmetic"]
    counts(exam, 1, 1)
    assert cell["avail"].text() == "1 easy · 1 hard reviewed" and cell["hard_over"].text() == ""
    assert [exam.totals[k].text() for k in ("questions", "points", "minutes")] == ["2", "36", "7.5"]
    assert exam.propose_button.isEnabled()
    exam.propose()
    host.drain()
    assert exam.pick_summary.text() == "2 questions · 36 points · 7.5 min"
    assert {r["chip"].text() for r in exam.rows.values()} == {"Easy · 12 pts", "Hard · 24 pts"}
    assert all(r["changed"] is None for r in exam.rows.values())
    assert set(exam.labels.values()) == {"Slide 1"}
    assert str(exam.selection.seed) in exam.seed_label.text()


def test_zero_counts_are_left_out_of_the_blueprint(phase):
    _, _, _, window, _, _, _ = phase()
    exam = window.exam_tab
    window.save_topics(add_topic(window.topics, "Empty"))
    exam.refresh()
    assert "Empty" in exam.cells and list(exam.blueprint().counts) == ["Arithmetic"]


def test_no_topics_shows_empty_state(phase):
    _, _, _, window, _, _, _ = phase()
    exam = window.exam_tab
    window.save_topics(())
    assert not exam.cells and not exam.propose_button.isEnabled()
    assert "Questions tab" in exam.rows_box.itemAt(0).widget().text()


def test_questions_without_topic_are_counted_not_offered(phase):
    _, _, bank, window, _, _, _ = phase()
    exam = window.exam_tab
    assert exam.untopiced.isHidden()
    add_reviewed(bank, window, "orphan", sources=())
    assert not exam.untopiced.isHidden()
    assert exam.untopiced.text() == "1 reviewed exam question has no topic and can't be used."
    assert exam.cells["Arithmetic"]["avail"].text() == "1 easy · 0 hard reviewed"
    add_reviewed(bank, window, "orphan2", sources=())
    assert exam.untopiced.text().startswith("2 reviewed exam questions have no topic")


def test_waiting_elp_drafts_are_shown(phase):
    _, _, bank, window, _, _, _ = phase()
    exam = window.exam_tab
    bank.create(replace(window.current, id="draft", revision=1, status="draft"))
    window.reload()
    exam.refresh()
    assert exam.cells["Arithmetic"]["draft"].text() == "1 exam draft waiting for review"


def test_skip_last_changes_availability_live(phase):
    host, _, bank, window, _, _, _ = phase()
    exam = window.exam_tab
    Manifest(json.dumps({"schema_version": 1, "bank_id": bank.id,
                         "items": [{"id": window.current.id}]}).encode()).write(bank.folder / "old.manifest.json")
    cell = exam.cells["Arithmetic"]
    assert cell["avail"].text().startswith("1 easy")
    exam.avoid.setValue(1)
    host.drain()
    assert cell["avail"].text() == "0 easy · 0 hard reviewed" and cell["easy_over"].text() == "Only 0 available"
    assert not exam.propose_button.isEnabled()
    exam.avoid.setValue(0)
    assert cell["avail"].text().startswith("1 easy") and cell["easy_over"].text() == ""


def test_infeasible_blueprint_is_reported_without_relaxing(phase):
    host, _, _, window, _, _, _ = phase()
    exam = window.exam_tab
    exam.propose(Blueprint({"Arithmetic": {"easy": 2}}))  # Bypasses the disabled button.
    host.drain()
    assert exam.selection is None and exam.stack.currentIndex() == 0
    assert "needs 2 easy questions; only 1 is eligible" in exam.reason.text()


def test_change_counts_keeps_counts_and_new_pick_changes_seed(phase):
    host, _, bank, window, _, _, _ = phase()
    exam = propose(host, window)
    seed, blueprint = exam.selection.seed, exam.selection.blueprint
    exam.again_button.click()
    host.drain()
    assert exam.stack.currentIndex() == 1 and exam.selection.seed != seed
    assert exam.selection.blueprint == blueprint
    exam.back_button.click()
    assert exam.stack.currentIndex() == 0
    assert exam.cells["Arithmetic"]["easy"].value() == 1 and exam.counts["Arithmetic"] == {"easy": 1, "hard": 0}


def test_topic_save_discards_the_proposal(phase):
    host, _, _, window, _, _, _ = phase()
    exam = propose(host, window)
    assert window.save_topics(add_topic(window.topics, "Geometry"))
    assert exam.selection is None and exam.stack.currentIndex() == 0
    assert "Geometry" in exam.cells and exam.cells["Arithmetic"]["easy"].value() == 1


def test_swap_is_disabled_without_alternatives(phase):
    host, _, bank, window, _, _, _ = phase()
    exam = propose(host, window)
    assert not exam.rows[window.current.id]["swap"].isEnabled()
    assert not exam.selection.alternatives(window.current.id)


def test_changed_source_blocks_approval_without_a_way_around(phase, monkeypatch):
    host, _, bank, window, ref, _, _ = phase()
    questions = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: questions.append(a) or QMessageBox.StandardButton.Yes)
    bank.set_source_changed(window.current.id, True, expected_sources=window.current.sources)
    window.reload()
    exam = propose(host, window)
    row = exam.rows[window.current.id]
    assert exam.blocked.text() == ("1 picked question is linked to a changed slide. "
                                   "Review or swap before freezing.")
    assert row["changed"] is not None and not exam.approve_button.isEnabled()
    opened = []
    window.open_question = opened.append
    row["review"].click()
    assert opened == [window.current.id]
    exam.approve()  # Even called directly, nothing is frozen.
    host.drain()
    assert exam.stack.currentIndex() == 1 and not list(bank.folder.glob("*.manifest.json")) and not questions


def test_fresh_check_finding_a_change_blocks_and_updates_rows(phase, monkeypatch):
    host, deck, bank, window, ref, _, _ = phase()
    questions = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: questions.append(a) or QMessageBox.StandardButton.Yes)
    exam = propose(host, window)
    assert exam.approve_button.isEnabled() and exam.blocked.isHidden()
    host.sources[ref] = SlideSource(text="Overwritten source")
    deck.path.write_text("Reread this fresh text before approval")
    exam.approve_button.click()
    host.drain()
    assert host.last_deck_text == "Reread this fresh text before approval"
    assert exam.stack.currentIndex() == 1 and not list(bank.folder.glob("*.manifest.json"))
    assert exam.blocked.text().startswith("1 picked question is linked to a changed slide.")
    assert exam.rows[window.current.id]["changed"] is not None and not exam.approve_button.isEnabled()
    assert not questions and not window.busy


def test_source_changed_during_freeze_is_blocked_too(phase):
    host, deck, bank, window, ref, _, _ = phase()
    exam = propose(host, window)
    host.sources[ref] = SlideSource(text="Overwritten source")
    exam.issue.freeze(exam.selection)
    host.drain()
    assert exam.issue.manifest is None and not list(bank.folder.glob("*.manifest.json"))
    assert exam.stack.currentIndex() == 1 and exam.blocked.text().startswith("1 picked question is linked")
    assert not exam.approve_button.isEnabled() and not window.busy


def test_conflict_at_approval_stays_on_review(phase, monkeypatch):
    host, _, bank, window, _, _, _ = phase()
    exam = propose(host, window)
    bank.set_status(window.current.id, window.current.revision, "retired")
    errors = []
    monkeypatch.setattr(window, "error", errors.append)
    exam.approve_button.click()
    host.drain()
    assert errors and exam.stack.currentIndex() == 1 and exam.issue.manifest is None


def test_returning_to_the_tab_refreshes_changed_flags(phase):
    host, _, bank, window, _, _, _ = phase()
    exam = propose(host, window)
    bank.set_source_changed(window.current.id, True, expected_sources=window.current.sources)
    exam.sync_flags()
    host.drain()
    assert exam.rows[window.current.id]["changed"] is not None and exam.selection.items[0].source_changed
    assert not exam.approve_button.isEnabled()


def test_edit_after_issue_leaves_manifest_and_documents_alone(phase):
    host, _, bank, window, _, _, _ = phase()
    view = issue(host, window)
    before = view.manifest_path.read_bytes()
    exam_before = (host.root / "exam.html").read_bytes()
    window.stem.setPlainText("Changed after issue")
    window.assistant_note.setPlainText("New note")
    assert window.save()
    assert bank.get(window.current.id).status == "draft"
    assert view.manifest_path.read_bytes() == before
    assert (host.root / "exam.html").read_bytes() == exam_before
    assert Manifest.read(view.manifest_path).data()['items'][0]['assistant_note'] != "New note"


def test_start_another_exam_returns_to_counts(phase):
    host, _, _, window, _, _, _ = phase()
    view = propose_approve(host, window)
    view.another()
    assert window.exam_tab.stack.currentIndex() == 0 and view.manifest is None
    assert window.exam_tab.cells["Arithmetic"]["easy"].value() == 1


@pytest.mark.parametrize("v1", [True, False])
def test_opening_a_manifest_lands_on_the_frozen_step(phase, monkeypatch, v1):
    host, _, bank, window, _, _, _ = phase()
    path = bank.folder / "20260102T030405000000Z-abcd1234.manifest.json"
    if v1:
        options = [{"id": str(i), "text": f"O{i}", "correct": i == 0, "rationale": ""} for i in range(5)]
        Manifest(json.dumps({"schema_version": 1, "bank_id": bank.id, "media_base64": {},
                             "blueprint": {"counts": {"x": 1}, "difficulty_mix": {"easy": 1}, "seed": 5},
                             "items": [{"id": "q", "revision": 1, "stem": "Stem", "difficulty": "easy",
                                        "topic": "x", "media": [], "assistant_note": "", "options": options}],
                             "scoring": {"rules": {"easy": {"points": 12, "minutes": 2.5},
                                                   "hard": {"points": 24, "minutes": 5}}, "floor": 0,
                                         "tables": {}}}).encode()).write(path)
    else:
        path = propose_approve(host, window).manifest_path
    exam = window.exam_tab
    exam.reset()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(path), ""))
    exam.open_issued()
    host.drain()
    assert exam.stack.currentWidget() is exam.issue
    assert exam.issue.manifest_path == path and exam.issue.corrections_button.isEnabled()
    assert exam.issue.summary.text().startswith("1 question · 12 points · 2.5 min")
    assert exam.issue.file_name.text() == path.name
    if v1:
        assert "issued 2026-01-0" in exam.issue.summary.text()


def test_totals_without_rules_show_only_the_count():
    from exam_forge.ui.issue import manifest_totals
    assert manifest_totals({"items": [{"difficulty": "easy"}, {"difficulty": "hard"}]}) == "2 questions"
    rules = {"scoring": {"rules": {"easy": {"points": 12, "minutes": 2.5}}}}
    assert manifest_totals({"items": [{}], **rules}) == "1 question"


def test_dark_palette_uses_the_dark_accent_and_red_values(phase):
    host, _, bank, window, _, _, _ = phase()
    exam = window.exam_tab
    bank.set_source_changed(window.current.id, True, expected_sources=window.current.sources)
    bank.create(replace(window.current, id="draft", revision=1, status="draft"))
    window.reload()
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    exam.setPalette(palette)
    assert red_color(exam) == "#fca5a5" and accent_color(exam) == "#c4b5fd" and accent_color(exam, True) == "#fbbf24"
    exam.refresh()
    counts(exam, 2, 0)
    assert "#fca5a5" in exam.cells["Arithmetic"]["easy_over"].styleSheet()
    assert "#fbbf24" in exam.cells["Arithmetic"]["draft"].styleSheet()
    counts(exam, 1, 0)
    exam.propose()
    host.drain()
    assert "#c4b5fd" in exam.blocked.styleSheet()
    assert "#c4b5fd" in exam.rows[window.current.id]["changed"].styleSheet()
    assert "#fbbf24" in exam.issue.key_title.styleSheet()


def test_correction_is_recorded_in_separate_sheet(phase):
    host, _, _, window, _, _, _ = phase()
    view = issue(host, window)
    before = view.manifest_path.read_bytes()
    view.corrections()
    host.drain()
    sheet = view.correction_sheet
    sheet.change.setPlainText("Accept option B too")
    sheet.reason.setPlainText("Ambiguous wording")
    sheet.append.click()
    assert sheet.busy and not sheet.append.isEnabled()
    assert all(not field.isEnabled() for field in (sheet.item, sheet.change, sheet.reason))
    QTest.keyClicks(sheet.change, "New text while saving")
    QTest.keyClicks(sheet.reason, "New reason while saving")
    assert sheet.change.toPlainText() == "Accept option B too"
    assert sheet.reason.toPlainText() == "Ambiguous wording"
    view.corrections()  # Reopening cannot create a concurrent writer.
    assert view.correction_sheet is sheet
    host.drain()
    rows = read_corrections(view.journal_path)
    assert len(rows) == 1 and rows[0]['change'] == "Accept option B too"
    assert all(field.isEnabled() for field in (sheet.item, sheet.change, sheet.reason, sheet.append))
    assert sheet.change.toPlainText() == sheet.reason.toPlainText() == ""
    assert view.manifest_path.read_bytes() == before


def test_failed_correction_save_reenables_fields_and_keeps_input(phase, monkeypatch):
    host, _, _, window, _, _, _ = phase()
    view = issue(host, window)
    view.corrections()
    host.drain()
    sheet = view.correction_sheet
    sheet.change.setPlainText("Accept option B too")
    sheet.reason.setPlainText("Ambiguous wording")

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr("exam_forge.ui.corrections.append_correction", fail)
    sheet.record()
    assert all(not field.isEnabled() for field in (sheet.item, sheet.change, sheet.reason, sheet.append))
    host.drain()
    assert all(field.isEnabled() for field in (sheet.item, sheet.change, sheet.reason, sheet.append))
    assert sheet.change.toPlainText() == "Accept option B too"
    assert sheet.reason.toPlainText() == "Ambiguous wording"
    assert "disk full" in sheet.message.text()


@pytest.mark.parametrize("corrupt", ["invalid JSON", {"schema_version": 1}, "invalid items"])
def test_corrupt_manifest_is_reported_and_valid_history_still_applies(phase, corrupt):
    host, _, bank, window, _, _, _ = phase()
    question = window.current
    replacement = add_reviewed(bank, window, "replacement")
    Manifest(json.dumps({"schema_version": 1, "bank_id": bank.id,
                         "items": [{"id": question.id}]}).encode()).write(bank.folder / "valid.manifest.json")
    path = bank.folder / "corrupt.manifest.json"
    if corrupt == "invalid items":
        corrupt = {"schema_version": 1, "bank_id": bank.id, "items": None}
    path.write_text(json.dumps(corrupt) if isinstance(corrupt, dict) else corrupt)
    exam = window.exam_tab
    exam.avoid.setValue(1)
    host.drain()
    assert path.name in exam.history_note.text() and "Could not read exam history" in exam.history_note.text()
    assert exam.cells["Arithmetic"]["avail"].text() == "1 easy · 0 hard reviewed"
    exam.propose()
    host.drain()
    assert exam.selection and exam.selection.feasible
    assert [i.id for i in exam.selection.items] == [replacement.id]
    assert any(row.item.id == question.id for row in exam.selection.excluded)
    assert path.name in window.notice.text()


def test_zero_avoid_last_does_not_read_manifest_history(phase, monkeypatch):
    host, _, bank, window, _, _, _ = phase()
    (bank.folder / "corrupt.manifest.json").write_text("invalid JSON")

    def unexpected_read(*args):
        raise AssertionError("History must not be read when avoid_last is zero")

    monkeypatch.setattr(Manifest, "read", unexpected_read)
    exam = window.exam_tab
    assert exam.avoid.value() == 0
    exam.refresh()
    exam.propose()
    host.drain()
    assert exam.selection and exam.selection.feasible
    assert not exam.history_note.text()


@pytest.mark.parametrize("first", [0, 1])
def test_exam_and_key_cannot_share_a_file(phase, monkeypatch, first):
    host, _, _, window, _, _, _ = phase()
    view = propose_approve(host, window)
    errors = []
    monkeypatch.setattr(window, "error", errors.append)
    shared, other = host.root / "shared.html", host.root / "other.html"
    pick = iter([shared, shared, other])
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(next(pick)), ""))
    view.documents(first)
    host.drain()
    written = shared.read_bytes()
    view.documents(1 - first)
    host.drain()
    assert errors == ["Exam and private key need separate files."] and shared.read_bytes() == written
    view.documents(1 - first)
    host.drain()
    assert other.exists() and len(errors) == 1
