# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""One correction sheet and one pending writer per journal in this application."""
from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from ..manifest import append_correction, read_corrections

_SHEETS = {}


def open_corrections(host, manifest, path):
    path = path.resolve()
    sheet = _SHEETS.get(path)
    if sheet is None:
        sheet = CorrectionSheet(host, manifest, path)
        _SHEETS[path] = sheet  # Retain the sheet, including while a worker is writing.
    sheet.show()
    sheet.raise_()
    sheet.activateWindow()
    sheet.load()
    return sheet


class CorrectionSheet(QDialog):
    def __init__(self, host, manifest, path):
        super().__init__()
        self.host, self.path, self.busy = host, path, False
        self.setWindowTitle("Exam Forge · Corrections")
        self.resize(700, 600)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(str(path)))
        self.trail = QPlainTextEdit()
        self.trail.setReadOnly(True)
        layout.addWidget(self.trail, 1)
        self.item = QComboBox()
        self.item.addItems([i['id'] for i in manifest.data()['items']])
        layout.addWidget(host.Field("Frozen question", self.item))
        self.change, self.reason = QPlainTextEdit(), QPlainTextEdit()
        layout.addWidget(host.Field("Change", self.change))
        layout.addWidget(host.Field("Reason", self.reason))
        self.append = host.Button("Record correction", "primary")
        self.append.clicked.connect(self.record)
        layout.addWidget(self.append)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

    def run(self, work, done):
        if self.busy:
            return
        self.busy = True
        self.append.setEnabled(False)

        def completed(result, error):
            self.busy = False
            self.append.setEnabled(True)
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
        if QMessageBox.question(self, "Record correction", f"Append this correction for {item}?\n\n{change}\nReason: {reason}") != QMessageBox.StandardButton.Yes:
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

    def closeEvent(self, event):
        if self.busy:
            self.message.setText("Wait for the journal operation to finish.")
            event.ignore()
        else:
            event.accept()
