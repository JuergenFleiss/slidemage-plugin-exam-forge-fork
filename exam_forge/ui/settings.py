# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exam Forge settings, built when the host opens Settings."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QPlainTextEdit

from ..ai_support import ai_service
from ..drafting import DEFAULT_GUIDANCE


def add_folder_section(host, controller, page):
    deck = controller.open_deck()
    if deck is None:
        page.body.addWidget(host.Field("Question folder", host.caption("Open a deck to change its question folder.")))
        return
    path = host.caption("")
    path.setWordWrap(True)
    change = host.Button("Change folder…", compact=True)

    def show():
        folder = controller.bank_folder(deck)
        path.setText(f"{deck.name}: {folder if folder is not None else 'not chosen yet'}")

    def pick():
        if controller.change_folder(deck, page):
            show()

    change.clicked.connect(pick)
    show()
    page.body.addWidget(host.Field("Question folder", path))
    page.body.addWidget(change, 0, Qt.AlignmentFlag.AlignLeft)


def build_settings(host, controller):
    on_change = controller.refresh_ai_support
    page = host.Page()
    add_folder_section(host, controller, page)
    checkbox = QCheckBox("Enable AI support")
    checkbox.setChecked(bool(host.settings().get("ai_support", False)))
    notice = host.caption("")
    notice.setWordWrap(True)
    page.body.addWidget(checkbox)
    page.body.addWidget(notice)
    guidance = QPlainTextEdit()
    guidance.setPlainText(host.settings().get("drafting_guidance") or DEFAULT_GUIDANCE)
    field = host.Field("AI drafting guidance", guidance)
    reset = host.Button("Reset to default")
    page.body.addWidget(field)
    page.body.addWidget(reset)

    def save_guidance():
        value = guidance.toPlainText()
        settings = host.settings()
        settings.set("drafting_guidance", value if value.strip() and value != DEFAULT_GUIDANCE else None)
        settings.save()

    def reset_guidance():
        guidance.setPlainText(DEFAULT_GUIDANCE)
        save_guidance()

    guidance.textChanged.connect(save_guidance)
    reset.clicked.connect(reset_guidance)

    def update(enabled):
        field.setVisible(enabled)
        reset.setVisible(enabled)
        settings = host.settings()
        settings.set("ai_support", enabled)
        settings.save()
        service = ai_service(host) if enabled else None
        if enabled and (service is None or not service.available()):
            notice.setText("AI support needs an AI plugin, such as Claude or Ollama. Install and enable one in Settings.")
        else:
            notice.clear()
        on_change()

    checkbox.toggled.connect(update)
    if checkbox.isChecked():
        update(True)
    else:
        field.hide()
        reset.hide()
    return page
