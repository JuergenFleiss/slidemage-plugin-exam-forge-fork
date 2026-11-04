# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Issuing an exam, as the Exam tab walks through it: propose a pick that matches the counts exactly, swap single
questions, freeze an immutable snapshot after a fresh source check, and record corrections in a separate journal.

freeze performs disk and host reads; run it in a worker with a fresh deck.
Manifest data includes answer keys and media bytes and must be kept private.
"""
from __future__ import annotations

import base64
import json
import os
import random
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from .bank import FORMAT_VERSION, Bank, ConflictError
from .model import KINDS, Item, Topic, agree, plural, topic_of
from .rules import MINUTES, POINTS, SCORING_FLOOR, ExamRules, exam_issues, exam_totals, scoring_table
from .sources import ItemCheck, check_item

DIFFICULTIES = tuple(POINTS)
Cell = tuple[str, str, str]   # (topic, kind, difficulty): one count of a blueprint


def cell_of(item: Item, topics: Mapping[str, str]) -> Cell:
    return topics.get(item.id, ""), item.kind, item.difficulty


def cell_name(kind: str, difficulty: str) -> str:
    """'easy' or 'hard open-answer', for "needs 2 … questions"."""
    return difficulty if kind == "choice" else f"{difficulty} open-answer"


def topic_names(items: Iterable[Item], topics: Iterable[Topic]) -> dict[str, str]:
    """Each question's topic name, resolved once from its first slide; "" without one."""
    topics = tuple(topics)
    return {item.id: getattr(topic_of(item, topics), "name", "") for item in items}


@dataclass(frozen=True)
class Blueprint:
    """Exact counts per topic, question type and difficulty; points and minutes follow from the difficulties."""

    counts: Mapping[Cell, int]
    avoid_last: int = 0

    def __post_init__(self):
        for (topic, kind, difficulty), count in self.counts.items():
            if (not isinstance(topic, str) or not topic or kind not in KINDS or difficulty not in DIFFICULTIES
                    or type(count) is not int or count < 0):
                raise ValueError("Blueprint counts need a topic, a question type, a difficulty and a nonnegative "
                                 "whole number")
        if type(self.avoid_last) is not int or self.avoid_last < 0:
            raise ValueError("avoid_last must be a nonnegative integer")
        object.__setattr__(self, "counts", {cell: n for cell, n in sorted(self.counts.items()) if n})

    def as_dict(self) -> dict:
        """As the manifest stores it: one row per requested cell."""
        return {"counts": [{"topic": t, "kind": k, "difficulty": d, "count": n} for (t, k, d), n in self.counts.items()],
                "avoid_last": self.avoid_last}

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@dataclass(frozen=True)
class Assembly:
    blueprint: Blueprint
    rules: ExamRules
    seed: int
    items: tuple[Item, ...]
    candidates: tuple[Item, ...]
    topics: Mapping[str, str]   # item ID -> topic name, resolved when the exam was proposed
    problems: tuple[str, ...] = ()

    @property
    def feasible(self) -> bool:
        return not self.problems

    @property
    def total_minutes(self) -> float:
        return exam_totals(i.difficulty for i in self.items)[1]

    @property
    def total_points(self) -> int:
        return exam_totals(i.difficulty for i in self.items)[0]

    def alternatives(self, item_id: str) -> tuple[Item, ...]:
        """Unselected candidates in the same topic and cell as a selected item."""
        current = next((i for i in self.items if i.id == item_id), None)
        if current is None:
            return ()
        selected, cell = {i.id for i in self.items}, cell_of(current, self.topics)
        return tuple(c for c in self.candidates if c.id not in selected and cell_of(c, self.topics) == cell)


def _screen(items: Sequence[Item], topics: Mapping[str, str], previous_exams, avoid_last: int,
            rules: ExamRules) -> list[Item]:
    """The eligible questions, ordered by ID: reviewed exam questions that fit the rules, have a topic and a
    difficulty, and were not used in the last `avoid_last` exams."""
    if len({i.id for i in items}) != len(items):
        raise ValueError("Supply one current revision per item ID")
    recent = {id_ for exam in previous_exams[-avoid_last:] for id_ in exam} if avoid_last else set()
    return [item for item in sorted(items, key=lambda i: i.id)
            if item.status == "reviewed" and item.use == "elp" and not exam_issues(item, rules)
            and item.difficulty in DIFFICULTIES and topics.get(item.id) and item.id not in recent]


def available(items: Sequence[Item], topics: Mapping[str, str], rules: ExamRules, previous_exams=(),
              avoid_last: int = 0) -> Counter[Cell]:
    """Eligible questions per cell, as assemble would see them."""
    return Counter(cell_of(item, topics) for item in _screen(items, topics, previous_exams, avoid_last, rules))


def assemble(items: Sequence[Item], blueprint: Blueprint, *, seed: int, topics: Mapping[str, str],
             rules: ExamRules, previous_exams: Sequence[Sequence[str]] = ()) -> Assembly:
    """Select reviewed exam questions, with no relaxed constraints or partial result.

    Pass current revisions and their topic names. History is oldest first and
    contains stable item IDs: reuse avoidance covers all revisions. Input order
    does not affect a seeded selection. Changed slides remain a separate flag.
    Each cell is filled exactly from the questions in it.
    """
    if type(seed) is not int:
        raise ValueError("Seed must be an integer")
    topics = dict(topics)
    candidates = [item for item in _screen(items, topics, previous_exams, blueprint.avoid_last, rules)
                  if cell_of(item, topics) in blueprint.counts]
    result = Assembly(blueprint, rules, seed, (), tuple(candidates), topics)
    problems = [] if blueprint.total else ["The blueprint requests no items."]
    pools = []
    for cell, n in blueprint.counts.items():
        pool = [i for i in candidates if cell_of(i, topics) == cell]
        if n > len(pool):
            have = len(pool)
            problems.append(f"Topic {cell[0]!r} needs {plural(n, cell_name(*cell[1:]) + ' question')}; "
                            f"only {have} {agree(have, 'is', 'are')} eligible.")
        pools.append((n, pool))
    if problems:
        return replace(result, problems=tuple(problems))
    rng = random.Random(seed)
    chosen = [i for n, pool in pools for i in rng.sample(pool, n)]
    return replace(result, items=tuple(sorted(chosen, key=lambda i: i.id)))


def swap(selection: Assembly, outgoing_id: str, incoming_id: str) -> Assembly:
    """Swap only within the same topic, type and difficulty, from the eligible pool."""
    if not selection.feasible:
        raise ValueError("Cannot swap an infeasible selection")
    pool = {i.id: i for i in selection.alternatives(outgoing_id)}
    if incoming_id not in pool:
        raise ValueError("Swap needs a selected item and an unselected eligible replacement "
                         "of the same topic, type and difficulty")
    items = tuple(pool[incoming_id] if i.id == outgoing_id else i for i in selection.items)
    return replace(selection, items=items)


class ChangedSourcesError(ValueError):
    """All selected questions whose sources changed or could not be checked."""

    def __init__(self, checks: Iterable[ItemCheck]):
        self.checks = tuple(checks)
        super().__init__("Sources changed or unavailable: " + ", ".join(c.item.id for c in self.checks))


@dataclass(frozen=True)
class Manifest:
    """An immutable UTF-8 JSON snapshot; data() returns a detached copy."""

    encoded: bytes

    def __post_init__(self) -> None:
        data = json.loads(self.encoded)
        if not isinstance(data, dict) or data.get("schema_version") != FORMAT_VERSION:
            raise ValueError("unsupported manifest schema")

    def data(self) -> dict:
        return json.loads(self.encoded)

    def write(self, path: str | Path) -> None:
        """Create a new manifest without overwriting an issued one."""
        path = Path(path)
        name = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix="." + path.name + ".",
                                             delete=False) as stream:
                name = stream.name
                stream.write(self.encoded + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            # Linking publishes the complete file atomically and refuses an existing path.
            os.link(name, path)
        finally:
            if name is not None:
                os.unlink(name)

    @classmethod
    def read(cls, path: str | Path) -> Manifest:
        return cls(Path(path).read_bytes())


def freeze(host, deck, bank: Bank, selections: Iterable[tuple[str, int]], blueprint: dict, rules: ExamRules,
           *, topics: Mapping[str, str]) -> Manifest:
    """Check and freeze selected current reviewed revisions, preserving their order.

    ChangedSourcesError carries every failing ItemCheck, and the bank records their
    changed slides, so the Questions tab shows them too. Other failures abort the
    entire issue. Blueprint must contain JSON values; no caller-owned mutable
    object or bank media is retained in the returned snapshot. Each item snapshot
    stores its topic name from `topics`, so later topic changes do not alter it.
    """
    selections = tuple(selections)
    if not selections or len({item_id for item_id, _ in selections}) != len(selections):
        raise ValueError("select at least one question, without duplicates")
    items = []
    for item_id, revision in selections:
        item = bank.get(item_id)
        if item.revision != revision:
            raise ConflictError(f"stale revision for {item_id}")
        if item.status != "reviewed" or item.use != "elp":
            raise ValueError(f"{item_id} must be a reviewed exam question")
        errors = exam_issues(item, rules)
        if errors:
            raise ValueError(f"{item_id}: {' '.join(errors)}")
        items.append(item)
    checks = [check_item(host, deck, item) for item in items]
    changed = [check for check in checks if check.source_changed]
    if changed:
        for check in changed:
            bank.set_changed_slides(check.item.id, check.changed_slides, expected_sources=check.item.sources)
        raise ChangedSourcesError(changed)
    attachments = {}
    for item in items:
        for media in item.media:
            attachments[media.name] = base64.b64encode(bank.read_media(media)).decode("ascii")
    # Detect edits or retirement while the checks and media reads were running.
    for item in items:
        if bank.get(item.id) != item:
            raise ConflictError(f"question changed during freeze: {item.id}")
    data = {
        "schema_version": FORMAT_VERSION,
        "bank_id": bank.id,
        "issued": datetime.now(timezone.utc).isoformat(),
        "items": [{**asdict(item), "topic": topics.get(item.id, "")} for item in items],
        "media_base64": attachments,
        "blueprint": blueprint,
        "scoring": {"rules": {**{d: {"points": POINTS[d], "minutes": MINUTES[d]} for d in POINTS},
                              "answers": asdict(rules.answers), "kinds": list(rules.kinds)},
                    "floor": SCORING_FLOOR,
                    "tables": {str(p): [asdict(row) for row in scoring_table(p, rules.answers)]
                               for p in POINTS.values()} if "choice" in rules.kinds else {}},
    }
    return Manifest(json.dumps(data, ensure_ascii=False, sort_keys=True,
                               allow_nan=False, separators=(",", ":")).encode("utf-8"))


def read_history(bank: Bank) -> tuple[list[list[str]], list[str]]:
    """Earlier exams' item IDs, oldest first, and a message for each unreadable manifest."""
    previous, errors = [], []
    for path in sorted(bank.folder.glob("*.manifest.json")):
        try:
            data = Manifest.read(path).data()
            if data["bank_id"] == bank.id:
                previous.append([i["id"] for i in data["items"]])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"Could not read exam history {path.name}: {exc}")
    return previous, errors


def read_corrections(path: str | Path) -> list[dict]:
    """Read complete JSON lines; ignore an unfinished final line after a crash."""
    path = Path(path)
    if not path.exists():
        return []
    raw = path.read_bytes()
    lines = raw.split(b"\n")[:-1]
    result = []
    for line in lines:
        entry = json.loads(line)
        if not isinstance(entry, dict):
            raise ValueError("invalid correction entry")  # noqa: TRY004
        result.append(entry)
    return result


def append_correction(path: str | Path, item_id: str, change: str, reason: str,
                      *, time: str | None = None) -> None:
    """Append one correction, discarding only a crash-truncated final line.

    A single writer must serialize calls to this journal.
    """
    if not all(isinstance(value, str) and value.strip() for value in (item_id, change, reason)):
        raise ValueError("item, change and reason are required")
    if time is None:
        time = datetime.now(timezone.utc).isoformat()
    if not isinstance(time, str) or not time.strip():
        raise ValueError("time is required")
    path = Path(path)
    # Refuse corruption in complete lines before touching the file.
    read_corrections(path)
    entry = json.dumps({"item_id": item_id, "change": change, "reason": reason, "time": time},
                       ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    with path.open("a+b") as stream:
        stream.seek(0)
        raw = stream.read()
        if raw and not raw.endswith(b"\n"):
            stream.truncate(raw.rfind(b"\n") + 1)
        stream.seek(0, os.SEEK_END)
        stream.write(entry)
        stream.flush()
        os.fsync(stream.fileno())
