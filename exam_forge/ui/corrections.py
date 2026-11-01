# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""One correction sheet and one pending writer per journal in this application."""
from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import (
    QComboBox,
    QMessageBox,
    QPlainTextEdit,
)

from ..manifest import append_correction, read_corrections
from .sheet import Sheet

_SHEETS = {}


def open_corrections(host, manifest, path, parent=None):
    path = path.resolve()
    sheet = _SHEETS.get(path)
    if sheet is None:
        sheet = CorrectionSheet(host, manifest, path, parent)
        _SHEETS[path] = sheet  # Retain the sheet, including while a worker is writing.
    sheet.show()
    sheet.raise_()
    sheet.activateWindow()
    sheet.load()
    return sheet


class CorrectionSheet(Sheet):
    def __init__(self, host, manifest, path, parent=None):
        super().__init__(host, "Exam Forge · Corrections", parent)
        self.sheet.setModal(False)
        self.host, self.path, self.busy = host, path, False
        self.resize(700, 640)
        layout = self.body
        layout.addWidget(host.Label(str(path), "mono"))
        self.trail = QPlainTextEdit()
        self.trail.setReadOnly(True)
        layout.addWidget(self.trail, 1)
        self.item = QComboBox()
        self.item.addItems([i['id'] for i in manifest.data()['items']])
        layout.addWidget(host.Field("Frozen question", self.item))
        self.change, self.reason = QPlainTextEdit(), QPlainTextEdit()
        layout.addWidget(host.Field("Change", self.change))
        layout.addWidget(host.Field("Reason", self.reason))
        self.append = self.add_button("Record correction", kind="primary")
        self.append.clicked.connect(self.record)
        self.message = host.caption("")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

    def run(self, work, done):
        if self.busy:
            return
        self.busy = True
        for field in (self.item, self.change, self.reason, self.append):
            field.setEnabled(False)

        def completed(result, error):
            self.busy = False
            for field in (self.item, self.change, self.reason, self.append):
                field.setEnabled(True)
            if error:
                self.message.setText(str(error))
            else:
                done(result)
        self.host.run_in_background(work, completed)

    def render(self, rows):
        self.trail.setPlainText("\n\n".join(f"{r['time']} · {r['item_id']}\n{r['change']}\nReason: {r['reason']}" for r in rows) or "No corrections recorded.")

    def load(self):
        self.run(lambda: read_corrections(self.path), self.render)

    def record(self):
        if self.busy:
            return
        item, change, reason = self.item.currentText(), self.change.toPlainText(), self.reason.toPlainText()
        if not change.strip() or not reason.strip():
            self.message.setText("Change and reason are required.")
            return
        if QMessageBox.question(self.sheet, "Record correction", f"Append this correction for {item}?\n\n{change}\nReason: {reason}") != QMessageBox.StandardButton.Yes:
            return

        def work():
            lock = QLockFile(str(self.path) + ".lock")
            if not lock.tryLock(0):
                raise ValueError("Another writer is using this correction journal. Retry when it finishes.")
            try:
                append_correction(self.path, item, change, reason)
                return read_corrections(self.path)
            finally:
                lock.unlock()

        def done(rows):
            self.render(rows)
            self.change.clear()
            self.reason.clear()
            self.message.setText("Correction recorded. The issued manifest is unchanged.")
        self.run(work, done)

    def may_close(self):
        if self.busy:
            self.message.setText("Wait for the journal operation to finish.")
        return not self.busy
