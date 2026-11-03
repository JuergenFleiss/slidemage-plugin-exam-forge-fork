# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native export-menu activation, availability and dismissal."""
from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel
from test_questions_tab import (
    lab as lab,  # noqa: PLC0414 - expose shared pytest fixtures
)
from test_ui import app as app  # noqa: PLC0414
from test_ui import env as env  # noqa: PLC0414
from test_ui import fill


def open_menu(window):
    window.show()
    window.export_button.click()
    QApplication.processEvents()
    assert window.export_menu.isVisible()
    return window.export_menu


def test_empty_bank_and_draft_only_availability(env):
    _, _, _, _, window = env
    menu = open_menu(window)
    assert all(not action.isEnabled() and action.toolTip() for action in menu.rows)
    menu.close()
    fill(window)
    menu = open_menu(window)
    assert not menu.moodle_action.isEnabled()
    assert menu.table_action.isEnabled() and menu.html_action.isEnabled()
    assert all(not row.toolTip() for action, row in menu.rows.items() if action.isEnabled())
    menu.close()


def test_moodle_needs_a_reviewed_revision_question_fitting_the_rules(lab):
    _, _, _, _, window, *_ = lab
    menu = open_menu(window)
    assert menu.moodle_action.isEnabled()
    menu.close()
    window.items = [replace(item, use="elp") for item in window.items]
    menu = open_menu(window)
    assert not menu.moodle_action.isEnabled() and "No reviewed revision-quiz" in menu.moodle_action.toolTip()
    menu.close()
    window.items = [replace(item, use="revision", options=()) for item in window.items]
    menu = open_menu(window)
    assert not menu.moodle_action.isEnabled() and "answer rules" in menu.moodle_action.toolTip()
    menu.close()


@pytest.mark.parametrize("which", ["moodle", "table", "html"])
@pytest.mark.parametrize("keyboard", [False, True])
def test_selection_closes_before_the_existing_export_handler(lab, monkeypatch, which, keyboard):
    _, _, _, _, window, *_ = lab
    calls = []
    monkeypatch.setattr(window, "may_leave", lambda: calls.append(window.export_menu.isVisible()) or False)
    menu = open_menu(window)
    action = getattr(menu, which + "_action")
    if keyboard:
        for _ in range([menu.moodle_action, menu.table_action, menu.html_action].index(action) + 1):
            QTest.keyClick(menu, Qt.Key.Key_Down)
        assert menu.activeAction() is action
        assert menu.rows[action].selected()
        QTest.keyClick(menu, Qt.Key.Key_Return)
    else:
        QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center())
    assert calls == [False] and not menu.isVisible()


def test_up_down_skip_disabled_items_and_escape_closes(env):
    _, _, _, _, window = env
    fill(window)
    menu = open_menu(window)
    QTest.keyClick(menu, Qt.Key.Key_Down)
    assert menu.activeAction() is menu.table_action
    QTest.keyClick(menu, Qt.Key.Key_Down)
    assert menu.activeAction() is menu.html_action
    QTest.keyClick(menu, Qt.Key.Key_Up)
    assert menu.activeAction() is menu.table_action
    QTest.keyClick(menu, Qt.Key.Key_Escape)
    assert not menu.isVisible()


def test_click_away_and_busy_close_menu(lab):
    _, _, _, _, window, *_ = lab
    menu = open_menu(window)
    QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=QPoint(-5, -5))
    assert not menu.isVisible()
    open_menu(window)
    window.set_busy(True)
    assert not menu.isVisible() and not window.export_button.isEnabled()
    window.set_busy(False)
    assert window.export_button.isEnabled()


def test_descriptions_chips_and_exam_hint(lab):
    _, _, _, _, window, *_ = lab
    menu = open_menu(window)
    assert [action.text() for action in menu.rows] == [
        "Moodle quiz (XML)", "Question table (CSV)", "Question page (HTML)"]
    assert all(row.sub.text() for row in menu.rows.values())
    assert menu.rows[menu.moodle_action].chip is None
    for action in (menu.table_action, menu.html_action):
        assert menu.rows[action].chip.property("tone") == "warning"
        assert any(label.text() == "Answer key" for label in menu.rows[action].chip.findChildren(QLabel))
    assert any(label.text() == "Exams: use the Exam tab" for label in menu.findChildren(QLabel))
    assert not hasattr(window, "table_button") and not hasattr(window, "html_button")
    menu.close()
