# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Open-answer exam questions: storage, rules, exam proposals, documents, AI drafting, editor and Exam tab."""
import json
from dataclasses import replace

import pytest
from slidemage.plugin_api import AiChatResult, Route

from exam_forge.exam import Blueprint, assemble, available
from exam_forge.bank import AnswerRulesStore, Bank
from exam_forge.drafting import DraftReplyError, build_request, build_update_request, parse_reply
from exam_forge.exam_document import export_exam_documents
from exam_forge.exam import freeze
from exam_forge.model import Item, Option, SourceLink
from exam_forge.rules import AnswerRules, ExamRules, exam_issues, question_issues
from exam_forge.exports import questions_csv

BOTH = AnswerRules(exam_kinds=("choice", "open"))
OPEN = AnswerRules(exam_kinds=("open",))


def open_item(item_id="o", difficulty="easy", **fields):
    return Item(item_id, 1, "Explain Bayes' theorem.", use="elp", kind="open", difficulty=difficulty,
                model_answer=fields.pop("model_answer", "P(A|B) = P(B|A) P(A) / P(B)"), grading_notes="2 points for",
                **fields)


def choice_item(item_id="c", difficulty="easy"):
    return Item(item_id, 1, "Pick", tuple(Option(str(i), f"O{i}", i == 0, "Why") for i in range(5)),
                use="elp", difficulty=difficulty)


def test_an_open_question_round_trips(tmp_path):
    bank = Bank(tmp_path / "bank", create=True)
    bank.create(open_item())
    assert json.loads((bank.folder / "o.json").read_text())["revisions"][0]["kind"] == "open"
    assert Bank(bank.folder).get("o") == open_item()


def test_rules_for_each_use_and_type():
    assert question_issues(open_item(), AnswerRules()) == ["be multiple choice"]
    assert question_issues(open_item(), OPEN) == [] and question_issues(open_item(), BOTH) == []
    assert question_issues(open_item(model_answer=" "), OPEN) == ["have a model answer"]
    assert question_issues(choice_item(), OPEN) == ["be an open-answer question"]
    assert question_issues(replace(open_item(), use="revision"), BOTH) == ["be multiple choice"]
    assert exam_issues(open_item(), ExamRules(kinds=("open",))) == []
    with pytest.raises(ValueError):
        AnswerRules(exam_kinds=())
    with pytest.raises(ValueError):
        Item("x", 1, kind="essay")


def test_store_keeps_the_exam_types(tmp_path):
    store = AnswerRulesStore(tmp_path)
    store.save(BOTH)
    assert store.load() == BOTH and json.loads(store.path.read_text())["exam_kinds"] == ["choice", "open"]


def test_proposals_count_each_type_separately():
    items = [replace(choice_item(f"c{i}"), status="reviewed") for i in range(3)]
    items += [replace(open_item(f"o{i}", "hard"), status="reviewed") for i in range(2)]
    of = {item.id: "t" for item in items}
    rules = ExamRules(kinds=("choice", "open"))
    assert available(items, of, rules) == {("t", "choice", "easy"): 3, ("t", "open", "hard"): 2}
    assert available(items, of, ExamRules())[("t", "open", "hard")] == 0   # multiple choice only: open ones excluded
    picked = assemble(items, Blueprint({("t", "choice", "easy"): 2, ("t", "open", "hard"): 1}), seed=1, topics=of,
                      rules=rules)
    assert sorted(i.kind for i in picked.items) == ["choice", "choice", "open"]
    assert [i.id for i in picked.alternatives(next(i.id for i in picked.items if i.kind == "open"))] in (["o0"], ["o1"])
    short = assemble(items, Blueprint({("t", "open", "easy"): 1}), seed=1, topics=of, rules=rules)
    assert short.problems == ("Topic 't' needs 1 easy open-answer question; only 0 are eligible.",)


def test_documents_leave_room_and_the_key_holds_the_model_answer(tmp_path):
    bank = Bank(tmp_path / "bank", create=True)
    for item in (choice_item(), open_item(difficulty="hard")):
        bank.create(item)
        bank.set_status(item.id, 1, "reviewed")
    manifest = freeze(None, None, bank, [("c", 2), ("o", 2)], {}, ExamRules(kinds=("choice", "open")),
                      topics={})
    assert manifest.data()["schema_version"] == 1
    exam, key = export_exam_documents(manifest).values()
    assert exam.count('class="answer-space"') == 1 and "P(A|B)" not in exam
    assert "P(A|B) = P(B|A) P(A) / P(B)" in key and "2 points for" in key and "graded by hand" in key
    assert "Frozen scoring tables" in key and "36 points" in exam
    only_open = freeze(None, None, bank, [("o", 2)], {}, ExamRules(kinds=("open",)), topics={})
    assert "Frozen scoring tables" not in export_exam_documents(only_open)['key']
    with pytest.raises(ValueError, match="be multiple choice"):
        freeze(None, None, bank, [("o", 2)], {}, ExamRules(), topics={})


def test_ai_drafts_and_updates_open_questions():
    link = SourceLink("s", "1", "en", "instructor", "h", "Slide text")
    request = build_request((link,), OPEN, use="elp", kind="open")
    assert '"model_answer"' in request.instructions and '"options"' not in request.instructions
    with pytest.raises(ValueError):
        build_request((link,), AnswerRules(), use="elp", kind="open")    # the deck's exam is multiple choice
    row = {"slide": 1, "stem": "Explain", "model_answer": "Because", "grading_notes": "1 point",
           "objective": "Understand", "difficulty": "hard"}
    route = Route("r", "chat", "m")
    item, = parse_reply(AiChatResult(json.dumps([row]), route), OPEN, sources=(link,), use="elp", kind="open")
    assert item.kind == "open" and item.model_answer == "Because" and not item.options
    with pytest.raises(DraftReplyError):
        parse_reply(AiChatResult(json.dumps([{**row, "options": []}]), route), OPEN, sources=(link,), use="elp",
                    kind="open")
    update = build_update_request(item, "New text", OPEN)
    assert json.loads(update.text)["question"]["model_answer"] == "Because" and "options" not in update.text


def test_table_shows_the_type_and_model_answer():
    text = questions_csv([open_item()]).decode("utf-8-sig")
    assert "Open answer" in text and "P(A|B) = P(B|A) P(A) / P(B)" in text


def test_editor_writes_an_open_answer_question(lab):
    _, _, bank, _, window, *_ = lab
    AnswerRulesStore(bank.folder).save(BOTH)
    window.answer_rules_changed()
    window.questions.new_question()
    assert window.editor.kind_field.isHidden() and not window.editor.choice_box.isHidden()
    window.editor.use.set_value("elp")
    assert not window.editor.kind_field.isHidden()
    window.editor.kind.set_value("open")
    assert window.editor.choice_box.isHidden() and not window.editor.open_box.isHidden()
    window.editor.stem.setPlainText("Explain it.")
    window.editor.difficulty.set_value("easy")
    assert "have a model answer" in window.editor.status_line.text()
    window.editor.model_answer.setPlainText("The model answer")
    window.editor.grading_notes.setPlainText("Notes")
    candidate = window.editor.candidate()
    assert candidate.kind == "open" and candidate.options == () and candidate.model_answer == "The model answer"
    assert window.editor.save(), window.notice.text()
    saved = bank.get(window.editor.current.id)
    assert saved.kind == "open" and saved.grading_notes == "Notes"
    window.editor.populate(saved)
    assert window.editor.model_answer.toPlainText() == "The model answer" and not window.editor.dirty()
    AnswerRulesStore(bank.folder).save(AnswerRules())
    window.answer_rules_changed()
    assert "be multiple choice" in window.editor.status_line.text()
    window.editor.use.set_value("revision")                     # a revision quiz is multiple choice
    assert window.editor.kind.value() == "choice" and len(window.editor.rows) >= 2
