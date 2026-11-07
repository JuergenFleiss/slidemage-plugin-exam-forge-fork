# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Question-bank exports in a native popup, with rows from the host's themed kit, and the exports themselves. Each
export opens in the deck's question folder: that folder is already private, like the exports, which hold answers."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMenu, QWidgetAction

from ..bank import BankError, atomic_write_bytes
from ..model import agree, plural
from ..exports import export_moodle_xml, questions_csv, questions_html
from ..rules import question_issues
from .kit import confirm, deck_language, slide_title, source_details


class ExportMenu(QMenu):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setToolTipsVisible(True)
        self.rows = {}
        self.moodle_action = self.add_export(
            "Moodle quiz (XML)", "Reviewed revision-quiz questions, ready to import into Moodle.", self.export)
        self.table_action = self.add_export(
            "Question table (CSV)", "Every question as a row, for Excel or LibreOffice.", self.export_table,
            answer_key=True)
        self.html_action = self.add_export(
            "Question page (HTML)", "Every question with its answers as one page.", self.export_html,
            answer_key=True)
        self.addSeparator()
        hint = QWidgetAction(self)
        label = window.host.Label("Exams: use the Exam tab", "caption")
        label.setContentsMargins(window.host.theme().space_xl, window.host.theme().space_sm, window.host.theme().space_xl, window.host.theme().space_sm)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        hint.setDefaultWidget(label)
        hint.setEnabled(False)
        self.addAction(hint)
        self.aboutToShow.connect(self.refresh)
        self.hovered.connect(self.select_row)
        self.aboutToHide.connect(lambda: self.select_row(None))

    def add_export(self, title, description, callback, *, answer_key=False):
        action = QWidgetAction(self)
        action.setText(title)
        row = self.window.host.RowButton(title, description, chip=("Answer key", "warning") if answer_key else ("", ""))
        if row.chip is not None:
            row.layout().setAlignment(row.chip, Qt.AlignmentFlag.AlignVCenter)
        # Let QMenu own mouse and keyboard activation, including disabled-item tooltips.
        row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row.setAccessibleName(title)
        row.setAccessibleDescription(description)
        row.ensurePolished()
        # Size from the host's fonts, so neither the title nor the chip is clipped at a larger scale.
        row.setMinimumWidth(max(row.sizeHint().width(), row.fontMetrics().horizontalAdvance("M" * 38)))
        action.setDefaultWidget(row)
        self.rows[action] = row
        self.addAction(action)

        def selected():
            self.close()
            callback()

        action.triggered.connect(selected)
        return action

    def select_row(self, selected):
        for action, row in self.rows.items():
            row.set_selected(action is selected)

    def refresh(self):
        window = self.window
        reviewed = [item for item in window.items if item.status == "reviewed" and item.use == "revision"]
        eligible = any(not question_issues(item, window.answer_rules) for item in reviewed)
        moodle_reason = ("No reviewed revision-quiz questions to export." if not reviewed else
                         "No reviewed revision-quiz questions fit the answer rules.")
        for action, available, reason in (
            (self.moodle_action, eligible, moodle_reason),
            (self.table_action, bool(window.items), "The question bank is empty."),
            (self.html_action, bool(window.items), "The question bank is empty."),
        ):
            if window.busy:
                reason = "Wait for the current operation to finish."
            elif available:
                reason = ""
            action.setEnabled(not reason)
            action.setToolTip(reason)
            self.rows[action].setToolTip(reason)

    # -- the exports ---------------------------------------------------------------------------------------------

    def export(self):
        """Reviewed revision-quiz questions to Moodle XML, after a fresh source check."""
        if self.window.may_leave():
            self.window.check_sources(then=self.export_checked)

    def export_checked(self, report):
        window = self.window
        bank, rules = window.bank, window.answer_rules
        reviewed = [checked.item for checked in report.items
                    if checked.item.status == "reviewed" and checked.item.use == "revision"]
        items = [item for item in reviewed if not question_issues(item, rules)]
        skipped = len(reviewed) - len(items)
        left_out = (f" {plural(skipped, 'reviewed question')} that no longer fit the answer rules "
                    f"{agree(skipped, 'was', 'were')} left out.") if skipped else ""
        if not items:
            window.error("No reviewed revision-quiz questions to export." + left_out)
            return
        changed = [checked for checked in report.items if checked.item in items and checked.source_changed]
        if changed:
            details = [f"• {checked.item.stem} ({checked.item.id}): {line}"
                       for checked in changed for line in source_details(checked)]
            if not confirm(window, "Source changed", "Changed sources:\n" + "\n".join(details) +
                           "\n\nContinue exporting?", warning=True):
                return
        filename = self.save_as("Export reviewed revision questions", "revision-quiz", ".xml", "Moodle XML (*.xml)")
        if not filename:
            return

        def work():
            if any(bank.get(item.id) != item for item in items):
                raise BankError("A question changed during export. Check slides and try again.")
            atomic_write_bytes(Path(filename), export_moodle_xml(items, bank=bank, rules=rules), ".moodle-")
            return len(items)

        window.work(work, lambda count: window.toast(f"Exported {count} reviewed questions to {filename}.{left_out}"))

    def export_table(self):
        """Every question of the bank, with its answers and rationales, as a CSV table."""
        self.export_all(".csv", "CSV table (*.csv)", questions_csv)

    def export_html(self):
        """Every question of the bank, with its answers and rationales, as one HTML page like the exam key."""
        bank, language = self.window.bank, deck_language(self.window.host, self.window.deck)
        self.export_all(".html", "HTML page (*.html)", lambda items, topics, slides: questions_html(
            items, topics, slides, bank.read_media, language).encode("utf-8"))

    def export_all(self, suffix, kind, render):
        window = self.window
        if not window.may_leave():
            return
        filename = self.save_as("Export all questions with answers", "questions", suffix, kind)
        if not filename:
            return
        bank, topics = window.bank, window.topics
        slides = {ref.id: (ref.position, slide_title(ref)) for ref in window.slides or ()}

        def work():
            items, corrupt = bank.load()
            atomic_write_bytes(Path(filename), render(items, topics, slides), ".questions-")
            return len(items), len(corrupt)

        def done(counts):
            exported, unreadable = counts
            if unreadable:
                window.error(f"Exported {plural(exported, 'question')} to {filename}; {plural(unreadable, 'question')} "
                             f"could not be read and {agree(unreadable, 'is', 'are')} missing.")
            else:
                window.toast(f"Exported {plural(exported, 'question')} to {filename}. It holds the answers: keep it "
                             "private.")
        window.work(work, done)

    def save_as(self, title, name, suffix, kind):
        """The chosen file, with its suffix, or "" when cancelled."""
        filename, _ = QFileDialog.getSaveFileName(self.window, title, str(self.window.bank.folder / (name + suffix)),
                                                  kind)
        return filename if not filename or filename.lower().endswith(suffix) else filename + suffix
