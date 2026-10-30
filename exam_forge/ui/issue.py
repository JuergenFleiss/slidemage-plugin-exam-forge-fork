# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Second issuing step: immutable manifest, entry documents and corrections."""
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..elp_document import export_elp_documents
from ..manifest import ChangedSourcesError, Manifest, freeze
from .corrections import open_corrections
from .work import changed_details, fresh_deck


class IssueView(QWidget):
    def __init__(self, exam):
        super().__init__()
        self.exam, self.window, self.host = exam, exam.window, exam.host
        self.manifest = self.manifest_path = self.journal_path = None
        self.selection = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("2 · Issue and frozen manifest"))
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        layout.addWidget(self.summary, 1)
        self.paths = QLabel()
        self.paths.setWordWrap(True)
        layout.addWidget(self.paths)
        row = QHBoxLayout()
        self.freeze_button = self.host.Button("Freeze approved exam", "primary")
        self.freeze_button.clicked.connect(self.freeze)
        self.documents_button = self.host.Button("Save exam and key documents…")
        self.documents_button.clicked.connect(self.documents)
        self.corrections_button = self.host.Button("Corrections…")
        self.corrections_button.clicked.connect(self.corrections)
        for button in (self.freeze_button, self.documents_button, self.corrections_button):
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        back = self.host.Button("Back to selection")
        back.clicked.connect(lambda: exam.stack.setCurrentIndex(0))
        row.addWidget(back)
        layout.addLayout(row)
        self.reset()

    def reset(self):
        self.manifest = self.manifest_path = self.journal_path = self.selection = None
        self.summary.clear()
        self.paths.clear()
        self.freeze_button.setEnabled(False)
        self.documents_button.setEnabled(False)
        self.corrections_button.setEnabled(False)

    def prepare(self, selection):
        self.reset()
        self.selection = selection
        self.freeze_button.setEnabled(True)
        self.summary.setPlainText(f"Approved, not issued · {selection.total_points} points · {selection.total_minutes} minutes\n\n" +
            "\n".join(f"{i.id} r{i.revision}: {i.stem}" for i in selection.items))
        self.paths.setText("Freezing creates a new private manifest and a separate correction journal in the bank folder.")

    def freeze(self):
        if self.selection is None or self.manifest is not None or self.window.busy:
            return
        selection, bank, deck = self.selection, self.window.bank, self.window.deck
        stem = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-") + uuid4().hex[:8]
        path = bank.folder / (stem + ".manifest.json")
        self.window.set_busy(True)
        blueprint = {**asdict(selection.blueprint), "seed": selection.seed,
                     "taught": self.exam.taught_blueprint}

        def work():
            manifest = freeze(self.host, fresh_deck(deck), bank,
                              [(i.id, i.revision) for i in selection.items], blueprint, selection.rules)
            manifest.write(path)
            return Manifest.read(path)

        def done(manifest, error):
            self.window.set_busy(False)
            if isinstance(error, ChangedSourcesError):
                details = changed_details(error.checks)
                self.summary.setPlainText("Issue stopped: source changed.\n" + details)
                self.window.error("Issue stopped. Review changed sources before issuing:\n" + details)
            elif error:
                self.window.error(error)
            else:
                self.show_manifest(manifest, path)
                self.documents()
        self.host.run_in_background(work, done)

    def show_manifest(self, manifest, path):
        self.manifest, self.manifest_path = manifest, Path(path)
        self.journal_path = self.manifest_path.with_suffix(".corrections.jsonl")
        data = manifest.data()
        self.summary.setPlainText("Issued · immutable manifest\n\n" + "\n".join(
            f"{i['id']} r{i['revision']}: {i['stem']}" for i in data['items']) +
            "\n\nFrozen blueprint\n" + str(data['blueprint']) + "\n\nFrozen rules\n" + str(data['scoring']['rules']))
        self.paths.setText(f"Manifest: {path}\nCorrection journal: {self.journal_path}")
        self.freeze_button.setEnabled(False)
        self.documents_button.setEnabled(True)
        self.corrections_button.setEnabled(True)

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

    def documents(self):
        if self.manifest is None:
            return
        if QMessageBox.warning(self, "Private answer key", "The key file holds answers, rationales and scoring rules. Save it in a private location. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        exam_path, _ = QFileDialog.getSaveFileName(self, "Save exam entry copy", str(self.window.bank.folder / "exam.html"), "HTML (*.html)")
        if not exam_path:
            return
        key_path, _ = QFileDialog.getSaveFileName(self, "Save private key and scoring sheet", str(self.window.bank.folder / "key.html"), "HTML (*.html)")
        if not key_path:
            return
        paths = [Path(p if p.lower().endswith(".html") else p + ".html").resolve() for p in (exam_path, key_path)]
        if paths[0] == paths[1]:
            self.window.error("Exam and private key need separate files.")
            return
        manifest = self.manifest

        def work():
            documents = export_elp_documents(manifest)
            for path, document in zip(paths, documents):
                path.write_text(document, encoding="utf-8")
        self.window.work(work, lambda _: self.window.notice.setText(f"Saved exam: {paths[0]}\nPrivate key: {paths[1]}"))

    def corrections(self):
        if self.manifest is not None:
            self.correction_sheet = open_corrections(self.host, self.manifest, self.journal_path)
