# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exam Forge settings, built when the host opens Settings."""
from PySide6.QtWidgets import QCheckBox, QLabel

from ..ai_support import ai_service


def build_settings(host, on_change=lambda: None):
    page = host.Page()
    checkbox = QCheckBox("Enable AI support")
    checkbox.setChecked(bool(host.settings().get("ai_support", False)))
    notice = QLabel()
    notice.setWordWrap(True)
    page.body.addWidget(checkbox)
    page.body.addWidget(notice)

    def update(enabled):
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
    return page
