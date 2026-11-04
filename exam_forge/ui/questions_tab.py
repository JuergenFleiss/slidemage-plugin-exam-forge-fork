# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Questions tab: topics, slides and the questions of one slide in three columns; source checks, XML and table
export.
The middle column lists the slides, each with its number of questions, or the questions themselves."""
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
)

from ..bank import BankError, atomic_write_bytes
from ..drafting import ai_enabled
from ..model import KINDS, STATUS, USES, agree, ai_draft, deck_order, plural, slide_ids, slide_label, topic_of
from ..moodle_xml import export_moodle_xml
from ..question_document import questions_html
from ..rules import question_issues
from ..sources import check_bank, fresh_deck, link_slide
from ..table_export import questions_csv
from .drafting import DraftDialog
from .kit import Box, Fit, confirm, deck_language, dot, label, line, passive, row, slide_title, source_details, tone
from .review_changes import ReviewChangesDialog
from .topic_dialog import TopicDialog

KEY = Qt.ItemDataRole.UserRole
REVIEW = ("attention", "unlinked")
FOCUS = (("slides", "Slides"), ("questions", "Questions"))


def off_rules(item, rules):
    """A reviewed question that the deck's answer rules, changed since, no longer allow."""
    return item.status == "reviewed" and bool(question_issues(item, rules))


def needs_attention(item, rules):
    return (item.source_changed and item.status != "retired") or ai_draft(item) or off_rules(item, rules)


class QuestionsTab:
    def build_questions_tab(self):
        host = self.host
        self.view, self.sel_slide = "all", None
        self.focus = "slides"
        self.slides = None
        self.slide_gen = 0
        self.checked_at = None
        self.syncing = False
        self.topic_dialog = self.review_dialog = None
        split = QSplitter()
        self.topic_list, self.slide_list = host.PlainList(), host.PlainList()
        self.stack = QStackedWidget()
        self.cards = host.PlainList()
        for listw in (self.topic_list, self.slide_list, self.cards):
            listw.setFrameShape(QFrame.Shape.NoFrame)
            listw.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
            listw.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            listw.fit = Fit(listw)
        self.cards.setSpacing(4)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(8)
        self.focus_switch = host.SegmentedControl(list(FOCUS))
        self.focus_switch.set_current(self.focus)
        self.focus_switch.changed.connect(self.focus_changed)
        for listw in (self.topic_list, self.slide_list):
            column = host.Card()
            column.layout().setContentsMargins(4, 6, 4, 6)
            if listw is self.slide_list:
                column.layout().addWidget(self.focus_switch, 0, Qt.AlignmentFlag.AlignLeft)
            column.layout().addWidget(listw)
            split.addWidget(column)
        split.addWidget(self.stack)
        self.slide_page = Box()
        body = QVBoxLayout(self.slide_page)
        body.setContentsMargins(10, 4, 4, 0)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        self.crumb_dot, self.crumb = dot(), host.caption("")
        crumb = QHBoxLayout()
        crumb.setSpacing(4)
        crumb.addWidget(self.crumb_dot)
        crumb.addWidget(self.crumb, 1)
        self.slide_title = label(host, role="title", clip=True)
        titles.addLayout(crumb)
        titles.addWidget(self.slide_title)
        head.addLayout(titles, 1)
        self.open_button = host.Button("Open slide", compact=True)
        self.open_button.clicked.connect(self.open_selected_slide)
        head.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignTop)
        body.addLayout(head)
        self.changed_bar = Box()
        bar = QHBoxLayout(self.changed_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        self.changed_label = tone(host.Label(""), "violet")
        self.changed_label.setWordWrap(True)
        self.review_button = host.Button("Review changes", compact=True)
        self.review_button.clicked.connect(self.review_slide)
        bar.addWidget(self.changed_label, 1)
        bar.addWidget(self.review_button)
        body.addWidget(self.changed_bar)
        self.empty_label = host.Label("No questions on this slide yet.", "secondary")
        body.addWidget(self.empty_label)
        self.draft_button = tone(host.Button("✦ Draft with AI"), "amber")
        self.draft_button.clicked.connect(self.draft_with_ai)
        self.write_button = host.Button("Write question", "primary")
        self.write_button.clicked.connect(self.write_question)
        self.actions = Box()
        buttons = QHBoxLayout(self.actions)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addWidget(self.draft_button)
        buttons.addWidget(self.write_button)
        buttons.addStretch()
        body.addWidget(self.actions)
        body.addWidget(self.cards, 1)
        self.stack.addWidget(self.slide_page)
        self.placeholder = host.Label("", "secondary")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.placeholder)
        self.build_editor(host, self.stack)
        self.editor.setEnabled(False)
        self.refresh_ai_support()
        split.setSizes([260, 330, 590])
        split.setStretchFactor(2, 1)
        self.topic_list.currentItemChanged.connect(self.topic_changed)
        self.slide_list.currentItemChanged.connect(self.slide_changed)
        self.slide_list.itemClicked.connect(self.review_row_opened)
        self.slide_list.itemActivated.connect(self.review_row_opened)
        self.cards.itemClicked.connect(self.card_opened)
        self.cards.itemActivated.connect(self.card_opened)
        return split

    def refresh_ai_support(self):
        self.draft_button.setVisible(ai_enabled(self.host))

    # -- model ---------------------------------------------------------------------------------------------

    def changed_on(self, item):
        """The linked slides that changed under a question that is still in use."""
        return [] if item.status == "retired" else list(item.changed_slides)

    def changed_slides(self):
        return {slide for item in self.items for slide in self.changed_on(item)}

    def slide_ref(self, slide_id):
        return next((ref for ref in self.slides or () if ref.id == slide_id), None)

    def slide_name(self, slide_id):
        ref = self.slide_ref(slide_id)
        return slide_label(slide_id, ref.position, slide_title(ref)) if ref else slide_label(slide_id)

    def owner(self, slide_id):
        return next((t for t in self.topics if slide_id in t.slides), None)

    def topic_by_id(self, topic_id):
        return next((t for t in self.topics if t.id == topic_id), None)

    def unlinked(self):
        # A retired question on a slide that left the deck is flagged nowhere else, yet can be restored.
        known = {ref.id for ref in self.slides} if self.slides is not None else None
        return [item for item in self.items if not item.sources or (
            known is not None and item.status == "retired" and not known & set(slide_ids(item)))]

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
            return [item for item in self.items if needs_attention(item, self.answer_rules)]
        if self.view == "unlinked":
            return self.in_deck_order(self.unlinked())
        return self.view_questions() if self.focus == "questions" else []

    def in_deck_order(self, items):
        positions = {ref.id: n for n, ref in enumerate(self.slides or ())}
        return sorted(items, key=lambda item: deck_order(item, positions))

    def view_questions(self):
        """The questions a topic row counts, in deck order."""
        if self.view == "all":
            return self.in_deck_order(self.items)
        topic = self.topic_by_id(self.view)
        return self.in_deck_order([item for item in self.items if item.sources and getattr(
            topic_of(item, self.topics), "id", None) == getattr(topic, "id", None)])

    def listing(self):
        """Whether the middle column lists questions rather than slides."""
        return self.view in REVIEW or self.focus == "questions"

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
            if hasattr(widget, "set_selected"):
                widget.set_selected(True)

    def chip(self, text, chip_tone="neutral", accent=""):
        chip = self.host.Chip(text, chip_tone)
        chip.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        return passive(tone(chip, accent))

    def render_topics(self):
        host, items = self.host, self.items
        rules = self.answer_rules
        attention = [item for item in items if needs_attention(item, rules)]
        owned = {slide for t in self.topics for slide in t.slides}
        bare = [r for r in self.slides or () if r.id not in owned]
        topic_ids = {item.id: topic_of(item, self.topics) for item in items}

        def entry(title, subtitle, topic=None, badge=0, edit=None):
            widget = host.RowButton(title, subtitle)
            widget.layout().setContentsMargins(8, 7, 8, 7)
            widget.layout().setSpacing(8)
            widget.layout().insertWidget(0, dot(topic))
            if badge:
                widget.layout().addWidget(passive(host.Badge(str(badge))))
            if edit:
                pencil = host.Button("✎", compact=True)
                pencil.setFixedWidth(pencil.fontMetrics().horizontalAdvance("✎") + 20)
                pencil.setToolTip("Edit topic")
                pencil.setAccessibleName(f"Edit topic {title}")
                pencil.clicked.connect(lambda _=False, i=edit: self.edit_topic(i))
                widget.layout().addWidget(pencil)
            return widget

        def build(add):
            slides = f"{plural(len(self.slides), 'slide')} · " if self.slides is not None else ""
            add("all", entry("All slides", f"{slides}{plural(len(items), 'question')}"))
            for topic in self.topics:
                mine = [i for i in items if topic_ids[i.id] is not None and topic_ids[i.id].id == topic.id]
                add(topic.id, entry(topic.name, f"{plural(len(topic.slides), 'slide')} · {plural(len(mine), 'question')}",
                                    topic, sum(needs_attention(i, rules) for i in mine), topic.id))
            if bare:
                loose = [i for i in items if i.sources and topic_ids[i.id] is None]
                add("none", entry("No topic", f"{plural(len(bare), 'slide')} · {plural(len(loose), 'question')}",
                                  None, sum(needs_attention(i, rules) for i in loose)))
            new = host.Button("+ New topic", compact=True)
            new.setMinimumWidth(new.fontMetrics().boundingRect("+ New topic").width() + 28)
            new.clicked.connect(lambda: self.edit_topic())
            add(None, row([new], []), False)
            add(None, row([], [passive(label(host, "Review", role="caption"))]), False)
            changed, drafts = len(self.changed_slides()), sum(ai_draft(i) for i in items)
            broken = sum(off_rules(i, rules) for i in items)
            subtitle = f"{plural(changed, 'changed slide')} · {plural(drafts, 'AI draft')}"
            if broken:
                subtitle += f" · {broken} off the answer rules"
            add("attention", entry("Needs attention", subtitle,
                                   None, len(attention)))
            if self.unlinked():
                add("unlinked", entry("Unlinked questions", plural(len(self.unlinked()), "question")))

        self.fill(self.topic_list, build, self.view)

    def render_slides(self):
        by_slide = {}
        for item in self.items:
            for slide in slide_ids(item):
                by_slide.setdefault(slide, []).append(item)

        self.focus_switch.setVisible(self.view not in REVIEW)

        def build(add):
            if self.listing():
                for item in self.view_items():
                    add(item.id, self.card(item, compact=True), dim=item.status == "retired")
                if self.view not in REVIEW and not self.view_items():
                    add(None, row([], [passive(label(self.host, "No questions yet.", role="secondary"))]), False)
                return
            for ref in self.view_slides():
                mine = by_slide.get(ref.id, [])
                chips = [self.chip("Changed", accent="violet")] if any(ref.id in self.changed_on(i) for i in mine) else []
                drafts = sum(ai_draft(i) for i in mine)
                if drafts:
                    chips.append(self.chip(plural(drafts, "AI draft"), accent="amber"))
                text = plural(len(mine), "question") if mine else "No questions yet"
                widget = self.host.RowButton(slide_title(ref), text)
                widget.layout().setContentsMargins(8, 7, 8, 7)
                widget.layout().setSpacing(8)
                widget.layout().insertWidget(0, dot(self.owner(ref.id)))
                widget.layout().insertWidget(0, self.chip(str(ref.position or "·"), "muted"))
                for chip in chips:
                    widget.layout().addWidget(chip)
                add(ref.id, widget)
            if not self.slides:
                add(None, row([], [passive(label(self.host, "Loading slides…" if self.slides is None else "No slides.",
                                                 role="secondary"))]), False)

        self.fill(self.slide_list, build, self.listed_key())

    def listed_key(self):
        """The row of the middle column that is current: the open question's, or the selected slide's."""
        if self.listing():
            return self.current.id if self.current is not None else None
        return self.sel_slide

    def card(self, item, compact=False):
        stem = " ".join(item.stem.split()) or "Untitled question"
        host = self.host
        text = passive(label(host, stem if len(stem) < 180 else stem[:177] + "…", wrap=True))
        chips = [self.chip(*STATUS[item.status]), self.chip(USES[item.use], "muted")]
        if item.kind == "open":
            chips.append(self.chip(KINDS["open"], "muted"))
        if item.difficulty in ("easy", "hard"):
            chips.append(self.chip(item.difficulty.title(), "muted"))
        if item.media:
            chips.append(self.chip(plural(len(item.media), "image"), "muted"))
        if ai_draft(item):
            chips.append(self.chip("AI draft", accent="amber"))
        if off_rules(item, self.answer_rules):
            chips.append(self.chip("Off the answer rules", "warning"))
        if item.source_changed and item.status != "retired":
            chips.append(self.chip("Slide changed", accent="violet"))
        review = self.listing()
        others = [s for s in slide_ids(item) if review or s != self.sel_slide]
        if others:
            names = ", ".join(str(ref.position) if (ref := self.slide_ref(s)) else s for s in others)
            chips.append(passive(label(host, ("On slide " if review else "Also on slide ") + names, role="caption")))
        elif review:
            chips.append(passive(label(host, "No slide linked", role="caption")))
        summary = plural(len(item.options), "answer")
        if item.kind == "open":
            summary = "Model answer" if item.model_answer.strip() else "No model answer yet"
        elif item.use == "elp":
            summary += f" · {sum(o.correct for o in item.options)} correct"
        if compact:
            return passive(row([], [text, line(*chips)]))
        card = host.Card()
        top = QHBoxLayout()
        column = QVBoxLayout()
        column.setSpacing(6)
        column.addWidget(text)
        column.addLayout(line(*chips))
        top.addLayout(column, 1)
        top.addWidget(passive(label(host, summary, role="caption")), 0, Qt.AlignmentFlag.AlignTop)
        card.layout().addLayout(top)
        return passive(card)

    def render_slide_page(self):
        slide = self.sel_slide
        if slide is None:
            return
        ref, topic = self.slide_ref(slide), self.owner(slide)
        dot(topic, self.crumb_dot)
        self.crumb.setText((topic.name if topic else "No topic") + (f" · slide {ref.position}" if ref else ""))
        self.slide_title.setText((slide_title(ref)) if ref else f"Slide {slide}")
        self.open_button.setEnabled(ref is not None)
        mine = self.in_deck_order([i for i in self.items if slide in slide_ids(i)])
        changed = [i for i in mine if slide in self.changed_on(i)]
        self.changed_bar.setVisible(bool(changed))
        self.changed_label.setText(f"This slide changed after {plural(len(changed), 'question')} "
                                   f"{agree(len(changed), 'was', 'were')} written.")
        self.empty_label.setVisible(not mine)
        self.cards.setVisible(bool(mine))
        chosen = self.cards.currentItem()

        def build(add):
            for item in mine:
                add(item.id, self.card(item), dim=item.status == "retired")

        self.fill(self.cards, build, chosen.data(KEY) if chosen else None)

    def show_page(self):
        # The editor stays while it holds a question; a dropped one would be a disabled panel that ignores input.
        if self.stack.currentWidget() is self.editor and self.current is not None:
            return
        if self.listing() or self.sel_slide is None:
            self.placeholder.setText("Select a question to open it." if self.listing()
                              else "Select a slide to see its questions.")
            self.stack.setCurrentWidget(self.placeholder)
        else:
            self.stack.setCurrentWidget(self.slide_page)

    def sync_selection(self):
        """Show the stored selection again, after a navigation was refused."""
        self.syncing = True
        try:
            for listw, key in ((self.topic_list, self.view),
                               (self.slide_list, self.listed_key())):
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
        if self.syncing or key is None or self.listing() or key == self.sel_slide:
            return
        if not self.leave():
            self.sync_selection()
            return
        self.sel_slide = key
        self.render()

    def focus_changed(self, focus):
        if self.syncing or focus == self.focus:
            return
        if not self.leave():
            self.focus_switch.set_current(self.focus)
            return
        self.focus = focus
        self.stack.setCurrentWidget(self.placeholder)
        self.render()

    def review_row_opened(self, entry):
        if self.listing() and entry.data(KEY) is not None:
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
        self.set_back_label(f"‹ {self.slide_name(slide)}" if slide and not self.listing()
                            else f"‹ {self.view_name()}")
        if self.listing():
            self.sync_selection()

    def leave_editor(self):
        self.drop_editor()
        self.stack.setCurrentWidget(self.placeholder)
        self.render()

    def select(self, slide_id):
        """Show a slide without asking; the caller has already left the editor."""
        owner = self.owner(slide_id)
        fits = self.view == "all" or (owner is None and self.view == "none") or (owner and self.view == owner.id)
        self.view, self.sel_slide = self.view if fits else "all", slide_id
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
        created, host = self.current, self.host

        def done(link):
            if self.current is created:
                self.links += (link,)
                self.render_links()

        self.work(lambda: link_slide(host, ref, "instructor"), done)

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
        if not ai_enabled(self.host) or not self.may_leave():
            return
        topic = self.topic_by_id(self.view)
        dialog = DraftDialog(self.host, self.deck, self.bank, self, self.answer_rules, topics=self.topics,
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
                self.toast(f"{plural(len(drafts), 'AI draft')} added · review before use")

            self.work(lambda: [bank.create(item) for item in items], saved)

        dialog.finished.connect(finished)
        dialog.open()

    # -- data ----------------------------------------------------------------------------------------------

    def reload(self):
        try:
            self.items, errors = self.bank.load()
            if errors:
                self.error("\n".join(map(str, errors)))
            self.render()
            self.update_footer()
            self.changed(self.items)
            self.exam_tab.refresh()
        except (OSError, BankError, ValueError) as exc:
            self.error(exc)

    def load_slides(self):
        self.slide_gen += 1
        generation, host, deck = self.slide_gen, self.host, self.deck
        language = deck_language(host, deck)

        def done(refs, error):
            if generation != self.slide_gen:
                return
            if error:
                self.error(error)
                return
            self.slides = list({ref.id: ref for ref in reversed(refs)}.values())[::-1]
            self.render()
            self.update_footer()
            if self.current is not None:  # Its slide labels and banner come from these slides.
                self.draw_links()
                self.update_header(self.current)

        host.run_in_background(lambda: host.deck_slides(fresh_deck(deck), "instructor", language), done)

    def check_sources(self, *_, then=None, item_ids=None):
        if self.busy:
            return
        self.set_busy(True)
        self.notice.setText("Checking sources…")
        host, deck, bank = self.host, self.deck, self.bank

        def checked(report, error):
            self.set_busy(False)
            if error is None:
                self.checked_at = datetime.now()
            self.reload()      # A failed check may have recorded some questions before it stopped.
            self.load_slides()
            if error is not None:
                self.notice.setText("The source check did not finish.")
                self.error(error)
                return
            self.notice.setText(f"Source check complete · {plural(len(self.changed_slides()), 'changed slide')}.")
            if report.errors:
                self.error("\n".join(map(str, report.errors)))
                return
            if self.current and not self.new_item:
                # A marker is not revision content. Leave every typed field intact.
                latest = next((item for item in self.items if item.id == self.current.id), None)
                if latest:
                    self.current = replace(self.current, changed_slides=latest.changed_slides)
                    self.baseline = replace(self.baseline, changed_slides=latest.changed_slides)
                    self.update_header(self.current)
            if then:
                then(report)

        host.run_in_background(lambda: check_bank(host, fresh_deck(deck), bank, item_ids), checked)

    def export(self):
        if not self.may_leave():
            return
        self.check_sources(then=self.export_checked)

    def export_checked(self, report):
        rules = self.answer_rules
        reviewed = [checked.item for checked in report.items
                    if checked.item.status == "reviewed" and checked.item.use == "revision"]
        items = [item for item in reviewed if not question_issues(item, self.answer_rules)]
        skipped = len(reviewed) - len(items)
        left_out = (f" {plural(skipped, 'reviewed question')} that no longer fit the answer rules "
                    f"{agree(skipped, 'was', 'were')} left out.") if skipped else ""
        if not items:
            self.error("No reviewed revision-quiz questions to export." + left_out)
            return
        changed = [checked for checked in report.items if checked.item in items and checked.source_changed]
        if changed:
            details = [f"• {checked.item.stem} ({checked.item.id}): {line}"
                       for checked in changed for line in source_details(checked)]
            if not confirm(self, "Source changed", "Changed sources:\n" + "\n".join(details) + "\n\nContinue exporting?",
                           warning=True):
                return
        filename, _ = QFileDialog.getSaveFileName(self, "Export reviewed revision questions",
                                                  str(self.bank.folder / "revision-quiz.xml"), "Moodle XML (*.xml)")
        if not filename:
            return
        if not filename.lower().endswith(".xml"):
            filename += ".xml"
        self.set_busy(True)

        def work():
            if any(self.bank.get(item.id) != item for item in items):
                raise BankError("A question changed during export. Check slides and try again.")
            atomic_write_bytes(Path(filename), export_moodle_xml(items, bank=self.bank, rules=rules), ".moodle-")
            return len(items)

        def done(count, error):
            self.set_busy(False)
            if error:
                self.error(error)
            else:
                self.toast(f"Exported {count} reviewed questions to {filename}.{left_out}")
        self.host.run_in_background(work, done)

    def export_table(self):
        """Every question of the bank, with its answers and rationales, as a CSV table."""
        self.export_all(".csv", "CSV table (*.csv)", questions_csv)

    def export_html(self):
        """Every question of the bank, with its answers and rationales, as one HTML page like the exam key."""
        bank, language = self.bank, deck_language(self.host, self.deck)
        self.export_all(".html", "HTML page (*.html)", lambda items, topics, slides: questions_html(
            items, topics, slides, bank.read_media, language).encode("utf-8"))

    def export_all(self, suffix, kind, render):
        if not self.may_leave():
            return
        # The question folder is already private, like the export, which holds the answers.
        filename, _ = QFileDialog.getSaveFileName(self, "Export all questions with answers",
                                                  str(self.bank.folder / f"questions{suffix}"), kind)
        if not filename:
            return
        if not filename.lower().endswith(suffix):
            filename += suffix
        topics = self.topics
        slides = {ref.id: (ref.position, slide_title(ref)) for ref in self.slides or ()}
        self.set_busy(True)

        def work():
            items, corrupt = self.bank.load()
            atomic_write_bytes(Path(filename), render(items, topics, slides), ".questions-")
            return len(items), len(corrupt)

        def done(counts, error):
            self.set_busy(False)
            if error:
                self.error(error)
            elif counts[1]:
                self.error(f"Exported {plural(counts[0], 'question')} to {filename}; {plural(counts[1], 'question')} "
                           f"could not be read and {agree(counts[1], 'is', 'are')} missing.")
            else:
                self.toast(f"Exported {plural(counts[0], 'question')} to {filename}. "
                           "It holds the answers: keep it private.")
        self.host.run_in_background(work, done)

