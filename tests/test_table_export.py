# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
import csv
import io

from exam_forge.model import Item, Media, Option, Origin, SourceLink
from exam_forge.table_export import questions_csv
from exam_forge.topics import add_topic, assign_slide


def rows(data):
    assert data.startswith("﻿".encode())
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


def item(item_id, stem, slides=(), n=2, **fields):
    options = tuple(Option(f"{item_id}{k}", f"Answer {k} of {item_id}", k == 0, f"Why {k}") for k in range(n))
    return Item(item_id, 1, stem, options, sources=tuple(SourceLink(slide_id=s) for s in slides), **fields)


def test_one_row_per_question_in_deck_order_with_answers_side_by_side():
    topics = add_topic((), "Probability")
    topics = assign_slide(topics, topics[0].id, "b")
    items = [item("q1", "Nowhere"), item("q2", "On c and b", ["c", "b"], 3, difficulty="hard", objective="Bayes"),
             item("q3", "On b", ["b"], use="elp", status="reviewed", assistant_note="Fixed order"),
             item("q4", "Retired on a", ["a"], status="retired"),
             item("q5", "AI on gone", ["gone"], origin=Origin("ai", "r", "m"), source_changed=True,
                  media=(Media("i.png", "0" * 64, "An image"),))]
    table = rows(questions_csv(items, topics, {"a": (1, "Title a"), "b": (2, "Title b"), "c": (3, "Title c")}))
    head, body = table[0], {row[0]: dict(zip(table[0], row)) for row in table[1:]}
    assert head[-4:] == ["Note for teaching assistants", "Images", "Origin", "Slide changed"]
    assert "Answer 3 rationale" in head and "Answer 4" not in head
    assert [row[0] for row in table[1:]] == ["q4", "q3", "q2", "q5", "q1"]
    assert body["q2"]["Slides"] == "Slide 3 · Title c; Slide 2 · Title b" and body["q2"]["Topic"] == ""
    assert body["q3"]["Topic"] == "Probability" and body["q3"]["Used for"] == "ELP exam"
    assert body["q2"]["Answer 1 correct"] == "yes" and body["q2"]["Answer 3"] == "Answer 2 of q2"
    assert body["q2"]["Answer 2 rationale"] == "Why 1" and body["q3"]["Answer 3"] == ""
    assert body["q2"]["Difficulty"] == "Hard" and body["q2"]["Learning objective"] == "Bayes"
    assert body["q3"]["Note for teaching assistants"] == "Fixed order" and body["q3"]["Status"] == "Reviewed"
    assert body["q5"]["Slides"] == "Slide gone" and body["q5"]["Origin"] == "AI"
    assert body["q5"]["Images"] == "1" and body["q5"]["Slide changed"] == "yes"


def test_formulas_are_not_run_and_text_survives():
    table = rows(questions_csv([item("q", '=HYPERLINK("x")\nsecond line, "quoted" \\( \\frac{1}{2} \\) Größe')]))
    assert table[1][8] == '\'=HYPERLINK("x")\nsecond line, "quoted" \\( \\frac{1}{2} \\) Größe'


def test_an_empty_bank_has_only_the_header():
    table = rows(questions_csv([]))
    assert len(table) == 1 and "Answer 1" not in table[0]
