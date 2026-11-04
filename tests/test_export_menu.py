# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native export-menu activation, availability and dismissal."""
from dataclasses import replace

import pytest
from fake_host import fill
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def open_menu(window):
    window.show()
    window.export_button.click()
    QApplication.processEvents()
    assert window.export_menu.isVisible()
    return window.export_menu


def test_availability_follows_the_bank(env, lab):
    window = env[4]
    menu = open_menu(window)
    assert menu.moodle_action.isEnabled() and menu.table_action.isEnabled()
    menu.close()
    window.items = [replace(item, use="elp") for item in window.items]
    menu = open_menu(window)
    assert not menu.moodle_action.isEnabled() and "No reviewed revision-quiz" in menu.moodle_action.toolTip()
    menu.close()
    window.items = [replace(item, use="revision", options=()) for item in window.items]
    menu = open_menu(window)
    assert not menu.moodle_action.isEnabled() and "answer rules" in menu.moodle_action.toolTip()
    menu.close()
    window.items = []
    menu = open_menu(window)
    assert all(not action.isEnabled() and action.toolTip() for action in menu.rows)
    menu.close()


@pytest.mark.parametrize("keyboard", [False, True])
def test_selection_closes_the_menu_before_the_export_runs(lab, monkeypatch, keyboard):
    window = lab[4]
    calls = []
    monkeypatch.setattr(window, "may_leave", lambda: calls.append(window.export_menu.isVisible()) or False)
    menu = open_menu(window)
    if keyboard:
        QTest.keyClick(menu, Qt.Key.Key_Down)
        QTest.keyClick(menu, Qt.Key.Key_Down)
        assert menu.activeAction() is menu.table_action
        QTest.keyClick(menu, Qt.Key.Key_Return)
    else:
        QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(menu.html_action).center())
    assert calls == [False] and not menu.isVisible()


def test_the_keyboard_skips_disabled_rows_and_click_away_escape_and_busy_close_it(env):
    window = env[4]
    fill(window)
    menu = open_menu(window)
    QTest.keyClick(menu, Qt.Key.Key_Down)
    assert menu.activeAction() is menu.table_action  # Moodle is disabled: nothing is reviewed.
    QTest.keyClick(menu, Qt.Key.Key_Escape)
    assert not menu.isVisible()
    open_menu(window)
    QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=QPoint(-5, -5))
    assert not menu.isVisible()
    open_menu(window)
    window.set_busy(True)
    assert not menu.isVisible() and not window.export_button.isEnabled()
