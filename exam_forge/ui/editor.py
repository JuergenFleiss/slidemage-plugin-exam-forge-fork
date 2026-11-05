# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Question editor for the bank window."""
from dataclasses import replace
from functools import partial
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ..bank import BankError
from ..model import (
    IMAGE_NAMES,
    IMAGE_SUFFIXES,
    IMAGE_TYPES,
    KINDS,
    STATUS,
    USES,
    Item,
    Option,
    ai_draft,
    image_type,
    topic_of,
)
from ..rules import describe, question_issues
from ..sources import SlideIndex, Unresolved, fresh_deck, link_slide, rebaseline_links
from .kit import (
    Box,
    Fold,
    GrowingEdit,
    Switch,
    clear,
    confirm,
    dot,
    field,
    glyph_icon,
    mix,
    named,
    restyle,
    scroll_area,
    slide_title,
    source_details,
    tone,
)

PRIVATE = "Only in the private key."
IMAGE_FILTER = f"Images ({' '.join('*' + suffix for suffix in IMAGE_SUFFIXES)})"
THUMB_WIDTH, THUMB_HEIGHT = 160, 100
TA_WARNING = "Shared stem, exclusions or fixed option order. This appears in the exam file; do not put answers here."


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


def png_bytes(image):
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


class StemEdit(GrowingEdit):
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
        self.alt = field(QLineEdit(media.alt))
        self.alt.setPlaceholderText("Describe the image")
        self.alt.setToolTip("Shown under the image in exams, so do not put the answer in it.")
        self.alt.setAccessibleName("Image description")
        self.alt.textChanged.connect(lambda: self.changed.emit(self))
        self.remove = host.IconButton("close", "Remove image")
        self.remove.clicked.connect(lambda: self.removed.emit(self))
        line.addWidget(self.thumb)
        line.addWidget(self.alt, 1)
        line.addWidget(self.remove)


class Tick(QAbstractButton):
    """The round button that marks an answer correct: filled with the theme's ok colour and a ✓ when it is, an empty
    ring when not, with a faint ✓ while the pointer is over an empty one. The ✓, not the colour, carries the meaning."""

    CHECK = ((5, 12.5), (9.5, 17), (19, 7.5))   # a ✓ in a 24 px box

    def __init__(self, host):
        super().__init__()
        self.host = host
        self.setCheckable(True)
        self.setFixedSize(36, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)    # a click does not draw the focus ring
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)  # repainted as the pointer comes and goes, for the faint ✓

    def paintEvent(self, event):
        t = self.host.theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        circle = QRectF(3, 2, 30, 30)
        if self.hasFocus():
            painter.setPen(QPen(QColor(t.focus), 2))
            painter.drawEllipse(circle.adjusted(-1.5, -1.5, 1.5, 1.5))
        if self.isChecked():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.ok))
            painter.drawEllipse(circle)
            glyph = t.surface if t.dark else t.on_solid
        else:
            painter.setPen(QPen(QColor(t.text_4), 1.5))
            painter.setBrush(QColor(t.surface))
            painter.drawEllipse(circle.adjusted(0.75, 0.75, -0.75, -0.75))
            if not (self.underMouse() and self.isEnabled()):
                return
            glyph = mix(t.ok, t.surface, 0.45)
        scale, left, top = 15 / 24, circle.center().x() - 7.5, circle.center().y() - 7.5
        pen = QPen(QColor(glyph), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline([QPointF(left + x * scale, top + y * scale) for x, y in self.CHECK])


class AnswerRow(QFrame):
    """One answer as a card: tick | answer | remove, and the rationale under the answer. A correct answer's card is
    tinted green (window_sheet)."""
    changed = Signal()
    ticked = Signal(object)
    removed = Signal(object)

    def __init__(self, host, option):
        super().__init__()
        self.setObjectName("efAnswer")
        self.option_id = option.id
        grid = QGridLayout(self)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)
        grid.setColumnStretch(1, 1)
        # Made in the order Tab visits them: tick, answer, rationale, remove.
        self.tick = Tick(host)
        self.tick.clicked.connect(lambda: self.ticked.emit(self))
        self.text = GrowingEdit(option.text)
        self.text.setPlaceholderText("Answer")
        self.why = GrowingEdit(option.rationale)
        self.why.setObjectName("efWhy")
        self.why.setPlaceholderText("Why it is right or wrong · only in the private key")
        self.remove = host.IconButton("close", "Remove answer")
        self.remove.clicked.connect(lambda: self.removed.emit(self))
        grid.addWidget(self.tick, 0, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.text, 0, 1, Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(self.remove, 0, 2, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.why, 1, 1)
        self.set_correct(option.correct)
        self.text.textChanged.connect(self.changed)
        self.why.textChanged.connect(self.changed)

    def correct(self):
        return self.tick.isChecked()

    def set_correct(self, on):
        """The tick and the card's tint together."""
        self.tick.setChecked(on)
        restyle(self, "correct", on)

    def number(self, number):
        """Names for screen readers, by the answer's place in the list."""
        self.tick.setAccessibleName(f"Option {number} is correct")
        self.text.setAccessibleName(f"Answer {number}")
        self.why.setAccessibleName(f"Rationale for answer {number}")
        self.remove.setToolTip(f"Remove answer {number}")

    def option(self):
        return Option(self.option_id, self.text.toPlainText(), self.correct(), self.why.toPlainText())


class ItemEditor(QWidget):
    """The question editor. The window holds the bank, deck, rules, topics and slides and runs the editor's work; the
    editor tells its tab when it stored a question, when it closes, and which question to compare."""

    saved = Signal(object)    # Item: a new revision or status is stored.
    closed = Signal()         # The back link was pressed and may_leave agreed.
    compare = Signal(object)  # Item: Compare was pressed.

    def __init__(self, window):
        super().__init__()
        self.window, self.host = window, window.host
        host = self.host
        self.current = self.baseline = None
        self.new_item = False
        self.links = self.attachments = ()
        self._loading = False
        self.rows = []
        self.link_grid_buttons = []
        page = host.Page()
        page.body.setContentsMargins(0, 0, 0, 0)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(page)
        card = host.Card()
        page.body.addWidget(card)
        outer = card.layout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        content = host.Page()
        outer.addWidget(scroll_area(content), 1)
        body = content.body
        body.setContentsMargins(20, 12, 20, 16)
        body.setSpacing(20)
        # One row: the back link on the left, the question's chips on the right.
        self.back_button = host.Button("", "ghost", compact=True)
        self.back_button.clicked.connect(self.back)
        self.back_button.hide()
        tags = QHBoxLayout()
        tags.setSpacing(6)
        tags.addWidget(self.back_button)
        tags.addStretch()
        self.status_chip = host.Chip("Draft")
        self.rev_chip = host.Chip("rev 1", "muted")
        self.rev_chip.setProperty("mono", True)
        self.ai_tag = host.Chip("AI-assisted", "muted")
        self.draft_tag = tone(host.Chip("AI draft", icon="auto_awesome"), "amber")
        for chip in (self.draft_tag, self.ai_tag, self.status_chip, self.rev_chip):
            tags.addWidget(chip)
        body.addLayout(tags)
        self.source_banner = tone(host.Label("A linked slide changed after this question was written."), "violet")
        self.source_banner.setWordWrap(True)
        self.compare_button = host.Button("Compare", compact=True)
        self.compare_button.clicked.connect(lambda: self.compare.emit(self.current))
        self.still_button = host.Button("Still correct", compact=True)
        self.still_button.clicked.connect(self.still_correct)
        self.source_bar = Box()
        bar = QHBoxLayout(self.source_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(self.source_banner, 1)
        bar.addWidget(self.compare_button)
        bar.addWidget(self.still_button)
        body.addWidget(self.source_bar)
        self.stem = field(StemEdit(lines=3, most=10))
        self.stem.image_pasted.connect(self.add_image)
        self.stem.file_dropped.connect(self.add_image_file)
        self.stem.setPlaceholderText(r"\( … \)")
        question = QVBoxLayout()
        question.setSpacing(10)
        body.addLayout(question)
        question.addWidget(host.Field("Question", self.stem))
        self.media_box = QVBoxLayout()
        question.addLayout(self.media_box)
        self.image_rows = []
        row = QHBoxLayout()
        self.image_button = host.Button("+ Add image…", compact=True)
        self.image_button.clicked.connect(self.attach_image)
        self.paste_button = host.Button("Paste image", compact=True)
        self.paste_button.clicked.connect(self.paste_image)
        row.addWidget(self.image_button)
        row.addWidget(self.paste_button)
        caption = host.caption("or paste or drop one into the question")
        caption.setWordWrap(True)
        row.addWidget(caption, 1)   # it takes the rest of the line, so it wraps only in a narrow window
        question.addLayout(row)
        # Multiple choice: the answers. Open answer: a model answer and grading notes instead.
        self.choice_box = Box()
        choice = QVBoxLayout(self.choice_box)
        choice.setContentsMargins(0, 0, 0, 0)
        choice.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(named(QLabel("Answers"), "efLabel"), 0, Qt.AlignmentFlag.AlignBottom)
        self.hint = host.caption("")
        self.hint.setWordWrap(True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        head.addWidget(self.hint, 1)
        choice.addLayout(head)
        self.answers = QVBoxLayout()
        self.answers.setSpacing(8)
        choice.addLayout(self.answers)
        self.add_button = host.Button("Add answer", "ghost", compact=True)
        self.add_button.clicked.connect(lambda: self.add_option())
        choice.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignLeft)
        body.addWidget(self.choice_box)
        self.open_box = Box()
        open_answer = QVBoxLayout(self.open_box)
        open_answer.setContentsMargins(0, 0, 0, 0)
        open_answer.setSpacing(14)
        self.model_answer = field(GrowingEdit(lines=4, most=10))
        self.model_answer.setPlaceholderText("What a full answer contains · " + PRIVATE.lower().rstrip("."))
        self.grading_notes = field(GrowingEdit(lines=3, most=8))
        self.grading_notes.setPlaceholderText("How to award the points · " + PRIVATE.lower().rstrip("."))
        open_answer.addWidget(host.Field("Model answer", self.model_answer))
        open_answer.addWidget(host.Field("Grading notes", self.grading_notes))
        body.addWidget(self.open_box)
        self.use = Switch(host, tuple((name, use) for use, name in USES.items()))
        self.kind = Switch(host, tuple((name, kind) for kind, name in KINDS.items()))
        self.difficulty = Switch(host, (("Easy", "easy"), ("Hard", "hard")), clearable=True)
        row = QHBoxLayout()
        row.setSpacing(24)
        row.addWidget(host.Field("Used for", self.use))
        self.kind_field = host.Field("Type", self.kind)
        row.addWidget(self.kind_field)
        row.addWidget(host.Field("Difficulty", self.difficulty))
        body.addLayout(row)
        self.objective = field(QLineEdit())
        body.addWidget(host.Field("Learning objective", self.objective))
        head = QHBoxLayout()
        head.setSpacing(4)
        head.addWidget(named(QLabel("Linked slides"), "efLabel"))
        head.addStretch()
        self.topic_label, self.topic_dot, self.topic_name = host.caption(""), dot(), host.caption("")
        for part in (self.topic_label, self.topic_dot, self.topic_name):
            head.addWidget(part)
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
        self.assistant_note = field(GrowingEdit(lines=2, most=6))
        self.assistant_note.setPlaceholderText(TA_WARNING)
        warning = host.caption(TA_WARNING)
        warning.setWordWrap(True)
        self.note_fold.box.addWidget(warning)
        self.note_fold.box.addWidget(self.assistant_note)
        body.addWidget(self.note_fold)
        body.addStretch()
        bar = named(QFrame(), "efFooter")   # a hairline above it
        footer = QHBoxLayout(bar)
        footer.setContentsMargins(20, 10, 20, 12)
        self.status_line = host.Label("", "secondary")
        self.status_line.setWordWrap(True)
        footer.addWidget(self.status_line, 1)
        self.retire_button = host.Button("Retire", "ghost")
        self.retire_button.clicked.connect(lambda: self.change_status("retired"))
        self.restore_button = host.Button("Restore as draft")
        self.restore_button.clicked.connect(lambda: self.change_status("draft"))
        self.save_button = host.Button("Save draft")
        self.save_button.clicked.connect(self.save)
        self.approve_button = host.Button("Mark reviewed", "primary")
        self.approve_button.clicked.connect(lambda: self.change_status("reviewed"))
        for button in (self.retire_button, self.restore_button, self.save_button, self.approve_button):
            footer.addWidget(button)
        outer.addWidget(bar)
        for signal in (self.stem.textChanged, self.objective.textChanged, self.assistant_note.textChanged,
                       self.difficulty.changed, self.model_answer.textChanged, self.grading_notes.textChanged):
            signal.connect(self.touch)
        self.use.changed.connect(self.use_changed)
        self.kind.changed.connect(self.kind_changed)
        for chip in (self.ai_tag, self.draft_tag, self.source_bar):
            chip.hide()
        self.setEnabled(False)

    def retheme(self, t):
        """Draw the icons in the theme's colours, and the ticks again."""
        self.back_button.setIcon(glyph_icon("chevron_left", t.text, 18))
        self.add_button.setIcon(glyph_icon("add", t.text_2))
        self.approve_button.setIcon(glyph_icon("check", t.on_inverse))
        for row in self.rows:
            row.tick.update()

    # -- state ---------------------------------------------------------------------------------------------

    def candidate(self):
        # Answers typed before switching to Open answer stay in the editor until it is left, but are not saved.
        is_open = self.kind.value() == "open"
        return replace(self.current, stem=self.stem.toPlainText(),
                       options=() if is_open else tuple(r.option() for r in self.rows),
                       objective=self.objective.text(), difficulty=self.difficulty.value(),
                       use=self.use.value(), sources=self.links, media=self.attachments,
                       assistant_note=self.assistant_note.toPlainText(), kind=self.kind.value(),
                       model_answer=self.model_answer.toPlainText() if is_open else "",
                       grading_notes=self.grading_notes.toPlainText() if is_open else "")

    def dirty(self):
        return self.current is not None and (self.new_item or self.candidate() != self.baseline)

    def may_leave(self):
        if self.window.busy:
            self.window.error("Wait for the current operation to finish.")
            return False
        if not self.dirty():
            return True
        answer = QMessageBox.question(self, "Unsaved question", "Save this question before continuing?",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                      QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def drop(self):
        """Close the question without asking; the caller has already asked."""
        self.current = self.baseline = None
        self.new_item = False
        self.links = self.attachments = ()
        self.setEnabled(False)

    def set_back_label(self, text):
        self.back_button.setText(text)
        self.back_button.setVisible(bool(text))

    def back(self):
        if self.may_leave():
            self.closed.emit()

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
        clear(self.answers)
        self.rows = []
        for option in item.options:
            self.add_option(option)
        self.objective.setText(item.objective)
        self.use.set_value(item.use)
        self.kind.set_value(item.kind)
        self.model_answer.setPlainText(item.model_answer)
        self.grading_notes.setPlainText(item.grading_notes)
        self.difficulty.set_value(item.difficulty)
        self._loading = False
        self.setEnabled(True)
        self.close_link_grid()
        self.update_header(item)
        self.draw_links()
        self.render_media()
        self.refresh_answers()
        self.baseline = self.candidate()
        self.refresh_footer()

    def refresh(self):
        """The deck's answer rules or slides changed: show what they mean for the open question."""
        if self.current is not None:
            self.draw_links()
            self.update_header(self.current)
            self.refresh_answers()

    def take_check(self, latest):
        """A source check stored which slides changed. That is not revision content: every typed field stays."""
        if self.current is not None and not self.new_item and latest.id == self.current.id:
            self.current = replace(self.current, changed_slides=latest.changed_slides)
            self.baseline = replace(self.baseline, changed_slides=latest.changed_slides)
            self.update_header(self.current)

    def update_header(self, item):
        # Like the cards: an AI draft until it is reviewed, AI-assisted for good after that.
        source = item.ai_edit if item.ai_edit.kind == "ai" else item.origin
        self.ai_tag.setToolTip(f"{source.route} · {source.model}")
        self.draft_tag.setToolTip(f"{source.route} · {source.model}")
        self.draft_tag.setVisible(ai_draft(item))
        self.ai_tag.setVisible(source.kind == "ai" and not ai_draft(item))
        name, chip_tone = STATUS[item.status]
        set_chip(self.status_chip, "New question" if self.new_item else name, chip_tone)
        set_chip(self.rev_chip, f"rev {item.revision}")
        self.rev_chip.setVisible(not self.new_item)
        first = self.window.slide_ref(item.changed_slides[0]) if item.changed_slides else None
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
        dirty, issues = self.dirty(), reviewed_issues(self.candidate(), self.window.answer_rules)
        status = item.status
        if status == "retired":
            issues = []
        # The answer rules can change after a question was reviewed.
        off_rules = status == "reviewed" and not dirty and question_issues(item, self.window.answer_rules)
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
        tone(self.status_line, "error" if shown else "")
        reviewed_clean = status == "reviewed" and not dirty and not self.new_item
        self.retire_button.setVisible(reviewed_clean)
        self.retire_button.setEnabled(reviewed_clean)
        self.restore_button.setVisible(status == "retired")
        self.restore_button.setEnabled(status == "retired")
        self.save_button.setEnabled(dirty)
        self.approve_button.setText("Reviewed" if reviewed_clean else "Mark reviewed")
        self.approve_button.setEnabled(status != "retired" and not issues and not reviewed_clean)

    # -- answers -------------------------------------------------------------------------------------------

    def rule(self):
        return self.window.answer_rules.of(self.use.value())

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
        kinds = self.window.answer_rules.kinds(self.use.value())
        self._loading = True
        if self.kind.value() not in kinds:
            self.kind.set_value(kinds[0])
        self._loading = False
        self.kind_changed()

    def kind_changed(self):
        if self._loading:
            return
        if self.kind.value() == "choice":
            self._loading = True
            while len(self.rows) < (self.rule().min_options if self.use.value() == "exam" else 2):
                self.add_option()
            self._loading = False
        self.touch()

    def refresh_answers(self):
        use, kind = self.use.value(), self.kind.value()
        self.hint.setText(f"{USES[use]}: {describe(self.rule())}")
        self.choice_box.setVisible(kind != "open")
        self.open_box.setVisible(kind == "open")
        # The switch shows for an exam question, and for any question whose type its use does not allow.
        self.kind_field.setVisible(use == "exam" or kind not in self.window.answer_rules.kinds(use))
        self.add_button.setEnabled(len(self.rows) < self.cap())
        self.add_button.setVisible(len(self.rows) < self.cap())
        for number, row in enumerate(self.rows, 1):
            row.number(number)

    def new_question(self, *_):
        if not self.may_leave():
            return False
        self.populate(Item(uuid4().hex, 1, options=(Option(uuid4().hex, ""), Option(uuid4().hex, ""))), True)
        return True

    # -- lifecycle -----------------------------------------------------------------------------------------

    def save(self, *_):
        if self.current is None or self.window.busy:
            return False
        if not self.dirty():
            return True
        item = replace(self.candidate(), status="draft")
        try:
            saved = self.window.bank.create(item) if self.new_item else self.window.bank.save(item)
        except (OSError, BankError, ValueError) as exc:
            self.window.error(exc)
            return False
        self.populate(saved)
        self.saved.emit(saved)
        self.window.toast(f"Saved revision {saved.revision} as Draft.")
        return True

    def change_status(self, target):
        if self.current is None or not self.save():
            return
        if target == "draft" and self.current.status == "draft":
            return
        if target == "reviewed":
            issues = reviewed_issues(self.current, self.window.answer_rules)
            if issues:
                self.window.error("Before marking reviewed: " + " · ".join(issues))
                return
        item = self.current

        def proceed(report):
            checked = report.items[0]
            if checked.item.revision != item.revision:
                self.window.error("Question changed during the check. Your editor is intact; save or reopen before continuing.")
                return
            if checked.source_changed:
                details = "\n".join("• " + line for line in source_details(checked))
                if not confirm(self, "Source changed", f"{item.stem}\n\n{details}\n\nContinue and mark this question "
                               f"{target}? The Source changed flag remains.", warning=True):
                    return
            try:
                updated = self.window.bank.set_status(item.id, item.revision, target)
                self.populate(updated)
                self.saved.emit(updated)
            except (OSError, BankError, ValueError) as exc:
                self.window.error(exc)

        self.window.check_sources(then=proceed, item_ids=[item.id])

    def still_correct(self):
        """Accept the slides as they are now; the status is kept and the flag cleared."""
        if self.current is None or self.new_item or self.window.busy:
            return
        if self.dirty():
            if not self.may_leave():
                return
            if self.dirty():  # Discarded: show the stored question again.
                self.populate(self.current)
        item, host, deck, bank = self.current, self.host, self.window.deck, self.window.bank

        def work():
            return bank.rebaseline(item.id, item.revision, rebaseline_links(host, fresh_deck(deck), item))

        def done(updated):
            self.populate(updated)
            self.saved.emit(updated)
            self.window.toast("Kept · matches the current slide.")
        self.window.work(work, done)

    # -- linked slides -------------------------------------------------------------------------------------

    def draw_links(self):
        clear(self.link_box)
        for index, link in enumerate(self.links):
            row = Box()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            name = self.host.Button(self.window.slide_name(link.slide_id), compact=True)
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

    def add_link(self, link):
        if not any(s.slide_id == link.slide_id for s in self.links):
            self.links += (link,)
            self.render_links()

    def update_topic(self):
        topic = topic_of(self.candidate(), self.window.topics) if self.current is not None else None
        self.topic_label.setText("Topic from first slide:" if topic else "No topic")
        dot(topic, self.topic_dot)
        self.topic_name.setText(topic.name if topic else "")

    def remove_source(self, index, *_):
        self.links = tuple(s for i, s in enumerate(self.links) if i != index)
        self.render_links()

    def open_source(self, link, *_):
        host, deck = self.host, self.window.deck

        def done(ref, error):
            if isinstance(error, Unresolved):
                self.window.error("Source slide is missing or ambiguous. Link it again from the grid.")
            elif error:
                self.window.error(error)
            else:
                try:
                    host.open_slide(ref)
                except (OSError, ValueError, RuntimeError) as exc:
                    self.window.error(exc)
        host.run_in_background(lambda: SlideIndex(host, fresh_deck(deck)).find(link), done)

    def close_link_grid(self):
        self.link_grid.hide()
        self.link_grid_buttons = []
        clear(self.link_grid_layout)

    def toggle_link_grid(self):
        """The deck's slides as the window lists them; a slide is linked in the instructor view."""
        if self.link_grid.isVisibleTo(self):
            self.close_link_grid()
            return
        self.close_link_grid()
        linked = {s.slide_id for s in self.links}
        for index, ref in enumerate(self.window.slides or ()):
            button = self.host.Button(f"{ref.position} · {slide_title(ref)}", compact=True)
            button.setEnabled(ref.id not in linked)
            button.clicked.connect(partial(self.link_slide_ref, ref))
            self.link_grid_buttons.append(button)
            self.link_grid_layout.addWidget(button, index // 2, index % 2)
        if not self.window.slides:
            self.link_grid_layout.addWidget(self.host.caption("No slides found in this deck yet."), 0, 0)
        self.link_grid.show()

    def link_slide_ref(self, ref, *_):
        host = self.host

        def done(link):
            self.close_link_grid()
            self.add_link(link)
        self.window.work(lambda: link_slide(host, ref, "instructor"), done)

    # -- images --------------------------------------------------------------------------------------------

    def render_media(self):
        clear(self.media_box)
        self.image_rows = []
        for media in self.attachments:
            try:
                data = self.window.bank.read_media(media)
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
            media = self.window.bank.store_media("image" + IMAGE_TYPES[mime][1][0], data)
        except (OSError, BankError, ValueError) as exc:
            self.window.error(exc)
            return
        if any(m.sha256 == media.sha256 for m in self.attachments):
            self.window.error("This image is already attached to this question.")
            return
        self.attachments += (media,)
        self.render_media()
        self.image_rows[-1].alt.setFocus()

    def add_image_file(self, path):
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            self.window.error(exc)
            return
        self.add_image(data)

    def attach_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Add image", "", IMAGE_FILTER)
        if path:
            self.add_image_file(path)

    def paste_image(self):
        image = QGuiApplication.clipboard().image()
        if image.isNull():
            self.window.error("The clipboard holds no image. Copy one first.")
            return
        self.add_image(png_bytes(image))

    def image_described(self, row):
        self.attachments = tuple(replace(m, alt=row.alt.text()) if m.name == row.name else m for m in self.attachments)
        self.touch()

    def remove_image(self, row):
        self.attachments = tuple(m for m in self.attachments if m.name != row.name)
        self.render_media()
