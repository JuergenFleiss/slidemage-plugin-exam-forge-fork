# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact exam blueprint selection, swaps and the pre-approval source check.

Approval and freeze each recheck sources.
"""
from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from .bank import Bank, ConflictError
from .elp import ElpRules, check_elp
from .model import Item
from .sources import BankCheck, ItemCheck, check_bank

_DEFAULT_RULES = ElpRules()


@dataclass(frozen=True)
class ExcludedItem:
    item: Item
    reasons: tuple[str, ...]


_DIFFICULTIES = ("easy", "hard")
# A blueprint cell: a difficulty for multiple choice, as before open answers; "open easy" and "open hard" otherwise.
CELLS = ("easy", "hard", "open easy", "open hard")
CELL_NAMES = {"easy": "easy", "hard": "hard", "open easy": "easy open-answer", "open hard": "hard open-answer"}


def cell_of(item: Item) -> str:
    return item.difficulty if item.kind == "choice" else f"open {item.difficulty}"
_NO_TOPIC = "Question has no topic; link it to a slide in a topic."


@dataclass(frozen=True)
class Blueprint:
    """Exact counts per topic and cell (CELLS); minutes follow from ElpRules. Open-answer cells are kept only when
    given, so a multiple-choice blueprint reads as before."""

    counts: Mapping[str, Mapping[str, int]]
    avoid_last: int = 0

    def __post_init__(self):
        counts = {}
        for name, cell in self.counts.items():
            if (not isinstance(name, str) or not name or set(cell) - set(CELLS)
                    or any(type(v) is not int or v < 0 for v in cell.values())):
                raise ValueError("Blueprint counts need topic names and nonnegative easy/hard integers")
            counts[name] = {d: cell.get(d, 0) for d in CELLS if d in _DIFFICULTIES or d in cell}
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
    rules: ElpRules
    seed: int
    items: tuple[Item, ...]
    candidates: tuple[Item, ...]
    excluded: tuple[ExcludedItem, ...]
    topic_of: Callable[[Item], str]
    problems: tuple[str, ...] = ()

    @property
    def feasible(self) -> bool:
        return not self.problems

    @property
    def total_minutes(self) -> float:
        return float(sum((Decimal(str(getattr(self.rules, i.difficulty).minutes))
                          for i in self.items), Decimal(0)))

    @property
    def total_points(self) -> int:
        return sum(getattr(self.rules, i.difficulty).points for i in self.items)

    def alternatives(self, item_id: str) -> tuple[Item, ...]:
        """Unselected candidates in the same topic and cell as a selected item."""
        current = next((i for i in self.items if i.id == item_id), None)
        if current is None:
            return ()
        selected = {i.id for i in self.items}
        cell = (self.topic_of(current), cell_of(current))
        return tuple(c for c in self.candidates if c.id not in selected
                     and (self.topic_of(c), cell_of(c)) == cell)


def _screen(items: Sequence[Item], topic_of: Callable[[Item], str], previous_exams,
            avoid_last: int, wanted: Callable[[str, str], bool] | None = None, rules: ElpRules = _DEFAULT_RULES):
    """Split items into eligible candidates and explained exclusions, ordered by ID."""
    if len({i.id for i in items}) != len(items):
        raise ValueError("Supply one current revision per item ID")
    recent = {id_ for exam in previous_exams[-avoid_last:] for id_ in exam} if avoid_last else set()
    candidates, excluded = [], []
    for item in sorted(items, key=lambda i: i.id):
        topic, reasons = topic_of(item), []
        if item.status != "reviewed":
            reasons.append("Item is not reviewed.")
        if item.use != "elp":
            reasons.append("Item is not an exam question.")
        reasons.extend(check_elp(item, rules.answers, rules.kinds))
        if item.difficulty not in _DIFFICULTIES:
            reasons.append("Item has no supported difficulty class.")
        if not topic:
            reasons.append(_NO_TOPIC)
        elif wanted and item.difficulty in _DIFFICULTIES and not wanted(topic, cell_of(item)):
            reasons.append("Item is outside the requested topic counts.")
        if item.id in recent:
            reasons.append(f"Item was used in the last {avoid_last} exams.")
        if reasons:
            excluded.append(ExcludedItem(item, tuple(reasons)))
        else:
            candidates.append(item)
    return candidates, excluded


def available(items: Sequence[Item], topic_of: Callable[[Item], str], previous_exams=(),
              avoid_last: int = 0, rules: ElpRules = _DEFAULT_RULES) -> dict[str, dict[str, int]]:
    """Eligible counts per topic and cell, as assemble would see them; open-answer cells only when there are some."""
    result: dict[str, dict[str, int]] = {}
    for item in _screen(items, topic_of, previous_exams, avoid_last, rules=rules)[0]:
        cells = result.setdefault(topic_of(item), dict.fromkeys(_DIFFICULTIES, 0))
        cells[cell_of(item)] = cells.get(cell_of(item), 0) + 1
    return result


def waiting_elp_drafts(items: Sequence[Item], topic_of: Callable[[Item], str]) -> dict[str, int]:
    """Exam questions still in draft, counted per topic."""
    return dict(Counter(t for i in items if i.use == "elp" and i.status == "draft"
                        if (t := topic_of(i))))


def assemble(items: Sequence[Item], blueprint: Blueprint, *, seed: int,
             topic_of: Callable[[Item], str], rules: ElpRules = _DEFAULT_RULES,
             previous_exams: Sequence[Sequence[str]] = ()) -> Assembly:
    """Select reviewed exam questions, with no relaxed constraints or partial result.

    Pass current revisions. History is oldest first and
    contains stable item IDs: reuse avoidance covers all revisions. Input order
    does not affect a seeded selection. source_changed remains a separate flag.
    Each (topic, cell) is filled exactly from the items topic_of and cell_of put there.
    """
    if type(seed) is not int:
        raise ValueError("Seed must be an integer")
    candidates, excluded = _screen(items, topic_of, previous_exams, blueprint.avoid_last,
                                   lambda t, d: blueprint.counts.get(t, {}).get(d, 0) > 0, rules)
    result = Assembly(blueprint, rules, seed, (), tuple(candidates), tuple(excluded), topic_of)
    problems = [] if blueprint.total else ["The blueprint requests no items."]
    cells = []
    for name, cell in sorted(blueprint.counts.items()):
        for key in CELLS:
            n = cell.get(key, 0)
            pool = [i for i in candidates if topic_of(i) == name and cell_of(i) == key]
            if n > len(pool):
                have = len(pool)
                problems.append(f"Topic {name!r} needs {n} {CELL_NAMES[key]} question{'s' * (n != 1)}; "
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


@dataclass(frozen=True)
class ApprovalCheck:
    selection: Assembly
    sources: BankCheck

    @property
    def changed(self) -> tuple[ItemCheck, ...]:
        return tuple(row for row in self.sources.items if row.source_changed)

    def approve(self, *, accept_changed: bool = False) -> Assembly:
        """Call after the lecturer approves and sees the changed-question list."""
        if self.changed and not accept_changed:
            raise ValueError("Acknowledge changed sources before approving: " +
                             ", ".join(row.item.id for row in self.changed))
        return self.selection


def check_approval(host, deck, bank: Bank, selection: Assembly,
                   done: Callable[[ApprovalCheck | None, BaseException | None], None]) -> None:
    """D10: always check afresh, report changes, reject stale selected revisions.

    Uses sources.check_bank's worker and callback convention. A failed check
    produces no approval object. No status transition or manifest is written.
    """
    if not selection.feasible:
        raise ValueError("Cannot approve an infeasible selection")

    def checked(report, error):
        if error is not None:
            done(None, error)
            return
        current = {row.item.id: row.item for row in report.items}
        if report.errors or set(current) != {i.id for i in selection.items}:
            done(None, ConflictError("Not all selected questions could be checked"))
            return
        if any(current[i.id] != replace(i, source_changed=current[i.id].source_changed)
               for i in selection.items):
            done(None, ConflictError("Selected questions changed; assemble or swap again"))
            return
        updated = replace(selection, items=tuple(current[i.id] for i in selection.items))
        done(ApprovalCheck(updated, report), None)

    check_bank(host, deck, bank, checked, item_ids=[i.id for i in selection.items])
