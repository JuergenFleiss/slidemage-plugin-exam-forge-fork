# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Questions tab: topics, slides and the questions of one slide in three columns; source checks and XML export."""
import os
import tempfile
from dataclasses import replace
from datetime import datetime
from html import escape
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..bank import BankError
from ..moodle_xml import export_moodle_xml
from ..sources import check_bank, link_slide
from ..topics import topic_color, topic_of
from .drafting import DraftDialog, plural
from .editor import accent_color, is_dark
from .review_changes import ReviewChangesDialog
from .topic_dialog import TopicDialog
from .work import fresh_deck

KEY = Qt.ItemDataRole.UserRole
REVIEW = ("attention", "unlinked")
STATUS = {"draft": ("Draft", "neutral"), "reviewed": ("Reviewed", "ok"), "retired": ("Retired", "muted")}
USES = {"revision": "Revision quiz", "elp": "ELP exam"}
ELP_NOTE = "ELP questions without a linked slide are left out of exam counts until they are linked."


def slide_ids(item):
    return list(dict.fromkeys(link.slide_id for link in item.sources))


def ai_draft(item):
    return item.status == "draft" and (item.origin.kind == "ai" or bool(item.ai_edit_route))


def needs_attention(item):
    return (item.source_changed and item.status != "retired") or ai_draft(item)


def retired_last(items):
    return sorted(items, key=lambda item: item.status == "retired")


def passive(widget):
    widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    return widget


def label(text="", *, bold=False, wrap=False, clip=False):
    widget = QLabel(text)
    widget.setWordWrap(wrap)
    if bold:
        font = widget.font()
        font.setBold(True)
        widget.setFont(font)
    if clip:  # A long title is cut off by the column instead of widening it.
        widget.setMinimumWidth(0)
        widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return widget


def line(*parts):
    box = QHBoxLayout()
    box.setSpacing(6)
    for part in parts:
        box.addWidget(part)
    box.addStretch()
    return box


def row(lead=(), body=(), trail=()):
    """A list row: leading widgets, a column of lines, trailing widgets."""
    widget = QWidget()
    outer = QHBoxLayout(widget)
    outer.setContentsMargins(6, 4, 6, 4)
    for part in lead:
        outer.addWidget(part)
    column = QVBoxLayout()
    column.setSpacing(2)
    for part in body:
        column.addLayout(part) if isinstance(part, QHBoxLayout) else column.addWidget(part)
    outer.addLayout(column, 1)
    for part in trail:
        outer.addWidget(part)
    return widget


class Fit(QObject):
    """Row heights follow the list's width, so wrapped text is never clipped."""

    def __init__(self, listw):
        super().__init__(listw)
        self.listw, self.width = listw, -1
        listw.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize and obj.width() != self.width:
            self.refit()
        return False

    def refit(self):
        self.width = width = self.listw.viewport().width()
        for index in range(self.listw.count()):
            entry = self.listw.item(index)
            widget = self.listw.itemWidget(entry)
            if widget is not None:
                layout = widget.layout()
                height = layout.totalHeightForWidth(width) if layout.hasHeightForWidth() else 0
                entry.setSizeHint(QSize(width, max(height, widget.sizeHint().height())))


class QuestionsTab:
    def build_questions_tab(self):
        host = self.host
        self.view, self.sel_slide = "all", None
        self.slides = None
        self.slide_gen = 0
        self.changed_links, self.checked_at = {}, None
        self.elp_told, self.elp_text = False, ""
        self.syncing = False
        self.topic_dialog = self.review_dialog = None
        split = QSplitter()
        self.topic_list, self.slide_list = QListWidget(), QListWidget()
        self.stack = QStackedWidget()
        self.cards = QListWidget()
        for listw in (self.topic_list, self.slide_list, self.cards):
            listw.setFrameShape(QFrame.Shape.NoFrame)
            listw.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
            listw.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            listw.fit = Fit(listw)
        self.cards.setSpacing(3)
        split.addWidget(self.topic_list)
        split.addWidget(self.slide_list)
        split.addWidget(self.stack)
        self.slide_page = host.Page()
        self.slide_page.body.setContentsMargins(14, 10, 14, 10)
        body = self.slide_page.body
        head = QHBoxLayout()
        titles = QVBoxLayout()
        self.crumb = QLabel()
        self.crumb.setTextFormat(Qt.TextFormat.RichText)
        self.slide_title = label(bold=True, clip=True)
        titles.addWidget(self.crumb)
        titles.addWidget(self.slide_title)
        head.addLayout(titles, 1)
        self.open_button = host.Button("Open slide", compact=True)
        self.open_button.clicked.connect(self.open_selected_slide)
        head.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignTop)
        body.addLayout(head)
        self.changed_bar = QWidget()
        bar = QHBoxLayout(self.changed_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        self.changed_label = QLabel()
        self.changed_label.setWordWrap(True)
        self.tint(self.changed_label)
        self.review_button = host.Button("Review changes", compact=True)
        self.review_button.clicked.connect(self.review_slide)
        bar.addWidget(self.changed_label, 1)
        bar.addWidget(self.review_button)
        body.addWidget(self.changed_bar)
        self.empty_label = QLabel("No questions on this slide yet.")
        body.addWidget(self.empty_label)
        self.draft_button = host.Button("✦ Draft with AI")
        self.tint(self.draft_button, True)
        self.draft_button.clicked.connect(self.draft_with_ai)
        self.write_button = host.Button("Write question", "primary")
        self.write_button.clicked.connect(self.write_question)
        self.actions = QWidget()
        buttons = QHBoxLayout(self.actions)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addWidget(self.draft_button)
        buttons.addWidget(self.write_button)
        buttons.addStretch()
        body.addWidget(self.actions)
        body.addWidget(self.cards, 1)
        self.stack.addWidget(self.slide_page)
        self.placeholder = QLabel()
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.placeholder)
        self.build_editor(host, self.stack)
        self.editor.setEnabled(False)
        self.refresh_ai_support()
        split.setSizes([250, 320, 610])
        split.setStretchFactor(2, 1)
        self.topic_list.currentItemChanged.connect(self.topic_changed)
        self.slide_list.currentItemChanged.connect(self.slide_changed)
        self.slide_list.itemClicked.connect(self.review_row_opened)
        self.slide_list.itemActivated.connect(self.review_row_opened)
        self.cards.itemClicked.connect(self.card_opened)
        self.cards.itemActivated.connect(self.card_opened)
        return split

    def refresh_ai_support(self):
        self.draft_button.setVisible(bool(self.host.settings().get("ai_support", False)))

    # -- model ---------------------------------------------------------------------------------------------

    def changed_on(self, item):
        """The linked slides that changed under a flagged question; all of them until a check says which."""
        if not item.source_changed or item.status == "retired":
            return []
        own = slide_ids(item)
        return [s for s in own if s in self.changed_links.get(item.id, ())] or own

    def changed_slides(self):
        return {slide for item in self.items for slide in self.changed_on(item)}

    def slide_ref(self, slide_id):
        return next((ref for ref in self.slides or () if ref.id == slide_id), None)

    def slide_name(self, slide_id):
        ref = self.slide_ref(slide_id)
        return f"Slide {ref.position} · {ref.title or ref.id_slug}" if ref else f"Slide {slide_id}"

    def owner(self, slide_id):
        return next((t for t in self.topics if slide_id in t.slides), None)

    def topic_by_id(self, topic_id):
        return next((t for t in self.topics if t.id == topic_id), None)

    def unlinked(self):
        return [item for item in self.items if not item.sources]

    def view_slides(self):
        refs = self.slides or []
        if self.view == "all":
            return refs
        if self.view == "none":
            return [ref for ref in refs if self.owner(ref.id) is None]
        topic = self.topic_by_id(self.view)
        return [ref for ref in refs if topic and ref.id in topic.slides]

    def view_items(self):
        if self.view == "attention":
            return [item for item in self.items if needs_attention(item)]
        return retired_last(self.unlinked()) if self.view == "unlinked" else []

    def view_name(self):
        topic = self.topic_by_id(self.view)
        return topic.name if topic else {"all": "All slides", "none": "No topic", "attention": "Needs attention",
                                         "unlinked": "Unlinked questions"}[self.view]

    def ensure_view(self):
        if (self.view not in ("all", "none", *REVIEW) and self.topic_by_id(self.view) is None
                or self.view == "unlinked" and not self.unlinked()
                or self.view == "none" and self.slides is not None and not self.view_slides()):
            self.view = "all"
        if self.view in REVIEW:
            self.sel_slide = None
        elif self.slides and (self.sel_slide is None or self.sel_slide not in {r.id for r in self.view_slides()}):
            shown = self.view_slides()
            self.sel_slide = shown[0].id if shown else None

    # -- rendering -----------------------------------------------------------------------------------------

    def render(self):
        self.ensure_view()
        self.syncing = True
        try:
            self.render_topics()
            self.render_slides()
            self.render_slide_page()
            self.show_page()
        finally:
            self.syncing = False

    def fill(self, listw, build, current):
        """Rebuild a list, keeping the scroll position and the current row."""
        bar = listw.verticalScrollBar()
        scroll = bar.value()
        listw.clear()
        build(lambda *args, **kw: self.add_row(listw, current, *args, **kw))
        listw.fit.refit()
        listw.doItemsLayout()
        bar.setValue(scroll)

    def add_row(self, listw, current, key, widget, selectable=True, dim=False):
        entry = QListWidgetItem()
        entry.setData(KEY, key)
        entry.setSizeHint(widget.sizeHint())
        if not selectable:
            entry.setFlags(Qt.ItemFlag.ItemIsEnabled)
        listw.addItem(entry)
        listw.setItemWidget(entry, widget)
        if dim:
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(0.5)
            widget.setGraphicsEffect(effect)
        if selectable and key == current:
            listw.setCurrentItem(entry)

    def dot(self, topic):
        text = f"<span style='color:{topic_color(topic.color, is_dark(self))}'>●</span>" if topic else ""
        widget = QLabel(text)
        widget.setTextFormat(Qt.TextFormat.RichText)
        widget.setFixedWidth(14)
        return passive(widget)

    def tint(self, widget, amber=False):
        # Read the window's palette: a widget that is not yet in the window still has the application's.
        widget.setStyleSheet(f"color: {accent_color(self, amber)};")

    def chip(self, text, tone="neutral", amber=None):
        chip = self.host.Chip(text, tone)
        if amber is not None:
            self.tint(chip, amber)
        return passive(chip)

    def render_topics(self):
        host, items = self.host, self.items
        attention = [item for item in items if needs_attention(item)]
        owned = {slide for t in self.topics for slide in t.slides}
        bare = [r for r in self.slides or () if r.id not in owned]
        topic_ids = {item.id: topic_of(item, self.topics) for item in items}

        def entry(title, subtitle, topic=None, badge=0, edit=None):
            parts = [passive(label(title, bold=True, clip=True)), passive(label(subtitle, clip=True))]
            trail = [passive(host.Chip(str(badge)))] if badge else []
            if edit:
                pencil = host.Button("✎", compact=True)
                pencil.setToolTip("Edit topic")
                pencil.setAccessibleName(f"Edit topic {title}")
                pencil.clicked.connect(lambda _=False, i=edit: self.edit_topic(i))
                trail.append(pencil)
            return row([self.dot(topic)], parts, trail)

        def build(add):
            slides = f"{plural(len(self.slides), 'slide')} · " if self.slides is not None else ""
            add("all", entry("All slides", f"{slides}{plural(len(items), 'question')}"))
            for topic in self.topics:
                mine = [i for i in items if topic_ids[i.id] is not None and topic_ids[i.id].id == topic.id]
                add(topic.id, entry(topic.name, f"{plural(len(topic.slides), 'slide')} · {plural(len(mine), 'question')}",
                                    topic, sum(needs_attention(i) for i in mine), topic.id))
            if bare:
                loose = [i for i in items if i.sources and topic_ids[i.id] is None]
                add("none", entry("No topic", f"{plural(len(bare), 'slide')} · {plural(len(loose), 'question')}",
                                  None, sum(needs_attention(i) for i in loose)))
            new = host.Button("+ New topic", compact=True)
            new.clicked.connect(lambda: self.edit_topic())
            add(None, row([new], []), False)
            add(None, row([], [passive(label("Review"))]), False)
            changed, drafts = len(self.changed_slides()), sum(ai_draft(i) for i in items)
            add("attention", entry("Needs attention", f"{plural(changed, 'changed slide')} · {plural(drafts, 'AI draft')}",
                                   None, len(attention)))
            if self.unlinked():
                add("unlinked", entry("Unlinked questions", plural(len(self.unlinked()), "question")))

        self.fill(self.topic_list, build, self.view)

    def render_slides(self):
        by_slide = {}
        for item in self.items:
            for slide in slide_ids(item):
                by_slide.setdefault(slide, []).append(item)

        def build(add):
            if self.view in REVIEW:
                for item in self.view_items():
                    add(item.id, self.card(item, compact=True), dim=item.status == "retired")
                return
            for ref in self.view_slides():
                mine = by_slide.get(ref.id, [])
                box = QLabel(str(ref.position or "·"))
                box.setAlignment(Qt.AlignmentFlag.AlignCenter)
                box.setFrameShape(QFrame.Shape.Box)
                box.setFixedSize(34, 26)
                chips = [self.chip("Changed", amber=False)] if any(ref.id in self.changed_on(i) for i in mine) else []
                drafts = sum(ai_draft(i) for i in mine)
                if drafts:
                    chips.append(self.chip(plural(drafts, "AI draft"), amber=True))
                text = plural(len(mine), "question") if mine else "No questions yet"
                add(ref.id, row([passive(box), self.dot(self.owner(ref.id))],
                                [passive(label(ref.title or ref.id_slug, bold=True, clip=True)), line(passive(label(text)), *chips)]))
            if not self.slides:
                add(None, row([], [passive(label("Loading slides…" if self.slides is None else "No slides."))]), False)

        self.fill(self.slide_list, build, None if self.view in REVIEW else self.sel_slide)

    def card(self, item, compact=False):
        stem = " ".join(item.stem.split()) or "Untitled question"
        text = passive(label(stem if len(stem) < 180 else stem[:177] + "…", wrap=True))
        chips = [self.chip(*STATUS[item.status]), self.chip(USES[item.use], "muted")]
        if item.difficulty in ("easy", "hard"):
            chips.append(self.chip(item.difficulty.title(), "muted"))
        if ai_draft(item):
            chips.append(self.chip("AI draft", amber=True))
        if item.source_changed and item.status != "retired":
            chips.append(self.chip("Slide changed", amber=False))
        review = self.view in REVIEW
        others = [s for s in slide_ids(item) if review or s != self.sel_slide]
        if others:
            names = ", ".join(str(ref.position) if (ref := self.slide_ref(s)) else s for s in others)
            chips.append(passive(label(("On slide " if review else "Also on slide ") + names)))
        elif review:
            chips.append(passive(label("No slide linked")))
        summary = plural(len(item.options), "answer")
        if item.use == "elp":
            summary += f" · {sum(o.correct for o in item.options)} correct"
        return passive(row([], [text, line(*chips)], [] if compact else [passive(label(summary))]))

    def render_slide_page(self):
        slide = self.sel_slide
        if slide is None:
            return
        ref, topic = self.slide_ref(slide), self.owner(slide)
        where = escape(topic.name) if topic else "No topic"
        number = f" · slide {ref.position}" if ref else ""
        self.crumb.setText(f"{self.dot(topic).text()} {where}{number}")
        self.slide_title.setText((ref.title or ref.id_slug) if ref else f"Slide {slide}")
        self.open_button.setEnabled(ref is not None)
        mine = retired_last([i for i in self.items if slide in slide_ids(i)])
        changed = [i for i in mine if slide in self.changed_on(i)]
        self.changed_bar.setVisible(bool(changed))
        self.changed_label.setText(f"This slide changed after {plural(len(changed), 'question')} "
                                   f"{'was' if len(changed) == 1 else 'were'} written.")
        self.empty_label.setVisible(not mine)
        self.cards.setVisible(bool(mine))
        chosen = self.cards.currentItem()

        def build(add):
            for item in mine:
                add(item.id, self.card(item), dim=item.status == "retired")

        self.fill(self.cards, build, chosen.data(KEY) if chosen else None)

    def show_page(self):
        if self.stack.currentWidget() is self.editor:
            return
        if self.view in REVIEW or self.sel_slide is None:
            self.placeholder.setText("Select a question to open it." if self.view in REVIEW
                              else "Select a slide to see its questions.")
            self.stack.setCurrentWidget(self.placeholder)
        else:
            self.stack.setCurrentWidget(self.slide_page)

    def sync_selection(self):
        """Show the stored selection again, after a navigation was refused."""
        self.syncing = True
        try:
            for listw, key in ((self.topic_list, self.view),
                               (self.slide_list, None if self.view in REVIEW else self.sel_slide)):
                listw.setCurrentItem(next((listw.item(i) for i in range(listw.count())
                                           if key is not None and listw.item(i).data(KEY) == key), None))
        finally:
            self.syncing = False

    def update_footer(self):
        self.checked_label.setText("Slides not checked yet" if self.checked_at is None else
                                   f"Slides checked {self.checked_at:%H:%M} · {len(self.changed_slides())} changed")

    # -- navigation ----------------------------------------------------------------------------------------

    def leave(self):
        """Ask about unsaved work before the view changes; drop the editor when the answer allows it."""
        if not self.may_leave():
            return False
        self.drop_editor()
        return True

    def drop_editor(self):
        self.current = self.baseline = None
        self.new_item = False
        self.links = self.attachments = ()
        self.editor.setEnabled(False)

    def topic_changed(self, entry, _previous):
        key = entry.data(KEY) if entry else None
        if self.syncing or key is None or key == self.view:
            return
        if not self.leave():
            self.sync_selection()
            return
        self.view, self.sel_slide = key, None
        self.render()

    def slide_changed(self, entry, _previous):
        key = entry.data(KEY) if entry else None
        if self.syncing or key is None or self.view in REVIEW or key == self.sel_slide:
            return
        if not self.leave():
            self.sync_selection()
            return
        self.sel_slide = key
        self.render()

    def review_row_opened(self, entry):
        if self.view in REVIEW and entry.data(KEY) is not None:
            self.open_card(entry.data(KEY))

    def card_opened(self, entry):
        self.open_card(entry.data(KEY))

    def open_card(self, item_id):
        if not self.may_leave():
            return False
        try:
            item = self.bank.get(item_id)
        except (OSError, BankError, KeyError) as exc:
            self.error(exc)
            return False
        self.populate(item)
        return True

    def populate(self, item, new=False):
        super().populate(item, new)
        self.stack.setCurrentWidget(self.editor)
        slide = self.sel_slide
        self.set_back_label(f"‹ {self.slide_name(slide)}" if slide and self.view not in REVIEW
                            else f"‹ {self.view_name()}")

    def leave_editor(self):
        self.drop_editor()
        self.stack.setCurrentWidget(self.placeholder)
        self.render()

    def select(self, slide_id, view=None):
        """Show a slide without asking; the caller has already left the editor."""
        if view is None:
            owner = self.owner(slide_id)
            fits = self.view == "all" or (owner is None and self.view == "none") or (owner and self.view == owner.id)
            view = self.view if fits else "all"
        self.view, self.sel_slide = view, slide_id
        self.render()

    def open_question(self, item_id):
        """Switch to the Questions tab and edit a question, on its first linked slide if it has one."""
        if not self.may_leave():
            return False
        try:
            item = self.bank.get(item_id)
        except (OSError, BankError, KeyError) as exc:
            self.error(exc)
            return False
        self.drop_editor()
        self.stack.setCurrentWidget(self.placeholder)
        slides = slide_ids(item)
        if slides:
            self.select(slides[0])
        else:
            self.view, self.sel_slide = "unlinked", None
            self.render()
        self.tabs.setCurrentIndex(0)
        self.populate(item)
        return True

    def open_selected_slide(self):
        ref = self.slide_ref(self.sel_slide)
        if ref is not None:
            try:
                self.host.open_slide(ref)
            except (OSError, ValueError, RuntimeError) as exc:
                self.error(exc)

    # -- topics --------------------------------------------------------------------------------------------

    def edit_topic(self, topic_id=None):
        if self.topic_dialog is not None and self.topic_dialog.isVisible():
            return
        dialog = self.topic_dialog = TopicDialog(self, self.topic_by_id(topic_id))
        dialog.finished.connect(lambda code, d=dialog: self.topic_edited(d, code))
        dialog.open()

    def topic_edited(self, dialog, code):
        if code != dialog.DialogCode.Accepted:
            return
        if dialog.created and self.may_leave():
            self.drop_editor()
            self.view, self.sel_slide = dialog.topic_id, None
        self.render()
        self.exam_tab.refresh()

    # -- reviewing changes ---------------------------------------------------------------------------------

    def review_changes(self, item):
        changed = self.changed_on(item)
        self.open_review([item], changed[0] if changed else None)

    def review_slide(self):
        slide = self.sel_slide
        items = [i for i in self.items if slide in self.changed_on(i)]
        if items:
            self.open_review(items, slide)

    def open_review(self, items, slide_id):
        dialog = self.review_dialog = ReviewChangesDialog(self, items, slide_id)
        dialog.edit_requested.connect(self.open_question)
        dialog.finished.connect(self.reviewed)
        dialog.open()

    def reviewed(self, *_):
        self.reload()
        if self.current is None or self.new_item or self.dirty():
            return
        try:
            latest = self.bank.get(self.current.id)
        except (OSError, BankError, KeyError):
            return
        if latest != self.current:
            self.populate(latest)

    # -- writing and drafting ------------------------------------------------------------------------------

    def write_question(self):
        ref = self.slide_ref(self.sel_slide)
        if ref is None or not self.new_question():
            return
        created, host, deck = self.current, self.host, self.deck
        view, language = self.last_link_context

        def work():
            refs = [r for r in host.deck_slides(fresh_deck(deck), view, language) if r.id == ref.id]
            return link_slide(host, refs[0] if refs else ref, view)

        def done(link):
            if self.current is created:
                self.links += (link,)
                self.render_links()

        self.work(work, done)

    def start_question(self, link):
        """Sidebar: select the slide and open a new question already linked to it."""
        if not self.may_leave():
            return
        self.drop_editor()
        self.stack.setCurrentWidget(self.placeholder)
        self.select(link.slide_id)
        if self.new_question():
            self.links = (link,)
            self.render_links()

    def draft_with_ai(self):
        if not self.host.settings().get("ai_support", False) or not self.may_leave():
            return
        topic = self.topic_by_id(self.view)
        dialog = DraftDialog(self.host, self.deck, self.bank, self, topics=self.topics,
                             topic_id=topic.id if topic else None, preselect=self.sel_slide)
        self.drafting_dialog = dialog
        self.set_busy(True)

        def finished(code):
            self.set_busy(False)
            if code != DraftDialog.DialogCode.Accepted:
                return
            bank, items = self.bank, dialog.items

            def saved(drafts):
                self.reload()
                self.drop_editor()
                self.stack.setCurrentWidget(self.placeholder)
                slides = [s for d in drafts for s in slide_ids(d)]
                if slides:
                    self.select(slides[0])
                message = f"{plural(len(drafts), 'AI draft')} added · review before use"
                self.notice.setText(message)
                self.host.show_message(message)

            self.work(lambda: [bank.create(item) for item in items], saved)

        dialog.finished.connect(finished)
        dialog.open()

    # -- data ----------------------------------------------------------------------------------------------

    def reload(self):
        try:
            self.items, errors = self.bank.load()
            if errors:
                self.error("\n".join(map(str, errors)))
            if not self.elp_told and any(i.use == "elp" and not i.sources and i.status != "retired" for i in self.items):
                self.elp_told, self.elp_text = True, ELP_NOTE
                self.notice.setText(ELP_NOTE)
            self.render()
            self.update_footer()
            self.changed(self.items)
            self.exam_tab.refresh()
        except (OSError, BankError, ValueError) as exc:
            self.error(exc)

    def load_slides(self):
        self.slide_gen += 1
        generation, host, deck = self.slide_gen, self.host, self.deck
        try:
            language = host.deck_language(deck)
        except Exception:
            language = "en"

        def done(refs, error):
            if generation != self.slide_gen:
                return
            if error:
                self.error(error)
                return
            self.slides = list({ref.id: ref for ref in reversed(refs)}.values())[::-1]
            self.render()
            self.update_footer()

        host.run_in_background(lambda: host.deck_slides(fresh_deck(deck), "instructor", language), done)

    def record_check(self, report, partial):
        if not partial:
            self.changed_links = {}
        for checked in report.items:
            changed = [s.source.slide_id for s in checked.sources if s.status != "unchanged"]
            if checked.source_changed and changed:
                self.changed_links[checked.item.id] = changed
            else:
                self.changed_links.pop(checked.item.id, None)
        self.checked_at = datetime.now()

    def check_sources(self, *_, then=None, item_ids=None):
        if self.busy:
            return
        self.set_busy(True)
        self.notice.setText("Checking sources…")

        def checked(report, error):
            self.set_busy(False)
            if report is not None:
                self.record_check(report, item_ids is not None)
            self.reload()
            self.load_slides()
            if error:
                self.error(error)
                return
            if report.errors:
                self.error("\n".join(map(str, report.errors)))
                return
            if self.current and not self.new_item:
                # A marker is not revision content. Leave every typed field intact.
                latest = next((item for item in self.items if item.id == self.current.id), None)
                if latest:
                    self.current = replace(self.current, source_changed=latest.source_changed)
                    self.baseline = replace(self.baseline, source_changed=latest.source_changed)
                    self.update_header(self.current)
            text = f"Source check complete · {sum(i.source_changed for i in self.items)} changed."
            self.notice.setText(f"{text} {self.elp_text}".strip())
            self.elp_text = ""
            if then:
                then(report)

        def prepared(deck, error):
            if error:
                checked(None, error)
            else:
                check_bank(self.host, deck, self.bank, checked, item_ids=item_ids)

        deck = self.deck
        self.host.run_in_background(lambda: fresh_deck(deck), prepared)

    def export(self):
        if not self.may_leave():
            return
        self.check_sources(then=self.export_checked)

    def export_checked(self, report):
        items = [checked.item for checked in report.items
                 if checked.item.status == "reviewed" and checked.item.use == "revision"]
        if not items:
            self.error("No reviewed revision-quiz questions to export.")
            return
        changed = [checked for checked in report.items if checked.item in items and checked.source_changed]
        if changed:
            details = [f"• {checked.item.stem} ({checked.item.id}): {source.source.slide_id} "
                       f"({source.source.view}, {source.source.language}) — {source.status} {source.message}"
                       for checked in changed for source in checked.sources if source.status != "unchanged"]
            answer = QMessageBox.warning(self, "Source changed", "Changed sources:\n" +
                                         "\n".join(details) +
                                         "\n\nContinue exporting?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        filename, _ = QFileDialog.getSaveFileName(self, "Export reviewed revision questions", "revision-quiz.xml", "Moodle XML (*.xml)")
        if not filename:
            return
        if not filename.lower().endswith(".xml"):
            filename += ".xml"
        self.set_busy(True)

        def work():
            if any(self.bank.get(item.id) != item for item in items):
                raise BankError("A question changed during export. Check slides and try again.")
            xml = export_moodle_xml(items, bank=self.bank)
            path = Path(filename)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".moodle-", delete=False) as stream:
                    temporary = stream.name
                    stream.write(xml)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
            return len(items)

        def done(count, error):
            self.set_busy(False)
            if error:
                self.error(error)
            else:
                self.notice.setText(f"Exported {count} reviewed questions to {filename}.")
        self.host.run_in_background(work, done)
