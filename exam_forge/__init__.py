# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exam Forge's SlideMage command."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from slidemage.plugin_api import Host


def activate(host: Host) -> None:
    from .ui.controller import SIDEBAR_KEY, Controller
    from .ui.settings import build_settings

    controller = Controller(host)
    host.add_command("exam-forge.open", "Exam Forge…", controller.open, enabled=lambda deck: deck is not None)
    host.add_menu_entry("deck", "exam-forge.open")
    host.add_sidebar_box(SIDEBAR_KEY, "Exam Forge", controller.sidebar, size="compact")
    host.add_settings_tab("Exam Forge", lambda: build_settings(host, controller))
