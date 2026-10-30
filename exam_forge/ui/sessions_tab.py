# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Select presentation logs for taught evidence."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..presentation_log import Sessions
from .work import schedule_for


class SessionsTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window, self.host = window, window.host
        self.loaded = False
        self.sessions = []
        layout = QVBoxLayout(self)
        self.logging = QLabel()
        self.logging.setWordWrap(True)
        layout.addWidget(self.logging)
        layout.addWidget(QLabel("Tick sessions to use as taught evidence. Refresh after a presentation ends."))
        button = self.host.Button("Refresh")
        button.clicked.connect(self.load)
        layout.addWidget(button)
        self.session_list = QListWidget()
        layout.addWidget(self.session_list, 1)
    @property
    def store(self):
        return Sessions(self.window.bank)

    def reset(self):
        self.loaded = False
        self.sessions = []
        self.session_list.clear()

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
