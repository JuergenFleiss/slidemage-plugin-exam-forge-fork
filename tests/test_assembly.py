# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
from collections import Counter
from dataclasses import replace
from itertools import product

import pytest

from exam_forge.assembly import Blueprint, assemble, available, swap, waiting_elp_drafts
from exam_forge.model import Item, Option
from exam_forge.rules import ExamRules

RULES = ExamRules()


def item(id_, *, topic="a", difficulty="easy", sources=(), **kwargs):
    """A reviewed exam question; its topic is kept in the learning objective, where topic() finds it."""
    return Item(id_, 1, stem=id_, objective=topic, difficulty=difficulty,
                options=tuple(Option(str(i), f"Option {i}", i < 2) for i in range(5)),
                status="reviewed", use="elp", sources=sources, **kwargs)


def pool():
    return [item(f"{topic}-{difficulty}-{n}", topic=topic, difficulty=difficulty)
            for topic in ("a", "b", "c") for difficulty in ("easy", "hard") for n in range(8)]


def topic(q):
    return q.objective


def bp(**cells):
    return Blueprint({t: {"easy": e, "hard": h} for t, (e, h) in cells.items()})


def test_45_minutes_216_points_seeded_exact_cells():
    blueprint = bp(a=(2, 2), b=(2, 2), c=(2, 2))
    result = assemble(pool(), blueprint, seed=42, topic_of=topic, rules=RULES)
    assert result.feasible and len(result.items) == 12
    assert result.total_minutes == 45 and result.total_points == 216
    assert Counter((topic(q), q.difficulty) for q in result.items) == Counter(
        {(t, d): 2 for t in "abc" for d in ("easy", "hard")})
    assert result.items == assemble(list(reversed(pool())), blueprint, seed=42, topic_of=topic, rules=RULES).items
    assert result.items != assemble(pool(), blueprint, seed=43, topic_of=topic, rules=RULES).items


@pytest.mark.parametrize("blueprint,questions,reason", [
    (bp(missing=(1, 0)), pool(), "Topic 'missing' needs 1 easy question; only 0 are eligible."),
    (bp(a=(9, 0)), pool(), "Topic 'a' needs 9 easy questions; only 8 are eligible."),
    (bp(a=(0, 3)), [item("h", difficulty="hard")], "needs 3 hard questions; only 1 is eligible."),
    (bp(a=(0, 0)), pool(), "no items"),
    (Blueprint({}), pool(), "no items"),
])
def test_infeasible_blueprint_never_relaxes_or_returns_partial_selection(blueprint, questions, reason):
    result = assemble(questions, blueprint, seed=1, topic_of=topic, rules=RULES)
    assert not result.feasible and not result.items
    assert any(reason in p for p in result.problems)
    with pytest.raises(ValueError, match="infeasible"):
        swap(result, "a", "b")
    assert len(assemble([item("e")], bp(a=(2, 1), b=(1, 0)), seed=1, topic_of=topic, rules=RULES).problems) == 3  # Every cell.


def test_cells_against_small_exhaustive_oracle():
    for capacities in product(range(3), repeat=4):
        questions = [item(f"{t}-{d}-{n}", topic=t, difficulty=d)
                     for (t, d), cap in zip(product(("a", "b"), ("easy", "hard")), capacities)
                     for n in range(cap)]
        expected = all(cap >= 1 for cap in capacities)
        result = assemble(questions, bp(a=(1, 1), b=(1, 1)), seed=7, topic_of=topic, rules=RULES)
        assert result.feasible == expected, capacities
        if expected:
            assert result.total_minutes == 15


def test_topic_comes_from_topic_of_and_a_question_without_one_is_left_out():
    names = {"x": "Algebra", "y": "", "z": "Algebra"}
    result = assemble([item("x"), item("y"), item("z")], bp(Algebra=(2, 0)), seed=1, topic_of=lambda q: names[q.id],
                      rules=RULES)
    assert result.feasible and [q.id for q in result.items] == [q.id for q in result.candidates] == ["x", "z"]


def test_recent_exams_and_ineligible_questions_are_left_out():
    questions = [item("old"), item("recent", changed_slides=("s",)), item("new")]
    questions += [replace(item("draft"), status="draft"), replace(item("quiz"), use="revision"),
                  replace(item("bad"), options=()), item("unknown", difficulty="medium"), item("off", topic="off")]
    blueprint = Blueprint({"a": {"easy": 1}}, avoid_last=1)
    result = assemble(questions, blueprint, seed=4, topic_of=topic, rules=RULES, previous_exams=[["old"], ["recent"]])
    assert {q.id for q in result.candidates} == {"old", "new"}
    assert assemble([item("recent", changed_slides=("s",))], replace(blueprint, avoid_last=0), seed=1,
                    topic_of=topic, rules=RULES, previous_exams=[["recent"]]).feasible
    assert not assemble([item("recent")], blueprint, seed=1, topic_of=topic, rules=RULES, previous_exams=[["recent"]]).feasible
    three = assemble([item(str(i)) for i in range(3)], bp(a=(3, 0)), seed=1, topic_of=topic, rules=RULES)
    assert three.feasible and three.total_minutes == 7.5 and three.total_points == 36


def test_swap_only_within_same_topic_and_difficulty():
    result = assemble(pool(), bp(a=(1, 1), b=(1, 0)), seed=1, topic_of=topic, rules=RULES)
    outgoing = result.items[0]
    alternatives = result.alternatives(outgoing.id)
    assert alternatives and all((topic(q), q.difficulty) == (topic(outgoing), outgoing.difficulty)
                                for q in alternatives)
    assert not set(alternatives) & set(result.items)
    swapped = swap(result, outgoing.id, alternatives[0].id)
    assert alternatives[0] in swapped.items and outgoing not in swapped.items
    assert swapped.total_minutes == result.total_minutes and outgoing in result.items
    wrong = [q for q in result.candidates if q not in result.items and
             (topic(q), q.difficulty) != (topic(outgoing), outgoing.difficulty)]
    assert wrong
    for out, replacement in [(outgoing.id, q.id) for q in wrong] + [
            (outgoing.id, "missing-replacement"), (outgoing.id, outgoing.id), ("missing", alternatives[0].id)]:
        with pytest.raises(ValueError):
            swap(result, out, replacement)
    assert result.alternatives("missing") == ()
    spare = assemble([item("only"), item("hard", difficulty="hard")], bp(a=(1, 0)), seed=1, topic_of=topic, rules=RULES)
    assert spare.alternatives("only") == ()


def test_available_and_waiting_drafts_share_eligibility():
    questions = [item("e1"), item("e2"), item("h1", difficulty="hard"), item("b1", topic="b"),
                 item("recent"), item("none", topic=""), replace(item("d1"), status="draft"),
                 replace(item("d2", topic="b"), status="draft"), replace(item("q"), status="draft", use="revision")]
    cells = {"easy": 0, "hard": 0, "open easy": 0, "open hard": 0}
    assert available(questions, topic, RULES) == {"a": {**cells, "easy": 3, "hard": 1}, "b": {**cells, "easy": 1}}
    assert available(questions, topic, RULES, [["recent"]], 1)["a"] == {**cells, "easy": 2, "hard": 1}
    assert available(questions, lambda q: "z", RULES) == {"z": {**cells, "easy": 5, "hard": 1}}
    assert waiting_elp_drafts(questions, topic) == {"a": 1, "b": 1}
    assert waiting_elp_drafts([item("n", topic="")], topic) == {}


@pytest.mark.parametrize("kwargs", [{"counts": {"a": {"easy": -1}}}, {"counts": {"a": {"medium": 1}}},
                                    {"counts": {"": {"easy": 1}}}, {"counts": {"a": {"easy": True}}},
                                    {"counts": {"a": {"easy": 1.5}}}, {"avoid_last": -1}])
def test_invalid_blueprint_configuration(kwargs):
    with pytest.raises(ValueError):
        Blueprint(**({"counts": {"a": {"easy": 1}}} | kwargs))
    cells = {"easy": 0, "hard": 0, "open easy": 0, "open hard": 0}
    assert Blueprint({"a": {"easy": 2}, "b": {}}, avoid_last=3).as_dict() == {
        "counts": {"a": {**cells, "easy": 2}, "b": cells}, "avoid_last": 3}

