# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Choose the private bank folder before opening or creating a bank."""
from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QMessageBox

from ..bank import Bank
from ..folder import folder_warnings, open_chosen_folder, remembered_folder


def choose_bank(host, deck, parent=None):
    """Open the remembered bank or ask for a folder. Return None on cancellation.

    A newly chosen risky folder needs explicit confirmation before any bank is written.
    Errors opening a bank or saving settings are allowed to reach the caller.
    """
    folder = remembered_folder(host, deck)
    if folder is not None and (folder / "bank.json").is_file():
        return Bank(folder)
    selected = QFileDialog.getExistingDirectory(parent, "Choose a folder for the question bank")
    if not selected:
        return None
    warnings = folder_warnings(host, deck, selected)
    if warnings:
        answer = QMessageBox.warning(
            parent, "Question bank folder", "\n\n".join(warnings) +
            "\n\nThe bank contains exam questions and answers. Use this folder anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return None
    folder = selected
    return open_chosen_folder(host, deck, folder)
