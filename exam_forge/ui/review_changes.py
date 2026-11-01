# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare old and new slide text and resolve each affected question."""
from dataclasses import replace
from html import escape

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)
from slidemage.plugin_api import AiChatError

from ..ai_support import ai_service
from ..diffing import word_diff
from ..drafting import DraftReplyError, build_update_request, parse_update_reply
from ..sources import _resolve, rebaseline_links
from .editor import accent, accent_color
from .work import fresh_deck

NO_SNAPSHOT = "Earlier text not stored"


def ordered(item, slide_id):
    """The item's links, the one for `slide_id` first."""
    return sorted(item.sources, key=lambda link: link.slide_id != slide_id)


class _Row(QWidget):
    def __init__(self, host, item):
        super().__init__()
        self.item, self.resolved = item, False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        top = QHBoxLayout()
        self.stem = QLabel(item.stem or "(no question text)")
        self.stem.setWordWrap(True)
        top.addWidget(self.stem, 1)
        self.chips = QHBoxLayout()
        self.chip = None
        top.addLayout(self.chips)
        self.keep, self.edit, self.update = (host.Button("Still correct"), host.Button("Edit"),
                                             host.Button("Update with AI"))
        for button in (self.keep, self.edit, self.update):
            top.addWidget(button)
        layout.addLayout(top)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.hide()
        layout.addWidget(self.note)

    def say(self, text):
        self.note.setText(text)
        self.note.setVisible(bool(text))


class ReviewChangesDialog(QDialog):
    edit_requested = Signal(str)

    def __init__(self, window, items, slide_id=None):
        super().__init__(window)
        self.shell, self.slide_id = window, slide_id
        self.host = host = window.host
        self.items = [item for item in items if item.status != "retired"]
        self.current = {}
        self.busy, self.generation = False, 0
        self.state = state = {"active": True, "cancel": None}

        def stop(*_):
            state["active"] = False
            cancel, state["cancel"] = state["cancel"], None
            if cancel:
                cancel()

        self.finished.connect(stop)
        self.destroyed.connect(stop)
        self.setWindowTitle("Review changes")
        self.resize(820, 640)
        layout = QVBoxLayout(self)
        panes = QHBoxLayout()
        self.old_pane, self.new_pane = QTextBrowser(), QTextBrowser()
        for caption, pane in (("Earlier slide text", self.old_pane), ("Current slide text", self.new_pane)):
            column = QVBoxLayout()
            column.addWidget(QLabel(caption))
            column.addWidget(pane)
            panes.addLayout(column)
        layout.addLayout(panes, 3)
        self.list = QListWidget()
        self.rows = []
        for item in self.items:
            row = _Row(host, item)
            row.keep.clicked.connect(lambda _=False, r=row: self.keep(r))
            row.edit.clicked.connect(lambda _=False, r=row: self.edit(r))
            row.update.clicked.connect(lambda _=False, r=row: self.ask_ai(r))
            entry = QListWidgetItem()
            entry.setSizeHint(row.sizeHint())
            self.list.addItem(entry)
            self.list.setItemWidget(entry, row)
            self.rows.append(row)
        layout.addWidget(self.list, 2)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        done = host.Button("Done", "primary")
        done.clicked.connect(self.accept)
        layout.addWidget(done)
        self.list.currentRowChanged.connect(self.show_row)
        self.sync()
        if self.rows:
            self.list.setCurrentRow(0)
        self.load()

    def load(self):
        self.generation += 1
        generation, items, slide_id = self.generation, self.items, self.slide_id
        self.message.setText("Reading the current slide text…")

        def work():
            deck, decks, out = fresh_deck(self.shell.deck), {}, {}
            for item in items:
                entries = []
                for link in ordered(item, slide_id):
                    try:
                        context = (link.view, link.language)
                        if context not in decks:
                            decks[context] = self.host.deck_slides(deck, *context)
                        ref = _resolve(decks[context], link)
                        entries.append((link, self.host.slide_source(ref).text, ref.title or ""))
                    except Exception as exc:
                        entries.append((link, exc, ""))
                out[item.id] = entries or [(None, ValueError("This question has no linked slide."), "")]
            return out

        def done(result, error):
            if not self.state["active"] or generation != self.generation:
                return
            self.current = result if not error else {i.id: [(None, error, "")] for i in items}
            self.message.setText("")
            self.sync()
            self.show_row(self.list.currentRow())

        self.host.run_in_background(work, done)

    def sync(self):
        ai = bool(self.host.settings().get("ai_support", False))
        for row in self.rows:
            entries = self.current.get(row.item.id)
            ok = bool(entries) and all(isinstance(text, str) for _, text, _ in entries)
            can = not self.busy and ok and not row.resolved
            row.keep.setEnabled(can)
            row.update.setVisible(ai)
            row.update.setEnabled(can)
            row.edit.setEnabled(not self.busy)

    def show_row(self, index):
        for pane in (self.old_pane, self.new_pane):
            pane.clear()
        if not 0 <= index < len(self.rows):
            return
        entries = self.current.get(self.rows[index].item.id)
        if entries is None:
            return
        old, new = [], []
        for link, text, title in entries:
            name = f"Slide {link.slide_id}" + (f" · {title}" if title else "") if link else ""
            head = f"<p><b>{escape(name)}</b></p>" if link else ""
            if not isinstance(text, str):
                note = f"<p>{escape(str(text))}</p>"
                old.append(head + note)
                new.append(head + note)
            elif not link.text:
                old.append(head + f"<p><i>{NO_SNAPSHOT}</i></p>")
                new.append(head + self.html([(text, False)], None))
            elif link.text != text:
                left, right = word_diff(link.text, text)
                old.append(head + self.html(left, "old"))
                new.append(head + self.html(right, "new"))
        if not new:
            new.append("<p>The slide text matches the stored text.</p>")
        self.old_pane.setHtml("".join(old))
        self.new_pane.setHtml("".join(new))

    def html(self, segments, side):
        color = accent_color(self.new_pane)
        out = []
        for text, changed in segments:
            part = escape(text).replace("\n", "<br>")
            if changed and side == "old":
                part = f'<span style="text-decoration: line-through;">{part}</span>'
            elif changed:
                part = f'<span style="color: {color}; font-weight: 600; text-decoration: underline;">{part}</span>'
            out.append(part)
        return f'<div style="white-space: pre-wrap;">{"".join(out)}</div>'

    def begin(self, row, text):
        self.busy = True
        row.say(text)
        self.sync()

    def finish(self, row, *, error=None, chip=None, amber=False, item=None, report=False):
        self.busy = False
        if item is not None:
            row.item = item
            row.stem.setText(item.stem or "(no question text)")
        if chip and not error:
            if row.chip:
                row.chip.deleteLater()
            row.chip = self.host.Chip(chip)
            accent(row.chip, amber)
            row.chips.addWidget(row.chip)
            row.resolved = True
        row.say(str(error) if error else "")
        self.sync()
        if item is not None:
            self.shell.reload()
        elif error is not None and report:
            self.shell.error(error)

    def keep(self, row):
        if self.busy:
            return
        item, host, bank = row.item, self.host, self.shell.bank
        self.begin(row, "Saving…")

        def work():
            links = rebaseline_links(host, fresh_deck(self.shell.deck), item)
            return bank.rebaseline(item.id, item.revision, links)

        def done(result, error):
            if self.state["active"]:
                self.finish(row, error=error, item=result, report=True, chip="Kept · matches the current slide")

        host.run_in_background(work, done)

    def edit(self, row):
        if not self.busy:
            self.edit_requested.emit(row.item.id)
            self.accept()

    def ask_ai(self, row):
        if self.busy:
            return
        item, host = row.item, self.host
        try:
            service = ai_service(host)
            if service is None or not service.available():
                row.say("Enable AI support and an AI plugin such as Claude or Ollama in Settings.")
                return
        except AiChatError as exc:
            row.say(str(exc))
            return
        self.begin(row, "Preparing the question and current slide text…")

        def work():
            links = rebaseline_links(host, fresh_deck(self.shell.deck), item)
            return links, build_update_request(item, "\n\n".join(link.text for link in links))

        def ready(prepared, error):
            if not self.state["active"]:
                return
            if error:
                self.finish(row, error=error, report=True)
                return
            links, request = prepared
            try:
                service = ai_service(host)
                if service is None:
                    self.finish(row, error="AI support is off.")
                    return
                row.say("Asking the AI… Closing this window discards the reply.")
                self.state["cancel"] = service.request(request, lambda r, e: self.replied(row, item, links, r, e))
            except AiChatError as exc:
                self.finish(row, error=exc)

        host.run_in_background(work, ready)

    def replied(self, row, item, links, result, error):
        if not self.state["active"]:
            return
        self.state["cancel"] = None
        if not self.host.settings().get("ai_support", False):
            self.finish(row, error="AI support is off. Reply discarded.")
            return
        if error is None and result is None:
            self.finish(row)  # The host's decline/cancel is deliberately quiet.
            return
        if error is None:
            try:
                updated = parse_update_reply(result, item)
            except DraftReplyError as exc:
                error = exc
        if error is not None:
            self.finish(row, error=error)
            return
        bank = self.shell.bank

        def work():
            bank.save(replace(updated, sources=links))
            return bank.set_source_changed(item.id, False)  # Written against the new text.

        def done(saved, error):
            if self.state["active"]:
                self.finish(row, error=error, item=saved, report=True, chip="Updated by AI · review the draft", amber=True)

        self.host.run_in_background(work, done)
