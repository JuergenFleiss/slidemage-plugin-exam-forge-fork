# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Session attachments and transcripts."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..sessions import Session, Sessions
from .work import schedule_for


class SessionsTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window, self.host = window, window.host
        self.loaded = False
        self.sessions = []
        self.transcript = None
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
        self.session_list = QListWidget()
        layout.addWidget(self.session_list, 1)
        self.details = QLabel("Choose a session.")
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumHeight(140)
        layout.addWidget(self.text)
        self.session_list.currentRowChanged.connect(self.select)

    @property
    def store(self):
        return Sessions(self.window.bank)

    def reset(self):
        self.loaded = False
        self.sessions = []
        self.session_list.clear()
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
        self.text.clear()
        if session is None:
            return
        self.details.setText(f"{session.name}\nRecording: {session.recording or 'none'}\nTranscript: {session.transcript or 'none'}")
        if session.transcript:
            store = self.store

            def done(transcript):
                self.transcript = transcript
                self.text.setPlainText("\n".join(s.text for s in transcript.segments))
                if transcript.errors:
                    self.window.error("\n".join(map(str, transcript.errors)))
            self.window.work(lambda: store.read_transcript(session), done)
