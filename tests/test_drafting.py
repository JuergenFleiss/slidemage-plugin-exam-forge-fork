# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Request allowlist, strict complete replies and immutable review provenance."""
import json
from dataclasses import replace

import pytest
from slidemage.plugin_api import AiChatResult, MediaDescription, Route, SlideSource

from exam_forge.bank import Bank
from exam_forge.drafting import DraftReplyError, DraftSlide, build_request, parse_reply
from exam_forge.model import SourceLink


def reply_row():
    return {"stem": "What is two plus two?", "options": [
        {"text": "4", "correct": True, "rationale": "Two pairs make four."},
        {"text": "3", "correct": False, "rationale": "One too few."}],
        "objective": "Addition", "topic": "Arithmetic", "difficulty": "easy"}


def result(rows=None, **kwargs):
    return AiChatResult(json.dumps({"items": rows if rows is not None else [reply_row()]}),
                        Route("captured-route", "chat", "captured-model"), **kwargs)


def test_request_is_only_selected_teaching_text(tmp_path):
    # No file read is permitted in the pure builder (the path doesn't even exist).
    slide = DraftSlide(SourceLink("PRIVATE_LINK", "1", "en", "instructor", "PRIVATE_HASH"),
                       SlideSource("SELECTED_SLIDE", (tmp_path / "PRIVATE_IMAGE_PATH",),
                                   (MediaDescription("PRIVATE_IMAGE_KEY", short="UNREVIEWED_DESCRIPTION"),)))
    request = build_request((slide,))
    assert json.loads(request.text) == {"slides": [{"text": "SELECTED_SLIDE"}]}
    assert "SELECTED_SLIDE" in request.text
    for excluded in ("PRIVATE_LINK", "PRIVATE_HASH", "PRIVATE_IMAGE_PATH", "PRIVATE_IMAGE_KEY",
                     "UNREVIEWED_DESCRIPTION", "PRIVATE_SLIDE", "OTHER_CUE_STUDENT_DATA"):
        assert excluded not in request.text + request.instructions


def test_image_description_review_is_explicit_and_only_for_selected_slides():
    source = SlideSource("teaching text", descriptions=(
        MediaDescription("PRIVATE_PATH", short="Reviewed short", long="Reviewed long", takeaway="Reviewed takeaway"),
        MediaDescription("decoration", decorative=True, short="Decorative")))
    choice = DraftSlide(SourceLink(), source)
    assert "image_descriptions" not in build_request((choice,)).text
    data = json.loads(build_request((replace(choice, descriptions_reviewed=True),)).text)
    assert data["slides"][0]["image_descriptions"] == [
        {"short": "Reviewed short", "long": "Reviewed long", "takeaway": "Reviewed takeaway"}]
    assert "PRIVATE_PATH" not in str(data) and "Decorative" not in str(data)


def test_request_requires_slides():
    with pytest.raises(ValueError, match="Choose at least"):
        build_request()


@pytest.mark.parametrize("text", ["", '{"items":[', "[]", "null", '{"items":[]}',
                                  '{"items":[],"items":[]}', "```json\n{}\n```", '{"items":[null]}'])
def test_bad_json_and_shape_are_refused(text):
    with pytest.raises(DraftReplyError, match="Malformed AI reply.*No drafts were added"):
        parse_reply(replace(result(), text=text))


@pytest.mark.parametrize("change", [
    {"stem": ""}, {"topic": 42}, {"options": []}, {"difficulty": "medium"},
    {"status": "reviewed"}, {"origin": {"kind": "author"}}, {"revision": 2},
    {"options": [{"text": "answer", "correct": "false", "rationale": "reason"}]},
])
def test_invalid_row_refuses_whole_batch(change):
    malformed = {**reply_row(), **change}
    with pytest.raises(DraftReplyError, match="No drafts were added"):
        parse_reply(result([reply_row(), malformed]))


def test_truncation_checked_even_when_json_is_complete():
    with pytest.raises(DraftReplyError, match="truncated.*No drafts were added"):
        parse_reply(result(truncated=True))


def test_draft_cannot_skip_review_and_provenance_survives_edit(tmp_path):
    sources = (SourceLink("s", "v", "en", "instructor", "hash"),)
    first, second = parse_reply(result([reply_row(), reply_row()]), sources=sources)
    assert first.id != second.id and first.options[0].id != second.options[0].id
    assert first.status == "draft" and first.revision == 1 and first.origin.kind == "ai"
    assert (first.origin.route, first.origin.model) == ("captured-route", "captured-model")
    assert first.sources == sources
    bank = Bank(tmp_path, create=True)
    with pytest.raises(ValueError, match="draft"):
        bank.create(replace(first, status="reviewed"))
    bank.create(first)
    reviewed = bank.set_status(first.id, 1, "reviewed")
    edited = bank.save(replace(reviewed, stem="Edited", status="draft"))
    assert edited.status == "draft" and edited.origin == reviewed.origin == first.origin
    assert bank.get(first.id, reviewed.revision) == reviewed


def test_default_model_is_preserved_verbatim_and_elp_rules_apply():
    reply = replace(result(), route=Route("default-route", "chat", ""))
    assert parse_reply(reply)[0].origin.model == ""
    with pytest.raises(DraftReplyError, match="five options"):
        parse_reply(reply, use="elp")
    row = reply_row()
    row["options"] += [{"text": str(i), "correct": False, "rationale": "Incorrect sum"} for i in range(3)]
    assert parse_reply(result([row]), use="elp")[0].use == "elp"
