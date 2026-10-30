# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Blueprint and selection; all selection rules live in modules."""
from secrets import randbits

from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..assembly import Blueprint, assemble, check_approval, swap
from ..elp import ElpRules
from ..manifest import Manifest
from .issue import IssueView
from .work import changed_details, fresh_deck


class ExamTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window, self.host = window, window.host
        self.selection = self.approved = None
        layout = QVBoxLayout(self)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        self.setup = QWidget()
        self.stack.addWidget(self.setup)
        body = QVBoxLayout(self.setup)
        heading = QHBoxLayout()
        heading.addWidget(QLabel("1 · Blueprint and selection"))
        issued = self.host.Button("Open issued manifest…")
        issued.clicked.connect(self.open_issued)
        heading.addWidget(issued)
        body.addLayout(heading)
        self.counts = QTableWidget(1, 2)
        self.counts.setHorizontalHeaderLabels(["Topic", "Exact count"])
        self.counts.horizontalHeader().setStretchLastSection(True)
        self.counts.setMaximumHeight(130)
        body.addWidget(self.counts)
        row = QHBoxLayout()
        add = self.host.Button("Add count row")
        add.clicked.connect(lambda: self.counts.insertRow(self.counts.rowCount()))
        row.addWidget(add)
        remove = self.host.Button("Remove count row")
        remove.clicked.connect(lambda: self.counts.removeRow(self.counts.currentRow()))
        row.addWidget(remove)
        body.addLayout(row)
        row = QHBoxLayout()
        self.easy, self.hard, self.avoid = QSpinBox(), QSpinBox(), QSpinBox()
        self.minutes = QDoubleSpinBox()
        self.minutes.setRange(.01, 1e6)
        self.minutes.setValue(45)
        for label, field in (("Easy count", self.easy), ("Hard count", self.hard),
                             ("Minutes (exact)", self.minutes), ("Avoid last N exams", self.avoid)):
            if isinstance(field, QSpinBox):
                field.setRange(0, 1000000)
            row.addWidget(self.host.Field(label, field))
        body.addLayout(row)
        row = QHBoxLayout()
        build = self.host.Button("Propose selection", "primary")
        build.clicked.connect(self.propose)
        row.addWidget(build)
        replace_button = self.host.Button("Swap selected question…")
        replace_button.clicked.connect(self.swap_selected)
        row.addWidget(replace_button)
        self.approve_button = self.host.Button("Approve selection…")
        self.approve_button.clicked.connect(self.approve)
        self.approve_button.setEnabled(False)
        row.addWidget(self.approve_button)
        body.addLayout(row)
        self.results = QListWidget()
        body.addWidget(self.results, 1)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        body.addWidget(self.report, 1)
        self.issue = IssueView(self)
        self.stack.addWidget(self.issue)
        self.counts.itemChanged.connect(self.invalidate)
        self.counts.model().rowsRemoved.connect(self.invalidate)
        for field in (self.easy, self.hard, self.minutes, self.avoid):
            field.valueChanged.connect(self.invalidate)

    def open_issued(self):
        self.stack.setCurrentWidget(self.issue)
        self.issue.open_manifest()

    def reset(self):
        self.invalidate()
        self.results.clear()
        self.report.clear()
        self.issue.reset()
        self.stack.setCurrentIndex(0)

    def invalidate(self, *_):
        self.selection = self.approved = None
        self.approve_button.setEnabled(False)
        if hasattr(self, "issue") and self.issue.manifest is None:
            self.issue.reset()
            self.stack.setCurrentWidget(self.setup)

    def blueprint(self):
        counts = {}
        for row in range(self.counts.rowCount()):
            name, count = (self.counts.item(row, col) for col in (0, 1))
            if not name or not name.text().strip():
                if count and count.text().strip():
                    raise ValueError("Each count needs a topic name.")
                continue
            name = name.text().strip()
            if name in counts:
                raise ValueError("Use one count row per topic.")
            counts[name] = int(count.text()) if count else 0
        return Blueprint(counts, {"easy": self.easy.value(), "hard": self.hard.value()},
                         self.minutes.value(), self.avoid.value())

    def propose(self):
        if not self.window.may_leave():
            return
        try:
            blueprint = self.blueprint()
            rules = ElpRules()
        except ValueError as exc:
            self.window.error(exc)
            return
        self.invalidate()
        self.results.clear()
        self.report.setPlainText("Preparing selection…")
        bank = self.window.bank
        seed = randbits(64)

        def work():
            items, errors = bank.load()
            if errors:
                raise ValueError("\n".join(map(str, errors)))
            previous, history_errors = [], []
            if blueprint.avoid_last:
                for path in sorted(bank.folder.glob("*.manifest.json")):
                    try:
                        manifest = Manifest.read(path).data()
                        if manifest['bank_id'] == bank.id:
                            previous.append([i['id'] for i in manifest['items']])
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        history_errors.append(f"Could not read exam history {path.name}: {exc}")
            return assemble(items, blueprint, seed=seed, rules=rules, previous_exams=previous), history_errors

        def done(data):
            self.selection, history_errors = data
            exclusions = "\n".join(f"{r.item.id}: {' '.join(r.reasons)}" for r in self.selection.excluded) or "None"
            self.report.setPlainText("\n".join(history_errors) + "\n\nBlueprint exclusions\n" + exclusions +
                "\n\n" + ("\n".join(self.selection.problems) if not self.selection.feasible else
                f"{self.selection.total_points} points · {self.selection.total_minutes} minutes · seed {seed}"))
            self.render_selection()
        self.window.work(work, done)

    def render_selection(self):
        self.results.clear()
        if self.selection:
            self.results.addItems([f"{i.id} r{i.revision} · {i.stem}" for i in self.selection.items])
        self.approve_button.setEnabled(bool(self.selection and self.selection.feasible))

    def swap_selected(self):
        row = self.results.currentRow()
        if self.selection is None or not 0 <= row < len(self.selection.items):
            return
        selected = {i.id for i in self.selection.items}
        pool = [i for i in self.selection.candidates if i.id not in selected]
        labels = [f"{i.id} · {i.stem}" for i in pool]
        if not labels:
            self.window.error("No unselected eligible replacements.")
            return
        label, ok = QInputDialog.getItem(self, "Swap question", "Replacement (all blueprint constraints still apply)", labels, 0, False)
        if ok:
            try:
                self.selection = swap(self.selection, self.selection.items[row].id, pool[labels.index(label)].id)
                self.approved = None
                self.render_selection()
            except ValueError as exc:
                self.window.error(exc)

    def approve(self):
        if self.selection is None or self.window.busy:
            return
        selection, bank, deck = self.selection, self.window.bank, self.window.deck
        self.window.set_busy(True)

        def checked(report, error):
            self.window.set_busy(False)
            if error:
                self.window.error(error)
                return
            details = changed_details(report.changed)
            text = "Approve this selection?" + ("\n\nSource changed:\n" + details if details else "\nAll selected sources were checked.")
            if QMessageBox.question(self, "Approve selection", text) != QMessageBox.StandardButton.Yes:
                return
            self.approved = report.approve(accept_changed=bool(report.changed))
            self.issue.prepare(self.approved)
            self.stack.setCurrentWidget(self.issue)

        def prepared(snapshot, error):
            if error:
                checked(None, error)
            else:
                check_approval(self.host, snapshot, bank, selection, checked)
        self.host.run_in_background(lambda: fresh_deck(deck), prepared)
