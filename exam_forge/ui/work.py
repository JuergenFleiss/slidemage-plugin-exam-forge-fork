# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared snapshots and presentation of module results."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DeckSnapshot:
    name: str
    path: Path
    text: str
    groups: tuple[str, ...]


# A scroll area on a card: the stylesheet would paint the page colour on its viewport.
SCROLL_STYLE = "QScrollArea, QScrollArea > QWidget { background: transparent; border: none; }"


def slide_title(ref):
    """A slide's title to show: without LaTeX markup."""
    return ref.title_text or ref.title or ref.id_slug


def fresh_deck(deck):
    return DeckSnapshot(deck.name, deck.path, deck.path.read_text(encoding="utf-8"), deck.groups)

