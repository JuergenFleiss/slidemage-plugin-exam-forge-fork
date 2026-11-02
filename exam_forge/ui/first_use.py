# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Choose the private bank folder before opening or creating a bank."""
from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QMessageBox

from ..bank import Bank
from ..folder import _absolute, copy_bank, folder_warnings, open_chosen_folder, remembered_folder


def accept_risks(host, deck, selected, parent=None):
    warnings = folder_warnings(host, deck, selected)
    if not warnings:
        return True
    answer = QMessageBox.warning(
        parent, "Question bank folder", "\n\n".join(warnings) +
        "\n\nThe bank contains exam questions and answers. Use this folder anyway?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


def choose_bank(host, deck, parent=None):
    """Open the remembered bank or ask for a folder. Return None on cancellation.

    A newly chosen risky folder needs explicit confirmation before any bank is written.
    Errors opening a bank or saving settings are allowed to reach the caller.
    """
    folder = remembered_folder(host, deck)
    selected = folder if folder is not None and (folder / "bank.json").is_file() else QFileDialog.getExistingDirectory(parent, "Choose a folder for the question bank")
    if not selected:
        return None
    if not accept_risks(host, deck, selected, parent):
        return None
    if folder is not None and selected == folder:
        return Bank(folder)
    return open_chosen_folder(host, deck, selected)


def change_bank(host, deck, current, parent=None, may_leave=lambda: True):
    """Ask for another folder for this deck. Return (bank, message), or None if nothing changed.

    A folder with a bank is used as it is; otherwise the current bank is copied there, so that
    the questions come along. The old folder is never changed: it is the user's to delete.
    """
    selected = QFileDialog.getExistingDirectory(parent, "Choose a folder for the question bank", str(current or ""))
    if not selected or (current is not None and _absolute(selected) == _absolute(current)):
        return None
    if not accept_risks(host, deck, selected, parent):
        return None
    target = _absolute(selected)
    has_bank = (target / "bank.json").is_file()
    if has_bank:
        count = len(Bank(target).load()[0])
        answer = QMessageBox.question(
            parent, "Question bank folder",
            f"This folder already has a question bank with {count} question{'' if count == 1 else 's'}. "
            "Use that bank for this deck? Nothing is merged; the questions in the current folder stay there.")
        if answer != QMessageBox.StandardButton.Yes:
            return None
    if not may_leave():
        return None
    copied = not has_bank and current is not None and (current / "bank.json").is_file()
    if copied:
        copy_bank(current, target)
    bank = open_chosen_folder(host, deck, target)
    if copied:
        return bank, (f"Questions copied to {target}. The old folder still holds its copy, "
                      f"including the answers; delete it once you have checked the new one: {current}")
    return bank, f"This deck now uses the question bank in {target}."
