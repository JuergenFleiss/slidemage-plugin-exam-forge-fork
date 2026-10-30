# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exam Forge's SlideMage command."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from slidemage.plugin_api import Deck, Host


def activate(host: Host) -> None:
    def run(deck: Deck) -> None:
        host.show_message("Exam Forge is not built yet.")

    host.add_command("exam-forge.open", "Exam Forge…", run, enabled=lambda deck: deck is not None)
    host.add_menu_entry("deck", "exam-forge.open")
