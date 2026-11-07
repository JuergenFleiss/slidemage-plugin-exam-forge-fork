# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exam tab in three steps: counts, review and swap, then freeze and save."""
from dataclasses import replace
from secrets import randbits

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from collections import Counter

from ..exam import DIFFICULTIES, Blueprint, assemble, available, read_history, swap, topic_names
from ..model import agree, plural
from ..rules import MINUTES, POINTS, ExamRules, exam_totals
from .issue import IssueView
from .kit import clear, dot, scroll_area, tone

STEPS = ("Counts", "Review & swap", "Freeze & save")


def columns(kinds):
    """The Counts step's columns, as (question type, difficulty, heading), for the exam's question types."""
    return [(kind, d, d.title() if kind == "choice" or len(kinds) == 1 else f"Open {d}")
            for kind in kinds for d in DIFFICULTIES]


def number(value):
    return f"{value:g}"


class ExamTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window, self.host = window, window.host
        self.rules = ExamRules()
        self.selection = self.pick_topics = None
        self.counts, self.spins, self.overs, self.notes, self.rows = {}, {}, {}, {}, {}
        self.avail, self.history, self.token = {}, None, 0
        layout = QVBoxLayout(self)
        self.steps = QHBoxLayout()
        self.step_labels = [self.host.Label("", "secondary") for _ in STEPS]
        for label in self.step_labels:
            self.steps.addWidget(label)
        self.steps.addStretch()
        layout.addLayout(self.steps)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.setup, self.review = self.page(), self.page()
        self.issue = IssueView(self)
        for page in (self.setup, self.review):
            card = self.host.Card()
            card.layout().addWidget(scroll_area(page))
            self.stack.addWidget(card)
        self.stack.addWidget(self.issue)
        self.build_setup(self.setup.body)
        self.build_review(self.review.body)
        self.set_step(0)
        self.render_counts()

    def muted(self, text=""):
        label = self.host.caption(text)
        label.setWordWrap(True)
        return label

    def page(self):
        page = self.host.Page()
        t = self.host.theme()
        page.body.setContentsMargins(t.space_xs, t.space_md, t.space_xs, t.space_md)
        return page

    # Step 1

    def build_setup(self, body):
        head = QHBoxLayout()
        title = QVBoxLayout()
        heading = self.host.Label("New exam", "title")
        title.addWidget(heading)
        title.addWidget(self.muted(f"Picks only reviewed exam questions. Easy = {POINTS['easy']} points, "
                                   f"{number(MINUTES['easy'])} min · Hard = {POINTS['hard']} points, "
                                   f"{number(MINUTES['hard'])} min."))
        head.addLayout(title, 1)
        issued = self.host.Button("Open issued manifest…")
        issued.clicked.connect(self.open_issued)
        head.addWidget(issued, 0, Qt.AlignmentFlag.AlignTop)
        body.addLayout(head)
        self.rows_box = QVBoxLayout()
        body.addLayout(self.rows_box)
        avoid = QHBoxLayout()
        avoid.addWidget(self.host.Label("Skip questions used in the last"))
        self.avoid = QSpinBox()
        self.avoid.setRange(0, 1000)
        self.avoid.valueChanged.connect(self.avoid_changed)
        avoid.addWidget(self.avoid)
        self.avoid_unit = self.host.Label("exams")
        avoid.addWidget(self.avoid_unit)
        avoid.addStretch()
        body.addLayout(avoid)
        self.history_note = tone(QLabel(), "error")
        self.history_note.setWordWrap(True)
        body.addWidget(self.history_note)
        self.untopiced = self.muted()
        body.addWidget(self.untopiced)
        grid = QGridLayout()
        self.totals = {}
        for column, (key, caption) in enumerate((("questions", "questions"), ("points", "points"),
                                                 ("minutes", "minutes"))):
            value = self.host.Label("0", "title")
            self.totals[key] = value
            grid.addWidget(value, 0, column)
            grid.addWidget(self.muted(caption), 1, column)
        body.addLayout(grid)
        self.reason = tone(QLabel(), "error")
        self.reason.setWordWrap(True)
        body.addWidget(self.reason)
        self.propose_button = self.host.Button("Propose exam", "primary")
        self.propose_button.clicked.connect(lambda: self.propose())
        body.addWidget(self.propose_button)
        body.addWidget(self.muted("A random pick that matches these counts exactly. "
                             "You can swap single questions in the next step."))
        body.addStretch()

    def render_counts(self):
        """One row per topic, one stepper per question type and difficulty; counts already set are kept."""
        topics, cols = self.window.topics, columns(self.rules.kinds)
        self.counts = {(t.name, k, d): self.counts.get((t.name, k, d), 0) for t in topics for k, d, _ in cols}
        clear(self.rows_box)
        self.spins, self.overs, self.notes = {}, {}, {}
        if not topics:
            self.rows_box.addWidget(self.muted("No topics yet. Create topics and link questions to "
                                               "slides in the Questions tab."))
        else:
            grid = QGridLayout()
            grid.setColumnStretch(0, 1)
            for column, text in enumerate(("Topic", *(heading for *_, heading in cols))):
                grid.addWidget(self.muted(text), 0, column)
            for row, topic in enumerate(topics, 1):
                info = QVBoxLayout()
                name = QHBoxLayout()
                name.addWidget(dot(topic))
                name.addWidget(self.host.Label(topic.name), 1)
                info.addLayout(name)
                avail, draft = self.notes[topic.name] = self.muted(), tone(QLabel(), "amber")
                draft.setWordWrap(True)
                info.addWidget(avail)
                info.addWidget(draft)
                grid.addLayout(info, row, 0)
                for column, (kind, difficulty, _) in enumerate(cols, 1):
                    cell = (topic.name, kind, difficulty)
                    stepper = QVBoxLayout()
                    spin = self.spins[cell] = QSpinBox()
                    spin.setRange(0, 999)
                    spin.setValue(self.counts[cell])
                    spin.valueChanged.connect(lambda value, c=cell: self.count_changed(c, value))
                    over = self.overs[cell] = tone(QLabel(), "error")
                    stepper.addWidget(spin)
                    stepper.addWidget(over)
                    stepper.addStretch()
                    grid.addLayout(stepper, row, column)
            self.rows_box.addLayout(grid)
        self.compute_avail()

    def compute_avail(self):
        items = self.window.items
        names = topic_names(items, self.window.topics)
        avoid = self.avoid.value()
        previous = self.history[0] if self.history and avoid else []
        self.avail = available(items, names, self.rules, previous, avoid)
        waiting = Counter(names[i.id] for i in items if i.use == "exam" and i.status == "draft" and names[i.id])
        for name, (avail, draft) in self.notes.items():
            avail.setText(" · ".join(f"{self.avail[(name, kind, d)]} {heading.lower()}"
                                     for kind, d, heading in columns(self.rules.kinds)) + " reviewed")
            n = waiting[name]
            draft.setText(f"{plural(n, 'exam draft')} waiting for review" if n else "")
            draft.setVisible(bool(n))
        lost = sum(1 for i in items if i.use == "exam" and i.status == "reviewed" and not names[i.id])
        self.untopiced.setText(f"{plural(lost, 'reviewed exam question')} {agree(lost, 'has', 'have')}"
                               " no topic and can't be used." if lost else "")
        self.untopiced.setVisible(bool(lost))
        errors = self.history[1] if self.history and avoid else []
        self.history_note.setText("\n".join(errors))
        self.update_counts()

    def update_counts(self):
        headings = {(kind, d): heading for kind, d, heading in columns(self.rules.kinds)}
        short = []
        for cell, over in self.overs.items():
            have = self.avail[cell]
            over.setText(f"Only {have} available" if self.counts[cell] > have else "")
            over.setVisible(self.counts[cell] > have)
            if self.counts[cell] > have:
                short.append(f"{cell[0]} ({headings[cell[1:]].lower()})")
        chosen = [d for (_, _, d), n in self.counts.items() for _ in range(n)]
        points, minutes = exam_totals(chosen)
        for key, value in (("questions", len(chosen)), ("points", points), ("minutes", number(minutes))):
            self.totals[key].setText(str(value))
        reason = ("Not enough reviewed questions for " + ", ".join(short) + ". Lower the counts first."
                  if short else "" if chosen or not self.spins else "Choose at least one question.")
        self.reason.setText(reason)
        self.propose_button.setEnabled(bool(chosen) and not short)

    def count_changed(self, cell, value):
        self.counts[cell] = value
        self.update_counts()

    def avoid_changed(self, value):
        self.avoid_unit.setText("exam" if value == 1 else "exams")
        if value and self.history is None:
            self.load_history()
        self.compute_avail()

    def load_history(self):
        bank, self.token = self.window.bank, self.token + 1
        token = self.token

        def done(result, error):
            if token != self.token:
                return
            if error:
                self.window.error(error)
            else:
                self.history = result
                self.compute_avail()
        self.host.run_in_background(lambda: read_history(bank), done)

    def refresh(self):
        """Re-render from the window's questions and topics; history is read again."""
        self.history, self.token = None, self.token + 1
        self.render_counts()
        if self.avoid.value():
            self.load_history()
        if self.selection is not None:
            self.mark_changed({i.id: i.changed_slides for i in self.window.items})

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def set_step(self, step):
        self.stack.setCurrentIndex(step)
        for i, (label, text) in enumerate(zip(self.step_labels, STEPS)):
            label.setText(f"{'✓' if i < step else i + 1} {text}")
            font = label.font()
            font.setBold(i == step)
            label.setFont(font)
            label.setEnabled(i <= step)

    def blueprint(self):
        return Blueprint(self.counts, self.avoid.value())

    def reset(self):
        self.selection = self.pick_topics = None
        self.counts, self.history, self.token = {}, None, self.token + 1
        self.avoid.blockSignals(True)
        self.avoid.setValue(0)
        self.avoid.blockSignals(False)
        self.issue.reset()
        self.set_step(0)
        self.render_counts()

    def invalidate(self, *_):
        """Topics or questions changed under a proposal: discard it, back to the counts."""
        self.selection = self.pick_topics = None
        if self.issue.manifest is None:
            self.set_step(0)
            self.render_counts()

    def open_issued(self):
        self.issue.open_manifest()

    def propose(self, blueprint=None, topics=None):
        if not self.window.may_leave():
            return
        try:
            blueprint = blueprint or self.blueprint()
        except ValueError as exc:
            self.window.error(exc)
            return
        topics = topics if topics is not None else self.window.topics
        bank, rules = self.window.bank, self.rules
        history, seed = self.history, randbits(64)

        def work():
            items, errors = bank.load()
            if errors:
                raise ValueError("\n".join(map(str, errors)))
            previous, errors = [], []
            if blueprint.avoid_last:
                previous, errors = history or read_history(bank)
            selection = assemble(items, blueprint, seed=seed, topics=topic_names(items, topics), rules=rules,
                                 previous_exams=previous)
            return selection, (previous, errors)

        def done(data):
            selection, history = data
            if blueprint.avoid_last:
                self.history = history
                self.compute_avail()
                if history[1]:
                    self.window.error("\n".join(history[1]))
            if not selection.feasible:
                self.reason.setText("\n".join(selection.problems))
                return
            self.selection, self.pick_topics = selection, topics
            self.render_review()
            self.set_step(1)
        self.window.work(work, done)

    # Step 2

    def build_review(self, body):
        head = QHBoxLayout()
        self.pick_summary = self.host.Label("", "heading")
        head.addWidget(self.pick_summary)
        self.seed_label = self.host.Label("", "mono")
        head.addWidget(self.seed_label)
        head.addStretch()
        self.back_button = self.host.Button("Change counts", "ghost")
        self.back_button.clicked.connect(lambda: self.set_step(0))
        self.again_button = self.host.Button("New pick")
        self.again_button.clicked.connect(self.new_pick)
        self.approve_button = self.host.Button("Approve and freeze", "primary")
        self.approve_button.clicked.connect(self.approve)
        for button in (self.back_button, self.again_button, self.approve_button):
            head.addWidget(button)
        body.addLayout(head)
        self.blocked = tone(QLabel(), "violet")
        self.blocked.setWordWrap(True)
        body.addWidget(self.blocked)
        self.groups = QVBoxLayout()
        body.addLayout(self.groups)
        body.addStretch()

    def changed_ids(self):
        return [i.id for i in self.selection.items if i.source_changed] if self.selection else []

    def render_review(self):
        clear(self.groups)
        self.rows = {}
        selection = self.selection
        if selection is None:
            return
        minutes = number(selection.total_minutes)
        self.pick_summary.setText(f"{plural(len(selection.items), 'question')} · "
                                  f"{selection.total_points} points · {minutes} min")
        self.seed_label.setText(f"seed {selection.seed}")
        changed = self.changed_ids()
        n = len(changed)
        self.blocked.setText(f"{plural(n, 'picked question')} {agree(n, 'is', 'are')} linked to a changed slide. "
                             "Review or swap before freezing." if n else "")
        self.blocked.setVisible(bool(n))
        self.approve_button.setEnabled(not n)
        topics = self.pick_topics
        by_topic = {}
        for item in selection.items:
            by_topic.setdefault(selection.topics[item.id], []).append(item)
        order = [t.name for t in topics if t.name in by_topic] + [n for n in by_topic
                                                                  if n not in {t.name for t in topics}]
        named = {t.name: t for t in topics}
        count = 0
        for name in order:
            head = QHBoxLayout()
            if name in named:
                head.addWidget(dot(named[name]))
            title = self.host.Label(name, "heading")
            head.addWidget(title)
            head.addWidget(self.muted(plural(len(by_topic[name]), "question")))
            head.addStretch()
            self.groups.addLayout(head)
            for item in by_topic[name]:
                count += 1
                self.groups.addLayout(self.row(count, item))

    def row(self, number_, item):
        line = QHBoxLayout()
        index = self.host.Label(str(number_), "mono")
        line.addWidget(index, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        stem = self.host.Label(item.stem)
        stem.setWordWrap(True)
        text.addWidget(stem)
        meta = QHBoxLayout()
        chip = self.host.Chip(f"{item.difficulty.capitalize()} · {POINTS[item.difficulty]} pts")
        meta.addWidget(chip)
        if item.kind == "open":
            meta.addWidget(self.host.Chip("Open answer", "muted"))
        if item.media:
            meta.addWidget(self.host.Chip(plural(len(item.media), "image"), "muted"))
        meta.addWidget(self.muted(self.window.slide_name(item.sources[0].slide_id) if item.sources else "No slide"))
        row = self.rows[item.id] = {"stem": stem, "chip": chip, "changed": None, "review": None}
        if item.source_changed:
            row["changed"] = tone(self.host.Chip("Slide changed"), "violet")
            meta.addWidget(row["changed"])
        meta.addStretch()
        text.addLayout(meta)
        line.addLayout(text, 1)
        if item.source_changed:
            row["review"] = self.host.Button("Review", compact=True)
            row["review"].clicked.connect(lambda _=False, i=item.id: self.window.open_question(i))
            line.addWidget(row["review"], 0, Qt.AlignmentFlag.AlignTop)
        row["swap"] = self.host.Button("Swap", compact=True)
        row["swap"].setEnabled(bool(self.selection.alternatives(item.id)))
        row["swap"].clicked.connect(lambda _=False, i=item.id: self.swap_item(i))
        line.addWidget(row["swap"], 0, Qt.AlignmentFlag.AlignTop)
        return line

    def new_pick(self):
        if self.selection:
            self.propose(self.selection.blueprint, self.pick_topics)

    def swap_item(self, item_id):
        if self.selection is None:
            return
        pool = list(self.selection.alternatives(item_id))
        labels = [f"{i.id} · {i.stem}" for i in pool]
        if not labels:
            return
        label, ok = QInputDialog.getItem(self, "Swap question", "Replacement (same topic and difficulty)",
                                         labels, 0, False)
        if ok:
            try:
                self.selection = swap(self.selection, item_id, pool[labels.index(label)].id)
            except ValueError as exc:
                self.window.error(exc)
                return
            self.render_review()

    def mark_changed(self, flags):
        """flags: item ID -> changed slides. Re-render when any selected question's differ."""
        items = tuple(replace(i, changed_slides=flags.get(i.id, i.changed_slides)) for i in self.selection.items)
        if items != self.selection.items:
            self.selection = replace(self.selection, items=items)
            self.render_review()

    def approve(self):
        """Freeze checks every source afresh; a changed one leads back here."""
        if self.selection is not None and not self.window.busy and not self.changed_ids():
            self.issue.freeze(self.selection)

    def frozen(self):
        """A manifest was issued or opened: show it; later exams see it in their history."""
        self.history, self.selection = None, None
        self.set_step(2)
