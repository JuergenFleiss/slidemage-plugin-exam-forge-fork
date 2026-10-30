# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Blueprint, taught evidence and selection; all selection rules live in modules."""
from dataclasses import asdict
from datetime import datetime

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
from ..elp import DifficultyRules, ElpRules
from ..manifest import Manifest
from ..sessions import Sessions
from ..presentation_log import read_log
from ..taught import TaughtOptions, build_evidence, filter_items
from .issue import IssueView
from .work import changed_details, fresh_deck, schedule_for


class ExamTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window, self.host = window, window.host
        self.selection = self.approved = None
        self.taught_blueprint = {}
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
        row = QHBoxLayout()
        self.slides = QCheckBox("Slides")
        defaults = self.host.settings().get("taught_defaults", {}).get(str(window.deck.path.resolve()), {})
        self.slides.setChecked(defaults.get("slides", True))
        row.addWidget(QLabel("Taught only"))
        row.addWidget(self.slides)
        save = self.host.Button("Save as deck default")
        save.clicked.connect(self.save_default)
        row.addWidget(save)
        body.addLayout(row)
        self.by = QComboBox()
        self.by.addItems(["topic", "objective"])
        self.counts = QTableWidget(1, 2)
        self.counts.setHorizontalHeaderLabels(["Topic / objective", "Exact count"])
        self.counts.horizontalHeader().setStretchLastSection(True)
        self.counts.setMaximumHeight(130)
        body.addWidget(self.host.Field("Count by", self.by))
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
        self.easy, self.hard, self.avoid, self.seed = QSpinBox(), QSpinBox(), QSpinBox(), QSpinBox()
        self.minutes = QDoubleSpinBox()
        self.minutes.setRange(.01, 1e6)
        self.minutes.setValue(45)
        for label, field in (("Easy count", self.easy), ("Hard count", self.hard),
                             ("Minutes (exact)", self.minutes), ("Avoid last N exams", self.avoid), ("Seed", self.seed)):
            if isinstance(field, QSpinBox):
                field.setRange(0, 1000000)
            row.addWidget(self.host.Field(label, field))
        body.addLayout(row)
        row = QHBoxLayout()
        self.rules_fields = []
        for name, points, minutes in (("Easy", 12, 2.5), ("Hard", 24, 5)):
            p, m = QSpinBox(), QDoubleSpinBox()
            p.setRange(12, 12000)
            p.setSingleStep(12)
            p.setValue(points)
            m.setRange(.01, 10000)
            m.setValue(minutes)
            self.rules_fields.append((p, m))
            row.addWidget(self.host.Field(name + " points", p))
            row.addWidget(self.host.Field(name + " minutes", m))
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
        self.by.currentTextChanged.connect(self.invalidate)
        for field in (self.slides,):
            field.toggled.connect(self.invalidate)
        for field in (self.easy, self.hard, self.minutes, self.avoid, self.seed,
                      *(f for pair in self.rules_fields for f in pair)):
            field.valueChanged.connect(self.invalidate)
        window.sessions_tab.session_list.itemChanged.connect(self.invalidate)

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

    def save_default(self):
        settings = self.host.settings()
        defaults = dict(settings.get("taught_defaults", {}))
        defaults[str(self.window.deck.path.resolve())] = asdict(TaughtOptions(self.slides.isChecked()))
        settings.set("taught_defaults", defaults)
        settings.save()
        self.window.notice.setText("Saved Slides setting as this deck's default.")

    def blueprint(self):
        counts = {}
        for row in range(self.counts.rowCount()):
            name, count = (self.counts.item(row, col) for col in (0, 1))
            if not name or not name.text().strip():
                if count and count.text().strip():
                    raise ValueError("Each count needs a topic or objective name.")
                continue
            name = name.text().strip()
            if name in counts:
                raise ValueError("Use one count row per topic or objective.")
            counts[name] = int(count.text()) if count else 0
        return Blueprint(counts, {"easy": self.easy.value(), "hard": self.hard.value()},
                         self.minutes.value(), self.by.currentText(), self.avoid.value())

    def propose(self):
        if not self.window.may_leave():
            return
        try:
            blueprint = self.blueprint()
            rules = ElpRules(*(DifficultyRules(p.value(), m.value()) for p, m in self.rules_fields))
        except ValueError as exc:
            self.window.error(exc)
            return
        self.invalidate()
        self.results.clear()
        self.report.setPlainText("Reading taught evidence…")
        bank, deck = self.window.bank, self.window.deck
        schedule = schedule_for(self.host, deck)
        chosen = self.window.sessions_tab.selected_ids()
        language = self.host.deck_language(deck)
        options = TaughtOptions(self.slides.isChecked())
        seed, as_of = self.seed.value(), datetime.now().astimezone()

        def work():
            snapshot = fresh_deck(deck)
            refs = {(view, lang): self.host.deck_slides(snapshot, view, lang)
                    for view in ("instructor", "during-course", "after-course") for lang in ("en", "de")}
            store = Sessions(bank)
            sessions = [s for s in store.load(deck=snapshot, schedule=schedule) if chosen is None or s.id in chosen]
            logs = {s.id: read_log(store.path(s.presentation)) for s in sessions if s.presentation}
            evidence = build_evidence(refs, sessions, logs, schedule=schedule, as_of=as_of,
                                      language=language)
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
            return refs, sessions, logs, evidence, items, previous, history_errors

        def prepared(data):
            refs, sessions, logs, evidence, items, previous, history_errors = data
            cutoff = None
            if options.slides and evidence.slide_method == "schedule":
                cutoff, ok = QInputDialog.getInt(self, "Confirm estimated cut-off", evidence.slide_reason +
                    "\nLast taught slide position (0 means none):", evidence.cutoff_position, 0, 1000000)
                if not ok:
                    self.report.setPlainText("Estimated cut-off not confirmed. No selection proposed.")
                    return

            def select():
                confirmed = build_evidence(refs, sessions, logs, schedule=schedule, as_of=as_of,
                                           language=language, cutoff_position=cutoff)
                filtered = filter_items(items, confirmed, options=options)
                selection = assemble(filtered.included, blueprint, seed=seed, rules=rules, previous_exams=previous)
                return confirmed, filtered, selection

            def done(result):
                evidence, filtered, self.selection = result
                self.taught_blueprint = {"options": asdict(options), "cutoff_position": evidence.cutoff_position,
                                        "method": evidence.slide_method, "sessions": [s.id for s in sessions],
                                        "as_of": as_of.isoformat()}
                exclusions = lambda rows: "\n".join(f"{r.item.id}: {' '.join(r.reasons)}" for r in rows) or "None"
                self.report.setPlainText("\n".join(filter(None, (*history_errors, evidence.slide_reason))) +
                    f"\nCut-off position: {evidence.cutoff_position}\n\nTaught-only exclusions\n" + exclusions(filtered.excluded) +
                    "\n\nBlueprint exclusions\n" + exclusions(self.selection.excluded) +
                    "\n\n" + ("\n".join(self.selection.problems) if not self.selection.feasible else
                    f"{self.selection.total_points} points · {self.selection.total_minutes} minutes · seed {seed}"))
                self.render_selection()
            self.window.work(select, done)
        self.window.work(work, prepared)

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
