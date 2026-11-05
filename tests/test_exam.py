# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Issuing an exam: exact proposals and swaps, the frozen manifest, its source check and the correction journal."""
import base64
import json
import os
from collections import Counter
from dataclasses import replace
from datetime import datetime
from itertools import product
from pathlib import Path

import pytest
from fake_host import FakeDeck, FakeHost
from slidemage.plugin_api import SlideRef, SlideSource

from exam_forge.bank import Bank, ConflictError
from exam_forge.exam import (
    Blueprint,
    ChangedSourcesError,
    Manifest,
    append_correction,
    assemble,
    available,
    freeze,
    read_corrections,
    swap,
)
from exam_forge.model import Item, Option
from exam_forge.rules import ExamRules
from exam_forge.sources import link_slide

RULES = ExamRules()


def item(id_, *, topic="a", difficulty="easy", sources=(), **kwargs):
    """A reviewed exam question; its topic is kept in the learning objective, where topic() finds it."""
    return Item(id_, 1, stem=id_, objective=topic, difficulty=difficulty,
                options=tuple(Option(str(i), f"Option {i}", i < 2) for i in range(5)),
                status="reviewed", use="exam", sources=sources, **kwargs)


def pool():
    return [item(f"{topic}-{difficulty}-{n}", topic=topic, difficulty=difficulty)
            for topic in ("a", "b", "c") for difficulty in ("easy", "hard") for n in range(8)]


def topic(q):
    return q.objective


def pick(questions, blueprint, **kwargs):
    """assemble, with each question's topic taken from topic()."""
    return assemble(questions, blueprint, topics={q.id: topic(q) for q in questions}, rules=RULES, **kwargs)


def bp(**cells):
    """Multiple-choice counts: topic=(easy, hard)."""
    return Blueprint({(t, "choice", d): n for t, counts in cells.items() for d, n in zip(("easy", "hard"), counts)})


def test_45_minutes_216_points_seeded_exact_cells():
    blueprint = bp(a=(2, 2), b=(2, 2), c=(2, 2))
    result = pick(pool(), blueprint, seed=42)
    assert result.feasible and len(result.items) == 12
    assert result.total_minutes == 45 and result.total_points == 216
    assert Counter((topic(q), q.difficulty) for q in result.items) == Counter(
        {(t, d): 2 for t in "abc" for d in ("easy", "hard")})
    assert result.items == pick(list(reversed(pool())), blueprint, seed=42).items
    assert result.items != pick(pool(), blueprint, seed=43).items


@pytest.mark.parametrize("blueprint,questions,reason", [
    (bp(missing=(1, 0)), pool(), "Topic 'missing' needs 1 easy question; only 0 are eligible."),
    (bp(a=(9, 0)), pool(), "Topic 'a' needs 9 easy questions; only 8 are eligible."),
    (bp(a=(0, 3)), [item("h", difficulty="hard")], "needs 3 hard questions; only 1 is eligible."),
    (bp(a=(0, 0)), pool(), "no items"),
    (Blueprint({}), pool(), "no items"),
])
def test_infeasible_blueprint_never_relaxes_or_returns_partial_selection(blueprint, questions, reason):
    result = pick(questions, blueprint, seed=1)
    assert not result.feasible and not result.items
    assert any(reason in p for p in result.problems)
    with pytest.raises(ValueError, match="infeasible"):
        swap(result, "a", "b")
    assert len(pick([item("e")], bp(a=(2, 1), b=(1, 0)), seed=1).problems) == 3  # Every cell.


def test_cells_against_small_exhaustive_oracle():
    for capacities in product(range(3), repeat=4):
        questions = [item(f"{t}-{d}-{n}", topic=t, difficulty=d)
                     for (t, d), cap in zip(product(("a", "b"), ("easy", "hard")), capacities)
                     for n in range(cap)]
        expected = all(cap >= 1 for cap in capacities)
        result = pick(questions, bp(a=(1, 1), b=(1, 1)), seed=7)
        assert result.feasible == expected, capacities
        if expected:
            assert result.total_minutes == 15


def test_topic_comes_from_the_mapping_and_a_question_without_one_is_left_out():
    names = {"x": "Algebra", "y": "", "z": "Algebra"}
    result = assemble([item("x"), item("y"), item("z")], bp(Algebra=(2, 0)), seed=1, topics=names, rules=RULES)
    assert result.feasible and [q.id for q in result.items] == [q.id for q in result.candidates] == ["x", "z"]


def test_recent_exams_and_ineligible_questions_are_left_out():
    questions = [item("old"), item("recent", changed_slides=("s",)), item("new")]
    questions += [replace(item("draft"), status="draft"), replace(item("quiz"), use="revision"),
                  replace(item("bad"), options=()), item("unknown", difficulty="medium"), item("off", topic="off")]
    blueprint = Blueprint({("a", "choice", "easy"): 1}, avoid_last=1)
    result = pick(questions, blueprint, seed=4, previous_exams=[["old"], ["recent"]])
    assert {q.id for q in result.candidates} == {"old", "new"}
    assert pick([item("recent", changed_slides=("s",))], replace(blueprint, avoid_last=0), seed=1,
                    previous_exams=[["recent"]]).feasible
    assert not pick([item("recent")], blueprint, seed=1, previous_exams=[["recent"]]).feasible
    three = pick([item(str(i)) for i in range(3)], bp(a=(3, 0)), seed=1)
    assert three.feasible and three.total_minutes == 7.5 and three.total_points == 36


def test_swap_only_within_same_topic_and_difficulty():
    result = pick(pool(), bp(a=(1, 1), b=(1, 0)), seed=1)
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
    spare = pick([item("only"), item("hard", difficulty="hard")], bp(a=(1, 0)), seed=1)
    assert spare.alternatives("only") == ()


def test_available_shares_eligibility():
    questions = [item("e1"), item("e2"), item("h1", difficulty="hard"), item("b1", topic="b"),
                 item("recent"), item("none", topic=""), replace(item("d1"), status="draft"),
                 replace(item("d2", topic="b"), status="draft"), replace(item("q"), status="draft", use="revision")]
    names = {q.id: topic(q) for q in questions}
    assert available(questions, names, RULES) == {("a", "choice", "easy"): 3, ("a", "choice", "hard"): 1,
                                                  ("b", "choice", "easy"): 1}
    assert available(questions, names, RULES, [["recent"]], 1)[("a", "choice", "easy")] == 2
    assert available(questions, dict.fromkeys(names, "z"), RULES)[("z", "choice", "easy")] == 5


@pytest.mark.parametrize("counts", [{("a", "choice", "easy"): -1}, {("a", "choice", "medium"): 1},
                                    {("", "choice", "easy"): 1}, {("a", "essay", "easy"): 1},
                                    {("a", "choice", "easy"): True}, {("a", "choice", "easy"): 1.5}])
def test_invalid_blueprint_configuration(counts):
    with pytest.raises(ValueError):
        Blueprint(counts)
    with pytest.raises(ValueError):
        Blueprint({}, avoid_last=-1)
    assert Blueprint({("a", "open", "hard"): 2, ("b", "choice", "easy"): 0}, avoid_last=3).as_dict() == {
        "counts": [{"topic": "a", "kind": "open", "difficulty": "hard", "count": 2}], "avoid_last": 3}


@pytest.fixture
def setup(tmp_path):
    host = FakeHost(tmp_path)
    deck = FakeDeck("course", tmp_path / "deck.tex")
    ref = SlideRef(id="s", id_slug="s", path=tmp_path / "s.tex", language="en",
                   language_read="en", version="1", position=1)
    host.slides[(deck.path, "instructor", "en")] = [ref]
    host.sources[ref] = SlideSource(text="original")
    bank = Bank(tmp_path / "bank", create=True)
    media = bank.store_media("image.png", b"image bytes")
    link = link_slide(host, ref, "instructor")
    for name in ("q1", "q2"):
        bank.create(Item(name, 1, stem="Original", use="exam", difficulty="easy",
                         options=tuple(Option(str(i), str(i), i == 0) for i in range(5)),
                         sources=(link,), media=(media,)))
        bank.set_status(name, 1, "reviewed")
    return host, deck, ref, bank


def test_issue_is_detached_and_persistent_after_edit_and_retirement(setup, tmp_path):
    host, deck, _, bank = setup
    blueprint = {"topics": {"x": 2}}
    manifest = freeze(host, deck, bank, [("q1", 2), ("q2", 2)], blueprint, ExamRules(), topics={"q1": "T-q1", "q2": "T-q2"})
    path = tmp_path / "exam.json"
    manifest.write(path)
    blueprint["topics"]["x"] = 9
    detached = manifest.data()
    detached["items"][0]["stem"] = "tampered"
    bank.save(replace(bank.get("q1"), stem="Changed", status="draft"))
    bank.set_status("q2", 2, "retired")
    (bank.folder / "media" / bank.get("q2").media[0].name).write_bytes(b"corrupt")
    data = Manifest.read(path).data()
    assert data == manifest.data()
    assert [q["stem"] for q in data["items"]] == ["Original", "Original"]
    assert data["blueprint"]["topics"]["x"] == 2
    assert data["schema_version"] == 1 and [q["topic"] for q in data["items"]] == ["T-q1", "T-q2"]
    assert datetime.fromisoformat(data["issued"]).tzinfo is not None  # The manifest says when it was issued.
    assert base64.b64decode(next(iter(data["media_base64"].values()))) == b"image bytes"
    assert data["items"][0]["media"][0]["sha256"]
    assert data["scoring"]["tables"]["12"][0]["wrong_tick"] == -3
    with pytest.raises(FileExistsError):
        manifest.write(path)


def test_manifest_is_complete_before_atomic_publication(tmp_path, monkeypatch):
    manifest = Manifest(b'{"schema_version":1}')
    path = tmp_path / "exam.manifest.json"
    link = os.link

    def publish(source, target):
        assert not path.exists()
        assert target == path
        assert path.parent == Path(source).parent
        assert Path(source).read_bytes() == manifest.encoded + b"\n"
        link(source, target)

    monkeypatch.setattr(os, "link", publish)
    manifest.write(path)
    assert Manifest.read(path).data() == manifest.data()
    assert list(tmp_path.iterdir()) == [path]


def test_failed_manifest_write_leaves_no_published_or_temporary_file(tmp_path, monkeypatch):
    def fail(_fd):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError, match="disk full"):
        Manifest(b'{"schema_version":1}').write(tmp_path / "exam.manifest.json")
    assert list(tmp_path.iterdir()) == []


def test_atomic_manifest_publication_refuses_a_concurrent_issuer(tmp_path, monkeypatch):
    path = tmp_path / "exam.manifest.json"
    link = os.link

    def publish(source, target):
        path.write_bytes(b"another issued manifest")
        link(source, target)

    monkeypatch.setattr(os, "link", publish)
    with pytest.raises(FileExistsError):
        Manifest(b'{"schema_version":1}').write(path)
    assert path.read_bytes() == b"another issued manifest"
    assert list(tmp_path.iterdir()) == [path]


def test_checks_all_sources_before_issue(setup):
    host, deck, ref, bank = setup
    host.sources[ref] = SlideSource(text="changed")
    with pytest.raises(ChangedSourcesError) as error:
        freeze(host, deck, bank, [("q1", 2), ("q2", 2)], {}, ExamRules(), topics={})
    assert [c.item.id for c in error.value.checks] == ["q1", "q2"]
    assert all(c.sources[0].status == "changed" for c in error.value.checks)
    assert bank.get("q1").changed_slides == ("s",)  # Recorded, so the Questions tab shows it too.
    host.slides[(deck.path, "instructor", "en")] = []
    with pytest.raises(ChangedSourcesError) as error:
        freeze(host, deck, bank, [("q1", 2)], {}, ExamRules(), topics={})
    assert error.value.checks[0].sources[0].status == "missing"


def test_detects_edit_during_source_check(setup, monkeypatch):
    host, deck, _, bank = setup
    read = host.slide_source
    def edit(ref):
        bank.save(replace(bank.get("q1"), status="draft", stem="new"))
        return read(ref)
    monkeypatch.setattr(host, "slide_source", edit)
    with pytest.raises(ConflictError):
        freeze(host, deck, bank, [("q1", 2)], {}, ExamRules(), topics={})


def test_corrections_survive_a_truncated_line_and_refuse_a_corrupt_one(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_correction(path, "q1", "Fixed key", "Typo", time="2026-10-30T12:00:00Z")
    original = path.read_bytes()
    with path.open("ab") as stream:
        stream.write(b'{"item_id":"incomplete')
    append_correction(path, "q2", "Fixed text", "Clarity", time="2026-10-30T13:00:00Z")
    assert path.read_bytes().startswith(original)
    assert [row["item_id"] for row in read_corrections(path)] == ["q1", "q2"]
    path.write_bytes(b"bad\n")
    with pytest.raises(ValueError):
        append_correction(path, "q", "change", "reason")
    assert path.read_bytes() == b"bad\n"


def test_only_the_current_format_opens():
    for bad in (0, 2, None):
        with pytest.raises(ValueError):
            Manifest(json.dumps({"schema_version": bad}).encode())
