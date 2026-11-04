# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Question editor for the bank window."""
from dataclasses import replace
from functools import partial
from html import escape
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QBuffer, QIODevice, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPalette, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..bank import BankError
from ..model import IMAGE_NAMES, IMAGE_SUFFIXES, IMAGE_TYPES, KINDS, USES, Item, Option, image_type, topic_of
from ..rules import describe, question_issues
from ..sources import SlideIndex, Unresolved, fresh_deck, link_slide, rebaseline_links
from .work import SCROLL_STYLE, slide_title

PRIVATE = "Only in the private key."
IMAGE_FILTER = f"Images ({' '.join('*' + suffix for suffix in IMAGE_SUFFIXES)})"
THUMB_WIDTH, THUMB_HEIGHT = 160, 100
TA_WARNING = "Shared stem, exclusions or fixed option order. This appears in the exam file; do not put answers here."


def is_dark(widget):
    return widget.palette().color(QPalette.ColorRole.Window).lightness() < 128


def accent_color(widget, amber=False):
    dark = is_dark(widget)
    return (("#fbbf24" if dark else "#8a5000") if amber
            else ("#c4b5fd" if dark else "#7040ac"))


def red_color(widget):
    return "#fca5a5" if is_dark(widget) else "#a8231a"


# Topic colour key: (light, dark). Kept clear of the reserved violet and amber accents.
_TOPIC_COLORS = {"blue": ("#1d4ed8", "#60a5fa"), "green": ("#15803d", "#4ade80"),
                 "red": ("#b91c1c", "#f87171"), "teal": ("#0f766e", "#2dd4bf"),
                 "pink": ("#be185d", "#f472b6"), "slate": ("#475569", "#94a3b8")}


def topic_color(key, dark):
    return _TOPIC_COLORS[key][1 if dark else 0]


def accent(widget, amber=False):
    # Only label foregrounds are explicit; surfaces and fields inherit the theme.
    # A local stylesheet also reaches the QLabel inside a host Chip.
    widget.setStyleSheet(f"color: {accent_color(widget, amber)};")


def reviewed_issues(item, rules):
    """What still stops a question from being marked reviewed, in the footer's wording."""
    issues = []
    if not item.stem.strip():
        issues.append("write the question")
    if any(not option.text.strip() for option in item.options):
        issues.append("fill in every answer")
    issues += question_issues(item, rules)
    if any(not media.alt.strip() for media in item.media):
        issues.append("describe every image")
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


def clear(layout):
    while layout.count():
        child = layout.takeAt(0)
        if child.widget():
            child.widget().hide()       # deleted later: not shown in the meantime
            child.widget().deleteLater()
        elif child.layout():
            clear(child.layout())


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


def png_bytes(image):
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


class StemEdit(QPlainTextEdit):
    """Question text that also takes images. Copied text often carries a picture of itself, so text wins."""
    image_pasted = Signal(bytes)
    file_dropped = Signal(str)

    @staticmethod
    def image_file(source):
        files = (url.toLocalFile() for url in source.urls() if url.isLocalFile())
        return next((name for name in files if Path(name).suffix.lower() in IMAGE_SUFFIXES), "")

    def canInsertFromMimeData(self, source):
        return bool(self.image_file(source)) or source.hasImage() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if name := self.image_file(source):
            self.file_dropped.emit(name)
        elif source.hasImage() and not source.hasText():
            self.image_pasted.emit(png_bytes(source.imageData()))
        else:
            super().insertFromMimeData(source)


class ImageRow(QWidget):
    changed = Signal(object)
    removed = Signal(object)

    def __init__(self, host, media, data):
        super().__init__()
        self.name = media.name
        line = QHBoxLayout(self)
        line.setContentsMargins(0, 0, 0, 0)
        self.thumb = QLabel()
        self.thumb.setFixedSize(THUMB_WIDTH, THUMB_HEIGHT)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap()
        if data is not None and pixmap.loadFromData(data):
            ratio = self.devicePixelRatioF()
            pixmap = pixmap.scaled(int(THUMB_WIDTH * ratio), int(THUMB_HEIGHT * ratio),
                                   Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            pixmap.setDevicePixelRatio(ratio)
            self.thumb.setPixmap(pixmap)
        else:
            self.thumb.setText("Image missing" if data is None else "No preview")
        self.alt = QLineEdit(media.alt)
        self.alt.setPlaceholderText("Describe the image")
        self.alt.setToolTip("Shown under the image in exams, so do not put the answer in it.")
        self.alt.setAccessibleName("Image description")
        self.alt.textChanged.connect(lambda: self.changed.emit(self))
        self.remove = host.Button("✕", compact=True)
        self.remove.setToolTip("Remove image")
        self.remove.clicked.connect(lambda: self.removed.emit(self))
        line.addWidget(self.thumb)
        line.addWidget(self.alt, 1)
        line.addWidget(self.remove)


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
        self.rows = []
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
        self.stem = StemEdit()
        self.stem.image_pasted.connect(self.add_image)
        self.stem.file_dropped.connect(self.add_image_file)
        self.stem.setPlaceholderText(r"\( … \)")
        self.stem.setMaximumHeight(135)
        body.addWidget(host.Field("Question", self.stem))
        self.media_box = QVBoxLayout()
        body.addLayout(self.media_box)
        self.image_rows = []
        row = QHBoxLayout()
        self.image_button = host.Button("+ Add image…", compact=True)
        self.image_button.clicked.connect(self.attach_image)
        self.paste_button = host.Button("Paste image", compact=True)
        self.paste_button.clicked.connect(self.paste_image)
        row.addWidget(self.image_button)
        row.addWidget(self.paste_button)
        row.addWidget(host.caption("or paste or drop one into the question"))
        row.addStretch()
        body.addLayout(row)
        # Multiple choice: the answers. Open answer: a model answer and grading notes instead.
        self.choice_box = Box()
        choice = QVBoxLayout(self.choice_box)
        choice.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(host.Label("Answers", "heading"))
        self.hint = host.caption("")
        head.addStretch()
        head.addWidget(self.hint)
        choice.addLayout(head)
        self.answers = QVBoxLayout()
        choice.addLayout(self.answers)
        self.add_button = host.Button("+ Add answer", compact=True)
        self.add_button.clicked.connect(lambda: self.add_option())
        choice.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignLeft)
        body.addWidget(self.choice_box)
        self.open_box = Box()
        open_answer = QVBoxLayout(self.open_box)
        open_answer.setContentsMargins(0, 0, 0, 0)
        self.model_answer = QPlainTextEdit()
        self.model_answer.setPlaceholderText("What a full answer contains · " + PRIVATE.lower().rstrip("."))
        self.model_answer.setMaximumHeight(120)
        self.grading_notes = QPlainTextEdit()
        self.grading_notes.setPlaceholderText("How to award the points · " + PRIVATE.lower().rstrip("."))
        self.grading_notes.setMaximumHeight(85)
        open_answer.addWidget(host.Field("Model answer", self.model_answer))
        open_answer.addWidget(host.Field("Grading notes", self.grading_notes))
        body.addWidget(self.open_box)
        self.use = Switch(host, tuple((name, use) for use, name in USES.items()))
        self.kind = Switch(host, tuple((name, kind) for kind, name in KINDS.items()))
        self.difficulty = Switch(host, (("Easy", "easy"), ("Hard", "hard")), clearable=True)
        row = QHBoxLayout()
        row.addWidget(host.Field("Used for", self.use))
        self.kind_field = host.Field("Type", self.kind)
        row.addWidget(self.kind_field)
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
                       self.difficulty.changed, self.model_answer.textChanged, self.grading_notes.textChanged):
            signal.connect(self.touch)
        self.use.changed.connect(self.use_changed)
        self.kind.changed.connect(self.kind_changed)
        for chip in (self.ai_tag, self.draft_tag, self.source_bar):
            chip.hide()

    # -- state ---------------------------------------------------------------------------------------------

    def candidate(self):
        # Answers typed before switching to Open answer stay in the editor until it is left, but are not saved.
        is_open = self.kind.currentData() == "open"
        return replace(self.current, stem=self.stem.toPlainText(),
                       options=() if is_open else tuple(r.option() for r in self.rows),
                       objective=self.objective.text(), difficulty=self.difficulty.currentData(),
                       use=self.use.currentData(), sources=self.links, media=self.attachments,
                       assistant_note=self.assistant_note.toPlainText(), kind=self.kind.currentData(),
                       model_answer=self.model_answer.toPlainText() if is_open else "",
                       grading_notes=self.grading_notes.toPlainText() if is_open else "")

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
        for row in self.rows:
            self.answers.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.rows = []
        for option in item.options:
            self.add_option(option)
        self.objective.setText(item.objective)
        self.use.setCurrentData(item.use)
        self.kind.setCurrentData(item.kind)
        self.model_answer.setPlainText(item.model_answer)
        self.grading_notes.setPlainText(item.grading_notes)
        self.difficulty.setCurrentData(item.difficulty)
        self._loading = False
        self.editor.setEnabled(True)
        self.close_link_grid()
        self.update_header(item)
        self.draw_links()
        self.render_media()
        self.refresh_answers()
        self.baseline = self.candidate()
        self.refresh_footer()

    def update_header(self, item):
        ai_edit = item.ai_edit.kind == "ai"
        provenance = (f"{item.ai_edit.route} · {item.ai_edit.model}" if ai_edit
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
        first = self.slide_ref(item.changed_slides[0]) if item.changed_slides else None
        self.source_banner.setText(f"Slide {first.position} changed after this question was written." if first
                                   else "A linked slide changed after this question was written.")
        self.source_bar.setVisible(item.source_changed)
        self.source_banner.setVisible(item.source_changed)
        self.compare_button.setVisible(item.source_changed)
        self.still_button.setVisible(item.source_changed)
        self.refresh_footer()

    def refresh_footer(self):
        item = self.current
        buttons = (self.retire_button, self.restore_button, self.save_button, self.approve_button)
        if item is None:
            self.status_line.setText("")
            for button in buttons:
                button.setEnabled(False)
            return
        dirty, issues = self.dirty(), reviewed_issues(self.candidate(), self.answer_rules)
        status = item.status
        if status == "retired":
            issues = []
        # The answer rules can change after a question was reviewed.
        off_rules = status == "reviewed" and not dirty and question_issues(item, self.answer_rules)
        if issues and (dirty or status == "draft"):
            text = "Before marking reviewed: " + " · ".join(issues)
        elif off_rules:
            text = "Reviewed, but it no longer fits the answer rules: " + " · ".join(off_rules)
        elif dirty:
            text = "Unsaved edits" if self.new_item else "Unsaved edits · saving creates a new draft revision"
        elif status == "draft":
            text = "Draft · saved"
        elif status == "reviewed":
            text = "Reviewed · ready to use"
        else:
            text = "Retired · restore it as a draft to edit or review again"
        shown = bool(issues and (dirty or status == "draft") or off_rules)
        self.status_line.setText(text)
        self.status_line.setStyleSheet(f"color: {red_color(self)};" if shown else "")
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

    def rule(self):
        return self.answer_rules.of(self.use.currentData())

    def cap(self):
        return self.rule().max_options

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
        if self.rule().max_correct == 1:
            for other in self.rows:
                other.set_correct(other is row)
        else:
            row.set_correct(row.tick.isChecked())
        self.touch()

    def use_changed(self):
        if self._loading:
            return
        # A revision quiz is multiple choice; an exam question takes a type the deck's exam uses.
        kinds = self.answer_rules.kinds(self.use.currentData())
        self._loading = True
        if self.kind.currentData() not in kinds:
            self.kind.setCurrentData(kinds[0])
        self._loading = False
        self.kind_changed()

    def kind_changed(self):
        if self._loading:
            return
        if self.kind.currentData() == "choice":
            self._loading = True
            while len(self.rows) < (self.rule().min_options if self.use.currentData() == "elp" else 2):
                self.add_option()
            self._loading = False
        self.touch()

    def refresh_answers(self):
        use, kind = self.use.currentData(), self.kind.currentData()
        self.hint.setText(f"{USES[use]}: {describe(self.rule())}")
        self.choice_box.setVisible(kind != "open")
        self.open_box.setVisible(kind == "open")
        # The switch shows for an exam question, and for any question whose type its use does not allow.
        self.kind_field.setVisible(use == "elp" or kind not in self.answer_rules.kinds(use))
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
        if target == "draft" and self.current.status == "draft":
            return
        if target == "reviewed":
            issues = reviewed_issues(self.current, self.answer_rules)
            if issues:
                self.error("Before marking reviewed: " + " · ".join(issues))
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

    def draw_links(self):
        clear(self.link_box)
        for index, link in enumerate(self.links):
            row = Box()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            name = self.host.Button(self.slide_name(link.slide_id), compact=True)
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
        self.touch()

    def update_topic(self):
        topic = topic_of(self.candidate(), self.topics) if self.current is not None else None
        if topic is None:
            self.topic_label.setText("No topic")
        else:
            dot = f"<span style='color:{topic_color(topic.color, is_dark(self))}'>●</span>"
            self.topic_label.setText(f"Topic from first slide: {dot} {escape(topic.name)}")

    def remove_source(self, index, *_):
        self.links = tuple(s for i, s in enumerate(self.links) if i != index)
        self.render_links()

    def open_source(self, link, *_):
        host, deck = self.host, self.deck

        def done(ref, error):
            if isinstance(error, Unresolved):
                self.error("Source slide is missing or ambiguous. Link it again from the grid.")
            elif error:
                self.error(error)
            else:
                try:
                    host.open_slide(ref)
                except (OSError, ValueError, RuntimeError) as exc:
                    self.error(exc)
        host.run_in_background(lambda: SlideIndex(host, fresh_deck(deck)).find(link), done)

    def close_link_grid(self):
        self.link_grid.hide()
        self.link_grid_buttons = []
        clear(self.link_grid_layout)

    def toggle_link_grid(self):
        """The deck's slides as the window lists them; a slide is linked in the instructor view."""
        if self.link_grid.isVisibleTo(self.editor):
            self.close_link_grid()
            return
        self.close_link_grid()
        linked = {s.slide_id for s in self.links}
        for index, ref in enumerate(self.slides or ()):
            button = self.host.Button(f"{ref.position} · {slide_title(ref)}", compact=True)
            button.setEnabled(ref.id not in linked)
            button.clicked.connect(partial(self.link_slide_ref, ref))
            self.link_grid_buttons.append(button)
            self.link_grid_layout.addWidget(button, index // 2, index % 2)
        if not self.slides:
            self.link_grid_layout.addWidget(self.host.caption("No slides found in this deck yet."), 0, 0)
        self.link_grid.show()

    def link_slide_ref(self, ref, *_):
        host = self.host

        def done(link):
            self.close_link_grid()
            if not any(s.slide_id == link.slide_id for s in self.links):
                self.links += (link,)
                self.render_links()
        self.work(lambda: link_slide(host, ref, "instructor"), done)

    # -- images --------------------------------------------------------------------------------------------

    def render_media(self):
        for row in self.image_rows:
            self.media_box.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.image_rows = []
        for media in self.attachments:
            try:
                data = self.bank.read_media(media)
            except (OSError, BankError):
                data = None
            row = ImageRow(self.host, media, data)
            row.changed.connect(self.image_described)
            row.removed.connect(self.remove_image)
            self.image_rows.append(row)
            self.media_box.addWidget(row)
        self.touch()

    def add_image(self, data):
        """Store an image for this question and put the cursor on its description."""
        try:
            mime = image_type(data)
            if mime is None:
                raise ValueError(f"Use a {IMAGE_NAMES} image.")
            media = self.bank.store_media("image" + IMAGE_TYPES[mime][1][0], data)
        except (OSError, BankError, ValueError) as exc:
            self.error(exc)
            return
        if any(m.sha256 == media.sha256 for m in self.attachments):
            self.error("This image is already attached to this question.")
            return
        self.attachments += (media,)
        self.render_media()
        self.image_rows[-1].alt.setFocus()

    def add_image_file(self, path):
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            self.error(exc)
            return
        self.add_image(data)

    def attach_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Add image", "", IMAGE_FILTER)
        if path:
            self.add_image_file(path)

    def paste_image(self):
        image = QGuiApplication.clipboard().image()
        if image.isNull():
            self.error("The clipboard holds no image. Copy one first.")
            return
        self.add_image(png_bytes(image))

    def image_described(self, row):
        self.attachments = tuple(replace(m, alt=row.alt.text()) if m.name == row.name else m for m in self.attachments)
        self.touch()

    def remove_image(self, row):
        self.attachments = tuple(m for m in self.attachments if m.name != row.name)
        self.render_media()
