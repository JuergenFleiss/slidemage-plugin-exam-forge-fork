# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Open-answer exam questions: storage, rules, exam proposals, documents, AI drafting, editor and Exam tab."""
import json
from dataclasses import replace

import pytest
from slidemage.plugin_api import AiChatResult, Route

from exam_forge.answer_rules import AnswerRulesStore
from exam_forge.assembly import Blueprint, assemble, available
from exam_forge.bank import Bank
from exam_forge.drafting import (
    DraftReplyError,
    DraftSlide,
    build_request,
    build_update_request,
    parse_reply,
)
from exam_forge.elp import ElpRules, check_elp
from exam_forge.elp_document import export_elp_documents
from exam_forge.manifest import freeze
from exam_forge.model import (
    AnswerRules,
    Item,
    Option,
    SourceLink,
    check_phase1,
    question_issues,
)
from exam_forge.table_export import questions_csv

BOTH = AnswerRules(exam_kinds=("choice", "open"))
OPEN = AnswerRules(exam_kinds=("open",))


def open_item(item_id="o", difficulty="easy", **fields):
    return Item(item_id, 1, "Explain Bayes' theorem.", use="elp", kind="open", difficulty=difficulty,
                model_answer=fields.pop("model_answer", "P(A|B) = P(B|A) P(A) / P(B)"), grading_notes="2 points for",
                **fields)


def choice_item(item_id="c", difficulty="easy"):
    return Item(item_id, 1, "Pick", tuple(Option(str(i), f"O{i}", i == 0, "Why") for i in range(5)),
                use="elp", difficulty=difficulty)


def test_files_stay_version_2_unless_they_hold_an_open_question(tmp_path):
    bank = Bank(tmp_path / "bank", create=True)
    bank.create(choice_item())
    bank.create(open_item())
    stored = {p.stem: json.loads(p.read_text()) for p in bank.files() if p.suffix == ".json"}
    assert stored["bank"]["schema_version"] == 2
    choice = next(d for d in stored.values() if d.get("revisions", [{}])[0].get("id") == "c")
    opened = next(d for d in stored.values() if d.get("revisions", [{}])[0].get("id") == "o")
    assert choice["schema_version"] == 2 and "kind" not in choice["revisions"][0]
    assert opened["schema_version"] == 3 and opened["revisions"][0]["kind"] == "open"
    assert Bank(bank.folder).get("o") == open_item()


def test_rules_for_each_use_and_type():
    assert question_issues(open_item(), AnswerRules()) == ["be multiple choice"]
    assert question_issues(open_item(), OPEN) == [] and question_issues(open_item(), BOTH) == []
    assert question_issues(open_item(model_answer=" "), OPEN) == ["have a model answer"]
    assert question_issues(choice_item(), OPEN) == ["be an open-answer question"]
    assert question_issues(replace(open_item(), use="revision"), BOTH) == ["be multiple choice"]
    assert check_phase1(replace(open_item(), use="revision")) == ["A revision-quiz question must be multiple choice."]
    assert check_elp(open_item(), kinds=("open",)) == []
    with pytest.raises(ValueError):
        AnswerRules(exam_kinds=())
    with pytest.raises(ValueError):
        Item("x", 1, kind="essay")


def test_store_keeps_the_exam_types(tmp_path):
    store = AnswerRulesStore(tmp_path)
    store.save(BOTH)
    assert store.load() == BOTH and json.loads(store.path.read_text())["exam_kinds"] == ["choice", "open"]


def test_proposals_count_each_type_separately():
    items = [replace(choice_item(f"c{i}"), status="reviewed", topic="t") for i in range(3)]
    items += [replace(open_item(f"o{i}", "hard"), status="reviewed", topic="t") for i in range(2)]
    of = lambda item: "t"
    rules = ElpRules(kinds=("choice", "open"))
    assert available(items, of, rules=rules) == {"t": {"easy": 3, "hard": 0, "open hard": 2}}
    assert available(items, of)["t"] == {"easy": 3, "hard": 0}   # multiple choice only: open ones excluded
    picked = assemble(items, Blueprint({"t": {"easy": 2, "open hard": 1}}), seed=1, topic_of=of, rules=rules)
    assert sorted(i.kind for i in picked.items) == ["choice", "choice", "open"]
    assert [i.id for i in picked.alternatives(next(i.id for i in picked.items if i.kind == "open"))] in (["o0"], ["o1"])
    short = assemble(items, Blueprint({"t": {"open easy": 1}}), seed=1, topic_of=of, rules=rules)
    assert short.problems == ("Topic 't' needs 1 easy open-answer question; only 0 are eligible.",)
    assert Blueprint({"t": {"easy": 1}}).as_dict()["counts"] == {"t": {"easy": 1, "hard": 0}}


def test_documents_leave_room_and_the_key_holds_the_model_answer(tmp_path):
    bank = Bank(tmp_path / "bank", create=True)
    for item in (choice_item(), open_item(difficulty="hard")):
        bank.create(item)
        bank.set_status(item.id, 1, "reviewed")
    manifest = freeze(None, None, bank, [("c", 2), ("o", 2)], {}, ElpRules(kinds=("choice", "open")),
                      topic_of=lambda i: "t")
    assert manifest.data()["schema_version"] == 3
    exam, key = export_elp_documents(manifest)
    assert exam.count('class="answer-space"') == 1 and "P(A|B)" not in exam
    assert "P(A|B) = P(B|A) P(A) / P(B)" in key and "2 points for" in key and "graded by hand" in key
    assert "Frozen scoring tables" in key and "36 points" in exam
    only_open = freeze(None, None, bank, [("o", 2)], {}, ElpRules(kinds=("open",)), topic_of=lambda i: "t")
    assert "Frozen scoring tables" not in export_elp_documents(only_open)[1]
    with pytest.raises(ValueError, match="be multiple choice"):
        freeze(None, None, bank, [("o", 2)], {}, ElpRules(), topic_of=lambda i: "t")


def test_ai_drafts_and_updates_open_questions():
    slide = DraftSlide(SourceLink("s", "1", "en", "instructor", "h"), type("S", (), {"text": "Slide text"})())
    request = build_request((slide,), use="elp", rules=OPEN, kind="open")
    assert '"model_answer"' in request.instructions and '"options"' not in request.instructions
    with pytest.raises(ValueError):
        build_request((slide,), use="elp", kind="open")          # the deck's exam is multiple choice
    row = {"slide": 1, "stem": "Explain", "model_answer": "Because", "grading_notes": "1 point",
           "objective": "Understand", "difficulty": "hard"}
    route = Route("r", "chat", "m")
    item, = parse_reply(AiChatResult(json.dumps([row]), route), sources=(slide.link,), use="elp", rules=OPEN,
                        kind="open")
    assert item.kind == "open" and item.model_answer == "Because" and not item.options
    with pytest.raises(DraftReplyError):
        parse_reply(AiChatResult(json.dumps([{**row, "options": []}]), route), sources=(slide.link,), use="elp",
                    rules=OPEN, kind="open")
    update = build_update_request(item, "New text", rules=OPEN)
    assert json.loads(update.text)["question"]["model_answer"] == "Because" and "options" not in update.text


def test_table_shows_the_type_and_model_answer():
    text = questions_csv([open_item()]).decode("utf-8-sig")
    assert "Open answer" in text and "P(A|B) = P(B|A) P(A) / P(B)" in text


def test_editor_writes_an_open_answer_question(lab):
    _, _, bank, _, window, *_ = lab
    AnswerRulesStore(bank.folder).save(BOTH)
    window.answer_rules_changed()
    window.new_question()
    assert window.kind_field.isHidden() and not window.choice_box.isHidden()
    window.use.setCurrentData("elp")
    assert not window.kind_field.isHidden()
    window.kind.setCurrentData("open")
    assert window.choice_box.isHidden() and not window.open_box.isHidden()
    window.stem.setPlainText("Explain it.")
    window.difficulty.setCurrentData("easy")
    assert "have a model answer" in window.status_line.text()
    window.model_answer.setPlainText("The model answer")
    window.grading_notes.setPlainText("Notes")
    candidate = window.candidate()
    assert candidate.kind == "open" and candidate.options == () and candidate.model_answer == "The model answer"
    assert window.save(), window.notice.text()
    saved = bank.get(window.current.id)
    assert saved.kind == "open" and saved.grading_notes == "Notes"
    window.populate(saved)
    assert window.model_answer.toPlainText() == "The model answer" and not window.dirty()
    AnswerRulesStore(bank.folder).save(AnswerRules())
    window.answer_rules_changed()
    assert "be multiple choice" in window.status_line.text()
    window.use.setCurrentData("revision")                     # a revision quiz is multiple choice
    assert window.kind.currentData() == "choice" and len(window.rows) >= 2
