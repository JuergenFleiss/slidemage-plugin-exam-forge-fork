# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact ELP blueprint selection, swaps and the pre-approval source check.

G7 filters with taught.filter_items first, shows both exclusion lists, and asks
the lecturer to approve. check_approval always reruns D10; its result requires
explicit acknowledgement of changed sources. G5 separately checks on freeze.
"""
from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from math import isfinite
from typing import Literal

from .bank import Bank, ConflictError
from .elp import ElpRules, check_elp
from .model import Item
from .sources import BankCheck, ItemCheck, check_bank
from .taught import ExcludedItem

_DEFAULT_RULES = ElpRules()


@dataclass(frozen=True)
class Blueprint:
    """Counts and difficulty mix are exact item counts; time fills the budget."""

    counts: Mapping[str, int]
    difficulty_mix: Mapping[str, int]
    minutes: float
    by: Literal["topic", "objective"] = "topic"
    avoid_last: int = 0

    def __post_init__(self):
        if self.by not in ("topic", "objective"):
            raise ValueError("Blueprint groups by topic or objective")
        for counts in (self.counts, self.difficulty_mix):
            if any(not isinstance(k, str) or not k or type(v) is not int or v < 0
                   for k, v in counts.items()):
                raise ValueError("Blueprint counts need names and nonnegative integers")
        if set(self.difficulty_mix) - {"easy", "hard"}:
            raise ValueError("Difficulty mix uses easy and hard")
        if (isinstance(self.minutes, bool) or not isinstance(self.minutes, (int, float))
                or not isfinite(self.minutes) or self.minutes <= 0):
            raise ValueError("Time budget must be positive and finite")
        if type(self.avoid_last) is not int or self.avoid_last < 0:
            raise ValueError("avoid_last must be a nonnegative integer")
        object.__setattr__(self, "counts", dict(self.counts))
        object.__setattr__(self, "difficulty_mix", dict(self.difficulty_mix))


@dataclass(frozen=True)
class Assembly:
    blueprint: Blueprint
    rules: ElpRules
    seed: int
    items: tuple[Item, ...]
    candidates: tuple[Item, ...]
    excluded: tuple[ExcludedItem, ...]
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


def _selection_problems(items: Sequence[Item], blueprint: Blueprint, rules: ElpRules) -> list[str]:
    problems = []
    groups = Counter(getattr(i, blueprint.by) for i in items)
    if groups != Counter(blueprint.counts):
        problems.append(f"Selection does not meet the {blueprint.by} counts.")
    mix = Counter(i.difficulty for i in items)
    if mix != Counter(blueprint.difficulty_mix):
        problems.append("Selection does not meet the difficulty mix.")
    minutes = sum((Decimal(str(getattr(rules, i.difficulty).minutes)) for i in items), Decimal(0))
    if minutes != Decimal(str(blueprint.minutes)):
        problems.append(f"Planned time is {minutes} minutes; the budget requires {blueprint.minutes}.")
    return problems


def assemble(items: Sequence[Item], blueprint: Blueprint, *, seed: int,
             rules: ElpRules = _DEFAULT_RULES,
             previous_exams: Sequence[Sequence[str]] = ()) -> Assembly:
    """Select reviewed ELP items, with no relaxed constraints or partial result.

    Pass current revisions after the taught filter. History is oldest first and
    contains stable item IDs: reuse avoidance covers all revisions. Input order
    does not affect a seeded selection. source_changed remains a separate flag.
    """
    if type(seed) is not int:
        raise ValueError("Seed must be an integer")
    if len({i.id for i in items}) != len(items):
        raise ValueError("Supply one current revision per item ID")
    recent = {id_ for exam in previous_exams[-blueprint.avoid_last:] for id_ in exam} if blueprint.avoid_last else set()
    candidates, excluded = [], []
    for item in sorted(items, key=lambda i: i.id):
        reasons = []
        if item.status != "reviewed":
            reasons.append("Item is not reviewed.")
        if item.use != "elp":
            reasons.append("Item is not an ELP question.")
        reasons.extend(check_elp(item))
        if item.difficulty not in ("easy", "hard"):
            reasons.append("Item has no supported difficulty class.")
        if not blueprint.counts.get(getattr(item, blueprint.by), 0):
            reasons.append(f"Item is outside the requested {blueprint.by} counts.")
        if item.id in recent:
            reasons.append(f"Item was used in the last {blueprint.avoid_last} exams.")
        if reasons:
            excluded.append(ExcludedItem(item, tuple(reasons)))
        else:
            candidates.append(item)

    result = Assembly(blueprint, rules, seed, (), tuple(candidates), tuple(excluded))
    problems = []
    if not sum(blueprint.counts.values()):
        problems.append("The blueprint requests no items.")
    if sum(blueprint.counts.values()) != sum(blueprint.difficulty_mix.values()):
        problems.append("Topic/objective counts and difficulty counts have different totals.")
    planned = sum((Decimal(str(getattr(rules, d).minutes)) * n
                   for d, n in blueprint.difficulty_mix.items()), Decimal(0))
    if planned != Decimal(str(blueprint.minutes)):
        problems.append(f"Difficulty mix plans {planned} minutes; the budget requires {blueprint.minutes}.")

    # With two difficulty classes each group's feasible easy count is an
    # interval. Respect their joint bounds before drawing any random samples.
    groups = []
    for name, count in sorted(blueprint.counts.items()):
        easy = [i for i in candidates if getattr(i, blueprint.by) == name and i.difficulty == "easy"]
        hard = [i for i in candidates if getattr(i, blueprint.by) == name and i.difficulty == "hard"]
        low, high = max(0, count - len(hard)), min(count, len(easy))
        if low > high:
            problems.append(f"{blueprint.by.title()} {name!r} needs {count} items; only {len(easy) + len(hard)} are eligible.")
        groups.append((count, easy, hard, low, high))
    remaining = blueprint.difficulty_mix.get("easy", 0)
    if not sum(g[3] for g in groups) <= remaining <= sum(g[4] for g in groups):
        problems.append("Eligible items cannot satisfy the difficulty mix within the topic/objective counts.")
    if problems:
        return replace(result, problems=tuple(problems))
    rng, chosen = random.Random(seed), []
    for index, (count, easy, hard, low, high) in enumerate(groups):
        rest = groups[index + 1:]
        lower = max(low, remaining - sum(g[4] for g in rest))
        upper = min(high, remaining - sum(g[3] for g in rest))
        take = rng.randint(lower, upper)
        chosen.extend(rng.sample(easy, take))
        chosen.extend(rng.sample(hard, count - take))
        remaining -= take
    return replace(result, items=tuple(sorted(chosen, key=lambda i: i.id)))


def swap(selection: Assembly, outgoing_id: str, incoming_id: str) -> Assembly:
    """Swap only within the eligible pool and preserve every blueprint constraint."""
    if not selection.feasible:
        raise ValueError("Cannot swap an infeasible selection")
    selected = {i.id for i in selection.items}
    pool = {i.id: i for i in selection.candidates}
    if outgoing_id not in selected or incoming_id in selected or incoming_id not in pool:
        raise ValueError("Swap needs a selected item and an unselected eligible replacement")
    items = tuple(pool[incoming_id] if i.id == outgoing_id else i for i in selection.items)
    problems = _selection_problems(items, selection.blueprint, selection.rules)
    if problems:
        raise ValueError(" ".join(problems))
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
