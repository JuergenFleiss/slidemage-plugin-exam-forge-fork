# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-modal window shell; worker completions alone update phase 2 widgets."""
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from slidemage.plugin_api import ServiceError

from ..presentation_log import PresentationLog
from .bank_tab import BankTab
from .editor import ItemEditor, accent
from .exam_tab import ExamTab
from .sessions_tab import SessionsTab


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
        self.sessions_tab = SessionsTab(self)
        self.exam_tab = ExamTab(self)
        self.tabs.addTab(self.exam_tab, "Exam")
        self.tabs.addTab(self.sessions_tab, "Sessions")
        self.tabs.currentChanged.connect(self.tab_changed)
        self.presentation_log = PresentationLog(host, bank.folder)
        self.start_logging()
        QApplication.instance().aboutToQuit.connect(self.presentation_log.close)
        self.destroyed.connect(self.presentation_log.close)
        self.reload()
        self.check_sources()

    def start_logging(self):
        try:
            available = self.presentation_log.start()
            self.sessions_tab.logging.setText("Presentation logging is active for this bank." if available else
                "Presentation logging unavailable: no presenter.events provider. Add sessions and confirm text matches manually.")
        except (ServiceError, OSError, RuntimeError) as exc:
            self.sessions_tab.logging.setText(f"Presentation logging unavailable: {exc}")
            self.error(exc)

    def switch_bank(self, bank):
        self.presentation_log.close()
        # Replace the window's bank only when there is no worker using it.
        self.bank = bank
        self.folder_label.setText(str(bank.folder))
        self.current = self.baseline = None
        self.new_item = False
        self.editor.setEnabled(False)
        self.sessions_tab.reset()
        self.exam_tab.reset()
        self.presentation_log.folder = bank.folder
        self.start_logging()
        self.reload()

    def tab_changed(self, index):
        if self.tabs.widget(index) is self.sessions_tab and not self.sessions_tab.loaded:
            self.when_idle(self.sessions_tab.load)

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
        self.editor.setEnabled(not busy and self.current is not None)
        for widget in (self.check_button, self.export_button, self.exam_tab, self.sessions_tab):
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
            event.accept()  # Keep logging while hidden; the controller retains the window.
        else:
            event.ignore()
