# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""A deck's answer rules, the checks that use them, and the exam's points, minutes and scoring sheet. Student
responses are not scored here."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from .model import KINDS, Item

MAX_OPTIONS = 10
_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
# Points and planned minutes of an exam question, by difficulty. Points are multiples of 12, so ticks score whole.
POINTS = {"easy": 12, "hard": 24}
MINUTES = {"easy": 2.5, "hard": 5}
SCORING_FLOOR = 0


@dataclass(frozen=True)
class AnswerRule:
    """How many answers a question of one use has, and how many of them are correct. One answer is always wrong."""

    min_options: int
    max_options: int
    min_correct: int
    max_correct: int

    def __post_init__(self) -> None:
        if any(type(value) is not int for value in
               (self.min_options, self.max_options, self.min_correct, self.max_correct)):
            raise ValueError("answer counts must be whole numbers")
        if not 2 <= self.min_options <= self.max_options <= MAX_OPTIONS:
            raise ValueError(f"answers must be between 2 and {MAX_OPTIONS}, the smaller number first")
        if not 1 <= self.min_correct <= self.max_correct:
            raise ValueError("at least one answer must be correct, the smaller number first")
        if self.max_correct >= self.max_options:
            raise ValueError("at least one answer must be wrong")


REVISION_ANSWERS = AnswerRule(2, 8, 1, 1)
EXAM_ANSWERS = AnswerRule(5, 5, 1, 4)


@dataclass(frozen=True)
class ExamRules:
    """The exam's answer rule and question types. Refuses an answer rule that does not split the points evenly."""

    answers: AnswerRule = EXAM_ANSWERS
    kinds: tuple[str, ...] = ("choice",)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kinds", tuple(self.kinds))
        if "choice" not in self.kinds:
            return      # an open answer is graded by hand: no ticks to split the points into
        # Every tick is worth whole points: P/n for a correct one, P/(answers - n) for a wrong one.
        total = self.answers.max_options
        for difficulty, points in POINTS.items():
            for n in range(self.answers.min_correct, self.answers.max_correct + 1):
                if points % n or points % (total - n):
                    raise ValueError(f"With {total} answers and {n} correct, the {points} points of "
                                     f"{'an' if difficulty == 'easy' else 'a'} {difficulty} question cannot be split "
                                     "into whole points per answer.")


@dataclass(frozen=True)
class AnswerRules:
    """A deck's answer rules: revision quizzes (Moodle, multiple choice) and exams, whose question types the deck
    chooses. Every multiple-choice exam question has the same number of answers."""

    revision: AnswerRule = REVISION_ANSWERS
    exam: AnswerRule = EXAM_ANSWERS
    exam_kinds: tuple[str, ...] = ("choice",)

    def __post_init__(self) -> None:
        object.__setattr__(self, "exam_kinds", tuple(k for k in KINDS if k in self.exam_kinds))
        if not self.exam_kinds:
            raise ValueError("an exam needs at least one question type")
        if self.exam.min_options != self.exam.max_options:
            raise ValueError("every multiple-choice exam question has the same number of answers")

    def of(self, use: str) -> AnswerRule:
        return self.exam if use == "exam" else self.revision

    def kinds(self, use: str) -> tuple[str, ...]:
        return self.exam_kinds if use == "exam" else ("choice",)

    def exam_rules(self) -> ExamRules:
        return ExamRules(self.exam, self.exam_kinds)


def exam_totals(difficulties: Iterable[str], points: Mapping[str, int] = POINTS,
                minutes: Mapping[str, float] = MINUTES) -> tuple[int, float]:
    """Total points and planned minutes; an issued exam passes the values frozen in it."""
    difficulties = list(difficulties)
    return (sum(points[d] for d in difficulties),
            float(sum((Decimal(str(minutes[d])) for d in difficulties), Decimal(0))))


@dataclass(frozen=True)
class ScoringRow:
    correct_options: int
    correct_tick: int
    wrong_tick: int


def scoring_table(points: int, answers: AnswerRule) -> tuple[ScoringRow, ...]:
    """Return the per-option values for a scoring sheet, with a zero floor: one row per allowed number of correct
    answers."""
    if type(points) is not int or points <= 0 or points % 12:
        raise ValueError("points must be a positive multiple of 12")
    total = answers.max_options
    return tuple(ScoringRow(n, points // n, -(points // (total - n)))
                 for n in range(answers.min_correct, answers.max_correct + 1))


def number_word(n: int) -> str:
    return _WORDS[n] if 0 <= n < len(_WORDS) else str(n)


def span(low: int, high: int) -> str:
    """'exactly five' or 'one to four'."""
    return f"exactly {number_word(low)}" if low == high else f"{number_word(low)} to {number_word(high)}"


def describe(rule: AnswerRule) -> str:
    return f"{span(rule.min_options, rule.max_options)} answers, {span(rule.min_correct, rule.max_correct)} correct"


def _answer_issues(item: Item, rule: AnswerRule) -> list[str]:
    """What breaks the rule, in words that follow 'must' or a list of things to do."""
    count, correct = len(item.options), sum(option.correct for option in item.options)
    issues = []
    if not rule.min_options <= count <= rule.max_options:
        issues.append(f"have {span(rule.min_options, rule.max_options)} answers")
    if not rule.min_correct <= correct <= rule.max_correct:
        plural = "" if rule.max_correct == 1 else "s"
        issues.append(f"mark {span(rule.min_correct, rule.max_correct)} correct answer{plural}")
    elif correct >= count:
        issues.append("leave at least one answer wrong")
    return issues


def _type_issues(item: Item, kinds: tuple[str, ...], rule: AnswerRule) -> list[str]:
    """What breaks a use's rules: its allowed types, then a model answer or the answer rule."""
    if item.kind not in kinds:
        return [" or ".join("be multiple choice" if k == "choice" else "be an open-answer question" for k in kinds)]
    if item.kind == "open":
        return [] if item.model_answer.strip() else ["have a model answer"]
    return _answer_issues(item, rule)


def question_issues(item: Item, rules: AnswerRules) -> list[str]:
    """What breaks the deck's rules for this question's use and type."""
    return _type_issues(item, rules.kinds(item.use), rules.of(item.use))


def exam_issues(item: Item, rules: ExamRules) -> list[str]:
    """Why a question cannot go into an exam with these rules, as sentences; drafts may still be saved."""
    return [f"An exam question must {issue}." for issue in _type_issues(item, rules.kinds, rules.answers)]
