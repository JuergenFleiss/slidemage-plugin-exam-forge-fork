# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Question editor for the bank window."""
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from PySide6.QtGui import QImageReader, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QTableWidget,
)

from ..bank import BankError
from ..elp import check_elp
from ..model import Item, Option, check_phase1
from .source_picker import SourcePicker


def accent_color(widget, amber=False):
    dark = widget.palette().color(QPalette.ColorRole.Window).lightness() < 128
    return (("#fbbf24" if dark else "#8a5000") if amber
            else ("#c4b5fd" if dark else "#7040ac"))


def accent(widget, amber=False):
    # Only label foregrounds are explicit; surfaces and fields inherit the theme.
    # A local stylesheet also reaches the QLabel inside a host Chip.
    widget.setStyleSheet(f"color: {accent_color(widget, amber)};")


class ItemEditor:
    def build_editor(self, host, split):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        split.addWidget(scroll)
        self.editor = host.Page()
        scroll.setWidget(self.editor)
        body = self.editor.body
        body.setContentsMargins(14, 10, 14, 10)
        self.heading = QLabel("Pick a question or create a new one.")
        self.heading.setWordWrap(True)
        body.addWidget(self.heading)
        tags = QHBoxLayout()
        self.source_tag = host.Chip("Source changed")
        self.ai_tag = host.Chip("AI-assisted")
        self.draft_tag = host.Chip("AI draft")
        accent(self.draft_tag, True)
        accent(self.source_tag)
        accent(self.ai_tag, True)
        tags.addWidget(self.source_tag)
        tags.addWidget(self.ai_tag)
        tags.addWidget(self.draft_tag)
        tags.addStretch()
        body.addLayout(tags)
        self.source_banner = QLabel("A source changed or could not be checked. Check the question and its answers.")
        self.source_banner.setWordWrap(True)
        accent(self.source_banner)
        body.addWidget(self.source_banner)
        self.stem = QPlainTextEdit()
        self.stem.setPlaceholderText(r"Plain text; inline math: \( ... \)")
        self.stem.setMaximumHeight(135)
        body.addWidget(host.Field("Question text", self.stem))
        self.assistant_note = QPlainTextEdit()
        self.assistant_note.setMaximumHeight(85)
        self.assistant_note.setPlaceholderText("Shared stem, exclusions or fixed option order. This appears in the exam file; do not put answers here.")
        body.addWidget(host.Field("Teaching assistant note · appears in the exam file", self.assistant_note))
        self.options = QTableWidget(0, 3)
        self.options.setHorizontalHeaderLabels(["Correct", "Answer", "Rationale"])
        self.options.horizontalHeader().setStretchLastSection(True)
        self.options.setColumnWidth(0, 65)
        self.options.setColumnWidth(1, 230)
        self.options.setMinimumHeight(195)
        body.addWidget(host.Field("Answer options · revision: one correct; ELP: five options, one to four correct", self.options))
        row = QHBoxLayout()
        add = host.Button("Add option", compact=True)
        add.clicked.connect(lambda: self.add_option())
        remove = host.Button("Remove option", compact=True)
        remove.clicked.connect(lambda: self.options.removeRow(self.options.currentRow()))
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        body.addLayout(row)
        self.objective, self.topic, self.difficulty = QLineEdit(), QLineEdit(), QLineEdit()
        self.use = QComboBox()
        self.use.addItem("Revision quiz", "revision")
        self.use.addItem("ELP exam", "elp")
        for label, field in (("Learning objective", self.objective), ("Topic", self.topic),
                             ("Difficulty", self.difficulty), ("Use", self.use)):
            body.addWidget(host.Field(label, field))
        self.source_list = QListWidget()
        self.source_list.setMaximumHeight(90)
        body.addWidget(host.Field("Source slides · view and language", self.source_list))
        row = QHBoxLayout()
        for text, action in (("Add slide…", self.pick_source), ("Open slide", self.open_source),
                             ("Remove source", self.remove_source)):
            button = host.Button(text, compact=True)
            button.clicked.connect(action)
            row.addWidget(button)
        body.addLayout(row)
        self.media_list = QListWidget()
        self.media_list.setMaximumHeight(85)
        body.addWidget(host.Field("Images (appended to the question)", self.media_list))
        row = QHBoxLayout()
        for text, action in (("Attach image…", self.attach_image), ("Edit alt text…", self.edit_alt),
                             ("Remove image", self.remove_image)):
            button = host.Button(text, compact=True)
            button.clicked.connect(action)
            row.addWidget(button)
        body.addLayout(row)
        row = QHBoxLayout()
        self.approve_button = host.Button("Mark reviewed", "primary")
        self.approve_button.clicked.connect(lambda: self.change_status("reviewed"))
        self.retire_button = host.Button("Retire question")
        self.retire_button.clicked.connect(lambda: self.change_status("retired"))
        self.restore_button = host.Button("Return to draft")
        self.restore_button.clicked.connect(lambda: self.change_status("draft"))
        self.save_button = host.Button("Save draft")
        self.save_button.clicked.connect(self.save)
        for button in (self.approve_button, self.retire_button, self.restore_button, self.save_button):
            row.addWidget(button)
        body.addLayout(row)
        body.addStretch()
    def candidate(self):
        options = []
        for row in range(self.options.rowCount()):
            correct = self.options.cellWidget(row, 0)
            options.append(Option(correct.property("option_id"), self.options.cellWidget(row, 1).toPlainText(),
                                  correct.isChecked(), self.options.cellWidget(row, 2).toPlainText()))
        return replace(self.current, stem=self.stem.toPlainText(), options=tuple(options),
                       objective=self.objective.text(), topic=self.topic.text(), difficulty=self.difficulty.text(),
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

    def populate(self, item, new=False):
        self.current, self.new_item = item, new
        self.links, self.attachments = item.sources, item.media
        self.stem.setPlainText(item.stem)
        self.assistant_note.setPlainText(item.assistant_note)
        self.options.setRowCount(0)
        for option in item.options:
            self.add_option(option)
        self.objective.setText(item.objective)
        self.topic.setText(item.topic)
        self.difficulty.setText(item.difficulty)
        self.use.setCurrentIndex(self.use.findData(item.use))
        self.editor.setEnabled(True)
        self.update_header(item)
        self.render_links()
        self.render_media()
        self.baseline = self.candidate()

    def update_header(self, item):
        self.heading.setText(f"Question {item.id} · Revision {item.revision} · {item.status.title()}\n"
                             "Saving edits creates a new draft revision.")
        self.source_tag.setVisible(item.source_changed)
        self.source_banner.setVisible(item.source_changed)
        self.ai_tag.setVisible(item.origin.kind == "ai")
        self.draft_tag.setVisible(item.origin.kind == "ai" and item.status == "draft")
        self.ai_tag.setToolTip(f"{item.origin.route} · {item.origin.model}")
        self.approve_button.setEnabled(item.status != "retired")
        self.approve_button.setText("Review again" if item.status == "reviewed" else "Mark reviewed")
        self.retire_button.setEnabled(item.status == "reviewed")
        self.restore_button.setEnabled(item.status == "retired")

    def add_option(self, option=None):
        option = option or Option(uuid4().hex, "")
        row = self.options.rowCount()
        self.options.insertRow(row)
        check = QCheckBox()
        check.setAccessibleName(f"Option {row + 1} is correct")
        check.setProperty("option_id", option.id)
        check.setChecked(option.correct)
        self.options.setCellWidget(row, 0, check)
        for column, text in ((1, option.text), (2, option.rationale)):
            field = QPlainTextEdit(text)
            field.setTabChangesFocus(True)
            self.options.setCellWidget(row, column, field)
        self.options.setRowHeight(row, 72)

    def new_question(self, *_):
        if not self.may_leave():
            return False
        self.populate(Item(uuid4().hex, 1, options=(Option(uuid4().hex, ""), Option(uuid4().hex, ""))), True)
        return True

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
        self.notice.setText(f"Saved revision {saved.revision} as Draft.")
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
            errors = check_elp(self.current) if self.current.use == "elp" else check_phase1(self.current)
            if not self.current.stem.strip() or len(self.current.options) < 2 or any(not o.text.strip() for o in self.current.options):
                errors.append("A question needs text and at least two nonempty answers.")
            if errors:
                self.error("\n".join(errors))
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

    def render_links(self):
        self.source_list.clear()
        self.source_list.addItems([f"{s.slide_id} · {s.version or 'current'} · {s.view} · {s.language}" for s in self.links])

    def pick_source(self):
        from .work import fresh_deck
        deck = self.deck
        self.set_busy(True)

        def ready(snapshot, error):
            self.set_busy(False)
            if error:
                self.error(error)
                return
            picker = SourcePicker(self.host, snapshot, self)
            if picker.exec() and picker.link not in self.links:
                self.links += (picker.link,)
                self.render_links()
        self.host.run_in_background(lambda: fresh_deck(deck), ready)

    def remove_source(self):
        row = self.source_list.currentRow()
        self.links = tuple(s for i, s in enumerate(self.links) if i != row)
        self.render_links()

    def open_source(self):
        row = self.source_list.currentRow()
        if not 0 <= row < len(self.links):
            return
        from .work import fresh_deck
        link, deck = self.links[row], self.deck

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
                self.error("Source slide is missing or ambiguous. Use Add slide to choose it.")
        self.host.run_in_background(work, done)

    def render_media(self):
        self.media_list.clear()
        self.media_list.addItems([f"{m.name[:16]}… · {m.alt or 'No alt text'}" for m in self.attachments])

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

