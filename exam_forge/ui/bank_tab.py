# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bank tab: the existing editor, filtering, source checks and XML export."""
import os
import tempfile
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..bank import BankError
from ..moodle_xml import export_moodle_xml
from ..sources import check_bank
from .editor import accent, accent_color
from .drafting import DraftDialog
from .work import fresh_deck


class BankTab:
    def build_bank_tab(self):
        host = self.host
        page = QWidget()
        layout = QVBoxLayout(page)
        split = QSplitter()
        layout.addWidget(split, 1)
        left = host.Page()
        left.body.setContentsMargins(0, 0, 10, 0)
        split.addWidget(left)
        new = host.Button("New question", "primary")
        new.clicked.connect(self.new_question)
        left.body.addWidget(new)
        self.draft_button = host.Button("Draft with AI…")
        self.draft_button.clicked.connect(self.draft_with_ai)
        left.body.addWidget(self.draft_button)
        self.refresh_ai_support()
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
        self.item_list.currentItemChanged.connect(self.select_item)
        self.build_editor(host, split)
        split.setSizes([330, 790])
        self.editor.setEnabled(False)
        self.source_tag.hide()
        self.ai_tag.hide()
        self.draft_tag.hide()
        self.source_banner.hide()
        return page

    def refresh_ai_support(self):
        self.draft_button.setVisible(bool(self.host.settings().get("ai_support", False)))

    def draft_with_ai(self):
        if not self.host.settings().get("ai_support", False) or not self.may_leave():
            return
        dialog = DraftDialog(self.host, self.deck, self.bank, self)
        self.drafting_dialog = dialog
        self.set_busy(True)

        def finished(code):
            self.set_busy(False)
            if code != DraftDialog.DialogCode.Accepted:
                return
            bank, items = self.bank, dialog.items

            def saved(drafts):
                self.reload()
                self.populate(drafts[0])
                self.notice.setText(f"Added {len(drafts)} AI draft(s). Edit and mark reviewed before use.")

            self.work(lambda: [bank.create(item) for item in items], saved)

        dialog.finished.connect(finished)
        dialog.open()

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
                    or (self.ai_filter.isChecked() and not (item.origin.kind == "ai" and item.status == "draft"))):
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

    def select_item(self, row, previous):
        if row is None:
            return
        item_id = row.data(Qt.ItemDataRole.UserRole)
        if not self.may_leave():
            self.item_list.blockSignals(True)
            self.item_list.setCurrentItem(previous)
            self.item_list.blockSignals(False)
            return
        try:
            self.populate(self.bank.get(item_id))
        except (OSError, BankError, KeyError) as exc:
            self.error(exc)

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

    def export(self):
        if not self.may_leave():
            return
        ids = {self.item_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.item_list.count())}
        self.check_sources(then=lambda report: self.export_checked(ids, report))

    def export_checked(self, ids, report):
        items = [checked.item for checked in report.items if checked.item.id in ids
                 and checked.item.status == "reviewed" and checked.item.use == "revision"]
        if not items:
            self.error("No reviewed revision-quiz questions match these filters.")
            return
        changed = [checked for checked in report.items if checked.item in items and checked.source_changed]
        if changed:
            details = [f"• {checked.item.stem} ({checked.item.id}): {source.source.slide_id} "
                       f"({source.source.view}, {source.source.language}) — {source.status} {source.message}"
                       for checked in changed for source in checked.sources if source.status != "unchanged"]
            answer = QMessageBox.warning(self, "Source changed", "Changed sources:\n" +
                                         "\n".join(details) +
                                         "\n\nContinue exporting?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        filename, _ = QFileDialog.getSaveFileName(self, "Export reviewed revision questions", "revision-quiz.xml", "Moodle XML (*.xml)")
        if not filename:
            return
        if not filename.lower().endswith(".xml"):
            filename += ".xml"
        self.set_busy(True)

        def work():
            if any(self.bank.get(item.id) != item for item in items):
                raise BankError("A question changed during export. Check sources and try again.")
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

