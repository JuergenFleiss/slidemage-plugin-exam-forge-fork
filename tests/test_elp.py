# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import replace

import pytest

from exam_forge.elp import SCORING_FLOOR, DifficultyRules, ElpRules, check_elp, scoring_table
from exam_forge.model import Item, Option
from exam_forge.moodle_xml import export_moodle_xml
from test_ui import app, env, fill  # shared offscreen editor fixtures


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


@pytest.mark.parametrize('correct', range(1, 5))
def test_valid_elp_item(correct):
    assert check_elp(item_with(5, correct)) == []


@pytest.mark.parametrize('count,correct', [(4, 1), (6, 1), (5, 0), (5, 5)])
def test_invalid_elp_item(count, correct):
    assert check_elp(item_with(count, correct))


def test_editor_reviews_multiple_answer_elp_and_moodle_stays_single_answer(env):
    host, _, bank, _, window = env
    item = fill(window)
    options = tuple(Option(str(i), f'Answer {i}', i < 2) for i in range(5))
    item = bank.save(replace(item, options=options, use='elp'))
    window.populate(item)
    window.change_status('reviewed')
    host.drain()
    assert window.current.status == 'reviewed'
    with pytest.raises(ValueError, match='single-answer'):
        export_moodle_xml([window.current], released_elp={(window.current.id, window.current.revision)})
