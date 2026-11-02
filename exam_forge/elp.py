# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""ELP item checks and scoring-sheet rules; student responses are not scored here."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .model import ELP_ANSWERS, AnswerRule, Item, type_issues


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
    answers: AnswerRule = ELP_ANSWERS
    kinds: tuple[str, ...] = ("choice",)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kinds", tuple(self.kinds))
        if "choice" not in self.kinds:
            return      # an open answer is graded by hand: no ticks to split the points into
        # Every tick is worth whole points: P/n for a correct one, P/(answers - n) for a wrong one.
        total = self.answers.max_options
        for name, rule in (("easy", self.easy), ("hard", self.hard)):
            for n in range(self.answers.min_correct, self.answers.max_correct + 1):
                if rule.points % n or rule.points % (total - n):
                    raise ValueError(f"With {total} answers and {n} correct, the {rule.points} points of {'an' if name == 'easy' else 'a'} {name} "
                                     "question cannot be split into whole points per answer.")


@dataclass(frozen=True)
class ScoringRow:
    correct_options: int
    correct_tick: int
    wrong_tick: int


def scoring_table(points: int, answers: AnswerRule = ELP_ANSWERS) -> tuple[ScoringRow, ...]:
    """Return the per-option values for a scoring sheet, with a zero floor: one row per allowed number of correct
    answers."""
    if type(points) is not int or points <= 0 or points % 12:
        raise ValueError("points must be a positive multiple of 12")
    total = answers.max_options
    return tuple(ScoringRow(n, points // n, -(points // (total - n)))
                 for n in range(answers.min_correct, answers.max_correct + 1))


def check_elp(item: Item, rule: AnswerRule = ELP_ANSWERS, kinds: tuple[str, ...] = ("choice",)) -> list[str]:
    """Return exam answer-structure errors, allowing incomplete drafts to be saved."""
    return [f"An exam question must {issue}." for issue in type_issues(item, kinds, rule)]
