# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
import json
from collections import Counter
from dataclasses import replace
from itertools import product
from pathlib import Path

import pytest
from fake_host import FakeDeck, FakeHost
from slidemage.plugin_api import SlideRef, SlideSource

from exam_forge.assembly import (
    Blueprint,
    assemble,
    available,
    check_approval,
    swap,
    waiting_elp_drafts,
)
from exam_forge.bank import Bank, ConflictError
from exam_forge.elp import DifficultyRules, ElpRules
from exam_forge.model import Item, Option, SourceLink
from exam_forge.sources import link_slide


def item(id_, *, topic="a", difficulty="easy", sources=(), **kwargs):
    return Item(id_, 1, stem=id_, topic=topic, objective=topic.upper(), difficulty=difficulty,
                options=tuple(Option(str(i), f"Option {i}", i < 2) for i in range(5)),
                status="reviewed", use="elp", sources=sources, **kwargs)


def ref(id_, position, language="en", **kwargs):
    return SlideRef(id=id_, id_slug=f"{id_}-{language}-Title", path=Path(f"/{id_}.tex"),
                    position=position, language=language, language_read=language,
                    version="1-0-0", **kwargs)


def pool():
    return [item(f"{topic}-{difficulty}-{n}", topic=topic, difficulty=difficulty)
            for topic in ("a", "b", "c") for difficulty in ("easy", "hard") for n in range(8)]


def topic(q):
    return q.topic


def bp(**cells):
    return Blueprint({t: {"easy": e, "hard": h} for t, (e, h) in cells.items()})


def test_45_minutes_216_points_seeded_exact_cells():
    blueprint = bp(a=(2, 2), b=(2, 2), c=(2, 2))
    result = assemble(pool(), blueprint, seed=42, topic_of=topic)
    assert result.feasible and len(result.items) == 12
    assert result.total_minutes == 45 and result.total_points == 216
    assert Counter((q.topic, q.difficulty) for q in result.items) == Counter(
        {(t, d): 2 for t in "abc" for d in ("easy", "hard")})
    assert result.items == assemble(list(reversed(pool())), blueprint, seed=42, topic_of=topic).items
    assert result.items != assemble(pool(), blueprint, seed=43, topic_of=topic).items


@pytest.mark.parametrize("blueprint,questions,reason", [
    (bp(missing=(1, 0)), pool(), "Topic 'missing' needs 1 easy question; only 0 are eligible."),
    (bp(a=(9, 0)), pool(), "Topic 'a' needs 9 easy questions; only 8 are eligible."),
    (bp(a=(0, 3)), [item("h", difficulty="hard")], "needs 3 hard questions; only 1 is eligible."),
    (bp(a=(0, 0)), pool(), "no items"),
    (Blueprint({}), pool(), "no items"),
])
def test_infeasible_blueprint_never_relaxes_or_returns_partial_selection(blueprint, questions, reason):
    result = assemble(questions, blueprint, seed=1, topic_of=topic)
    assert not result.feasible and not result.items
    assert any(reason in p for p in result.problems)
    with pytest.raises(ValueError, match="infeasible"):
        swap(result, "a", "b")


def test_every_short_cell_is_reported():
    result = assemble([item("e")], bp(a=(2, 1), b=(1, 0)), seed=1, topic_of=topic)
    assert len(result.problems) == 3


def test_cells_against_small_exhaustive_oracle():
    for capacities in product(range(3), repeat=4):
        questions = [item(f"{t}-{d}-{n}", topic=t, difficulty=d)
                     for (t, d), cap in zip(product(("a", "b"), ("easy", "hard")), capacities)
                     for n in range(cap)]
        expected = all(cap >= 1 for cap in capacities)
        result = assemble(questions, bp(a=(1, 1), b=(1, 1)), seed=7, topic_of=topic)
        assert result.feasible == expected, capacities
        if expected:
            assert result.total_minutes == 15


def test_topic_comes_from_topic_of_and_empty_topic_is_excluded():
    questions = [item("x", topic="stale"), item("y", topic="stale"), item("z", topic="other")]
    names = {"x": "Algebra", "y": "", "z": "Algebra"}
    result = assemble(questions, bp(Algebra=(2, 0)), seed=1, topic_of=lambda q: names[q.id])
    assert result.feasible and [q.id for q in result.items] == ["x", "z"]
    assert all(q.topic in ("stale", "other") for q in result.items)
    assert [r.reasons for r in result.excluded] == [("Question has no topic; link it to a slide in a topic.",)]


def test_recent_exams_exclude_stable_ids_and_all_other_exclusions_are_explained():
    questions = [item("old"), item("recent", source_changed=True), item("new")]
    questions += [replace(item("draft"), status="draft"), replace(item("quiz"), use="revision"),
                  replace(item("bad"), options=()), item("unknown", difficulty="medium"), item("off", topic="off")]
    blueprint = Blueprint({"a": {"easy": 1}}, avoid_last=1)
    result = assemble(questions, blueprint, seed=4, topic_of=topic, previous_exams=[["old"], ["recent"]])
    assert {q.id for q in result.candidates} == {"old", "new"}
    assert {q.item.id for q in result.excluded} == {"recent", "draft", "quiz", "bad", "unknown", "off"}
    assert all(row.reasons for row in result.excluded)
    assert assemble([item("recent", source_changed=True)], replace(blueprint, avoid_last=0), seed=1,
                    topic_of=topic, previous_exams=[["recent"]]).feasible
    assert not assemble([item("recent")], blueprint, seed=1, topic_of=topic, previous_exams=[["recent"]]).feasible
    rules = ElpRules(easy=DifficultyRules(36, 0.1))
    custom = assemble([item(str(i)) for i in range(3)], bp(a=(3, 0)), seed=1, topic_of=topic, rules=rules)
    assert custom.feasible and custom.total_minutes == 0.3 and custom.total_points == 108


def test_swap_only_within_same_topic_and_difficulty():
    result = assemble(pool(), bp(a=(1, 1), b=(1, 0)), seed=1, topic_of=topic)
    outgoing = result.items[0]
    alternatives = result.alternatives(outgoing.id)
    assert alternatives and all((q.topic, q.difficulty) == (outgoing.topic, outgoing.difficulty)
                                for q in alternatives)
    assert not set(alternatives) & set(result.items)
    swapped = swap(result, outgoing.id, alternatives[0].id)
    assert alternatives[0] in swapped.items and outgoing not in swapped.items
    assert swapped.total_minutes == result.total_minutes and outgoing in result.items
    wrong = [q for q in result.candidates if q not in result.items and
             (q.topic, q.difficulty) != (outgoing.topic, outgoing.difficulty)]
    assert wrong
    for out, replacement in [(outgoing.id, q.id) for q in wrong] + [
            (outgoing.id, "missing-replacement"), (outgoing.id, outgoing.id), ("missing", alternatives[0].id)]:
        with pytest.raises(ValueError):
            swap(result, out, replacement)
    assert result.alternatives("missing") == ()


def test_alternatives_empty_when_cell_has_no_spare():
    result = assemble([item("only"), item("hard", difficulty="hard")], bp(a=(1, 0)), seed=1, topic_of=topic)
    assert result.alternatives("only") == ()


def test_available_and_waiting_drafts_share_eligibility():
    questions = [item("e1"), item("e2"), item("h1", difficulty="hard"), item("b1", topic="b"),
                 item("recent"), item("none", topic=""), replace(item("d1"), status="draft"),
                 replace(item("d2", topic="b"), status="draft"), replace(item("q"), status="draft", use="revision")]
    assert available(questions, topic) == {"a": {"easy": 3, "hard": 1}, "b": {"easy": 1, "hard": 0}}
    assert available(questions, topic, [["recent"]], 1)["a"] == {"easy": 2, "hard": 1}
    assert available(questions, lambda q: "z") == {"z": {"easy": 5, "hard": 1}}
    assert waiting_elp_drafts(questions, topic) == {"a": 1, "b": 1}
    assert waiting_elp_drafts([item("n", topic="")], topic) == {}


def test_blueprint_normalizes_and_serializes():
    blueprint = Blueprint({"a": {"easy": 2}, "b": {}}, avoid_last=3)
    assert blueprint.as_dict() == {"counts": {"a": {"easy": 2, "hard": 0}, "b": {"easy": 0, "hard": 0}},
                                   "avoid_last": 3}
    assert json.loads(json.dumps(blueprint.as_dict())) == blueprint.as_dict()


@pytest.mark.parametrize("kwargs", [{"counts": {"a": {"easy": -1}}}, {"counts": {"a": {"medium": 1}}},
                                    {"counts": {"": {"easy": 1}}}, {"counts": {"a": {"easy": True}}},
                                    {"counts": {"a": {"easy": 1.5}}}, {"avoid_last": -1}])
def test_invalid_blueprint_configuration(kwargs):
    params = {"counts": {"a": {"easy": 1}}}
    with pytest.raises(ValueError):
        Blueprint(**(params | kwargs))


def test_approval_always_rechecks_fingerprints_and_requires_acknowledgement(tmp_path):
    host, deck = FakeHost(tmp_path), FakeDeck("Deck", tmp_path / "deck.tex")
    slide = ref("a", 1)
    host.slides[(deck.path, "instructor", "en")] = [slide]
    host.sources[slide] = SlideSource("Original text")
    bank = Bank(tmp_path / "bank", create=True)
    source = link_slide(host, slide, "instructor")
    draft = replace(item("q", sources=(source,)), status="draft")
    bank.create(draft)
    reviewed = bank.set_status(draft.id, 1, "reviewed")
    selection = assemble([reviewed], bp(a=(1, 0)), seed=1, topic_of=topic)

    def check():
        calls = []
        check_approval(host, deck, bank, selection, lambda result, error: calls.append((result, error)))
        assert len(calls) == 1
        return calls[0]

    first, error = check()
    assert error is None and not first.changed and first.approve().items == selection.items
    host.sources[slide] = SlideSource("Overwritten without changing version")
    changed, error = check()
    assert error is None and [row.item.id for row in changed.changed] == ["q"]
    assert bank.get("q").source_changed and selection.items[0].source_changed is False
    with pytest.raises(ValueError, match="q"):
        changed.approve()
    assert changed.approve(accept_changed=True).items[0].source_changed
    host.sources[slide] = SlideSource("Original text")
    restored, error = check()
    assert error is None and not restored.changed and not bank.get("q").source_changed
    bank.save(replace(reviewed, status="draft", stem="Edited"))
    stale, error = check()
    assert stale is None and isinstance(error, ConflictError)
    (bank.folder / "q.json").write_text("broken", encoding="utf-8")
    failed, error = check()
    assert failed is None and error is not None


