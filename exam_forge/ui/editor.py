# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Question editor for the bank window."""
from dataclasses import replace
from functools import partial
from html import escape
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImageReader, QPalette
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..bank import BankError
from ..elp import check_elp
from ..model import Item, Option, check_phase1
from ..sources import check_item, link_slide, rebaseline_links
from ..topics import topic_color, topic_of
from .work import SCROLL_STYLE, fresh_deck, slide_title

REVISION_MAX, ELP_COUNT = 8, 5
HINTS = {"revision": "Revision quiz: exactly one correct answer",
         "elp": "ELP exam: exactly five answers, one to four correct"}
TA_WARNING = "Shared stem, exclusions or fixed option order. This appears in the exam file; do not put answers here."


def is_dark(widget):
    return widget.palette().color(QPalette.ColorRole.Window).lightness() < 128


def accent_color(widget, amber=False):
    dark = is_dark(widget)
    return (("#fbbf24" if dark else "#8a5000") if amber
            else ("#c4b5fd" if dark else "#7040ac"))


def accent(widget, amber=False):
    # Only label foregrounds are explicit; surfaces and fields inherit the theme.
    # A local stylesheet also reaches the QLabel inside a host Chip.
    widget.setStyleSheet(f"color: {accent_color(widget, amber)};")


def reviewed_issues(item):
    """What still stops a question from being marked reviewed, in the footer's wording."""
    issues = []
    if not item.stem.strip():
        issues.append("write the question")
    if len(item.options) < 2:
        issues.append("add at least two answers")
    elif any(not option.text.strip() for option in item.options):
        issues.append("fill in every answer")
    if item.use == "elp":
        for error in check_elp(item):
            issues.append("have exactly five answers" if "five" in error else "mark one to four correct answers")
    else:
        if len(item.options) > REVISION_MAX:
            issues.append("keep at most eight answers")
        if check_phase1(item):
            issues.append("mark exactly one correct answer")
    if not item.sources:
        issues.append("link a slide")
    return issues


def set_chip(chip, text=None, tone=None):
    if text is not None:
        chip.set_text(text)
    if tone is not None:
        chip.set_tone(tone)


class Switch(QWidget):
    """Two-option switch. A value outside the options shows as unselected but is kept."""
    changed = Signal()

    def __init__(self, host, options, clearable=False):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.clearable, self.value = clearable, "" if clearable else options[0][1]
        self.control = host.SegmentedControl([(value, label) for label, value in options])
        self.buttons = dict(zip((value for _, value in options), self.control.buttons()))
        self.control.changed.connect(self.pick)
        row.addWidget(self.control)
        row.addStretch()
        self.sync()

    def sync(self):
        if self.value in self.buttons:
            self.control.set_current(self.value)
        else:
            self.control.clear_current()

    def currentData(self):
        return self.value

    def setCurrentData(self, value):
        value = value or ""
        changed = value != self.value
        self.value = value
        self.sync()
        if changed:
            self.changed.emit()

    def pick(self, value):
        self.setCurrentData("" if self.clearable and value == self.value else value)
        self.sync()


class Box(QWidget):
    """A container that shows what is behind it: the stylesheet paints the page colour on a plain QWidget only."""


class Fold(QWidget):
    def __init__(self, title):
        super().__init__()
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        self.header = QToolButton()
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setAutoRaise(True)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setArrowType(Qt.ArrowType.RightArrow)
        self.header.toggled.connect(self.set_open)
        self.content = Box()
        self.content.hide()
        self.box = QVBoxLayout(self.content)
        self.box.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.header)
        column.addWidget(self.content)

    def set_open(self, on):
        self.header.setChecked(on)
        self.content.setVisible(on)
        self.header.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)


class AnswerRow(QWidget):
    changed = Signal()
    ticked = Signal(object)
    removed = Signal(object)

    def __init__(self, host, option):
        super().__init__()
        self.option_id = option.id
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        top = QHBoxLayout()
        self.tick = QPushButton()
        self.tick.setFlat(True)
        self.tick.setFixedSize(30, 30)
        self.tick.setStyleSheet("padding: 0; min-width: 30px; max-width: 30px;")  # The glyph needs the whole button.
        self.tick.clicked.connect(lambda: self.ticked.emit(self))
        self.set_correct(option.correct)
        self.text = QPlainTextEdit(option.text)
        self.text.setTabChangesFocus(True)
        self.text.setFixedHeight(self.text.fontMetrics().lineSpacing() * 2 + 22)
        self.text.setPlaceholderText("Answer")
        self.remove = host.Button("✕", compact=True)
        self.remove.setToolTip("Remove answer")
        self.remove.clicked.connect(lambda: self.removed.emit(self))
        for widget, stretch in ((self.tick, 0), (self.text, 1), (self.remove, 0)):
            top.addWidget(widget, stretch)
        column.addLayout(top)
        self.why = QPlainTextEdit(option.rationale)
        self.why.setTabChangesFocus(True)
        self.why.setFixedHeight(self.why.fontMetrics().lineSpacing() + 24)
        self.why.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.why.setPlaceholderText("Why it is right or wrong · only in the private key")
        low = QHBoxLayout()
        low.setContentsMargins(36, 0, 0, 0)
        low.addWidget(self.why)
        column.addLayout(low)
        self.text.textChanged.connect(self.changed)
        self.why.textChanged.connect(self.changed)

    def correct(self):
        return self.tick.isChecked()

    def set_correct(self, on):
        self.tick.setCheckable(True)
        self.tick.setChecked(on)
        self.tick.setText("●" if on else "○")

    def option(self):
        return Option(self.option_id, self.text.toPlainText(), self.correct(), self.why.toPlainText())


class ItemEditor:
    def build_editor(self, host, split):
        self._loading = False
        self.labels, self.labels_tried, self.context_gen, self.context_key = {}, set(), 0, None
        self.rows = []
        self.last_link_context = ("instructor", host.deck_language(self.deck))
        self.link_grid_buttons = []
        self.editor = host.Page()
        self.editor.body.setContentsMargins(0, 0, 0, 0)
        split.addWidget(self.editor)
        card = host.Card()
        self.editor.body.addWidget(card)
        outer = card.layout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(SCROLL_STYLE)
        outer.addWidget(scroll, 1)
        content = host.Page()
        scroll.setWidget(content)
        body = content.body
        body.setContentsMargins(14, 10, 14, 10)
        self.back_button = host.Button("", compact=True)
        self.back_button.setFlat(True)
        self.back_button.clicked.connect(self.back)
        self.back_button.hide()
        body.addWidget(self.back_button, 0, Qt.AlignmentFlag.AlignLeft)
        tags = QHBoxLayout()
        self.status_chip = host.Chip("Draft")
        self.rev_chip = host.Chip("rev 1", "muted")
        self.ai_tag = host.Chip("AI-assisted", "muted")
        self.draft_tag = host.Chip("AI draft")
        accent(self.draft_tag, True)
        for chip in (self.draft_tag, self.ai_tag, self.status_chip, self.rev_chip):
            tags.addWidget(chip)
        tags.addStretch()
        body.addLayout(tags)
        self.source_banner = host.Label("A linked slide changed after this question was written.")
        self.source_banner.setWordWrap(True)
        accent(self.source_banner)
        self.compare_button = host.Button("Compare", compact=True)
        self.compare_button.clicked.connect(lambda: self.review_changes(self.current))
        self.still_button = host.Button("Still correct", compact=True)
        self.still_button.clicked.connect(self.still_correct)
        self.source_bar = Box()
        bar = QHBoxLayout(self.source_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(self.source_banner, 1)
        bar.addWidget(self.compare_button)
        bar.addWidget(self.still_button)
        body.addWidget(self.source_bar)
        self.stem = QPlainTextEdit()
        self.stem.setPlaceholderText(r"\( … \)")
        self.stem.setMaximumHeight(135)
        body.addWidget(host.Field("Question", self.stem))
        head = QHBoxLayout()
        head.addWidget(host.Label("Answers", "heading"))
        self.hint = host.caption("")
        head.addStretch()
        head.addWidget(self.hint)
        body.addLayout(head)
        self.answers = QVBoxLayout()
        body.addLayout(self.answers)
        self.add_button = host.Button("+ Add answer", compact=True)
        self.add_button.clicked.connect(lambda: self.add_option())
        body.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.use = Switch(host, (("Revision quiz", "revision"), ("ELP exam", "elp")))
        self.difficulty = Switch(host, (("Easy", "easy"), ("Hard", "hard")), clearable=True)
        row = QHBoxLayout()
        row.addWidget(host.Field("Used for", self.use))
        row.addWidget(host.Field("Difficulty", self.difficulty))
        body.addLayout(row)
        self.objective = QLineEdit()
        body.addWidget(host.Field("Learning objective", self.objective))
        head = QHBoxLayout()
        head.addWidget(host.Label("Linked slides", "heading"))
        head.addStretch()
        self.topic_label = host.caption("")
        self.topic_label.setTextFormat(Qt.TextFormat.RichText)
        head.addWidget(self.topic_label)
        body.addLayout(head)
        self.link_box = QVBoxLayout()
        body.addLayout(self.link_box)
        self.link_button = host.Button("+ Link another slide", compact=True)
        self.link_button.clicked.connect(self.toggle_link_grid)
        body.addWidget(self.link_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.link_grid = Box()
        self.link_grid_layout = QGridLayout(self.link_grid)
        self.link_grid_layout.setContentsMargins(0, 0, 0, 0)
        self.link_grid.hide()
        body.addWidget(self.link_grid)
        self.note_fold = Fold("Note for teaching assistants")
        self.assistant_note = QPlainTextEdit()
        self.assistant_note.setMaximumHeight(85)
        self.assistant_note.setPlaceholderText(TA_WARNING)
        warning = host.caption(TA_WARNING)
        warning.setWordWrap(True)
        self.note_fold.box.addWidget(warning)
        self.note_fold.box.addWidget(self.assistant_note)
        body.addWidget(self.note_fold)
        self.media_fold = Fold("Images (appended to the question)")
        self.media_list = QListWidget()
        self.media_list.setMaximumHeight(85)
        self.media_fold.box.addWidget(self.media_list)
        row = QHBoxLayout()
        for text, action in (("Attach image…", self.attach_image), ("Edit alt text…", self.edit_alt),
                             ("Remove image", self.remove_image)):
            button = host.Button(text, compact=True)
            button.clicked.connect(action)
            row.addWidget(button)
        row.addStretch()
        self.media_fold.box.addLayout(row)
        body.addWidget(self.media_fold)
        body.addStretch()
        footer = QHBoxLayout()
        footer.setContentsMargins(14, 8, 14, 8)
        self.status_line = host.Label("", "secondary")
        self.status_line.setWordWrap(True)
        footer.addWidget(self.status_line, 1)
        self.retire_button = host.Button("Retire")
        self.retire_button.clicked.connect(lambda: self.change_status("retired"))
        self.restore_button = host.Button("Restore as draft")
        self.restore_button.clicked.connect(lambda: self.change_status("draft"))
        self.save_button = host.Button("Save draft")
        self.save_button.clicked.connect(self.save)
        self.approve_button = host.Button("Mark reviewed", "primary")
        self.approve_button.clicked.connect(lambda: self.change_status("reviewed"))
        for button in (self.retire_button, self.restore_button, self.save_button, self.approve_button):
            footer.addWidget(button)
        outer.addLayout(footer)
        for signal in (self.stem.textChanged, self.objective.textChanged, self.assistant_note.textChanged,
                       self.difficulty.changed):
            signal.connect(self.touch)
        self.use.changed.connect(self.use_changed)
        for chip in (self.ai_tag, self.draft_tag, self.source_bar):
            chip.hide()

    # -- state ---------------------------------------------------------------------------------------------

    def candidate(self):
        return replace(self.current, stem=self.stem.toPlainText(), options=tuple(r.option() for r in self.rows),
                       objective=self.objective.text(), difficulty=self.difficulty.currentData(),
                       use=self.use.currentData(), sources=self.links, media=self.attachments,
                       assistant_note=self.assistant_note.toPlainText())

    def dirty(self):
        return self.current is not None and (self.new_item or self.candidate() != self.baseline)

    def may_leave(self):
        if self.busy:
            self.notice.setText("Wait for the current operation to finish.")
            return False
        if not self.dirty():
            return True
        answer = QMessageBox.question(self, "Unsaved question", "Save this question before continuing?",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                      QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def toast(self, text):
        """Shell hook: tell the user something went well."""

    def leave_editor(self):
        """Shell hook: the back link was pressed and may_leave agreed."""

    def review_changes(self, item):
        """Shell hook: Compare was pressed."""

    def set_back_label(self, text):
        self.back_button.setText(text)
        self.back_button.setVisible(bool(text))

    def back(self):
        if self.may_leave():
            self.leave_editor()

    def touch(self, *_):
        if self._loading or self.current is None:
            return
        self.refresh_answers()
        self.refresh_footer()
        self.update_topic()

    def populate(self, item, new=False):
        self._loading = True
        self.current, self.new_item = item, new
        self.links, self.attachments = item.sources, item.media
        self.stem.setPlainText(item.stem)
        self.assistant_note.setPlainText(item.assistant_note)
        self.note_fold.set_open(bool(item.assistant_note))
        self.media_fold.set_open(bool(item.media))
        for row in self.rows:
            self.answers.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.rows = []
        for option in item.options:
            self.add_option(option)
        self.objective.setText(item.objective)
        self.use.setCurrentData(item.use)
        self.difficulty.setCurrentData(item.difficulty)
        self._loading = False
        if item.sources:
            self.last_link_context = (item.sources[-1].view, item.sources[-1].language)
        self.editor.setEnabled(True)
        self.close_link_grid()
        self.update_header(item)
        self.draw_links()
        self.render_media()
        self.refresh_answers()
        self.baseline = self.candidate()
        self.refresh_footer()
        if not item.source_changed and {s.slide_id for s in item.sources} - self.labels.keys():
            self.load_context()

    def update_header(self, item):
        ai_edit = bool(item.ai_edit_route)
        provenance = (f"{item.ai_edit_route} · {item.ai_edit_model}" if ai_edit
                      else f"{item.origin.route} · {item.origin.model}")
        draft = item.status == "draft"
        self.ai_tag.setToolTip(provenance)
        self.draft_tag.setToolTip(provenance)
        self.ai_tag.setVisible(ai_edit or (item.origin.kind == "ai" and not draft))
        self.draft_tag.setVisible(item.origin.kind == "ai" and draft)
        tone = {"draft": "neutral", "reviewed": "ok", "retired": "muted"}[item.status]
        set_chip(self.status_chip, "New question" if self.new_item else item.status.title(), tone)
        set_chip(self.rev_chip, f"rev {item.revision}")
        self.rev_chip.setVisible(not self.new_item)
        key = (item.id, item.revision, item.source_changed)
        if key != self.context_key:
            self.context_key = key
            if item.source_changed:
                self.set_banner(None)
                self.load_context()
        self.source_bar.setVisible(item.source_changed)
        self.source_banner.setVisible(item.source_changed)
        self.compare_button.setVisible(item.source_changed)
        self.still_button.setVisible(item.source_changed)
        self.refresh_footer()

    def set_banner(self, position):
        self.source_banner.setText(f"Slide {position} changed after this question was written." if position
                                   else "A linked slide changed after this question was written.")

    def refresh_footer(self):
        item = self.current
        buttons = (self.retire_button, self.restore_button, self.save_button, self.approve_button)
        if item is None:
            self.status_line.setText("")
            for button in buttons:
                button.setEnabled(False)
            return
        dirty, issues = self.dirty(), reviewed_issues(self.candidate())
        status = item.status
        if status == "retired":
            issues = []
        if issues and (dirty or status == "draft"):
            text = "Before marking reviewed: " + " · ".join(issues)
        elif dirty:
            text = "Unsaved edits" if self.new_item else "Unsaved edits · saving creates a new draft revision"
        elif status == "draft":
            text = "Draft · saved"
        elif status == "reviewed":
            text = "Reviewed · ready to use"
        else:
            text = "Retired · restore it as a draft to edit or review again"
        shown = bool(issues and (dirty or status == "draft"))
        self.status_line.setText(text)
        self.status_line.setStyleSheet(f"color: {topic_color('red', is_dark(self))};" if shown else "")
        reviewed_clean = status == "reviewed" and not dirty and not self.new_item
        self.retire_button.setVisible(reviewed_clean)
        self.retire_button.setEnabled(reviewed_clean)
        self.restore_button.setVisible(status == "retired")
        self.restore_button.setEnabled(status == "retired")
        self.save_button.setEnabled(dirty)
        self.approve_button.setText("Reviewed" if reviewed_clean else "Mark reviewed")
        self.approve_button.setEnabled(status != "retired" and not issues and not reviewed_clean)

    def restyle_editor(self):
        # Children get the new palette after the window's event, so read it from the window.
        self.draft_tag.setStyleSheet(f"color: {accent_color(self, True)};")
        self.source_banner.setStyleSheet(f"color: {accent_color(self)};")
        if self.current is not None:
            self.refresh_footer()
            self.update_topic()

    # -- answers -------------------------------------------------------------------------------------------

    def cap(self):
        return ELP_COUNT if self.use.currentData() == "elp" else REVISION_MAX

    def add_option(self, option=None):
        row = AnswerRow(self.host, option or Option(uuid4().hex, ""))
        row.changed.connect(self.touch)
        row.ticked.connect(self.tick)
        row.removed.connect(self.remove_option)
        self.rows.append(row)
        self.answers.addWidget(row)
        self.touch()
        return row

    def remove_option(self, row):
        self.rows.remove(row)
        self.answers.removeWidget(row)
        row.hide()
        row.deleteLater()
        self.touch()

    def tick(self, row):
        if self.use.currentData() == "revision":
            for other in self.rows:
                other.set_correct(other is row)
        else:
            row.set_correct(row.tick.isChecked())
        self.touch()

    def use_changed(self):
        if self._loading:
            return
        if self.use.currentData() == "elp":
            self._loading = True
            while len(self.rows) < ELP_COUNT:
                self.add_option()
            self._loading = False
        self.touch()

    def refresh_answers(self):
        use = self.use.currentData()
        self.hint.setText(HINTS[use])
        self.add_button.setEnabled(len(self.rows) < self.cap())
        for number, row in enumerate(self.rows, 1):
            row.tick.setAccessibleName(f"Option {number} is correct")

    def new_question(self, *_):
        if not self.may_leave():
            return False
        self.populate(Item(uuid4().hex, 1, options=(Option(uuid4().hex, ""), Option(uuid4().hex, ""))), True)
        return True

    # -- lifecycle -----------------------------------------------------------------------------------------

    def save(self, *_):
        if self.current is None or self.busy:
            return False
        if not self.dirty():
            return True
        item = replace(self.candidate(), status="draft")
        try:
            saved = self.bank.create(item) if self.new_item else self.bank.save(item)
        except (OSError, BankError, ValueError) as exc:
            self.error(exc)
            return False
        self.populate(saved)
        self.reload()
        self.toast(f"Saved revision {saved.revision} as Draft.")
        return True

    def change_status(self, target):
        if self.current is None or not self.save():
            return
        if target == "retired" and self.current.status != "reviewed":
            self.error("Saved edits as a draft. Review this revision before retiring it.")
            return
        if target == "draft" and self.current.status == "draft":
            return
        if target == "reviewed":
            issues = reviewed_issues(self.current)
            if issues:
                self.error("Before marking reviewed: " + " · ".join(issues))
                return
            if self.current.status == "reviewed":
                try:
                    self.populate(self.bank.save(replace(self.current, status="draft")))
                    self.reload()
                except (OSError, BankError, ValueError) as exc:
                    self.error(exc)
                    return
        item = self.current

        def proceed(report):
            checked = report.items[0]
            if checked.item.revision != item.revision:
                self.error("Question changed during the check. Your editor is intact; save or reopen before continuing.")
                return
            if checked.source_changed:
                details = "\n".join(f"• {s.source.slide_id} ({s.source.view}, {s.source.language}): {s.status} {s.message}"
                                    for s in checked.sources if s.status != "unchanged")
                answer = QMessageBox.warning(self, "Source changed", f"{item.stem}\n\n{details}\n\n"
                                             f"Continue and mark this question {target}? The Source changed flag remains.",
                                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                             QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    return
            try:
                updated = self.bank.set_status(item.id, item.revision, target)
                self.populate(updated)
                self.reload()
            except (OSError, BankError, ValueError) as exc:
                self.error(exc)

        self.check_sources(then=proceed, item_ids=[item.id])

    def still_correct(self):
        """Accept the slides as they are now; the status is kept and the flag cleared."""
        if self.current is None or self.new_item or self.busy:
            return
        if self.dirty():
            if not self.may_leave():
                return
            if self.dirty():  # Discarded: show the stored question again.
                self.populate(self.current)
        item, host, deck, bank = self.current, self.host, self.deck, self.bank

        def work():
            return bank.rebaseline(item.id, item.revision, rebaseline_links(host, fresh_deck(deck), item))

        def done(updated):
            self.populate(updated)
            self.reload()
            self.toast("Kept · matches the current slide.")
        self.work(work, done)

    # -- linked slides -------------------------------------------------------------------------------------

    def slide_label(self, link):
        position, title = self.labels.get(link.slide_id, (None, ""))
        return f"Slide {position} · {title}" if position else f"Slide {link.slide_id}"

    def draw_links(self):
        while self.link_box.count():
            widget = self.link_box.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        for index, link in enumerate(self.links):
            row = Box()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            name = self.host.Button(self.slide_label(link), compact=True)
            name.setToolTip(f"{link.slide_id} · {link.view} · {link.language} · open in SlideMage")
            name.clicked.connect(partial(self.open_source, link))
            remove = self.host.Button("✕", compact=True)
            remove.setToolTip("Remove link")
            remove.clicked.connect(partial(self.remove_source, index))
            line.addWidget(name)
            line.addWidget(remove)
            line.addStretch()
            self.link_box.addWidget(row)
        self.update_topic()

    def render_links(self):
        self.draw_links()
        missing = {s.slide_id for s in self.links} - self.labels.keys() - self.labels_tried
        if missing and self.current is not None:
            self.load_context()
        self.touch()

    def update_topic(self):
        topic = topic_of(self.candidate(), self.topics) if self.current is not None else None
        if topic is None:
            self.topic_label.setText("No topic")
        else:
            dot = f"<span style='color:{topic_color(topic.color, is_dark(self))}'>●</span>"
            self.topic_label.setText(f"Topic from first slide: {dot} {escape(topic.name)}")

    def load_context(self):
        """Resolve slide titles and the changed slide's position on the worker."""
        item, links, host, deck = self.current, self.links, self.host, self.deck
        self.context_gen += 1
        generation = self.context_gen
        if item is None or not links:
            return
        self.labels_tried |= {s.slide_id for s in links}
        wanted = item.source_changed and not self.new_item

        def work():
            snapshot, decks, labels = fresh_deck(deck), {}, {}
            for link in links:
                context = (link.view, link.language)
                if context not in decks:
                    decks[context] = host.deck_slides(snapshot, *context)
                refs = [r for r in decks[context] if r.id == link.slide_id]
                refs = [r for r in refs if (r.version or "") == link.version] or refs
                if refs:
                    labels[link.slide_id] = (refs[0].position, slide_title(refs[0]))
            position = None
            if wanted:
                first = next((c.source for c in check_item(host, snapshot, item).sources if c.status != "unchanged"), None)
                position = labels.get(first.slide_id, (None,))[0] if first else None
            return labels, position

        def done(result, error):
            if error or generation != self.context_gen:
                return
            labels, position = result
            self.labels.update(labels)
            self.draw_links()
            self.set_banner(position)
        host.run_in_background(work, done)

    def remove_source(self, index, *_):
        self.links = tuple(s for i, s in enumerate(self.links) if i != index)
        self.render_links()

    def open_source(self, link, *_):
        from .work import fresh_deck
        deck = self.deck

        def work():
            return self.host.deck_slides(fresh_deck(deck), link.view, link.language)

        def done(refs, error):
            if error:
                self.error(error)
                return
            matches = [r for r in refs if r.id == link.slide_id]
            exact = [r for r in matches if (r.version or "") == link.version]
            matches = exact or matches
            if len({r.path for r in matches}) == 1:
                try:
                    self.host.open_slide(matches[0])
                except (OSError, ValueError, RuntimeError) as exc:
                    self.error(exc)
            else:
                self.error("Source slide is missing or ambiguous. Link it again from the grid.")
        self.host.run_in_background(work, done)

    def close_link_grid(self):
        self.link_grid.hide()
        self.link_grid_buttons = []
        while self.link_grid_layout.count():
            widget = self.link_grid_layout.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()

    def toggle_link_grid(self):
        if self.link_grid.isVisibleTo(self.editor):
            self.close_link_grid()
            return
        deck, host = self.deck, self.host
        view, language = self.last_link_context

        def work():
            return host.deck_slides(fresh_deck(deck), view, language)

        def done(refs):
            self.close_link_grid()
            linked = {s.slide_id for s in self.links}
            for index, ref in enumerate(refs):
                button = host.Button(f"{ref.position} · {slide_title(ref)}", compact=True)
                button.setEnabled(ref.id not in linked)
                button.clicked.connect(partial(self.link_slide_ref, ref, view))
                self.link_grid_buttons.append(button)
                self.link_grid_layout.addWidget(button, index // 2, index % 2)
            if not refs:
                self.link_grid_layout.addWidget(host.caption("No slides found for this view and language."), 0, 0)
            self.link_grid.show()
        self.work(work, done)

    def link_slide_ref(self, ref, view, *_):
        host = self.host

        def done(link):
            self.close_link_grid()
            if any(s.slide_id == link.slide_id for s in self.links):
                return
            self.labels[ref.id] = (ref.position, slide_title(ref))
            self.last_link_context = (view, link.language)
            self.links += (link,)
            self.render_links()
        self.work(lambda: link_slide(host, ref, view), done)

    # -- images --------------------------------------------------------------------------------------------

    def render_media(self):
        self.media_list.clear()
        self.media_list.addItems([f"{m.name[:16]}… · {m.alt or 'No alt text'}" for m in self.attachments])
        self.touch()

    def attach_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Attach image", "", "Images (*.png *.jpg *.jpeg *.gif *.webp *.svg)")
        if not path:
            return
        alt, ok = QInputDialog.getText(self, "Image description", "Alternative text")
        if not ok:
            return
        try:
            if not QImageReader(path).canRead():
                raise ValueError("This file could not be read as an image.")
            media = self.bank.store_media("image" + Path(path).suffix.lower(), Path(path).read_bytes(), alt)
            if any(m.name == media.name for m in self.attachments):
                self.error("This image is already attached. Edit its alternative text instead.")
                return
            self.attachments += (media,)
            self.media_fold.set_open(True)
            self.render_media()
        except (OSError, BankError, ValueError) as exc:
            self.error(exc)

    def edit_alt(self):
        row = self.media_list.currentRow()
        if 0 <= row < len(self.attachments):
            alt, ok = QInputDialog.getText(self, "Image description", "Alternative text", text=self.attachments[row].alt)
            if ok:
                self.attachments = tuple(replace(m, alt=alt) if i == row else m for i, m in enumerate(self.attachments))
                self.render_media()

    def remove_image(self):
        row = self.media_list.currentRow()
        self.attachments = tuple(m for i, m in enumerate(self.attachments) if i != row)
        self.render_media()
