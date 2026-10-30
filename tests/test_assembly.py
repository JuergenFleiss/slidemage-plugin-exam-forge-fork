# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
from collections import Counter
from dataclasses import replace
from itertools import combinations, product
from pathlib import Path

import pytest
from fake_host import FakeDeck, FakeHost
from slidemage.plugin_api import SlideRef, SlideSource

from exam_forge.assembly import Blueprint, assemble, check_approval, swap
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


def test_45_minutes_216_points_seeded_exact_counts_and_objectives():
    blueprint = Blueprint({"a": 4, "b": 4, "c": 4}, {"easy": 6, "hard": 6}, 45)
    result = assemble(pool(), blueprint, seed=42)
    assert result.feasible and len(result.items) == 12
    assert result.total_minutes == 45 and result.total_points == 216
    assert Counter(q.topic for q in result.items) == blueprint.counts
    assert Counter(q.difficulty for q in result.items) == blueprint.difficulty_mix
    assert result.items == assemble(list(reversed(pool())), blueprint, seed=42).items
    assert result.items != assemble(pool(), blueprint, seed=43).items


@pytest.mark.parametrize("blueprint,questions,reason", [
    (Blueprint({"missing": 1}, {"easy": 1}, 2.5), pool(), "only 0"),
    (Blueprint({"a": 1}, {"easy": 2}, 5), pool(), "different totals"),
    (Blueprint({"a": 1}, {"easy": 1}, 3), pool(), "budget requires"),
    (Blueprint({"a": 1}, {"hard": 1}, 5), [item("e")], "difficulty mix"),
    (Blueprint({}, {}, 5), pool(), "no items"),
])
def test_infeasible_blueprint_never_relaxes_or_returns_partial_selection(blueprint, questions, reason):
    result = assemble(questions, blueprint, seed=1)
    assert not result.feasible and not result.items
    assert any(reason in p for p in result.problems)
    with pytest.raises(ValueError, match="infeasible"):
        swap(result, "a", "b")


def test_joint_topic_difficulty_constraints_against_small_exhaustive_oracle():
    # Covers cases where a greedy per-topic sample would use scarce difficulty slots.
    for capacities in product(range(3), repeat=4):
        questions = [item(f"{t}-{d}-{n}", topic=t, difficulty=d)
                     for (t, d), cap in zip(product(("a", "b"), ("easy", "hard")), capacities)
                     for n in range(cap)]
        blueprint = Blueprint({"a": 1, "b": 1}, {"easy": 1, "hard": 1}, 7.5)
        expected = any(Counter(q.topic for q in pair) == blueprint.counts and
                       Counter(q.difficulty for q in pair) == blueprint.difficulty_mix
                       for pair in combinations(questions, 2))
        result = assemble(questions, blueprint, seed=7)
        assert result.feasible == expected, capacities
        if expected:
            assert result.total_minutes == 7.5


def test_recent_exams_exclude_stable_ids_and_all_other_exclusions_are_explained():
    questions = [item("old"), item("recent", source_changed=True), item("new")]
    questions += [replace(item("draft"), status="draft"), replace(item("quiz"), use="revision"),
                  replace(item("bad"), options=()), item("unknown", difficulty="medium"), item("off", topic="off")]
    blueprint = Blueprint({"a": 1}, {"easy": 1}, 2.5, avoid_last=1)
    result = assemble(questions, blueprint, seed=4, previous_exams=[["old"], ["recent"]])
    assert {q.id for q in result.candidates} == {"old", "new"}
    assert {q.item.id for q in result.excluded} == {"recent", "draft", "quiz", "bad", "unknown", "off"}
    assert all(row.reasons for row in result.excluded)
    assert assemble([item("recent", source_changed=True)], replace(blueprint, avoid_last=0), seed=1,
                    previous_exams=[["recent"]]).feasible
    assert not assemble([item("recent")], blueprint, seed=1, previous_exams=[["recent"]]).feasible
    rules = ElpRules(easy=DifficultyRules(36, 0.1))
    custom = assemble([item(str(i)) for i in range(3)], Blueprint({"a": 3}, {"easy": 3}, 0.3), seed=1, rules=rules)
    assert custom.feasible and custom.total_minutes == 0.3 and custom.total_points == 108


def test_swap_keeps_blueprint_and_eligible_pool():
    result = assemble(pool(), Blueprint({"a": 1, "b": 1}, {"easy": 1, "hard": 1}, 7.5), seed=1)
    outgoing = result.items[0]
    incoming = next(q for q in result.candidates if q not in result.items and
                    (q.topic, q.difficulty) == (outgoing.topic, outgoing.difficulty))
    swapped = swap(result, outgoing.id, incoming.id)
    assert incoming in swapped.items and outgoing not in swapped.items
    assert swapped.total_minutes == result.total_minutes and outgoing in result.items
    wrong = next(q for q in result.candidates if q not in result.items and q.topic != outgoing.topic)
    for out, replacement in ((outgoing.id, wrong.id), (outgoing.id, "missing-replacement"),
                             (outgoing.id, outgoing.id), ("missing", incoming.id)):
        with pytest.raises(ValueError):
            swap(result, out, replacement)


@pytest.mark.parametrize("kwargs", [{"counts": {"a": -1}}, {"difficulty_mix": {"medium": 1}},
                                    {"minutes": float("nan")}, {"minutes": True}, {"avoid_last": -1},
                                    {"counts": {"a": True}}])
def test_invalid_blueprint_configuration(kwargs):
    params = {"counts": {"a": 1}, "difficulty_mix": {"easy": 1}, "minutes": 2.5}
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
    selection = assemble([reviewed], Blueprint({"a": 1}, {"easy": 1}, 2.5), seed=1)

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


