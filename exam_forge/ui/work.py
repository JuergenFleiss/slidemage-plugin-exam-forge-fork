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


def fresh_deck(deck):
    return DeckSnapshot(deck.name, deck.path, deck.path.read_text(encoding="utf-8"), deck.groups)


def changed_details(checks):
    return "\n".join(f"{row.item.id}: {row.item.stem}\n" + "\n".join(
        f"  {s.source.slide_id} ({s.source.view}, {s.source.language}): {s.status} {s.message}"
        for s in row.sources if s.status != "unchanged") for row in checks)
