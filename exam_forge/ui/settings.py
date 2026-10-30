# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exam Forge settings, built when the host opens Settings."""
from PySide6.QtWidgets import QCheckBox, QLabel, QPlainTextEdit

from ..ai_support import ai_service
from ..drafting import DEFAULT_GUIDANCE


def build_settings(host, on_change=lambda: None):
    page = host.Page()
    checkbox = QCheckBox("Enable AI support")
    checkbox.setChecked(bool(host.settings().get("ai_support", False)))
    notice = QLabel()
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
