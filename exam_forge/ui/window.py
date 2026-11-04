# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-modal window shell: the Questions and Exam tabs, the top bar and the footer."""
from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from ..bank import AnswerRulesStore, BankError, TopicStore
from ..rules import AnswerRules
from .exam_tab import ExamTab
from .export_menu import ExportMenu
from .kit import tone_sheet
from .questions_tab import QuestionsTab


class Tabs(QStackedWidget):
    """Pages behind the host's segmented switch."""

    def __init__(self, host, pages):
        super().__init__()
        self.names = [name for name, _ in pages]
        for _, page in pages:
            self.addWidget(page)
        self.switch = host.SegmentedControl([(name, name) for name in self.names])
        self.switch.changed.connect(lambda name: super(Tabs, self).setCurrentIndex(self.names.index(name)))

    def setCurrentIndex(self, index):
        super().setCurrentIndex(index)
        self.switch.set_current(self.names[index])

    def setCurrentWidget(self, page):
        self.setCurrentIndex(self.indexOf(page))


class BankWindow(QuestionsTab, QWidget):
    """The content of a host window, which the host closes with SlideMage; show and close go to that window."""

    def __init__(self, host, deck, bank, changed):
        super().__init__()
        self.host, self.deck, self.bank, self.changed = host, deck, bank, changed
        self.items = []
        self.topics = ()
        self.answer_rules = AnswerRules()
        self.busy = False
        self.idle_callbacks = []
        self.shell = host.Window(f"Exam Forge · {deck.name}")
        self.shell.resize(1180, 800)
        self.shell.body.setContentsMargins(0, 0, 0, 0)
        self.shell.body.addWidget(self)
        self.shell.installEventFilter(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 8)
        layout.setSpacing(8)
        self.notice = host.caption("")  # The source check's status.
        self.notice.setWordWrap(True)
        self.check_button = host.Button("Check slides")
        self.check_button.clicked.connect(self.check_sources)
        self.export_button = host.Button("Export")
        self.export_menu = ExportMenu(self)
        self.export_button.clicked.connect(self.show_export_menu)
        questions = self.build_questions_tab()
        self.exam_tab = ExamTab(self)
        self.tabs = Tabs(host, (("Questions", questions), ("Exam", self.exam_tab)))
        top = QHBoxLayout()
        top.addWidget(self.tabs.switch)
        top.addStretch()
        top.addWidget(self.check_button)
        top.addWidget(self.export_button)
        layout.addLayout(top)
        layout.addWidget(self.notice)
        layout.addWidget(self.tabs, 1)
        foot = QHBoxLayout()
        self.folder_label = host.caption(str(bank.folder))
        self.folder_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.folder_label.setWordWrap(False)
        self.checked_label = host.caption("")
        self.checked_label.setWordWrap(False)
        foot.addWidget(host.IconLabel("lock", 14))
        foot.addWidget(self.folder_label, 1)
        foot.addWidget(self.checked_label)
        layout.addLayout(foot)
        self.retheme()
        self.load_topics()
        self.load_answer_rules()
        self.reload()
        self.load_slides()
        self.check_sources()
        host.on_theme_changed(lambda tokens: self.retheme())

    # The host window is what opens and closes.

    def show(self):
        self.shell.show()
        self.topic_list.setFocus()  # Not the switch: its focus ring is for the keyboard.

    def close(self):
        return self.shell.close()

    def raise_(self):
        self.shell.raise_()

    def activateWindow(self):
        self.shell.activateWindow()

    def resize(self, *size):
        self.shell.resize(*size)

    def windowTitle(self):
        return self.shell.windowTitle()

    def eventFilter(self, obj, event):
        if obj is self.shell and event.type() == QEvent.Type.Close and not self.may_leave():
            event.ignore()
            return True
        return super().eventFilter(obj, event)

    def toast(self, text):
        """Something went well."""
        self.host.toast(self.shell, text)

    def show_export_menu(self):
        self.export_menu.popup(self.export_button.mapToGlobal(QPoint(0, self.export_button.height())))

    def switch_bank(self, bank):
        # Replace the window's bank only when there is no worker using it.
        self.bank = bank
        self.folder_label.setText(str(bank.folder))
        self.editor.drop()
        self.stack.setCurrentWidget(self.placeholder)
        self.view, self.sel_slide, self.slides = "all", None, None
        self.checked_at = None
        self.exam_tab.reset()
        self.load_topics()
        self.load_answer_rules()
        self.reload()
        self.load_slides()

    def load_topics(self):
        """Read topics.json; a bank without one has no topics yet."""
        try:
            self.topics = TopicStore(self.bank.folder).load()
        except (OSError, BankError, ValueError) as exc:
            self.topics = ()
            self.error(exc)

    def load_answer_rules(self):
        """Read answer-rules.json; the defaults when there is none or it cannot be read."""
        try:
            self.answer_rules = AnswerRulesStore(self.bank.folder).load()
        except (OSError, BankError, ValueError) as exc:
            self.answer_rules = AnswerRules()
            self.error(f"{exc} The default answer rules apply.")
        self.exam_tab.rules = self.answer_rules.exam_rules()

    def answer_rules_changed(self):
        """Settings saved new rules: a proposed exam was screened with the old ones, so it is dropped."""
        self.load_answer_rules()
        self.exam_tab.invalidate()
        self.render()
        self.editor.refresh()

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

    def error(self, error):
        """Something failed or was refused."""
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
        self.editor.setEnabled(not busy and self.editor.current is not None)
        for widget in (self.check_button, self.export_button, self.exam_tab):
            widget.setEnabled(not busy)
        if busy:
            self.export_menu.close()
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

    def retheme(self):
        """Every tone's colour in the host's theme, in one stylesheet that the tagged widgets and the dialogs share."""
        self.setStyleSheet(tone_sheet(self.host.theme().dark))
