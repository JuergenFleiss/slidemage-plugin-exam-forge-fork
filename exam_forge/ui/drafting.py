# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small source chooser and one cancellable host AI request."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPlainTextEdit, QVBoxLayout,
)
from slidemage.plugin_api import ServiceError

from ..ai_support import ai_service
from ..drafting import DraftReplyError, DraftSlide, build_request, parse_reply
from ..model import SourceLink
from ..sources import fingerprint
from .work import fresh_deck


class DraftDialog(QDialog):
    def __init__(self, host, deck, bank, parent):
        super().__init__(parent)
        self.host, self.deck = host, deck
        self.items = ()
        self.refs = []
        self.sources = []
        self.generation = 0
        self.state = state = {"active": True, "cancel": None}

        def stop(*_):
            state["active"] = False
            cancel, state["cancel"] = state["cancel"], None
            if cancel:
                cancel()

        self.finished.connect(stop)
        self.destroyed.connect(stop)
        self.setWindowTitle("Draft with AI")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(720, 620)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Tick teaching sources to send. No sources are selected initially."))
        self.controls = host.Page()
        layout.addWidget(self.controls)
        row = QHBoxLayout()
        self.view, self.language, self.use = QComboBox(), QComboBox(), QComboBox()
        self.view.addItems(["instructor", "during-course", "after-course"])
        self.language.addItems(["en", "de"])
        self.use.addItem("Revision quiz", "revision")
        self.use.addItem("ELP exam", "elp")
        for label, field in (("View", self.view), ("Language", self.language), ("Use", self.use)):
            row.addWidget(host.Field(label, field))
        self.controls.body.addLayout(row)
        self.choices = QListWidget()
        self.controls.body.addWidget(self.choices)
        self.review = QCheckBox("I reviewed the selected slides' image descriptions for exam use; include them")
        self.controls.body.addWidget(self.review)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Select a row to inspect its source text and image descriptions before ticking it.")
        self.preview.setMaximumHeight(160)
        self.controls.body.addWidget(self.preview)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        row = QHBoxLayout()
        self.send = host.Button("Draft with AI", "primary")
        self.send.clicked.connect(self.generate)
        row.addWidget(self.send)
        cancel = host.Button("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        layout.addLayout(row)
        self.view.currentTextChanged.connect(self.load)
        self.language.currentTextChanged.connect(self.load)
        self.choices.currentRowChanged.connect(self.show_source)
        self.choices.itemChanged.connect(lambda *_: self.review.setChecked(False))
        self.load()

    def load(self, *_):
        self.generation += 1
        generation = self.generation
        view, language = self.view.currentText(), self.language.currentText()
        self.review.setChecked(False)
        self.choices.clear()
        self.preview.clear()
        self.send.setEnabled(False)
        self.message.setText("Loading sources…")

        def work():
            refs = self.host.deck_slides(fresh_deck(self.deck), view, language)
            sources = []
            for ref in refs:
                try:
                    sources.append(self.host.slide_source(ref))
                except Exception as exc:
                    sources.append(exc)
            return refs, sources

        def done(result, error):
            if not self.state["active"] or generation != self.generation:
                return
            if error:
                self.message.setText(str(error))
                return
            self.refs, self.sources = result
            labels = [f"Slide {r.position}: {r.title or r.id_slug} · {r.version or 'current'}"
                      + (" · hidden" if r.hidden else "") for r in self.refs]
            for label in labels:
                row = QListWidgetItem(label)
                row.setCheckState(Qt.CheckState.Unchecked)
                self.choices.addItem(row)
            self.send.setEnabled(True)
            self.message.setText("Choose slides.")

        self.host.run_in_background(work, done)

    def show_source(self, index):
        self.preview.clear()
        if index < 0:
            return
        source = self.sources[index]
        if isinstance(source, Exception):
            self.message.setText(str(source))
            return
        descriptions = "\n\n".join("\n".join((d.short, d.long, d.takeaway))
                                     for d in source.descriptions if not d.decorative)
        self.preview.setPlainText(source.text + "\n\nImage descriptions (require review tick):\n" + descriptions)

    def generate(self):
        # Check again after source selection: disabling AI closes the gate immediately.
        try:
            service = ai_service(self.host)
            if service is None or not service.available():
                self.message.setText("Enable AI support and an AI plugin such as Claude or Ollama in Settings.")
                return
        except ServiceError as exc:
            self.message.setText(str(exc))
            return
        indices = [i for i in range(self.choices.count())
                   if self.choices.item(i).checkState() == Qt.CheckState.Checked]
        refs = [self.refs[i] for i in indices if i < len(self.refs)]
        sources = [self.sources[i] for i in indices if i < len(self.sources)]
        view, language, use = self.view.currentText(), self.language.currentText(), self.use.currentData()
        reviewed = self.review.isChecked()
        guidance = self.host.settings().get("drafting_guidance")
        self.controls.setEnabled(False)
        self.send.setEnabled(False)
        self.message.setText("Preparing selected sources…")

        def work():
            slides = []
            for ref, source in zip(refs, sources):
                if isinstance(source, Exception):
                    raise source
                link = SourceLink(ref.id, ref.version or "", language, view, fingerprint(source))
                slides.append(DraftSlide(link, source, reviewed))
            return build_request(tuple(slides), use=use, guidance=guidance), tuple(s.link for s in slides)

        def ready(prepared, error):
            if not self.state["active"]:
                return
            if error:
                self.completed(None, error)
                return
            request, sources = prepared
            # Discovery is kept inside ai_support, including this last-moment gate.
            try:
                service = ai_service(self.host)
                if service is None:
                    self.reject()
                    return
                self.message.setText("Drafting… Cancel discards the reply.")
                self.state["cancel"] = service.request(
                    request, lambda result, error: self.completed(result, error, sources=sources, use=use))
            except ServiceError as exc:
                self.completed(None, exc)

        self.host.run_in_background(work, ready)

    def completed(self, result, error, *, sources=(), use="revision"):
        if not self.state["active"]:
            return
        self.state["cancel"] = None
        if not self.host.settings().get("ai_support", False):
            self.controls.setEnabled(True)
            self.send.setEnabled(True)
            self.message.setText("AI support is off. Reply discarded.")
            return
        if error is None and result is None:
            self.reject()  # The host's decline/cancel is deliberately quiet.
            return
        if error is None:
            try:
                self.items = parse_reply(result, sources=sources, use=use)
            except DraftReplyError as exc:
                error = exc
        if error is not None:
            self.controls.setEnabled(True)
            self.send.setEnabled(True)
            self.message.setText(str(error))
        else:
            self.accept()
