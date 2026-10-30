# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""ELP item checks and scoring-sheet rules; student responses are not scored here."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .model import Item


SCORING_FLOOR = 0


@dataclass(frozen=True)
class DifficultyRules:
    """Points and planned minutes for one difficulty class."""

    points: int
    minutes: float

    def __post_init__(self) -> None:
        if type(self.points) is not int or self.points <= 0 or self.points % 12:
            raise ValueError("points must be a positive multiple of 12")
        if (isinstance(self.minutes, bool) or not isinstance(self.minutes, (int, float))
                or not isfinite(self.minutes) or self.minutes <= 0):
            raise ValueError("minutes must be a positive finite number")


@dataclass(frozen=True)
class ElpRules:
    easy: DifficultyRules = DifficultyRules(12, 2.5)
    hard: DifficultyRules = DifficultyRules(24, 5)


@dataclass(frozen=True)
class ScoringRow:
    correct_options: int
    correct_tick: int
    wrong_tick: int


def scoring_table(points: int) -> tuple[ScoringRow, ...]:
    """Return the per-option values for a scoring sheet, with a zero floor."""
    if type(points) is not int or points <= 0 or points % 12:
        raise ValueError("points must be a positive multiple of 12")
    return tuple(ScoringRow(n, points // n, -(points // (5 - n)))
                 for n in range(1, 5))


def check_elp(item: Item) -> list[str]:
    """Return answer-structure errors, allowing incomplete drafts to be saved."""
    errors = []
    if len(item.options) != 5:
        errors.append("An ELP item must have exactly five options.")
    if not 1 <= sum(option.correct for option in item.options) <= 4:
        errors.append("An ELP item must have one to four correct options.")
    return errors
