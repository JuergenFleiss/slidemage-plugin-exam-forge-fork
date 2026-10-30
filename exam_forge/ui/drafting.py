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
from ..drafting import DraftPassage, DraftReplyError, DraftSlide, build_request, parse_reply
from ..model import SourceLink
from ..passages import Sessions
from ..sources import fingerprint
from .work import fresh_deck


class DraftDialog(QDialog):
    def __init__(self, host, deck, bank, parent):
        super().__init__(parent)
        self.host, self.deck, self.store = host, deck, Sessions(bank)
        self.items = ()
        self.refs, self.passages = [], []
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
            passages, skipped = [], 0
            for session in self.store.load(deck=self.deck):
                confirmed = [p for p in session.passages if p.confirmed]
                if not confirmed or not session.transcript:
                    continue
                transcript = self.store.read_transcript(session)
                for p in confirmed:
                    choice = DraftPassage(p, transcript)
                    try:
                        choice.text()
                    except ValueError:
                        skipped += 1
                    else:
                        passages.append((session, choice))
            return refs, passages, skipped

        def done(result, error):
            if not self.state["active"] or generation != self.generation:
                return
            if error:
                self.message.setText(str(error))
                return
            self.refs, self.passages, skipped = result
            labels = [f"Slide {r.position}: {r.title or r.id_slug} · {r.version or 'current'}"
                      + (" · hidden" if r.hidden else "") for r in self.refs]
            labels += [f"Talk: {s.name} · {p.passage.start:g}–{p.passage.end:g}s · "
                       + p.text() for s, p in self.passages]
            for label in labels:
                row = QListWidgetItem(label)
                row.setCheckState(Qt.CheckState.Unchecked)
                self.choices.addItem(row)
            self.send.setEnabled(True)
            self.message.setText("Choose slides and/or confirmed passages. "
                                 + (f"{skipped} stale confirmations omitted." if skipped else ""))

        self.host.run_in_background(work, done)

    def show_source(self, index):
        self.preview.clear()
        if index < 0:
            return
        if index >= len(self.refs):
            self.preview.setPlainText(self.passages[index - len(self.refs)][1].text())
            return
        ref, generation = self.refs[index], self.generation

        def done(source, error):
            if (not self.state["active"] or generation != self.generation
                    or self.choices.currentRow() != index):
                return
            if error:
                self.message.setText(str(error))
                return
            descriptions = "\n\n".join("\n".join((d.short, d.long, d.takeaway))
                                         for d in source.descriptions if not d.decorative)
            self.preview.setPlainText(source.text + "\n\nImage descriptions (require review tick):\n" + descriptions)

        self.host.run_in_background(lambda: self.host.slide_source(ref), done)

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
        passages = [self.passages[i - len(self.refs)] for i in indices if i >= len(self.refs)]
        view, language, use = self.view.currentText(), self.language.currentText(), self.use.currentData()
        reviewed = self.review.isChecked()
        self.controls.setEnabled(False)
        self.send.setEnabled(False)
        self.message.setText("Preparing selected sources…")

        def work():
            slides = []
            for ref in refs:
                source = self.host.slide_source(ref)
                link = SourceLink(ref.id, ref.version or "", language, view, fingerprint(source))
                slides.append(DraftSlide(link, source, reviewed))
            selected = tuple(DraftPassage(p.passage, self.store.read_transcript(s)) for s, p in passages)
            return build_request(tuple(slides), selected, use=use), tuple(s.link for s in slides)

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
