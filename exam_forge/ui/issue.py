# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Third step: freeze the approved exam, save its documents, record corrections in a separate journal."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..exam_document import export_elp_documents
from ..manifest import ChangedSourcesError, Manifest, append_correction, freeze, read_corrections
from ..model import plural
from ..rules import exam_totals
from ..sources import fresh_deck
from .kit import Sheet, confirm, deck_language, tone

_SHEETS = {}  # One correction sheet, and so one pending writer, per journal in this application.


def issued_at(path):
    """Local issue time from the manifest file name, or an empty string."""
    try:
        stamp = datetime.strptime(Path(path).name.split("-")[0], "%Y%m%dT%H%M%S%fZ")
        return stamp.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ""


def manifest_totals(data):
    """'N questions · P points · M min' from the frozen items and rules."""
    items, rules = data["items"], data["scoring"]["rules"]
    points, minutes = exam_totals((i["difficulty"] for i in items), {d: rules[d]["points"] for d in ("easy", "hard")},
                                  {d: rules[d]["minutes"] for d in ("easy", "hard")})
    return f"{plural(len(items), 'question')} · {points} points · {minutes:g} min"


class IssueView(QWidget):
    def __init__(self, exam):
        super().__init__()
        self.exam, self.window, self.host = exam, exam.window, exam.host
        self.manifest = self.manifest_path = self.journal_path = None
        self.saved = {}
        layout = QVBoxLayout(self)
        self.title = self.host.Label("Exam frozen", "title")
        layout.addWidget(self.title)
        self.summary = self.host.Label("", "secondary")
        layout.addWidget(self.summary)
        self.file_name = self.host.Label("", "mono")
        layout.addWidget(self.file_name)
        note = self.host.caption("Saved in your private bank folder. Later edits to questions do not change this exam.")
        note.setWordWrap(True)
        layout.addWidget(note)
        cards = QHBoxLayout()
        self.exam_button = self.host.Button("Save exam HTML…")
        self.exam_button.clicked.connect(lambda: self.documents(0))
        self.key_button = self.host.Button("Save key HTML…")
        self.key_button.clicked.connect(lambda: self.documents(1))
        self.key_title = tone(self.host.Label("🔒 Private key", "heading"), "amber")
        self.key_text = tone(self.host.Label("Answers, rationales and the scoring sheet. Keep this file private."),
                             "amber")
        cards.addWidget(self.card(self.host.Label("Exam copy", "heading"), self.host.Label(
            "Questions and answer options for students. No answers."), self.exam_button))
        cards.addWidget(self.card(self.key_title, self.key_text, self.key_button))
        layout.addLayout(cards)
        row = QHBoxLayout()
        self.corrections_button = self.host.Button("Record a correction…")
        self.corrections_button.clicked.connect(self.corrections)
        row.addWidget(self.corrections_button)
        row.addStretch()
        another = self.host.Button("Start another exam")
        another.clicked.connect(self.another)
        row.addWidget(another)
        layout.addLayout(row)
        layout.addStretch()
        self.reset()

    def card(self, title, text, button):
        frame = self.host.Card()
        box = frame.layout()
        text.setWordWrap(True)
        box.addWidget(title)
        box.addWidget(text)
        box.addWidget(button)
        box.addStretch()
        return frame

    def reset(self):
        self.manifest = self.manifest_path = self.journal_path = None
        self.saved = {}
        self.summary.clear()
        self.file_name.clear()
        for button in (self.exam_button, self.key_button, self.corrections_button):
            button.setEnabled(False)

    def another(self):
        self.reset()
        self.exam.set_step(0)

    def freeze(self, selection):
        if self.manifest is not None or self.window.busy:
            return
        bank, deck = self.window.bank, self.window.deck
        stem = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-") + uuid4().hex[:8]
        path = bank.folder / (stem + ".manifest.json")
        self.window.set_busy(True)
        blueprint = {**selection.blueprint.as_dict(), "seed": selection.seed}

        def work():
            manifest = freeze(self.host, fresh_deck(deck), bank,
                              [(i.id, i.revision) for i in selection.items], blueprint, selection.rules,
                              topic_of=selection.topic_of)
            manifest.write(path)
            return Manifest.read(path)

        def done(manifest, error):
            self.window.set_busy(False)
            if isinstance(error, ChangedSourcesError):
                self.exam.selection = selection
                self.exam.mark_changed({c.item.id: c.changed_slides for c in error.checks})
                self.exam.set_step(1)
                self.window.reload()   # freeze recorded the changed slides
            elif error:
                self.window.error(error)
            else:
                self.show_manifest(manifest, path)
        self.host.run_in_background(work, done)

    def show_manifest(self, manifest, path):
        self.manifest, self.manifest_path, self.saved = manifest, Path(path), {}
        self.journal_path = self.manifest_path.with_suffix(".corrections.jsonl")
        when = issued_at(path)
        self.summary.setText(manifest_totals(manifest.data()) + (f" · issued {when}" if when else ""))
        self.file_name.setText(self.manifest_path.name)
        for button in (self.exam_button, self.key_button, self.corrections_button):
            button.setEnabled(True)
        self.exam.frozen()

    def open_manifest(self):
        bank = self.window.bank
        path, _ = QFileDialog.getOpenFileName(self, "Open issued manifest", str(bank.folder), "Manifests (*.manifest.json)")
        if not path:
            return
        path = Path(path).resolve()
        if path.parent != bank.folder.resolve():
            self.window.error("Choose a manifest in this bank folder.")
            return

        def work():
            manifest = Manifest.read(path)
            if manifest.data()['bank_id'] != bank.id:
                raise ValueError("Manifest belongs to another bank.")
            return manifest
        self.window.work(work, lambda manifest: self.show_manifest(manifest, path))

    def documents(self, which):
        """Save the exam copy (0) or the private key and scoring sheet (1)."""
        if self.manifest is None:
            return
        if which and not confirm(self, "Private answer key", "The key file holds answers, rationales and scoring rules. "
                                 "Save it in a private location. Continue?", warning=True):
            return
        title, name = (("Save private key and scoring sheet", "key.html") if which
                       else ("Save exam entry copy", "exam.html"))
        chosen, _ = QFileDialog.getSaveFileName(self, title, str(self.window.bank.folder / name), "HTML (*.html)")
        if not chosen:
            return
        path = Path(chosen if chosen.lower().endswith(".html") else chosen + ".html").resolve()
        if self.saved.get(1 - which) == path:
            self.window.error("Exam and private key need separate files.")
            return
        manifest, language = self.manifest, deck_language(self.host, self.window.deck)

        def work():
            path.write_text(export_elp_documents(manifest, language)[which], encoding="utf-8")

        def done(_):
            if self.manifest is manifest:
                self.saved[which] = path
            self.window.toast(f"Saved {'private key' if which else 'exam'}: {path}")
        self.window.work(work, done)

    def corrections(self):
        if self.manifest is not None:
            open_corrections(self.host, self.manifest, self.journal_path, self.window)


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
        if not confirm(self.sheet, "Record correction", f"Append this correction for {item}?\n\n{change}\nReason: {reason}"):
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
