# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Question-bank exports in a native popup, with rows from the host's themed kit."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu, QWidgetAction

from ..rules import question_issues


class ExportMenu(QMenu):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setToolTipsVisible(True)
        self.rows = {}
        self.moodle_action = self.add_export(
            "Moodle quiz (XML)", "Reviewed revision-quiz questions, ready to import into Moodle.", window.export)
        self.table_action = self.add_export(
            "Question table (CSV)", "Every question as a row, for Excel or LibreOffice.", window.export_table,
            answer_key=True)
        self.html_action = self.add_export(
            "Question page (HTML)", "Every question with its answers as one page.", window.export_html,
            answer_key=True)
        self.addSeparator()
        hint = QWidgetAction(self)
        label = window.host.Label("Exams: use the Exam tab", "caption")
        label.setContentsMargins(12, 6, 12, 6)
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
