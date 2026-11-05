# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-modal window: the deck's data and the services its parts share, the top bar, the Questions and Exam tabs, and
the footer."""
from datetime import datetime

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from ..bank import AnswerRulesStore, BankError, TopicStore
from ..model import plural, slide_label
from ..rules import AnswerRules
from ..sources import check_bank, fresh_deck
from .exam_tab import ExamTab
from .export_menu import ExportMenu
from .kit import deck_language, slide_title, tone_sheet
from .questions_tab import QuestionsTab, changed_on


class Tabs(QStackedWidget):
    """Pages behind the host's segmented switch: the switch shows a page, and select() shows one and moves the
    switch."""

    def __init__(self, host, pages):
        super().__init__()
        self.pages = dict(pages)
        for page in self.pages.values():
            self.addWidget(page)
        self.switch = host.SegmentedControl([(name, name) for name in self.pages])
        self.switch.changed.connect(lambda name: self.setCurrentWidget(self.pages[name]))

    def select(self, name):
        self.setCurrentWidget(self.pages[name])
        self.switch.set_current(name)


class BankWindow(QWidget):
    """The content of a host window, which the host closes with SlideMage. It holds the deck's bank, questions,
    topics, answer rules and slides, and runs the work of its parts one operation at a time."""

    def __init__(self, host, deck, bank, changed):
        super().__init__()
        self.host, self.deck, self.bank, self.changed = host, deck, bank, changed
        self.items = []
        self.topics = ()
        self.answer_rules = AnswerRules()
        self.slides = None
        self.slide_gen = 0
        self.checked_at = None
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
        self.questions = QuestionsTab(self)
        self.editor = self.questions.editor
        self.exam_tab = ExamTab(self)
        self.tabs = Tabs(host, (("Questions", self.questions), ("Exam", self.exam_tab)))
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

    def present(self):
        """Show the host window in front."""
        self.shell.show()
        self.shell.raise_()
        self.shell.activateWindow()
        self.questions.topic_list.setFocus()  # Not the switch: its focus ring is for the keyboard.

    def eventFilter(self, obj, event):
        if obj is self.shell and event.type() == QEvent.Type.Close and not self.may_leave():
            event.ignore()
            return True
        return super().eventFilter(obj, event)

    def may_leave(self):
        return self.editor.may_leave()

    def retheme(self):
        """Every tone's colour in the host's theme, in one stylesheet that the tagged widgets and the dialogs share."""
        self.setStyleSheet(tone_sheet(self.host.theme().dark))

    def show_export_menu(self):
        self.export_menu.popup(self.export_button.mapToGlobal(QPoint(0, self.export_button.height())))

    # -- the parts' entry points ---------------------------------------------------------------------------------

    def refresh_ai_support(self):
        self.questions.refresh_ai_support()

    def open_question(self, item_id):
        return self.questions.open_question(item_id)

    def start_question(self, link):
        self.questions.start_question(link)

    # -- data ----------------------------------------------------------------------------------------------------

    def switch_bank(self, bank):
        # Replace the window's bank only when there is no worker using it.
        self.bank = bank
        self.folder_label.setText(str(bank.folder))
        self.editor.drop()
        self.questions.reset()
        self.questions.stack.setCurrentWidget(self.questions.placeholder)
        self.slides, self.checked_at = None, None
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
        self.questions.render()
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

    def reload(self):
        try:
            self.items, errors = self.bank.load()
            if errors:
                self.error("\n".join(map(str, errors)))
            self.questions.render()
            self.update_footer()
            self.changed(self.items)
            self.exam_tab.refresh()
        except (OSError, BankError, ValueError) as exc:
            self.error(exc)

    def load_slides(self):
        self.slide_gen += 1
        generation, host, deck = self.slide_gen, self.host, self.deck
        language = deck_language(host, deck)

        def done(refs, error):
            if generation != self.slide_gen:
                return
            if error:
                self.error(error)
                return
            self.slides = list({ref.id: ref for ref in reversed(refs)}.values())[::-1]
            self.questions.render()
            self.update_footer()
            self.editor.refresh()  # Its slide labels and banner come from these slides.

        host.run_in_background(lambda: host.deck_slides(fresh_deck(deck), "instructor", language), done)

    def check_sources(self, *_, then=None, item_ids=None):
        if self.busy:
            return
        self.set_busy(True)
        self.notice.setText("Checking sources…")
        host, deck, bank = self.host, self.deck, self.bank

        def checked(report, error):
            self.set_busy(False)
            if error is None:
                self.checked_at = datetime.now()
            self.reload()      # A failed check may have recorded some questions before it stopped.
            self.load_slides()
            if error is not None:
                self.notice.setText("The source check did not finish.")
                self.error(error)
                return
            self.notice.setText(f"Source check complete · {plural(len(self.changed_slides()), 'changed slide')}.")
            if report.errors:
                self.error("\n".join(map(str, report.errors)))
                return
            for item in self.items:
                self.editor.take_check(item)
            if then:
                then(report)

        host.run_in_background(lambda: check_bank(host, fresh_deck(deck), bank, item_ids), checked)

    def changed_slides(self):
        return {slide for item in self.items for slide in changed_on(item)}

    def slide_ref(self, slide_id):
        return next((ref for ref in self.slides or () if ref.id == slide_id), None)

    def slide_name(self, slide_id):
        ref = self.slide_ref(slide_id)
        return slide_label(slide_id, ref.position, slide_title(ref)) if ref else slide_label(slide_id)

    def owner(self, slide_id):
        """The topic a slide belongs to, or None."""
        return next((t for t in self.topics if slide_id in t.slides), None)

    def update_footer(self):
        self.checked_label.setText("Slides not checked yet" if self.checked_at is None else
                                   f"Slides checked {self.checked_at:%H:%M} · {len(self.changed_slides())} changed")

    # -- services --------------------------------------------------------------------------------------------------

    def toast(self, text):
        """Something went well."""
        self.host.toast(self.shell, text)

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
        self.questions.set_busy(busy)
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
