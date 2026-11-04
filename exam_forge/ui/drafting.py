# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Slide chooser and one cancellable host AI request."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSpinBox,
)
from slidemage.plugin_api import AiChatError

from ..ai_support import NO_REPLY, ai_service
from ..drafting import DraftReplyError, DraftSlide, build_request, parse_reply
from ..model import KINDS, SourceLink, plural
from ..sources import fingerprint
from .sheet import Sheet
from .work import fresh_deck, slide_title

VIEWS = (("Instructor view", "instructor"), ("During course", "during-course"), ("After course", "after-course"))


def key(ref):
    return ref.id, ref.version or ""


class DraftDialog(Sheet):
    def __init__(self, host, deck, bank, parent, rules, *, topics=(), topic_id=None, preselect=None):
        super().__init__(host, "Draft with AI", parent)
        self.host, self.deck, self.rules = host, deck, rules
        self.items = ()
        self.refs = []
        self.sources = []
        self.generation = 0
        self.loading = self.busy = False
        topic = next((t for t in topics if t.id == topic_id), None)
        self.scope = set(topic.slides) if topic else set()
        self.ticked, self.preselect = set(), preselect
        self.state = state = {"active": True, "cancel": None}

        def stop(*_):
            state["active"] = False
            cancel, state["cancel"] = state["cancel"], None
            if cancel:
                cancel()

        self.finished.connect(stop)
        self.destroyed.connect(stop)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(720, 660)
        layout = self.body
        self.controls = host.Page()
        layout.addWidget(self.controls)
        head = QHBoxLayout()
        head.addWidget(host.Label("Slides to draft from", "heading"))
        head.addStretch()
        self.show_all = QCheckBox("Show all slides")
        self.show_all.setVisible(bool(self.scope))
        self.show_all.toggled.connect(self.populate)
        head.addWidget(self.show_all)
        self.controls.body.addLayout(head)
        self.choices = QListWidget()
        self.controls.body.addWidget(self.choices)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Select a row to inspect its source text before ticking it.")
        self.preview.setMaximumHeight(140)
        self.controls.body.addWidget(self.preview)
        row = QHBoxLayout()
        self.count = QSpinBox()
        self.count.setRange(1, 30)
        self.count.setValue(1)
        self.use = QComboBox()
        self.use.addItem("Revision quiz", ("revision", "choice"))
        kinds = rules.exam_kinds
        for kind in kinds:
            self.use.addItem("Exam" if len(kinds) == 1 else f"Exam · {KINDS[kind].lower()}", ("elp", kind))
        self.view, self.language = QComboBox(), QComboBox()
        for label, value in VIEWS:
            self.view.addItem(label, value)
        codes = list(host.project_languages())          # the main language first: the default
        try:
            own = host.deck_language(deck)
            codes += [] if own in codes else [own]       # the deck's own, should it be another
        except Exception:
            own = codes[0]
        self.language.addItems(codes)
        self.language.setCurrentText(own)
        for label, field in (("How many", self.count), ("For", self.use),
                             ("Read slides as", self.view), ("Language", self.language)):
            row.addWidget(host.Field(label, field))
        self.controls.body.addLayout(row)
        self.note = host.caption("")
        self.note.setWordWrap(True)
        self.controls.body.addWidget(self.note)
        self.message = host.Label("", "secondary")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.add_button("Cancel", role="reject")
        self.send = self.add_button("Draft", kind="primary")
        self.send.clicked.connect(self.generate)
        self.view.currentIndexChanged.connect(self.load)
        self.language.currentTextChanged.connect(self.load)
        self.count.valueChanged.connect(self.refresh)
        self.choices.currentRowChanged.connect(self.show_source)
        self.choices.itemChanged.connect(self.tick)
        if preselect and self.scope and preselect not in self.scope:
            self.show_all.setChecked(True)
        self.refresh()
        self.load()

    def selected(self):
        return [i for i, ref in enumerate(self.refs) if key(ref) in self.ticked]

    def refresh(self, *_):
        n = len(self.selected())
        self.note.setText(
            f"Only the text of the {plural(n, 'ticked slide')} is sent, and SlideMage shows it to you "
            "before anything leaves your computer. Images, other questions, answer keys and student data "
            "are never sent. Drafts arrive labelled AI draft and wait for your review.")
        self.send.setText("Drafting…" if self.busy else f"Draft {plural(self.count.value(), 'question')}")
        self.send.setEnabled(not self.busy and not self.loading and n > 0)

    def tick(self, row):
        slide = key(self.refs[row.data(Qt.ItemDataRole.UserRole)])
        (self.ticked.add if row.checkState() == Qt.CheckState.Checked else self.ticked.discard)(slide)
        self.refresh()

    def populate(self, *_):
        shown = [i for i, ref in enumerate(self.refs) if ref.id in self.scope]
        if self.show_all.isChecked() or not shown:
            shown = range(len(self.refs))
        self.ticked &= {key(self.refs[i]) for i in shown}  # Only visibly ticked slides are ever sent.
        self.choices.blockSignals(True)
        self.choices.clear()
        self.preview.clear()
        for i in shown:
            r = self.refs[i]
            row = QListWidgetItem(f"Slide {r.position}: {slide_title(r)} · {r.version or 'current'}"
                                  + (" · hidden" if r.hidden else ""))
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setData(Qt.ItemDataRole.UserRole, i)
            row.setCheckState(Qt.CheckState.Checked if key(r) in self.ticked else Qt.CheckState.Unchecked)
            self.choices.addItem(row)
        self.choices.blockSignals(False)
        self.refresh()

    def load(self, *_):
        self.generation += 1
        generation = self.generation
        view, language = self.view.currentData(), self.language.currentText()
        self.loading = True
        self.choices.clear()
        self.preview.clear()
        self.refresh()
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
            self.loading = False
            if error:
                self.refs, self.sources = [], []
                self.message.setText(str(error))
                self.refresh()
                return
            self.refs, self.sources = result
            self.ticked &= {key(ref) for ref in self.refs}
            if self.preselect:
                first = next((ref for ref in self.refs if ref.id == self.preselect), None)
                self.preselect = None
                if first:
                    self.ticked.add(key(first))
            self.populate()
            self.message.setText("Choose slides.")

        self.host.run_in_background(work, done)

    def show_source(self, index):
        self.preview.clear()
        row = self.choices.item(index) if index >= 0 else None
        if row is None:
            return
        source = self.sources[row.data(Qt.ItemDataRole.UserRole)]
        if isinstance(source, Exception):
            self.message.setText(str(source))
            return
        self.preview.setPlainText(source.text)

    def idle(self):
        self.busy = False
        self.controls.setEnabled(True)
        self.refresh()

    def generate(self):
        # Check again after source selection: disabling AI closes the gate immediately.
        try:
            service = ai_service(self.host)
            if service is None or not service.available():
                self.message.setText("Enable AI support and an AI plugin such as Claude or Ollama in Settings.")
                return
        except AiChatError as exc:
            self.message.setText(str(exc))
            return
        indices = self.selected()
        refs = [self.refs[i] for i in indices]
        sources = [self.sources[i] for i in indices]
        view, language, (use, kind) = self.view.currentData(), self.language.currentText(), self.use.currentData()
        guidance = self.host.settings().get("drafting_guidance")
        count = self.count.value()
        self.controls.setEnabled(False)
        self.busy = True
        self.refresh()
        self.message.setText("Preparing selected sources…")

        def work():
            slides = []
            for ref, source in zip(refs, sources):
                if isinstance(source, Exception):
                    raise source
                link = SourceLink(ref.id, ref.version or "", language, view, fingerprint(source), source.text)
                slides.append(DraftSlide(link, source))
            request = build_request(tuple(slides), self.rules, use=use, guidance=guidance, count=count, kind=kind)
            return request, tuple(s.link for s in slides)

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
                    self.idle()
                    self.message.setText("AI support is off. Nothing was sent.")
                    return
                self.message.setText("Drafting… Cancel discards the reply.")
                self.state["cancel"] = service.request(
                    request, lambda result, error: self.completed(result, error, sources=sources, use=use, count=count,
                                                                  kind=kind))
            except AiChatError as exc:
                self.completed(None, exc)

        self.host.run_in_background(work, ready)

    def completed(self, result, error, *, sources=(), use="revision", count=1, kind="choice"):
        if not self.state["active"]:
            return
        self.state["cancel"] = None
        if not self.host.settings().get("ai_support", False):
            self.idle()
            self.message.setText("AI support is off. Reply discarded.")
            return
        if error is None and result is None:
            self.idle()
            self.message.setText(NO_REPLY)
            return
        if error is None:
            try:
                self.items = parse_reply(result, self.rules, sources=sources, use=use, count=count, kind=kind)
            except DraftReplyError as exc:
                error = exc
        if error is not None:
            self.idle()
            self.message.setText(str(error))
        else:
            self.accept()
