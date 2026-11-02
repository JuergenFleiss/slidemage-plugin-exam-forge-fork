# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stored question types. Validation for a particular output belongs outside the model."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class Option:
    id: str
    text: str
    correct: bool = False
    rationale: str = ""


@dataclass(frozen=True)
class SourceLink:
    slide_id: str = ""
    version: str = ""
    language: str = ""
    view: str = ""
    fingerprint: str = ""
    text: str = ""  # Snapshot for comparing; not part of the fingerprint.


@dataclass(frozen=True)
class Origin:
    kind: Literal["author", "ai"] = "author"
    route: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ("author", "ai"):
            raise ValueError("invalid origin")
        if self.kind == "ai" and not self.route:
            raise ValueError("AI origin requires a route")
        if self.kind == "author" and (self.route or self.model):
            raise ValueError("author origin cannot have a route or model")


@dataclass(frozen=True)
class Media:
    """An immutable image in the bank's media folder."""

    name: str
    sha256: str
    alt: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.name):
            raise ValueError("media filename must be simple")
        if not re.fullmatch(r"[a-f0-9]{64}", self.sha256):
            raise ValueError("media needs a SHA-256 digest")


KINDS = {"choice": "Multiple choice", "open": "Open answer"}
# Item.use "elp" is the exam: the stored name stays, the shown one is "Exam".
USES = {"revision": "Revision quiz", "elp": "Exam"}


@dataclass(frozen=True)
class Item:
    id: str
    revision: int
    stem: str = ""
    options: tuple[Option, ...] = ()
    objective: str = ""
    topic: str = ""
    difficulty: str = ""
    status: Literal["draft", "reviewed", "retired"] = "draft"
    use: Literal["elp", "revision"] = "revision"
    sources: tuple[SourceLink, ...] = ()
    source_changed: bool = False  # Current item-level check, not revision content.
    origin: Origin = field(default_factory=Origin)
    media: tuple[Media, ...] = ()
    assistant_note: str = ""
    ai_edit_route: str = ""  # Per revision; non-empty once an AI assisted an edit.
    ai_edit_model: str = ""
    # An open-answer question has no options: its model answer and grading notes go only into the private key.
    kind: Literal["choice", "open"] = "choice"
    model_answer: str = ""
    grading_notes: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", tuple(self.options))
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "media", tuple(self.media))
        if len({entry.name for entry in self.media}) != len(self.media):
            raise ValueError("media filenames must be unique")
        if not self.id or type(self.revision) is not int or self.revision < 1:
            raise ValueError("item needs an id and a positive revision")
        if self.status not in ("draft", "reviewed", "retired"):
            raise ValueError("invalid status")
        if self.use not in ("elp", "revision"):
            raise ValueError("invalid use")
        if self.kind not in KINDS:
            raise ValueError("invalid question type")
        if len({option.id for option in self.options}) != len(self.options):
            raise ValueError("option IDs must be unique")


MAX_OPTIONS = 10
_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


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
ELP_ANSWERS = AnswerRule(5, 5, 1, 4)


@dataclass(frozen=True)
class AnswerRules:
    """A deck's answer rules: revision quizzes (Moodle, multiple choice) and exams, whose question types the deck
    chooses. Every multiple-choice exam question has the same number of answers."""

    revision: AnswerRule = REVISION_ANSWERS
    elp: AnswerRule = ELP_ANSWERS
    exam_kinds: tuple[str, ...] = ("choice",)

    def __post_init__(self) -> None:
        object.__setattr__(self, "exam_kinds", tuple(k for k in KINDS if k in self.exam_kinds))
        if not self.exam_kinds:
            raise ValueError("an exam needs at least one question type")
        if self.elp.min_options != self.elp.max_options:
            raise ValueError("every multiple-choice exam question has the same number of answers")

    def of(self, use: str) -> AnswerRule:
        return self.elp if use == "elp" else self.revision

    def kinds(self, use: str) -> tuple[str, ...]:
        return self.exam_kinds if use == "elp" else ("choice",)


DEFAULT_RULES = AnswerRules()


def number_word(n: int) -> str:
    return _WORDS[n] if 0 <= n < len(_WORDS) else str(n)


def span(low: int, high: int) -> str:
    """'exactly five' or 'one to four'."""
    return f"exactly {number_word(low)}" if low == high else f"{number_word(low)} to {number_word(high)}"


def describe(rule: AnswerRule) -> str:
    return f"{span(rule.min_options, rule.max_options)} answers, {span(rule.min_correct, rule.max_correct)} correct"


def answer_issues(item: Item, rule: AnswerRule) -> list[str]:
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


def kind_issues(item: Item, kinds: tuple[str, ...]) -> list[str]:
    """answer_issues for a question of any type: its type must be allowed; an open one needs a model answer."""
    if item.kind not in kinds:
        return [" or ".join("be multiple choice" if k == "choice" else "be an open-answer question" for k in kinds)]
    return []


def type_issues(item: Item, kinds: tuple[str, ...], rule: AnswerRule) -> list[str]:
    """What breaks a use's rules: its allowed types, then a model answer or the answer rule."""
    issues = kind_issues(item, kinds)
    if issues:
        return issues
    if item.kind == "open":
        return [] if item.model_answer.strip() else ["have a model answer"]
    return answer_issues(item, rule)


def question_issues(item: Item, rules: AnswerRules) -> list[str]:
    """What breaks the deck's rules for this question's use and type."""
    return type_issues(item, rules.kinds(item.use), rules.of(item.use))


def check_phase1(item: Item, rule: AnswerRule = REVISION_ANSWERS) -> list[str]:
    """Return revision-quiz answer errors; storing incomplete drafts is allowed."""
    return [f"A revision-quiz question must {issue}." for issue in type_issues(item, ("choice",), rule)]
