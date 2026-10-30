# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""G7 integration proofs through real widgets and the fake host's worker queue."""
from dataclasses import replace
from datetime import datetime, timezone
from threading import get_ident

import pytest
from fake_host import FakeDeck
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QMessageBox,
    QTableWidgetItem,
)
from slidemage.plugin_api import (
    CourseSessionInfo,
    PageShown,
    PresentationEnded,
    PresentationStarted,
    SlideSource,
)
from test_presentation_log import Events
from test_ui import GuiHost, source

import exam_forge
from exam_forge.folder import open_chosen_folder
from exam_forge.manifest import Manifest, read_corrections
from exam_forge.passages import Sessions
from exam_forge.ui.corrections import _SHEETS


class PhaseHost(GuiHost):
    def deck_slides(self, deck, view, language):
        self.last_deck_text = deck.text
        return super().deck_slides(deck, view, language)

    def service(self, *args):
        assert get_ident() == self.ui_thread
        return super().service(*args)

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

    def make(*, events=True):
        root = tmp_path / ("logged" if events else "unlogged")
        root.mkdir()
        host = PhaseHost(root)
        deck = FakeDeck("Course", root / "course.tex")
        deck.path.write_text("Fresh deck text")
        folder = tmp_path / ("bank" if events else "manual-bank")
        folder.mkdir()
        bank = open_chosen_folder(host, deck, folder)
        presenter = Events()
        if events:
            host.offer("presenter.events", 1, presenter)

        class Schedule:
            def sessions(self, snapshot):
                assert get_ident() == host.ui_thread
                return [CourseSessionInfo("2020-01-01", "09:00", "10:00", "Room")]
        host.offer("course.sessions", 1, Schedule())
        exam_forge.activate(host)
        # FakeHost refuses service resolution during activation (D4).
        host.finish_activation()
        controller = host.commands["exam-forge.open"].run.__self__
        window = controller.open(deck)
        windows.append(window)
        host.drain()
        ref = source(host, deck)
        host.slides[(deck.path, "during-course", "en")] = [ref]
        if events:
            now = datetime(2020, 1, 1, 9, 30, tzinfo=timezone.utc)
            presenter.emit(PresentationStarted(deck.path, "during-course", "en", now, 100))
            presenter.emit(PageShown(now, 101, 1, ref.id, ref.version, 1, 1))
            presenter.emit(PresentationEnded(now, 120))
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
        exam.easy.setValue(1)
        exam.minutes.setValue(2.5)
        exam.seed.setValue(41)
        paths = iter([str(root / "exam.html"), str(root / "key.html")])
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (next(paths), ""))
        return host, deck, bank, window, ref, presenter, warnings

    yield make
    for sheet in _SHEETS.values():
        sheet.hide()
        sheet.deleteLater()
    _SHEETS.clear()
    for window in windows:
        window.presentation_log.close()
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


def test_bank_sessions_passages_swap_and_elp_documents(phase, tmp_path, monkeypatch):
    host, deck, bank, window, _ref, presenter, warnings = phase()
    tab = window.sessions_tab
    window.tabs.setCurrentWidget(tab)
    host.drain()
    assert "Room" in tab.current().name  # Cached schedule supplied to Sessions.load.
    recording, transcript = tmp_path / "lecture.mp4", tmp_path / "lecture.srt"
    recording.write_bytes(b"recording")
    transcript.write_text("1\n00:00:08,000 --> 00:00:33,000\nTwo plus two makes four.\n")
    for kind, path in (("recording", recording), ("transcript", transcript)):
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, p=path: (str(p), ""))
        tab.attach(kind)
        host.drain()
    tab.open_recording()
    assert host.opened_files == [recording]
    tab.sync_monotonic.setValue(100)
    tab.sync_recording.setValue(10)
    tab.save_sync()
    host.drain()
    tab.propose()
    host.drain()
    assert tab.proposals[0].start == 8  # G7 passed session.sync to the proposer.
    assert not tab.proposals[0].confirmed
    tab.confirm()
    host.drain()
    assert Sessions(bank).load(deck=deck)[0].passages[0].confirmed
    # A swap is offered from the module's eligible candidates.
    current = window.current
    other = bank.create(replace(current, id="replacement", revision=1, status="draft"))
    bank.set_status(other.id, other.revision, "reviewed")
    exam = window.exam_tab
    exam.slides.setChecked(False)
    exam.talk.setChecked(True)
    exam.save_default()
    assert host.settings().get("taught_defaults")[str(deck.path.resolve())] == {"slides": False, "talk": True}
    exam.propose()
    host.drain()
    outgoing = exam.selection.items[0].id
    exam.results.setCurrentRow(0)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a: (a[3][0], True))
    exam.swap_selected()
    assert exam.selection.items[0].id != outgoing
    exam.approve()
    host.drain()
    tab.session_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert exam.approved is None and not exam.issue.freeze_button.isEnabled()
    tab.session_list.item(0).setCheckState(Qt.CheckState.Checked)
    view = propose_approve(host, window)
    view.freeze()
    host.drain()
    exam_html = (host.root / "exam.html").read_text()
    assert "Teaching assistant note" in exam_html and current.assistant_note in exam_html
    assert "Correct —" not in exam_html and any("holds answers" in w for w in warnings)
    # UI reopen reads the issued snapshot, independent of the live bank.
    path = exam.issue.manifest_path
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(path), ""))
    exam.issue.open_manifest()
    host.drain()
    assert exam.issue.manifest.data() == Manifest.read(path).data()
    # Switching banks closes the old subscription rather than accumulating listeners.
    from exam_forge.bank import Bank
    window.switch_bank(Bank(tmp_path / "other-bank", create=True))
    assert len(presenter.callbacks) == 1
    assert window.presentation_log.folder == window.bank.folder
    window.presentation_log.close()
    assert not presenter.callbacks


def test_infeasible_blueprint_is_reported_without_relaxing(phase):
    host, _, _, window, _, _, _ = phase()
    exam = window.exam_tab
    exam.counts.item(0, 1).setText("2")
    exam.easy.setValue(2)
    exam.minutes.setValue(5)
    exam.propose()
    host.drain()
    assert not exam.selection.feasible and not exam.selection.items
    assert not exam.approve_button.isEnabled()
    assert all(p in exam.report.toPlainText() for p in exam.selection.problems)
    assert "Taught-only exclusions" in exam.report.toPlainText()
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
    view.corrections()  # Reopening cannot create a concurrent writer.
    assert view.correction_sheet is sheet
    host.drain()
    rows = read_corrections(view.journal_path)
    assert len(rows) == 1 and rows[0]['change'] == "Accept option B too"
    assert view.manifest_path.read_bytes() == before


def test_no_presenter_provider_uses_confirmed_schedule_cutoff(phase, monkeypatch):
    host, _, _, window, _, _, _ = phase(events=False)
    assert "no presenter.events provider" in window.sessions_tab.logging.text()
    seen = []
    monkeypatch.setattr(QInputDialog, "getInt", lambda *a: seen.append(a[3]) or (1, True))
    issue(host, window)
    assert seen == [1]
