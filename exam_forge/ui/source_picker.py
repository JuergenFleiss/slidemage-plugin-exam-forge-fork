# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deck source selection; reads and hashes stay on the host's worker."""
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QVBoxLayout,
)

from ..sources import link_slide


class SourcePicker(QDialog):
    def __init__(self, host, deck, parent):
        super().__init__(parent)
        self.host, self.deck = host, deck
        self.link = None
        self.refs = []
        self.generation = 0
        self.setWindowTitle("Add source slide")
        self.resize(620, 430)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.view = QComboBox()
        self.view.addItems(["instructor", "during-course", "after-course"])
        self.language = QComboBox()
        self.language.addItems(["en", "de"])
        row.addWidget(host.Field("View", self.view))
        row.addWidget(host.Field("Language", self.language))
        layout.addLayout(row)
        self.slides = QListWidget()
        layout.addWidget(self.slides)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        row = QHBoxLayout()
        open_button = host.Button("Open in editor")
        open_button.clicked.connect(self.open_slide)
        row.addWidget(open_button)
        self.add = host.Button("Add source", "primary")
        self.add.clicked.connect(self.capture)
        row.addWidget(self.add)
        cancel = host.Button("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        layout.addLayout(row)
        self.view.currentTextChanged.connect(self.load)
        self.language.currentTextChanged.connect(self.load)
        self.finished.connect(self.invalidate)
        self.load()

    def invalidate(self, *_):
        self.generation += 1

    def load(self, *_):
        self.generation += 1
        generation = self.generation
        view, language = self.view.currentText(), self.language.currentText()
        self.refs = []
        self.slides.clear()
        self.add.setEnabled(False)
        self.message.setText("Loading slides…")

        def done(refs, error):
            if generation != self.generation:
                return
            self.message.setText(str(error) if error else "Choose a slide; hidden slides are labelled.")
            if error is None:
                self.refs = refs
                self.slides.addItems([f"{ref.position}. {ref.title or ref.id_slug} · {ref.version or 'current'}"
                                      + (" · hidden" if ref.hidden else "") for ref in refs])
                self.add.setEnabled(bool(refs))

        self.host.run_in_background(lambda: self.host.deck_slides(self.deck, view, language), done)

    def selected(self):
        row = self.slides.currentRow()
        return self.refs[row] if 0 <= row < len(self.refs) else None

    def open_slide(self):
        ref = self.selected()
        if ref is not None:
            try:
                self.host.open_slide(ref)
            except (OSError, ValueError, RuntimeError) as exc:
                self.message.setText(str(exc))

    def capture(self):
        ref = self.selected()
        if ref is None:
            return
        generation = self.generation
        view = self.view.currentText()
        self.add.setEnabled(False)

        def done(link, error):
            if generation != self.generation:
                return
            self.add.setEnabled(True)
            if error:
                self.message.setText(str(error))
            else:
                self.link = link
                self.accept()

        self.host.run_in_background(lambda: link_slide(self.host, ref, view), done)
