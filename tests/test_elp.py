# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from exam_forge.elp import SCORING_FLOOR, DifficultyRules, ElpRules, check_elp, scoring_table
from exam_forge.model import Item, Option


@pytest.mark.parametrize("points, expected", [
    (12, [(12, -3), (6, -4), (4, -6), (3, -12)]),
    (24, [(24, -6), (12, -8), (8, -12), (6, -24)]),
])
def test_scoring_sheet_table(points, expected):
    assert SCORING_FLOOR == 0
    rows = scoring_table(points)
    assert [(r.correct_tick, r.wrong_tick) for r in rows] == expected
    for row in rows:
        assert row.correct_options * row.correct_tick + (5 - row.correct_options) * row.wrong_tick == 0


def test_rules_and_invalid_values():
    assert ElpRules() == ElpRules(DifficultyRules(12, 2.5), DifficultyRules(24, 5))
    assert ElpRules(easy=DifficultyRules(36, 7.5)).easy.minutes == 7.5
    for value in (0, -12, 13, 12.0, True):
        with pytest.raises(ValueError):
            DifficultyRules(value, 2.5)
        with pytest.raises(ValueError):
            scoring_table(value)
    for value in (0, -1, float('inf'), float('nan'), True, '5'):
        with pytest.raises(ValueError):
            DifficultyRules(12, value)


def item_with(count, correct):
    return Item('id', 1, options=tuple(Option(str(i), str(i), i < correct) for i in range(count)), use='elp')


@pytest.mark.parametrize('count,correct,valid', [
    (5, 1, True), (5, 2, True), (5, 3, True), (5, 4, True),
    (4, 1, False), (6, 1, False), (5, 0, False), (5, 5, False),
])
def test_elp_item_needs_five_answers_one_to_four_correct(count, correct, valid):
    assert (check_elp(item_with(count, correct)) == []) is valid
