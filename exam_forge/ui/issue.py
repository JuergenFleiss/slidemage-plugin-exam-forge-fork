# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Third step: freeze the approved exam, save its documents, record corrections."""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ..elp_document import export_elp_documents
from ..manifest import ChangedSourcesError, Manifest, freeze
from ..model import plural
from .corrections import open_corrections
from .editor import accent_color
from .work import fresh_deck


def issued_at(path):
    """Local issue time from the manifest file name, or an empty string."""
    try:
        stamp = datetime.strptime(Path(path).name.split("-")[0], "%Y%m%dT%H%M%S%fZ")
        return stamp.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ""


def manifest_totals(data):
    """'N questions · P points · M min' from the frozen items and rules, as far as they say."""
    items = data["items"]
    text = plural(len(items), "question")
    rules = data.get("scoring", {}).get("rules", {})
    try:
        rows = [rules[i["difficulty"]] for i in items]
        points = sum(r["points"] for r in rows)
        minutes = float(sum((Decimal(str(r["minutes"])) for r in rows), Decimal(0)))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return text
    return f"{text} · {points} points · {minutes:g} min"


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
        self.key_title = self.host.Label("🔒 Private key", "heading")
        self.key_text = self.host.Label("Answers, rationales and the scoring sheet. Keep this file private.")
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
        self.restyle()
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

    def restyle(self):
        for label in (self.key_title, self.key_text):
            label.setStyleSheet(f"color: {accent_color(self.exam, True)};")

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
                self.exam.mark_changed({c.item.id: True for c in error.checks})
                self.exam.set_step(1)
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
        if which and QMessageBox.warning(self, "Private answer key", "The key file holds answers, rationales and scoring rules. Save it in a private location. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
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
        manifest = self.manifest

        def work():
            path.write_text(export_elp_documents(manifest, self.host.deck_language(self.window.deck))[which],
                            encoding="utf-8")

        def done(_):
            if self.manifest is manifest:
                self.saved[which] = path
            self.window.toast(f"Saved {'private key' if which else 'exam'}: {path}")
        self.window.work(work, done)

    def corrections(self):
        if self.manifest is not None:
            self.correction_sheet = open_corrections(self.host, self.manifest, self.journal_path, self.window)
