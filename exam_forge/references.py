# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Which questions still link to a slide; SlideMage asks before it retires a slide ID. Runs on a worker thread."""
from __future__ import annotations

from pathlib import Path

from slidemage.plugin_api import ReferenceCheckUnavailable

from .bank import Bank, BankError
from .folder import _SETTINGS_KEY, _absolute, _inside


def slide_references(host, ids: frozenset[str]) -> list[str]:
    """One line per question that links one of the slide IDs, in the banks of this project's decks."""
    choices = host.settings().get(_SETTINGS_KEY, {})
    project = _absolute(host.project_root())
    lines = []
    for deck, folder in (dict(choices) if isinstance(choices, dict) else {}).items():
        if not _inside(_absolute(deck), project):
            continue
        folder = _absolute(folder)
        try:
            if not folder.is_dir():
                raise BankError("the folder is missing")
            items, corrupt = Bank(folder).load()
            if corrupt:
                raise corrupt[0]
        except BankError as exc:
            raise ReferenceCheckUnavailable(f"Exam Forge bank folder is unavailable: {folder} ({exc})") from exc
        for item in items:
            for slide in dict.fromkeys(link.slide_id for link in item.sources if link.slide_id in ids):
                stem = " ".join(item.stem.split())
                lines.append(f'Bank for deck "{Path(deck).stem}": question "{stem[:60]}" uses {slide}')
    return lines
