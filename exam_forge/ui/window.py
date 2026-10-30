# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 1 bank window. Only host worker completions touch widgets."""
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QImageReader, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..bank import BankError, ConflictError
from ..model import Item, Option, check_phase1
from ..moodle_xml import export_moodle_xml
from ..sources import check_bank
from .source_picker import SourcePicker


@dataclass(frozen=True)
class DeckSnapshot:
    name: str
    path: Path
    text: str
    groups: tuple[str, ...]


def fresh_deck(deck):
    """The API has no deck refresh call; reread its text before resolving sources."""
    return DeckSnapshot(deck.name, deck.path, deck.path.read_text(encoding="utf-8"), deck.groups)


def accent_color(widget, amber=False):
    dark = widget.palette().color(QPalette.ColorRole.Window).lightness() < 128
    return (("#fbbf24" if dark else "#8a5000") if amber
            else ("#c4b5fd" if dark else "#7040ac"))


def accent(widget, amber=False):
    # Only label foregrounds are explicit; surfaces and fields inherit the theme.
    # A local stylesheet also reaches the QLabel inside a host Chip.
    widget.setStyleSheet(f"color: {accent_color(widget, amber)};")


class BankWindow(QWidget):
    def __init__(self, host, deck, bank, changed):
        super().__init__(None, Qt.WindowType.Window)
        self.host, self.deck, self.bank, self.changed = host, deck, bank, changed
        self.current = None
        self.new_item = False
        self.baseline = None
        self.items = []
        self.links = ()
        self.attachments = ()
        self.busy = False
        self.idle_callbacks = []
        self.setWindowTitle(f"Exam Forge · {deck.name}")
        self.resize(1120, 790)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(host.Chip("Bank"))  # Space remains for future phases.
        self.folder_label = QLabel(str(bank.folder))
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(self.folder_label, 1)
        self.check_button = host.Button("Check now")
        self.check_button.clicked.connect(self.check_sources)
        top.addWidget(self.check_button)
        self.export_button = host.Button("Export Moodle XML…")
        self.export_button.setToolTip("Exports reviewed revision-quiz questions matching the list filters. ELP questions remain private.")
        self.export_button.clicked.connect(self.export)
        top.addWidget(self.export_button)
        layout.addLayout(top)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        split = QSplitter()
        layout.addWidget(split, 1)
        left = host.Page()
        left.body.setContentsMargins(0, 0, 10, 0)
        split.addWidget(left)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search questions")
        left.body.addWidget(self.search)
        new = host.Button("New question", "primary")
        new.clicked.connect(self.new_question)
        left.body.addWidget(new)
        self.status_filter = QComboBox()
        for text, value in (("All statuses", ""), ("Draft", "draft"), ("Reviewed", "reviewed"), ("Retired", "retired")):
            self.status_filter.addItem(text, value)
        left.body.addWidget(host.Field("Status", self.status_filter))
        filters = QHBoxLayout()
        self.source_filter = host.Button("Source changed", compact=True)
        self.ai_filter = host.Button("AI draft", compact=True)
        for button, amber in ((self.source_filter, False), (self.ai_filter, True)):
            button.setCheckable(True)
            accent(button, amber)
            filters.addWidget(button)
            button.toggled.connect(self.filter_items)
        left.body.addLayout(filters)
        self.use_filter = QComboBox()
        for text, value in (("All uses", ""), ("Revision quiz", "revision"), ("ELP exam", "elp")):
            self.use_filter.addItem(text, value)
        left.body.addWidget(host.Field("Use", self.use_filter))
        self.topic_filter = QComboBox()
        left.body.addWidget(host.Field("Topic", self.topic_filter))
        self.item_list = QListWidget()
        left.body.addWidget(self.item_list, 1)
        self.count = QLabel()
        left.body.addWidget(self.count)
        for field in (self.status_filter, self.use_filter, self.topic_filter):
            field.currentIndexChanged.connect(self.filter_items)
        self.search.textChanged.connect(self.filter_items)
        self.item_list.currentItemChanged.connect(self.select_item)
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
        self.options = QTableWidget(0, 3)
        self.options.setHorizontalHeaderLabels(["Correct", "Answer", "Rationale"])
        self.options.horizontalHeader().setStretchLastSection(True)
        self.options.setColumnWidth(0, 65)
        self.options.setColumnWidth(1, 230)
        self.options.setMinimumHeight(195)
        body.addWidget(host.Field("Answer options · phase 1: exactly one correct", self.options))
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
        split.setSizes([330, 790])
        self.editor.setEnabled(False)
        self.source_tag.hide()
        self.ai_tag.hide()
        self.draft_tag.hide()
        self.source_banner.hide()
        self.reload()
        self.check_sources()

    def error(self, error):
        self.notice.setText(str(error))
        self.host.show_message(str(error), error=True)

    def reload(self):
        try:
            self.items, errors = self.bank.load()
            if errors:
                self.error("\n".join(map(str, errors)))
            topic = self.topic_filter.currentData()
            self.topic_filter.blockSignals(True)
            self.topic_filter.clear()
            self.topic_filter.addItem("All topics", "")
            for value in sorted({item.topic for item in self.items if item.topic}):
                self.topic_filter.addItem(value, value)
            self.topic_filter.setCurrentIndex(max(0, self.topic_filter.findData(topic)))
            self.topic_filter.blockSignals(False)
            self.filter_items()
            self.changed(self.items)
        except (OSError, BankError, ValueError) as exc:
            self.error(exc)

    def filter_items(self, *_):
        selected = self.current.id if self.current else None
        self.item_list.blockSignals(True)
        self.item_list.clear()
        for item in self.items:
            if (self.status_filter.currentData() not in ("", item.status)
                    or self.use_filter.currentData() not in ("", item.use)
                    or self.topic_filter.currentData() not in (None, "", item.topic)
                    or (self.source_filter.isChecked() and not item.source_changed)
                    or (self.ai_filter.isChecked() and not (item.origin.kind == "ai" and item.status == "draft"))
                    or self.search.text().casefold() not in (item.stem + " " + item.topic + " " + item.id).casefold()):
                continue
            labels = [item.status.title(), item.use, item.topic, item.difficulty]
            if item.source_changed:
                labels.append("Source changed")
            if item.origin.kind == "ai":
                labels.append("AI draft" if item.status == "draft" else "AI-assisted")
            row = QListWidgetItem(f"{item.stem[:130] or 'Untitled question'}\n" + " · ".join(x for x in labels if x))
            if item.source_changed or (item.origin.kind == "ai" and item.status == "draft"):
                row.setForeground(QColor(accent_color(self, not item.source_changed)))
            row.setData(Qt.ItemDataRole.UserRole, item.id)
            self.item_list.addItem(row)
            if item.id == selected:
                self.item_list.setCurrentItem(row)
        self.count.setText(f"{self.item_list.count()} of {len(self.items)} questions")
        self.item_list.blockSignals(False)

    def candidate(self):
        options = []
        for row in range(self.options.rowCount()):
            correct = self.options.cellWidget(row, 0)
            options.append(Option(correct.property("option_id"), self.options.cellWidget(row, 1).toPlainText(),
                                  correct.isChecked(), self.options.cellWidget(row, 2).toPlainText()))
        return replace(self.current, stem=self.stem.toPlainText(), options=tuple(options),
                       objective=self.objective.text(), topic=self.topic.text(), difficulty=self.difficulty.text(),
                       use=self.use.currentData(), sources=self.links, media=self.attachments)

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

    def select_item(self, row, previous):
        if row is None:
            return
        if not self.may_leave():
            self.item_list.blockSignals(True)
            self.item_list.setCurrentItem(previous)
            self.item_list.blockSignals(False)
            return
        try:
            self.populate(self.bank.get(row.data(Qt.ItemDataRole.UserRole)))
        except (OSError, BankError, KeyError) as exc:
            self.error(exc)

    def populate(self, item, new=False):
        self.current, self.new_item = item, new
        self.links, self.attachments = item.sources, item.media
        self.stem.setPlainText(item.stem)
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
        except ConflictError as exc:
            box = QMessageBox(self)
            box.setWindowTitle("Question changed elsewhere")
            box.setText(f"{exc}\nYour edits are still in this editor. Keep editing, or save them as a new question.")
            copy = box.addButton("Save as new question", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Keep editing", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() != copy:
                return False
            try:
                saved = self.bank.create(replace(item, id=uuid4().hex, revision=1))
            except (OSError, BankError, ValueError) as error:
                self.error(error)
                return False
        except (OSError, BankError, ValueError) as exc:
            self.error(exc)
            return False
        self.populate(saved)
        self.reload()
        self.notice.setText(f"Saved revision {saved.revision} as Draft.")
        return True

    def when_idle(self, callback):
        if self.busy:
            self.idle_callbacks.append(callback)
        else:
            callback()

    def set_busy(self, busy):
        self.busy = busy
        self.editor.setEnabled(not busy and self.current is not None)
        self.check_button.setEnabled(not busy)
        self.export_button.setEnabled(not busy)
        if not busy:
            callbacks, self.idle_callbacks = self.idle_callbacks, []
            for callback in callbacks:
                QTimer.singleShot(0, callback)

    def check_sources(self, *_, then=None, item_ids=None):
        if self.busy:
            return
        self.set_busy(True)
        self.notice.setText("Checking sources…")

        def checked(report, error):
            self.set_busy(False)
            self.reload()
            if error:
                self.error(error)
                return
            if report.errors:
                self.error("\n".join(map(str, report.errors)))
                return
            if self.current and not self.new_item:
                # A marker is not revision content. Leave every typed field intact.
                latest = next((item for item in self.items if item.id == self.current.id), None)
                if latest:
                    self.current = replace(self.current, source_changed=latest.source_changed)
                    self.baseline = replace(self.baseline, source_changed=latest.source_changed)
                    self.update_header(self.current)
            self.notice.setText(f"Source check complete · {sum(i.source_changed for i in self.items)} changed.")
            if then:
                then(report)

        def prepared(deck, error):
            if error:
                checked(None, error)
            else:
                check_bank(self.host, deck, self.bank, checked, item_ids=item_ids)

        deck = self.deck
        self.host.run_in_background(lambda: fresh_deck(deck), prepared)

    def change_status(self, target):
        if self.current is None or not self.save():
            return
        if target == "retired" and self.current.status != "reviewed":
            self.error("Saved edits as a draft. Review this revision before retiring it.")
            return
        if target == "draft" and self.current.status == "draft":
            return
        if target == "reviewed":
            errors = check_phase1(self.current)
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

    def export(self):
        if not self.may_leave():
            return
        # Phase 1 exports the reviewed revision-use questions visible in the list.
        ids = {self.item_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.item_list.count())}
        items = [item for item in self.items if item.id in ids and item.status == "reviewed" and item.use == "revision"]
        if not items:
            self.error("No reviewed revision-quiz questions match these filters.")
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Export reviewed revision questions", "revision-quiz.xml", "Moodle XML (*.xml)")
        if not filename:
            return
        if not filename.lower().endswith(".xml"):
            filename += ".xml"
        self.set_busy(True)

        def work():
            xml = export_moodle_xml(items, bank=self.bank)
            path = Path(filename)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".moodle-", delete=False) as stream:
                    temporary = stream.name
                    stream.write(xml)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
            return len(items)

        def done(count, error):
            self.set_busy(False)
            if error:
                self.error(error)
            else:
                self.notice.setText(f"Exported {count} reviewed questions to {filename}.")
        self.host.run_in_background(work, done)

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
            event.accept()  # Hide; the manager keeps this top-level widget reachable.
        else:
            event.ignore()
