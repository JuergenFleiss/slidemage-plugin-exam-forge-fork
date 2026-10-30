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
from .bank_tab import BankTab
from .editor import ItemEditor, accent
from .exam_tab import ExamTab


class BankWindow(BankTab, ItemEditor, QWidget):
    def __init__(self, host, deck, bank, changed):
        super().__init__(None, Qt.WindowType.Window)
        self.host, self.deck, self.bank, self.changed = host, deck, bank, changed
        self.current = self.baseline = None
        self.new_item = False
        self.items = []
        self.links = self.attachments = ()
        self.busy = False
        self.idle_callbacks = []
        self.setWindowTitle(f"Exam Forge · {deck.name}")
        self.resize(1120, 790)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.folder_label = QLabel(str(bank.folder))
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(self.folder_label, 1)
        self.check_button = host.Button("Check now")
        self.check_button.clicked.connect(self.check_sources)
        top.addWidget(self.check_button)
        self.export_button = host.Button("Export Moodle XML…")
        self.export_button.clicked.connect(self.export)
        top.addWidget(self.export_button)
        layout.addLayout(top)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.tabs.addTab(self.build_bank_tab(), "Bank")
        self.exam_tab = ExamTab(self)
        self.tabs.addTab(self.exam_tab, "Exam")
        self.reload()
        self.check_sources()

    def switch_bank(self, bank):
        # Replace the window's bank only when there is no worker using it.
        self.bank = bank
        self.folder_label.setText(str(bank.folder))
        self.current = self.baseline = None
        self.new_item = False
        self.editor.setEnabled(False)
        self.exam_tab.reset()
        self.reload()

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
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "source_banner"):
            for widget in (self.source_tag, self.source_filter, self.source_banner):
                accent(widget)
            for widget in (self.ai_tag, self.ai_filter, self.draft_tag):
                accent(widget, True)
            self.filter_items()

    def closeEvent(self, event):
        if self.may_leave():
            event.accept()
        else:
            event.ignore()
