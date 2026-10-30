# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Session attachments and author-confirmed passage proposals."""
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..passages import (
    Session,
    Sessions,
    SyncPoint,
    propose_passages,
    propose_text_matches,
)
from ..presentation_log import read_log
from .work import fresh_deck, schedule_for


class SessionsTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window, self.host = window, window.host
        self.loaded = False
        self.sessions = []
        self.transcript = None
        self.proposals = ()
        layout = QVBoxLayout(self)
        self.logging = QLabel()
        self.logging.setWordWrap(True)
        layout.addWidget(self.logging)
        layout.addWidget(QLabel("Tick sessions to use as taught evidence. Refresh after a presentation ends."))
        row = QHBoxLayout()
        for label, action in (("Refresh", self.load), ("Add session…", self.add),
                              ("Attach recording…", lambda: self.attach("recording")),
                              ("Attach transcript…", lambda: self.attach("transcript")),
                              ("Open recording", self.open_recording)):
            button = self.host.Button(label)
            button.clicked.connect(action)
            row.addWidget(button)
        layout.addLayout(row)
        split = QSplitter()
        self.session_list, self.passage_list = QListWidget(), QListWidget()
        split.addWidget(self.session_list)
        split.addWidget(self.passage_list)
        layout.addWidget(split, 1)
        self.details = QLabel("Choose a session.")
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumHeight(140)
        layout.addWidget(self.text)
        row = QHBoxLayout()
        self.padding = QDoubleSpinBox()
        self.padding.setRange(0, 300)
        self.padding.setValue(3)
        row.addWidget(self.host.Field("Padding (seconds)", self.padding))
        self.sync_monotonic, self.sync_recording = QDoubleSpinBox(), QDoubleSpinBox()
        self.sync_monotonic.setRange(-1e12, 1e12)
        self.sync_recording.setRange(0, 1e12)
        row.addWidget(self.host.Field("Known presentation monotonic time", self.sync_monotonic))
        row.addWidget(self.host.Field("Corresponding recording seconds", self.sync_recording))
        sync = self.host.Button("Save sync point")
        sync.clicked.connect(self.save_sync)
        row.addWidget(sync)
        layout.addLayout(row)
        row = QHBoxLayout()
        propose = self.host.Button("Propose passages")
        propose.clicked.connect(self.propose)
        row.addWidget(propose)
        self.start, self.end = QDoubleSpinBox(), QDoubleSpinBox()
        for label, field in (("Start seconds", self.start), ("End seconds", self.end)):
            field.setRange(0, 1e12)
            row.addWidget(self.host.Field(label, field))
        confirm = self.host.Button("Confirm passage", "primary")
        confirm.clicked.connect(self.confirm)
        row.addWidget(confirm)
        layout.addLayout(row)
        self.session_list.currentRowChanged.connect(self.select)
        self.passage_list.currentRowChanged.connect(self.select_passage)

    @property
    def store(self):
        return Sessions(self.window.bank)

    def reset(self):
        self.loaded = False
        self.sessions = []
        self.session_list.clear()
        self.proposals = ()
        self.passage_list.clear()
        self.text.clear()

    def selected_ids(self):
        if not self.loaded:
            return None  # Until the author chooses, all discovered sessions are used.
        return {s.id for i, s in enumerate(self.sessions)
                if self.session_list.item(i).checkState() == Qt.CheckState.Checked}

    def current(self):
        row = self.session_list.currentRow()
        return self.sessions[row] if 0 <= row < len(self.sessions) else None

    def load(self):
        self.window.exam_tab.invalidate()
        schedule = schedule_for(self.host, self.window.deck)
        store, deck = self.store, self.window.deck
        chosen = self.selected_ids()
        known = {s.id for s in self.sessions}
        current = self.current()

        def done(sessions):
            self.sessions = sessions
            self.session_list.blockSignals(True)
            self.session_list.clear()
            for session in sessions:
                item = QListWidgetItem(session.name)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if chosen is None or session.id not in known or session.id in chosen
                                   else Qt.CheckState.Unchecked)
                self.session_list.addItem(item)
            self.loaded = True
            self.session_list.blockSignals(False)
            row = next((i for i, s in enumerate(sessions) if current and s.id == current.id), 0)
            self.session_list.setCurrentRow(row if sessions else -1)
        self.window.work(lambda: store.load(deck=deck, schedule=schedule), done)

    def add(self):
        name, ok = QInputDialog.getText(self, "Add session", "Session name")
        if ok and name.strip():
            store = self.store
            self.window.work(lambda: store.save(Session(name.strip())), lambda _: self.load())

    def attach(self, kind):
        session = self.current()
        if session is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, f"Attach {kind}", "",
            "Transcripts (*.srt *.vtt *.txt)" if kind == "transcript" else "Recordings (*)")
        if path:
            store = self.store
            self.window.work(lambda: store.attach(session, kind, path), lambda _: self.load())

    def open_recording(self):
        session = self.current()
        if session:
            try:
                self.store.open_recording(self.host, session)
            except (OSError, ValueError, RuntimeError) as exc:
                self.window.error(exc)

    def select(self, row):
        session = self.current()
        self.transcript = None
        self.proposals = session.passages if session else ()
        self.render_passages()
        self.text.clear()
        if session is None:
            return
        self.details.setText(f"{session.name}\nRecording: {session.recording or 'none'}\nTranscript: {session.transcript or 'none'}")
        self.sync_monotonic.setValue(session.sync.monotonic if session.sync else 0)
        self.sync_recording.setValue(session.sync.recording if session.sync else 0)
        if session.transcript:
            store = self.store

            def done(transcript):
                self.transcript = transcript
                self.text.setPlainText("\n".join(s.text for s in transcript.segments))
                if transcript.errors:
                    self.window.error("\n".join(map(str, transcript.errors)))
            self.window.work(lambda: store.read_transcript(session), done)

    def save_sync(self):
        session = self.current()
        if session is None:
            return
        if QMessageBox.question(self, "Sync point", "Save this sync point? Existing passages will need new proposals and confirmation.") != QMessageBox.StandardButton.Yes:
            return
        updated = replace(session, sync=SyncPoint(self.sync_monotonic.value(), self.sync_recording.value()), passages=())
        store = self.store
        self.window.work(lambda: store.save(updated), lambda _: self.load())

    def propose(self):
        session = self.current()
        if session is None:
            return
        if self.proposals and QMessageBox.question(self, "Replace proposals", "Replace the displayed passages? Confirm the new proposals before using them.") != QMessageBox.StandardButton.Yes:
            return
        store, deck, padding = self.store, self.window.deck, self.padding.value()
        language = self.host.deck_language(deck)

        def work():
            transcript = store.read_transcript(session) if session.transcript else None
            if session.presentation:
                proposals = propose_passages(read_log(store.path(session.presentation)), transcript,
                                             sync=session.sync, padding=padding)
            elif transcript:
                refs = self.host.deck_slides(fresh_deck(deck), "during-course", language)
                proposals = propose_text_matches(transcript, {r.id: self.host.slide_source(r).text for r in refs})
            else:
                raise ValueError("Attach a transcript or use a logged presentation before proposing passages.")
            return transcript, proposals

        def done(result):
            self.transcript, self.proposals = result
            self.render_passages()
            if self.transcript and self.transcript.errors:
                self.window.error("\n".join(map(str, self.transcript.errors)))
        self.window.work(work, done)

    def render_passages(self):
        self.passage_list.clear()
        self.passage_list.addItems([f"{p.slide_id} · {p.start}–{p.end} s · " +
            ("Confirmed" if p.confirmed else "Proposal") + (" · short visit: consider excluding" if p.suggest_exclusion else "")
            for p in self.proposals])
        if self.proposals:
            self.passage_list.setCurrentRow(0)

    def select_passage(self, row):
        if 0 <= row < len(self.proposals):
            passage = self.proposals[row]
            self.start.setValue(passage.start or 0)
            self.end.setValue(passage.end or 0)
            if self.transcript:
                self.text.setPlainText("\n".join(self.transcript.segments[i].text for i in passage.segments))

    def confirm(self):
        session, row = self.current(), self.passage_list.currentRow()
        if session is None or not 0 <= row < len(self.proposals):
            return
        if QMessageBox.question(self, "Confirm passage", f"Confirm {self.proposals[row].slide_id} at {self.start.value()}–{self.end.value()} seconds as taught talk?") != QMessageBox.StandardButton.Yes:
            return
        try:
            passage = self.proposals[row].confirm(self.transcript, start=self.start.value(), end=self.end.value())
            proposals = tuple(passage if i == row else p for i, p in enumerate(self.proposals))
            updated = replace(session, passages=proposals)
        except ValueError as exc:
            self.window.error(exc)
            return
        store = self.store
        self.window.work(lambda: store.save(updated), lambda _: self.load())
