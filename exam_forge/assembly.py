# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact exam blueprint selection and swaps; freeze checks the sources again before issuing."""
from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from .model import Item, plural
from .rules import ExamRules, exam_issues, exam_totals

_DIFFICULTIES = ("easy", "hard")
# A blueprint cell: a difficulty for multiple choice; "open easy" and "open hard" for open answers.
CELLS = ("easy", "hard", "open easy", "open hard")
CELL_NAMES = {"easy": "easy", "hard": "hard", "open easy": "easy open-answer", "open hard": "hard open-answer"}


def cell_of(item: Item) -> str:
    return item.difficulty if item.kind == "choice" else f"open {item.difficulty}"


@dataclass(frozen=True)
class Blueprint:
    """Exact counts per topic and cell (CELLS); points and minutes follow from the difficulties."""

    counts: Mapping[str, Mapping[str, int]]
    avoid_last: int = 0

    def __post_init__(self):
        counts = {}
        for name, cell in self.counts.items():
            if (not isinstance(name, str) or not name or set(cell) - set(CELLS)
                    or any(type(v) is not int or v < 0 for v in cell.values())):
                raise ValueError("Blueprint counts need topic names and nonnegative easy/hard integers")
            counts[name] = {d: cell.get(d, 0) for d in CELLS}
        if type(self.avoid_last) is not int or self.avoid_last < 0:
            raise ValueError("avoid_last must be a nonnegative integer")
        object.__setattr__(self, "counts", counts)

    def as_dict(self) -> dict:
        return {"counts": {t: dict(c) for t, c in self.counts.items()}, "avoid_last": self.avoid_last}

    @property
    def total(self) -> int:
        return sum(sum(c.values()) for c in self.counts.values())


@dataclass(frozen=True)
class Assembly:
    blueprint: Blueprint
    rules: ExamRules
    seed: int
    items: tuple[Item, ...]
    candidates: tuple[Item, ...]
    topic_of: Callable[[Item], str]
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
        selected = {i.id for i in self.items}
        cell = (self.topic_of(current), cell_of(current))
        return tuple(c for c in self.candidates if c.id not in selected
                     and (self.topic_of(c), cell_of(c)) == cell)


def _screen(items: Sequence[Item], topic_of: Callable[[Item], str], previous_exams, avoid_last: int,
            rules: ExamRules) -> list[Item]:
    """The eligible questions, ordered by ID: reviewed exam questions that fit the rules, have a topic and a
    difficulty, and were not used in the last `avoid_last` exams."""
    if len({i.id for i in items}) != len(items):
        raise ValueError("Supply one current revision per item ID")
    recent = {id_ for exam in previous_exams[-avoid_last:] for id_ in exam} if avoid_last else set()
    return [item for item in sorted(items, key=lambda i: i.id)
            if item.status == "reviewed" and item.use == "elp" and not exam_issues(item, rules)
            and item.difficulty in _DIFFICULTIES and topic_of(item) and item.id not in recent]


def available(items: Sequence[Item], topic_of: Callable[[Item], str], rules: ExamRules, previous_exams=(),
              avoid_last: int = 0) -> dict[str, dict[str, int]]:
    """Eligible counts per topic and cell, as assemble would see them."""
    result: dict[str, dict[str, int]] = {}
    for item in _screen(items, topic_of, previous_exams, avoid_last, rules):
        cells = result.setdefault(topic_of(item), dict.fromkeys(CELLS, 0))
        cells[cell_of(item)] += 1
    return result


def waiting_elp_drafts(items: Sequence[Item], topic_of: Callable[[Item], str]) -> dict[str, int]:
    """Exam questions still in draft, counted per topic."""
    return dict(Counter(t for i in items if i.use == "elp" and i.status == "draft"
                        if (t := topic_of(i))))


def assemble(items: Sequence[Item], blueprint: Blueprint, *, seed: int,
             topic_of: Callable[[Item], str], rules: ExamRules,
             previous_exams: Sequence[Sequence[str]] = ()) -> Assembly:
    """Select reviewed exam questions, with no relaxed constraints or partial result.

    Pass current revisions. History is oldest first and
    contains stable item IDs: reuse avoidance covers all revisions. Input order
    does not affect a seeded selection. source_changed remains a separate flag.
    Each (topic, cell) is filled exactly from the items topic_of and cell_of put there.
    """
    if type(seed) is not int:
        raise ValueError("Seed must be an integer")
    candidates = [item for item in _screen(items, topic_of, previous_exams, blueprint.avoid_last, rules)
                  if blueprint.counts.get(topic_of(item), {}).get(cell_of(item), 0) > 0]
    result = Assembly(blueprint, rules, seed, (), tuple(candidates), topic_of)
    problems = [] if blueprint.total else ["The blueprint requests no items."]
    cells = []
    for name, cell in sorted(blueprint.counts.items()):
        for key in CELLS:
            n = cell.get(key, 0)
            pool = [i for i in candidates if topic_of(i) == name and cell_of(i) == key]
            if n > len(pool):
                have = len(pool)
                problems.append(f"Topic {name!r} needs {plural(n, CELL_NAMES[key] + ' question')}; "
                                f"only {have} {'is' if have == 1 else 'are'} eligible.")
            if n:
                cells.append((n, pool))
    if problems:
        return replace(result, problems=tuple(problems))
    rng = random.Random(seed)
    chosen = [i for n, pool in cells for i in rng.sample(pool, n)]
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

