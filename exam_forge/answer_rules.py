# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""A deck's answer rules, in answer-rules.json in its bank folder; without the file, the defaults apply."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .bank import CorruptFileError, SchemaError, _atomic_write
from .elp import ElpRules
from .model import KINDS, AnswerRule, AnswerRules

RULES_SCHEMA_VERSION = 1


def exam_rules(rules: AnswerRules) -> ElpRules:
    """The exam's points, minutes and answer rules; refuses answer rules that do not split the points evenly."""
    return ElpRules(answers=rules.elp, kinds=rules.exam_kinds)


class AnswerRulesStore:
    def __init__(self, folder):
        self.path = Path(folder) / "answer-rules.json"

    def load(self) -> AnswerRules:
        if not self.path.exists():
            return AnswerRules()
        try:
            with self.path.open(encoding="utf-8") as stream:
                data = json.load(stream)
            if not isinstance(data, dict) or type(data.get("schema_version")) is not int:
                raise ValueError("missing schema version")
            if data["schema_version"] > RULES_SCHEMA_VERSION:
                raise SchemaError(f"{self.path}: newer schema {data['schema_version']}")
            if data["schema_version"] != RULES_SCHEMA_VERSION:
                raise ValueError("unsupported schema version")
            kinds = data["exam_kinds"]
            if type(kinds) is not list or not set(kinds) <= set(KINDS):
                raise ValueError("invalid exam question types")
            rules = AnswerRules(**{use: AnswerRule(**data[use]) for use in ("revision", "elp")},
                                exam_kinds=tuple(kinds))
            exam_rules(rules)
            return rules
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise CorruptFileError(f"{self.path}: {exc}") from exc

    def save(self, rules: AnswerRules) -> None:
        exam_rules(rules)
        _atomic_write(self.path, {"schema_version": RULES_SCHEMA_VERSION, **asdict(rules)})
