# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Folder choices and exposure checks; independent of the user interface."""
from __future__ import annotations

from pathlib import Path
import subprocess

from .bank import Bank

_SETTINGS_KEY = "bank_folders"
_VIEWS = ("instructor", "during-course", "after-course")


def _absolute(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def remembered_folder(host, deck) -> Path | None:
    """Return an existing folder for this deck, or None when it needs choosing again."""
    choices = host.settings().get(_SETTINGS_KEY, {})
    if not isinstance(choices, dict):
        return None
    value = choices.get(str(_absolute(deck.path)))
    if not isinstance(value, str) or not value:
        return None
    folder = _absolute(value)
    return folder if folder.is_dir() else None


def _ignored_by_git(folder: Path, project: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(project), "check-ignore", "-q", "--", str(folder)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def folder_warnings(host, deck, folder: str | Path) -> list[str]:
    """Describe risks in a selection, resolving symlinks before containment checks."""
    folder = _absolute(folder)
    warnings = []
    if any(_inside(folder, _absolute(host.deck_output_folder(deck, view))) for view in _VIEWS):
        warnings.append("This folder is inside a deck output folder and may be shared with the output.")
    project = _absolute(host.project_root())
    if _inside(folder, project) and not _ignored_by_git(folder, project):
        warnings.append("This folder is inside the project and is not ignored by Git (or Git could not check it).")
    return warnings


def open_chosen_folder(host, deck, folder: str | Path) -> Bank:
    """Open or create the bank in an existing, explicitly approved directory and remember it."""
    folder = _absolute(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"Bank folder does not exist: {folder}")
    bank = Bank(folder, create=True)
    settings = host.settings()
    choices = settings.get(_SETTINGS_KEY, {})
    choices = dict(choices) if isinstance(choices, dict) else {}
    choices[str(_absolute(deck.path))] = str(folder)
    settings.set(_SETTINGS_KEY, choices)
    settings.save()
    return bank
