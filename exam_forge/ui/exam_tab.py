# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exam tab in three steps: counts, review and swap, then freeze and save."""
from dataclasses import replace
from decimal import Decimal
from secrets import randbits

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..assembly import (
    Blueprint,
    assemble,
    available,
    check_approval,
    swap,
    waiting_elp_drafts,
)
from ..elp import ElpRules
from ..manifest import Manifest
from ..topics import topic_color, topic_name
from .editor import accent_color
from .issue import IssueView
from .work import SCROLL_STYLE, fresh_deck

STEPS = ("Counts", "Review & swap", "Freeze & save")
_DIFFICULTIES = ("easy", "hard")


def red_color(widget):
    dark = widget.palette().color(QPalette.ColorRole.Window).lightness() < 128
    return "#fca5a5" if dark else "#a8231a"


def clear(layout):
    while layout.count():
        child = layout.takeAt(0)
        if child.widget():
            child.widget().deleteLater()
        elif child.layout():
            clear(child.layout())


def number(value):
    return f"{value:g}"


def plural(n, word):
    return f"{n} {word}{'s' * (n != 1)}"


def read_history(bank):
    """Earlier exams' item IDs, oldest first, and a message for each unreadable manifest."""
    previous, errors = [], []
    for path in sorted(bank.folder.glob("*.manifest.json")):
        try:
            data = Manifest.read(path).data()
            if data["bank_id"] == bank.id:
                previous.append([i["id"] for i in data["items"]])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"Could not read exam history {path.name}: {exc}")
    return previous, errors


def slide_labels(host, deck, items):
    """Item ID -> 'Slide N' from the deck's refs for the item's first link, else the slide ID."""
    wanted = {}
    for item in items:
        if item.sources:
            link = item.sources[0]
            wanted.setdefault((link.view, link.language), []).append((item.id, link.slide_id))
    labels = {}
    for (view, language), pairs in wanted.items():
        try:
            refs = {}
            for ref in host.deck_slides(deck, view, language):
                refs.setdefault(ref.id, ref)
        except (OSError, ValueError):
            refs = {}
        for item_id, slide_id in pairs:
            ref = refs.get(slide_id)
            labels[item_id] = f"Slide {ref.position}" if ref and ref.position else f"Slide {slide_id}"
    return labels


class ExamTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window, self.host = window, window.host
        self.rules = ElpRules()
        self.selection = self.pick_topics = None
        self.labels, self.counts, self.cells, self.rows = {}, {}, {}, {}
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
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setStyleSheet(SCROLL_STYLE)
            scroll.setWidget(page)
            card = self.host.Card()
            card.layout().addWidget(scroll)
            self.stack.addWidget(card)
        self.stack.addWidget(self.issue)
        self.build_setup(self.setup.body)
        self.build_review(self.review.body)
        self.set_step(0)
        self.render_counts()

    def tint(self, widget, kind):
        """Text colour only, taken from this tab's palette so rebuilt widgets match the theme."""
        color = red_color(self) if kind == "red" else accent_color(self, kind == "amber")
        widget.setStyleSheet(f"color: {color};")

    def muted(self, text=""):
        label = self.host.caption(text)
        label.setWordWrap(True)
        return label

    def page(self):
        page = self.host.Page()
        page.body.setContentsMargins(4, 8, 4, 8)
        return page

    # Step 1

    def build_setup(self, body):
        head = QHBoxLayout()
        title = QVBoxLayout()
        heading = self.host.Label("New ELP exam", "title")
        title.addWidget(heading)
        r = self.rules
        title.addWidget(self.muted(f"Picks only reviewed ELP questions. Easy = {r.easy.points} points, "
                              f"{number(r.easy.minutes)} min · Hard = {r.hard.points} points, "
                              f"{number(r.hard.minutes)} min."))
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
        self.history_note = QLabel()
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
        self.reason = QLabel()
        self.reason.setWordWrap(True)
        body.addWidget(self.reason)
        self.propose_button = self.host.Button("Propose exam", "primary")
        self.propose_button.clicked.connect(lambda: self.propose())
        body.addWidget(self.propose_button)
        body.addWidget(self.muted("A random pick that matches these counts exactly. "
                             "You can swap single questions in the next step."))
        body.addStretch()

    def render_counts(self):
        topics = self.window.topics
        self.counts = {t.name: self.counts.get(t.name, dict.fromkeys(_DIFFICULTIES, 0)) for t in topics}
        clear(self.rows_box)
        self.cells = {}
        if not topics:
            self.rows_box.addWidget(self.muted("No topics yet. Create topics and link questions to "
                                          "slides in the Questions tab."))
        else:
            grid = QGridLayout()
            grid.setColumnStretch(0, 1)
            for column, text in enumerate(("Topic", "Easy", "Hard")):
                grid.addWidget(self.muted(text), 0, column)
            dark = self.palette().color(QPalette.ColorRole.Window).lightness() < 128
            for row, topic in enumerate(topics, 1):
                cell = self.cells[topic.name] = {}
                info = QVBoxLayout()
                name = QHBoxLayout()
                dot = QLabel("●")
                dot.setStyleSheet(f"color: {topic_color(topic.color, dark)};")
                name.addWidget(dot)
                name.addWidget(self.host.Label(topic.name), 1)
                info.addLayout(name)
                cell["avail"], cell["draft"] = self.muted(), QLabel()
                cell["draft"].setWordWrap(True)
                self.tint(cell["draft"], "amber")
                info.addWidget(cell["avail"])
                info.addWidget(cell["draft"])
                grid.addLayout(info, row, 0)
                for column, difficulty in enumerate(_DIFFICULTIES, 1):
                    stepper = QVBoxLayout()
                    spin = cell[difficulty] = QSpinBox()
                    spin.setRange(0, 999)
                    spin.setValue(self.counts[topic.name][difficulty])
                    spin.valueChanged.connect(
                        lambda value, n=topic.name, d=difficulty: self.count_changed(n, d, value))
                    over = cell[difficulty + "_over"] = QLabel()
                    self.tint(over, "red")
                    stepper.addWidget(spin)
                    stepper.addWidget(over)
                    stepper.addStretch()
                    grid.addLayout(stepper, row, column)
            self.rows_box.addLayout(grid)
        self.compute_avail()

    def compute_avail(self):
        topics, items = self.window.topics, self.window.items
        of = lambda item: topic_name(item, topics)  # noqa: E731
        avoid = self.avoid.value()
        previous = self.history[0] if self.history and avoid else []
        self.avail = available(items, of, previous, avoid, rules=self.rules)
        waiting = waiting_elp_drafts(items, of)
        for name, cell in self.cells.items():
            have = self.avail.get(name, {})
            cell["avail"].setText(f"{have.get('easy', 0)} easy · {have.get('hard', 0)} hard reviewed")
            n = waiting.get(name, 0)
            cell["draft"].setText(f"{plural(n, 'ELP draft')} waiting for review" if n else "")
            cell["draft"].setVisible(bool(n))
        lost = sum(1 for i in items if i.use == "elp" and i.status == "reviewed" and not of(i))
        self.untopiced.setText(f"{plural(lost, 'reviewed ELP question')} {'has' if lost == 1 else 'have'}"
                               " no topic and can't be used." if lost else "")
        self.untopiced.setVisible(bool(lost))
        errors = self.history[1] if self.history and avoid else []
        self.history_note.setText("\n".join(errors))
        self.tint(self.history_note, "red")
        self.update_counts()

    def update_counts(self):
        short, easy, hard = [], 0, 0
        for name, cell in self.cells.items():
            counts = self.counts[name]
            easy, hard = easy + counts["easy"], hard + counts["hard"]
            for difficulty in _DIFFICULTIES:
                have = self.avail.get(name, {}).get(difficulty, 0)
                over = counts[difficulty] > have
                cell[difficulty + "_over"].setText(f"Only {have} available" if over else "")
                cell[difficulty + "_over"].setVisible(over)
                if over:
                    short.append(f"{name} ({difficulty})")
        r = self.rules
        minutes = easy * Decimal(str(r.easy.minutes)) + hard * Decimal(str(r.hard.minutes))
        for key, value in (("questions", easy + hard), ("points", easy * r.easy.points + hard * r.hard.points),
                           ("minutes", number(float(minutes)))):
            self.totals[key].setText(str(value))
        reason = ("Not enough reviewed questions for " + ", ".join(short) + ". Lower the counts first."
                  if short else "" if easy + hard or not self.cells else "Choose at least one question.")
        self.reason.setText(reason)
        self.tint(self.reason, "red")
        self.propose_button.setEnabled(bool(easy + hard) and not short)

    def count_changed(self, name, difficulty, value):
        self.counts[name][difficulty] = value
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
        """Re-render the counts from the window's questions and topics; history is read again."""
        self.history, self.token = None, self.token + 1
        self.render_counts()
        if self.avoid.value():
            self.load_history()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self.sync_flags()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "groups"):
            self.render_counts()
            self.render_review()
            self.issue.restyle()

    def set_step(self, step):
        self.stack.setCurrentIndex(step)
        for i, (label, text) in enumerate(zip(self.step_labels, STEPS)):
            label.setText(f"{'✓' if i < step else i + 1} {text}")
            font = label.font()
            font.setBold(i == step)
            label.setFont(font)
            label.setEnabled(i <= step)

    def blueprint(self):
        counts = {n: dict(c) for n, c in self.counts.items() if any(c.values())}
        return Blueprint(counts, self.avoid.value())

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
        bank, deck, host, rules = self.window.bank, self.window.deck, self.host, self.rules
        history, seed = self.history, randbits(64)
        topic_of = lambda item: topic_name(item, topics)  # noqa: E731

        def work():
            items, errors = bank.load()
            if errors:
                raise ValueError("\n".join(map(str, errors)))
            previous, errors = [], []
            if blueprint.avoid_last:
                previous, errors = history or read_history(bank)
            selection = assemble(items, blueprint, seed=seed, topic_of=topic_of, rules=rules,
                                 previous_exams=previous)
            return selection, (previous, errors), slide_labels(host, fresh_deck(deck), selection.candidates)

        def done(data):
            selection, history, self.labels = data
            if blueprint.avoid_last:
                self.history = history
                self.compute_avail()
                if history[1]:
                    self.window.notice.setText("\n".join(history[1]))
            if not selection.feasible:
                self.reason.setText("\n".join(selection.problems))
                self.tint(self.reason, "red")
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
        self.blocked = QLabel()
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
        self.blocked.setText(
            f"{n} picked question{' is' if n == 1 else 's are'} linked to a changed slide. "
            "Review or swap before freezing." if n else "")
        self.blocked.setVisible(bool(n))
        self.tint(self.blocked, "violet")
        self.approve_button.setEnabled(not n)
        topics = self.pick_topics
        by_topic = {}
        for item in selection.items:
            by_topic.setdefault(selection.topic_of(item), []).append(item)
        order = [t.name for t in topics if t.name in by_topic] + [n for n in by_topic
                                                                  if n not in {t.name for t in topics}]
        colors = {t.name: t.color for t in topics}
        dark = self.palette().color(QPalette.ColorRole.Window).lightness() < 128
        count = 0
        for name in order:
            head = QHBoxLayout()
            if name in colors:
                dot = QLabel("●")
                dot.setStyleSheet(f"color: {topic_color(colors[name], dark)};")
                head.addWidget(dot)
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
        rule = getattr(self.rules, item.difficulty)
        chip = self.host.Chip(f"{item.difficulty.capitalize()} · {rule.points} pts")
        meta.addWidget(chip)
        if item.media:
            meta.addWidget(self.host.Chip(f"{len(item.media)} image{'s' if len(item.media) != 1 else ''}", "muted"))
        meta.addWidget(self.muted(self.labels.get(item.id) or
                             (f"Slide {item.sources[0].slide_id}" if item.sources else "No slide")))
        row = self.rows[item.id] = {"stem": stem, "chip": chip, "changed": None, "review": None}
        if item.source_changed:
            row["changed"] = self.host.Chip("Slide changed")
            self.tint(row["changed"], "violet")
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

    def sync_flags(self):
        """Back on the tab: pick up source checks made elsewhere, keeping the selection otherwise."""
        selection, bank = self.selection, self.window.bank
        if selection is None or self.stack.currentIndex() != 1:
            return

        def work():
            return {i.id: bank.get(i.id).source_changed for i in selection.items}

        def done(flags, error):
            if error:
                self.window.error(error)
            elif self.selection is selection:
                self.mark_changed(flags)
        self.host.run_in_background(work, done)

    def mark_changed(self, flags):
        """flags: item ID -> source_changed. Re-render when any selected flag differs."""
        items = tuple(replace(i, source_changed=flags.get(i.id, i.source_changed))
                      for i in self.selection.items)
        if items != self.selection.items:
            self.selection = replace(self.selection, items=items)
            self.render_review()

    def approve(self):
        if self.selection is None or self.window.busy or self.changed_ids():
            return
        selection, bank, deck = self.selection, self.window.bank, self.window.deck
        self.window.set_busy(True)

        def checked(report, error):
            self.window.set_busy(False)
            if error:
                self.window.error(error)
            elif report.changed:
                self.selection = report.selection
                self.render_review()
            else:
                self.issue.freeze(report.selection)

        def prepared(snapshot, error):
            if error:
                checked(None, error)
            else:
                check_approval(self.host, snapshot, bank, selection, checked)
        self.host.run_in_background(lambda: fresh_deck(deck), prepared)

    def frozen(self):
        """A manifest was issued or opened: show it; later exams see it in their history."""
        self.history, self.selection = None, None
        self.set_step(2)
