# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""G7 integration proofs through real widgets and the fake host's worker queue."""
import json
from dataclasses import replace
from threading import get_ident

import pytest
from fake_host import FakeDeck
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QMessageBox,
    QTableWidgetItem,
)
from slidemage.plugin_api import SlideSource
from test_ui import GuiHost, source

import exam_forge
from exam_forge.folder import open_chosen_folder
from exam_forge.manifest import Manifest, read_corrections
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
        for _ in range(3):
            window.add_option()
        for row in range(5):
            window.options.cellWidget(row, 1).setPlainText(str(row + 1))
        window.options.cellWidget(0, 0).setChecked(True)
        window.topic.setText("Arithmetic")
        window.difficulty.setText("easy")
        window.use.setCurrentIndex(window.use.findData("elp"))
        from exam_forge.ui.source_picker import SourcePicker
        picker = SourcePicker(host, deck, window)
        host.drain()
        picker.slides.setCurrentRow(0)
        picker.capture()
        host.drain()
        window.links = (picker.link,)
        picker.deleteLater()
        window.save()
        window.approve_button.click()
        host.drain()
        assert window.current.status == "reviewed"
        exam = window.exam_tab
        exam.counts.setItem(0, 0, QTableWidgetItem("Arithmetic"))
        exam.counts.setItem(0, 1, QTableWidgetItem("1"))
        exam.counts.setItem(0, 2, QTableWidgetItem("0"))
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


def propose_approve(host, window):
    exam = window.exam_tab
    window.tabs.setCurrentWidget(exam)
    exam.propose()
    host.drain()
    assert exam.selection and exam.selection.feasible, exam.report.toPlainText()
    exam.approve_button.click()
    host.drain()
    assert exam.approved and exam.stack.currentWidget() is exam.issue
    assert not exam.issue.manifest  # Approval is a separate step from issuing.
    return exam.issue


def issue(host, window):
    view = propose_approve(host, window)
    view.freeze_button.click()
    host.drain()
    assert view.manifest_path.exists()
    assert "Correct" in (host.root / "key.html").read_text()
    return view


def test_bank_swap_and_elp_documents(phase, tmp_path, monkeypatch):
    host, deck, bank, window, _ref, _, warnings = phase()
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == ["Bank", "Exam"]
    # A swap is offered from the module's eligible candidates.
    current = window.current
    other = bank.create(replace(current, id="replacement", revision=1, status="draft"))
    bank.set_status(other.id, other.revision, "reviewed")
    exam = window.exam_tab
    exam.propose()
    host.drain()
    outgoing = exam.selection.items[0].id
    exam.results.setCurrentRow(0)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a: (a[3][0], True))
    exam.swap_selected()
    assert exam.selection.items[0].id != outgoing
    exam.approve()
    host.drain()
    view = propose_approve(host, window)
    seed = exam.selection.seed
    view.freeze()
    host.drain()
    frozen = view.manifest.data()
    assert frozen["blueprint"]["seed"] == seed
    assert "by" not in frozen["blueprint"]
    assert frozen["scoring"]["rules"]["easy"] == {"points": 12, "minutes": 2.5}
    assert frozen["scoring"]["rules"]["hard"] == {"points": 24, "minutes": 5}
    exam_html = (host.root / "exam.html").read_text()
    assert "Teaching assistant note" in exam_html and current.assistant_note in exam_html
    assert "Correct —" not in exam_html and any("holds answers" in w for w in warnings)
    # UI reopen reads the issued snapshot, independent of the live bank.
    path = exam.issue.manifest_path
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(path), ""))
    exam.open_issued()
    host.drain()
    assert exam.issue.manifest.data() == Manifest.read(path).data()


def test_infeasible_blueprint_is_reported_without_relaxing(phase):
    host, _, _, window, _, _, _ = phase()
    exam = window.exam_tab
    exam.counts.item(0, 1).setText("2")
    exam.propose()
    host.drain()
    assert not exam.selection.feasible and not exam.selection.items
    assert not exam.approve_button.isEnabled()
    assert all(p in exam.report.toPlainText() for p in exam.selection.problems)
    assert "Blueprint exclusions" in exam.report.toPlainText()


def test_source_changed_after_approval_stops_freeze(phase, monkeypatch):
    host, deck, bank, window, ref, _, _ = phase()
    view = propose_approve(host, window)
    host.sources[ref] = SlideSource(text="Overwritten source")
    deck.path.write_text("Reread this fresh text before freeze")
    view.freeze()
    host.drain()
    assert view.manifest is None and not list(bank.folder.glob("*.manifest.json"))
    assert ref.id in view.summary.toPlainText() and window.current.id in view.summary.toPlainText()
    assert host.last_deck_text == "Reread this fresh text before freeze"
    questions = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: questions.append(a[2]) or QMessageBox.StandardButton.Yes)
    view = propose_approve(host, window)
    assert any("Source changed" in q and ref.id in q and window.current.id in q for q in questions)
    view.freeze()
    host.drain()
    assert not list(bank.folder.glob("*.manifest.json"))


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
    replacement = bank.create(replace(question, id="replacement", revision=1, status="draft"))
    bank.set_status(replacement.id, 1, "reviewed")
    Manifest(json.dumps({"schema_version": 1, "bank_id": bank.id,
                         "items": [{"id": question.id}]}).encode()).write(bank.folder / "valid.manifest.json")
    path = bank.folder / "corrupt.manifest.json"
    if corrupt == "invalid items":
        corrupt = {"schema_version": 1, "bank_id": bank.id, "items": None}
    path.write_text(json.dumps(corrupt) if isinstance(corrupt, dict) else corrupt)
    exam = window.exam_tab
    exam.avoid.setValue(1)
    exam.propose()
    host.drain()
    assert exam.selection and exam.selection.feasible
    assert [i.id for i in exam.selection.items] == [replacement.id]
    assert any(row.item.id == question.id for row in exam.selection.excluded)
    assert path.name in exam.report.toPlainText() and "Could not read exam history" in exam.report.toPlainText()


def test_zero_avoid_last_does_not_read_manifest_history(phase, monkeypatch):
    host, _, bank, window, _, _, _ = phase()
    (bank.folder / "corrupt.manifest.json").write_text("invalid JSON")

    def unexpected_read(*args):
        raise AssertionError("History must not be read when avoid_last is zero")

    monkeypatch.setattr(Manifest, "read", unexpected_read)
    exam = window.exam_tab
    assert exam.avoid.value() == 0
    exam.propose()
    host.drain()
    assert exam.selection and exam.selection.feasible
    assert "corrupt.manifest.json" not in exam.report.toPlainText()
