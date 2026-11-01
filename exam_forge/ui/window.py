# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-modal window shell; worker completions alone update phase 2 widgets."""
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from ..bank import BankError
from ..topics import TopicStore, migrate, topic_name
from .editor import ItemEditor
from .exam_tab import ExamTab
from .questions_tab import QuestionsTab


class BankWindow(QuestionsTab, ItemEditor, QWidget):
    def __init__(self, host, deck, bank, changed):
        super().__init__(None, Qt.WindowType.Window)
        self.host, self.deck, self.bank, self.changed = host, deck, bank, changed
        self.current = self.baseline = None
        self.new_item = False
        self.items = []
        self.topics = ()
        self.links = self.attachments = ()
        self.busy = False
        self.idle_callbacks = []
        self.setWindowTitle(f"Exam Forge · {deck.name}")
        self.resize(1120, 790)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addStretch()
        self.check_button = host.Button("Check slides")
        self.check_button.clicked.connect(self.check_sources)
        top.addWidget(self.check_button)
        self.export_button = host.Button("Export to Moodle")
        self.export_button.clicked.connect(self.export)
        top.addWidget(self.export_button)
        layout.addLayout(top)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.tabs.addTab(self.build_questions_tab(), "Questions")
        self.exam_tab = ExamTab(self)
        self.tabs.addTab(self.exam_tab, "Exam")
        foot = QHBoxLayout()
        self.folder_label = QLabel(str(bank.folder))
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.checked_label = QLabel()
        foot.addWidget(QLabel("🔒"))
        foot.addWidget(self.folder_label, 1)
        foot.addWidget(self.checked_label)
        layout.addLayout(foot)
        self.load_topics()
        self.reload()
        self.load_slides()
        self.check_sources()

    def switch_bank(self, bank):
        # Replace the window's bank only when there is no worker using it.
        self.bank = bank
        self.folder_label.setText(str(bank.folder))
        self.drop_editor()
        self.stack.setCurrentWidget(self.placeholder)
        self.view, self.sel_slide, self.slides = "all", None, None
        self.changed_links, self.checked_at = {}, None
        self.elp_told, self.elp_text = False, ""
        self.exam_tab.reset()
        self.load_topics()
        self.reload()
        self.load_slides()

    def load_topics(self):
        """Read topics.json; on a bank's first open, create it from legacy question topics."""
        store = TopicStore(self.bank.folder)
        try:
            if store.exists():
                self.topics = store.load()
                return
            items, errors = self.bank.load()
            # An unreadable question could hold a topic, so migrate only from a complete bank.
            if errors:
                self.topics = ()
                return
            self.topics, notes = migrate(items)
            store.save(self.topics)
            if notes:
                self.notice.setText(" ".join(notes))
        except (OSError, BankError, ValueError) as exc:
            self.topics = ()
            self.error(exc)

    def save_topics(self, topics):
        """Persist topics. A proposed exam was resolved with the old ones, so it is dropped."""
        try:
            TopicStore(self.bank.folder).save(topics)
        except (OSError, ValueError) as exc:
            self.error(exc)
            return False
        self.topics = tuple(topics)
        self.exam_tab.invalidate()
        return True

    def topic_name_of(self, item):
        return topic_name(item, self.topics)

    def error(self, error):
        self.notice.setText(str(error))
        self.host.show_message(str(error), error=True)

    def when_idle(self, callback):
        if self.busy:
            self.idle_callbacks.append(callback)
        else:
            callback()

    def set_busy(self, busy):
        self.busy = busy
        self.draft_button.setEnabled(not busy)
        self.write_button.setEnabled(not busy)
        self.editor.setEnabled(not busy and self.current is not None)
        for widget in (self.check_button, self.export_button, self.exam_tab):
            widget.setEnabled(not busy)
        if not busy:
            callbacks, self.idle_callbacks = self.idle_callbacks, []
            for callback in callbacks:
                QTimer.singleShot(0, callback)

    def work(self, work, done):
        if self.busy:
            return
        self.set_busy(True)

        def completed(result, error):
            self.set_busy(False)
            if error:
                self.error(error)
            else:
                done(result)
        self.host.run_in_background(work, completed)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "topic_list"):
            self.tint(self.changed_label)
            self.tint(self.draft_button, True)
            self.restyle_editor()
            self.render()

    def closeEvent(self, event):
        if self.may_leave():
            event.accept()
        else:
            event.ignore()
