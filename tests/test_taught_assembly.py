# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from itertools import combinations, product
from pathlib import Path

import pytest
from fake_host import FakeDeck, FakeHost
from slidemage.plugin_api import CourseSessionInfo, SlideRef, SlideSource

from exam_forge.assembly import Blueprint, assemble, check_approval, swap
from exam_forge.bank import Bank, ConflictError
from exam_forge.elp import DifficultyRules, ElpRules
from exam_forge.model import Item, Option, SourceLink
from exam_forge.presentation_log import Session
from exam_forge.sources import link_slide
from exam_forge.taught import TaughtOptions, build_evidence, filter_items


def item(id_, *, topic="a", difficulty="easy", sources=(), **kwargs):
    return Item(id_, 1, stem=id_, topic=topic, objective=topic.upper(), difficulty=difficulty,
                options=tuple(Option(str(i), f"Option {i}", i < 2) for i in range(5)),
                status="reviewed", use="elp", sources=sources, **kwargs)


def ref(id_, position, language="en", **kwargs):
    return SlideRef(id=id_, id_slug=f"{id_}-{language}-Title", path=Path(f"/{id_}.tex"),
                    position=position, language=language, language_read=language,
                    version="1-0-0", **kwargs)


def decks():
    # Deliberately out of list order: only position can pair these translations.
    return {(view, lang): [ref(f"{name}-{lang}", pos, lang, hidden=(pos == 2))
                          for name, pos in (("c", 3), ("a", 1), ("b", 2), ("d", 4))]
            for view in ("instructor", "during-course") for lang in ("de", "en")}


def log(slide="a-de", *, view="instructor", version="1-0-0"):
    return [{"type": "PresentationStarted", "monotonic": 100, "view": view, "language": "de"},
            {"type": "PageShown", "monotonic": 100, "page": 1, "slide_id": slide, "version": version},
            {"type": "PresentationEnded", "monotonic": 110}]


def schedule():
    return [CourseSessionInfo(f"2026-10-{day:02d}", "10:00", "11:00", "Room") for day in (1, 8, 15)]


def evidence():
    return build_evidence(decks(), [
        Session("Presented", "logged", presentation="presentations/logged.jsonl"),
    ], {"logged": log()})


@pytest.mark.parametrize("slides,expected", [
    (False, {"slide", "other", "both", "untaught", "unlinked"}),
    (True, {"slide"}),
])
def test_filter_states_and_all_sources_must_be_covered(slides, expected):
    questions = [item("slide", sources=(SourceLink("a-en"),)),
                 item("other", sources=(SourceLink("c-de"),)),
                 item("both", sources=(SourceLink("a-en"), SourceLink("c-en"))),
                 item("untaught", sources=(SourceLink("a-en"), SourceLink("d-en"))),
                 item("unlinked")]
    result = filter_items(questions, evidence(), options=TaughtOptions(slides))
    assert {q.id for q in result.included} == expected
    assert {row.item.id for row in result.excluded} == {q.id for q in questions} - expected
    assert all(row.reasons for row in result.excluded)


def test_tick_uses_deck_default_and_per_exam_override():
    questions = [item("other", sources=(SourceLink("c-en"),))]
    default = TaughtOptions(False)
    assert filter_items(questions, evidence(), deck_default=default).included == tuple(questions)
    override = filter_items(questions, evidence(), deck_default=default, options=TaughtOptions(True))
    assert not override.included and override.options == TaughtOptions(True)
    assert default == TaughtOptions(False)


def test_logs_win_over_schedule_translation_by_position_and_skips():
    data = build_evidence(decks(), [Session("Log", "l", presentation="l.jsonl")], {"l": log()},
                          schedule=schedule(), as_of=datetime(2026, 11, 1, tzinfo=UTC), cutoff_position=4)
    assert data.slides == {"a-de", "a-en"}
    assert data.slide_method == "log" and data.cutoff_position is None
    # The log's language is not an ID: a translated ID in an EN reading still matches.
    rows = log("c-en")
    assert build_evidence(decks(), [Session("L", "l", presentation="l")], {"l": rows}).slides == {"c-de", "c-en"}


@pytest.mark.parametrize("slide_id", ["a-de", "a-en"])
@pytest.mark.parametrize("logged,linked,included", [
    ("1-0-0", "2-0-0", False), ("1-0-0", "1-0-0", True),
    (None, "2-0-0", True), ("1-0-0", "", True),
])
def test_logged_pinned_versions_must_match_but_unpinned_versions_cannot_be_compared(slide_id, logged, linked, included):
    data = build_evidence(decks(), [Session("Log", "l", presentation="l")], {"l": log(version=logged)})
    assert data.slide_versions[slide_id] == {logged}
    question = item("q", sources=(SourceLink(slide_id, linked),))
    result = filter_items([question], data)
    assert bool(result.included) == included
    if not included:
        assert result.excluded[0].item == question
        assert all(value in result.excluded[0].reasons[0] for value in (slide_id, logged, linked, "version"))


def test_any_shown_version_can_supply_slide_evidence():
    sessions = [Session("Old", "old", presentation="old"), Session("New", "new", presentation="new")]
    logs = {"old": log(version="1-0-0"), "new": log(version="2-0-0")}
    questions = [item(v, sources=(SourceLink("a-en", v),)) for v in ("1-0-0", "2-0-0")]
    data = build_evidence(decks(), sessions, logs)
    assert data.slide_versions["a-en"] == {"1-0-0", "2-0-0"}
    assert filter_items(questions, data).included == tuple(questions)

def test_audience_state_jump_back_and_unknown_slide_ids():
    rows = log()
    rows[2:2] = [{"type": "ScreenState", "monotonic": 102, "state": "black", "audience_page": 1},
                 {"type": "PageShown", "monotonic": 103, "page": 2, "slide_id": "c-de", "version": None},
                 {"type": "ScreenState", "monotonic": 104, "state": "black", "audience_page": 2},
                 {"type": "ScreenState", "monotonic": 105, "state": "shown", "audience_page": 1}]
    session = Session("Log", "l", presentation="l")
    result = build_evidence(decks(), [session], {"l": rows})
    assert result.slides == {"a-de", "a-en"}
    result = build_evidence(decks(), [session], {"l": log(None)}, schedule=schedule(),
                            as_of=datetime(2026, 11, 1, tzinfo=UTC))
    assert result.slide_method == "log" and result.slides == frozenset()
    assert result.slide_reason
    assert not filter_items([item("q", sources=(SourceLink("a-de"),))], result,
                            options=TaughtOptions(True)).included
    assert build_evidence(decks(), [session], {"l": []}).slides == frozenset()
    with pytest.raises(ValueError, match="Read the presentation log"):
        build_evidence(decks(), [session])


def test_schedule_fallback_completed_sessions_visible_order_and_explicit_cutoff():
    kwargs = {"schedule": schedule(), "as_of": datetime(2026, 10, 8, 11, tzinfo=UTC)}
    result = build_evidence(decks(), **kwargs)
    assert result.slide_method == "schedule" and result.cutoff_position == 3
    assert result.slides == {"a-de", "a-en", "c-de", "c-en"}
    assert "confirm" in result.slide_reason
    assert build_evidence(decks(), **kwargs, cutoff_position=1).slides == {"a-de", "a-en"}
    assert not build_evidence(decks(), schedule=schedule(), as_of=datetime(2026, 9, 1, tzinfo=UTC)).slides
    assert "d-en" in build_evidence(decks(), schedule=schedule(), as_of=datetime(2026, 11, 1, tzinfo=UTC)).slides
    with pytest.raises(ValueError, match="as_of"):
        build_evidence(decks(), schedule=schedule())
    with pytest.raises(ValueError, match="Cutoff"):
        build_evidence(decks(), **kwargs, cutoff_position=-1)
    unknown_end = [replace(schedule()[0], end="")]
    assert not build_evidence(decks(), schedule=unknown_end, as_of=datetime(2026, 10, 1, 12, tzinfo=UTC)).slides


@pytest.mark.parametrize("schedule_value", [None, []])
def test_unavailable_evidence_reports_reason_without_disabling_requested_filter(schedule_value):
    result = build_evidence(decks(), schedule=schedule_value)
    assert result.slides is None
    filtered = filter_items([item("q", sources=(SourceLink("a-en"),))], result)
    assert not filtered.included and any("unavailable" in r for r in filtered.excluded[0].reasons)


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
    objectives = Blueprint({"A": 4, "B": 4, "C": 4}, {"easy": 6, "hard": 6}, 45, by="objective")
    assert assemble(pool(), objectives, seed=42).items == result.items


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
    for out, replacement in ((outgoing.id, wrong.id), (outgoing.id, "untaught"),
                             (outgoing.id, outgoing.id), ("missing", incoming.id)):
        with pytest.raises(ValueError):
            swap(result, out, replacement)


@pytest.mark.parametrize("kwargs", [{"counts": {"a": -1}}, {"difficulty_mix": {"medium": 1}},
                                    {"minutes": float("nan")}, {"minutes": True}, {"avoid_last": -1},
                                    {"by": "invalid"}, {"counts": {"a": True}}])
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


def test_logged_audience_visibility_freeze_black_unknown_and_unfinished():
    def page(t, number, slide):
        return {"type": "PageShown", "monotonic": t, "page": number,
                "slide_id": slide, "version": "1-0-0"}

    def screen(t, state, number):
        return {"type": "ScreenState", "monotonic": t, "state": state, "audience_page": number}

    rows = [log()[0], page(100, 1, "a-de"), screen(110, "frozen", 1),
            page(115, 2, "b-de"), screen(120, "shown", 1), page(125, 3, "c-de"),
            screen(130, "shown", 2), screen(135, "black", 2), page(138, 4, "d-de"),
            screen(140, "black", 4), screen(145, "shown", 3), screen(148, "shown", 99),
            {"type": "PresentationEnded", "monotonic": 150}]
    session = Session("Log", "l", presentation="l")
    assert build_evidence(decks(), [session], {"l": rows}).slides == {
        "a-de", "a-en", "b-de", "b-en", "c-de", "c-en"}
    assert build_evidence(decks(), [session], {"l": rows[:2]}).slides == frozenset()
    assert build_evidence(decks(), [session], {"l": rows[:3]}).slides == {"a-de", "a-en"}
    with pytest.raises(ValueError, match="begin"):
        build_evidence(decks(), [session], {"l": rows[1:]})
    for bad in (99, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="monotonic"):
            build_evidence(decks(), [session], {"l": [rows[0], page(bad, 1, "a-de")]})
