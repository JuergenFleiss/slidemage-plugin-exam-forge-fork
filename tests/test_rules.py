# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exam scoring, totals and answer checks."""
import pytest

from exam_forge.model import Item, Option
from exam_forge.rules import (EXAM_ANSWERS, MINUTES, POINTS, SCORING_FLOOR, AnswerRule, ExamRules, exam_issues,
                              exam_totals, scoring_table)


@pytest.mark.parametrize("points, expected", [
    (12, [(12, -3), (6, -4), (4, -6), (3, -12)]),
    (24, [(24, -6), (12, -8), (8, -12), (6, -24)]),
])
def test_scoring_sheet_table(points, expected):
    assert SCORING_FLOOR == 0
    rows = scoring_table(points, EXAM_ANSWERS)
    assert [(r.correct_tick, r.wrong_tick) for r in rows] == expected
    for row in rows:
        assert row.correct_options * row.correct_tick + (5 - row.correct_options) * row.wrong_tick == 0


def test_points_minutes_and_totals():
    assert (POINTS, MINUTES) == ({"easy": 12, "hard": 24}, {"easy": 2.5, "hard": 5})
    assert exam_totals(["easy"] * 6 + ["hard"] * 6) == (216, 45) and exam_totals([]) == (0, 0)
    assert exam_totals(["easy", "hard"], {"easy": 36, "hard": 60}, {"easy": .1, "hard": .2}) == (96, 0.3)
    for value in (0, -12, 13, 12.0, True):
        with pytest.raises(ValueError):
            scoring_table(value, EXAM_ANSWERS)
    with pytest.raises(ValueError, match="With 6 answers and 1 correct"):
        ExamRules(AnswerRule(6, 6, 1, 4))
    assert ExamRules(AnswerRule(6, 6, 1, 4), ("open",)).kinds == ("open",)  # Graded by hand: no ticks to split.


def item_with(count, correct):
    return Item('id', 1, options=tuple(Option(str(i), str(i), i < correct) for i in range(count)), use='exam')


@pytest.mark.parametrize('count,correct,valid', [
    (5, 1, True), (5, 2, True), (5, 3, True), (5, 4, True),
    (4, 1, False), (6, 1, False), (5, 0, False), (5, 5, False),
])
def test_an_exam_question_needs_five_answers_one_to_four_correct(count, correct, valid):
    assert (exam_issues(item_with(count, correct), ExamRules()) == []) is valid
