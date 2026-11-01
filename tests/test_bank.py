# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
import json
from dataclasses import replace

import pytest

from exam_forge import bank as storage
from exam_forge.bank import Bank, ConflictError, SchemaError
from exam_forge.model import Item, Option, Origin, SourceLink, check_phase1


@pytest.fixture
def bank(tmp_path):
    return Bank(tmp_path, create=True)


def question(item_id="q1"):
    return Item(item_id, 1, "Question?", (Option("a", "Yes", True, "Why"),
                                          Option("b", "Also", True)),
                "objective", "topic", "hard", "draft", "elp",
                (SourceLink("slide", "2", "en", "lecture", "hash"),), True,
                Origin("ai", "route", "model"))


def test_round_trip_and_phase_check(bank):
    item = question()
    bank.create(item)
    reopened = Bank(bank.folder)
    assert reopened.id == bank.id
    assert reopened.load() == ([item], [])
    assert reopened.get(item.id, 1) == item
    assert check_phase1(item)
    assert not check_phase1(replace(item, options=(item.options[0],)))


def test_conflict_and_reviewed_immutability(bank):
    original = bank.create(question())
    reviewed = bank.set_status(original.id, original.revision, "reviewed")
    edited = bank.save(replace(reviewed, stem="Changed", status="draft"))
    assert edited.revision == 3 and edited.status == "draft"
    assert bank.get(original.id, 2) == reviewed
    assert bank.get(original.id, 1) == original
    with pytest.raises(ConflictError):
        bank.save(replace(reviewed, stem="Stale", status="draft"))
    with pytest.raises(ValueError, match="origin"):
        bank.save(replace(edited, origin=Origin()))


def test_corrupt_file_is_reported_and_other_items_load(bank):
    good = bank.create(question("good"))
    bank.create(question("bad"))
    (bank.folder / "bad.json").write_text("{broken", encoding="utf-8")
    items, errors = bank.load()
    assert items == [good]
    assert len(errors) == 1 and "bad.json" in str(errors[0])


def test_newer_schema_refused_for_bank_and_item(bank):
    bank.create(question())
    item_path = bank.folder / "q1.json"
    data = json.loads(item_path.read_text())
    data["schema_version"] = storage.SCHEMA_VERSION + 1
    item_path.write_text(json.dumps(data))
    with pytest.raises(SchemaError):
        bank.load()
    index_path = bank.folder / "bank.json"
    data = json.loads(index_path.read_text())
    data["schema_version"] = storage.SCHEMA_VERSION + 1
    index_path.write_text(json.dumps(data))
    with pytest.raises(SchemaError):
        Bank(bank.folder)


def test_ids_never_reused_after_retirement(bank):
    item = bank.create(question())
    reviewed = bank.set_status(item.id, item.revision, "reviewed")
    retired = bank.set_status(item.id, reviewed.revision, "retired")
    with pytest.raises(ConflictError):
        bank.create(question())
    assert bank.load() == ([retired], [])
    assert bank.get(item.id, 1) == item



def test_interrupted_replace_leaves_old_file(bank, monkeypatch):
    item = bank.create(question())
    before = (bank.folder / "q1.json").read_bytes()
    def fail_replace(*args):
        raise OSError("interrupted")
    monkeypatch.setattr(storage.os, "replace", fail_replace)
    with pytest.raises(OSError, match="interrupted"):
        bank.save(replace(item, stem="New"))
    assert (bank.folder / "q1.json").read_bytes() == before
    assert bank.get(item.id) == item
    assert not list(bank.folder.glob(".q1.json.*"))


def test_source_check_does_not_conflict_with_editor_or_create_revision(bank):
    item = bank.create(replace(question(), source_changed=False))
    changed = bank.set_source_changed(item.id, True)
    assert changed.revision == item.revision and changed.source_changed
    data = json.loads((bank.folder / "q1.json").read_text())
    assert data["source_changed"] is True
    assert len(data["revisions"]) == 1
    assert "source_changed" not in data["revisions"][0]
    assert bank.get(item.id).source_changed and bank.load()[0][0].source_changed
    edited = bank.save(replace(item, stem="Edited"))
    assert edited.revision == 2 and edited.source_changed
    assert bank.get(item.id, 1).source_changed
    cleared = bank.set_source_changed(item.id, False)
    assert cleared.revision == 2 and not cleared.source_changed
    assert len(json.loads((bank.folder / "q1.json").read_text())["revisions"]) == 2


def test_status_moves_and_rejected_transitions(bank):
    draft = bank.create(question())
    current = draft
    for target in ("reviewed", "retired", "draft"):
        for refused in {"draft", "reviewed", "retired", "invalid"} - {target}:
            with pytest.raises(ValueError, match="transition"):
                bank.set_status(current.id, current.revision, refused)
        before = current
        current = bank.set_status(current.id, current.revision, target)
        assert current == replace(before, revision=before.revision + 1, status=target)
        assert bank.get(before.id, before.revision) == before
        with pytest.raises(ConflictError, match="stale"):
            bank.set_status(before.id, before.revision, target)
    for status in ("reviewed", "retired"):
        with pytest.raises(ValueError, match="draft"):
            bank.save(replace(current, status=status))
    assert bank.get(current.id) == current


def test_failed_creation_still_reserves_id(bank, monkeypatch):
    original_write = storage._atomic_write
    def fail_item(path, data):
        if path.name == "q1.json":
            raise OSError("interrupted")
        original_write(path, data)
    monkeypatch.setattr(storage, "_atomic_write", fail_item)
    with pytest.raises(OSError):
        bank.create(question())
    with pytest.raises(ConflictError):
        bank.create(question())
    assert len(bank.load()[1]) == 1


def test_media_round_trip_hash_validation_and_immutable_reviewed_image(bank):
    from exam_forge.bank import BankError
    from exam_forge.model import Media
    first = bank.store_media("first.png", b"first picture", "First image")
    assert first.sha256 in first.name
    item = bank.create(replace(question(), media=(first,)))
    reviewed = bank.set_status(item.id, item.revision, "reviewed")
    second = bank.store_media("replacement.png", b"second picture", "Second image")
    edited = bank.save(replace(reviewed, status="draft", media=(second,)))
    assert edited.status == "draft" and edited.revision == reviewed.revision + 1
    reopened = Bank(bank.folder)
    assert reopened.get(item.id).media == (second,)
    assert reopened.get(item.id, reviewed.revision).media == (first,)
    assert reopened.read_media(first) == b"first picture"
    assert reopened.read_media(second) == b"second picture"
    assert bank.store_media("same.png", b"first picture", "New alt text").name == first.name
    (bank.folder / "media" / first.name).write_bytes(b"tampered")
    with pytest.raises(BankError, match="hash mismatch"):
        reopened.read_media(first)
    with pytest.raises(ValueError, match="filename"):
        Media("../outside.png", first.sha256)
    with pytest.raises(ValueError, match="SHA-256"):
        Media("ok.png", "invalid")
    with pytest.raises(ValueError, match="unique"):
        replace(item, media=(first, first))


def test_interrupted_media_write_leaves_existing_images_intact(bank, monkeypatch):
    first = bank.store_media("first.png", b"old image")
    def fail(*args):
        raise OSError("interrupted")
    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(OSError, match="interrupted"):
        bank.store_media("new.png", b"new image")
    assert bank.read_media(first) == b"old image"
    assert list((bank.folder / "media").iterdir()) == [bank.folder / "media" / first.name]


@pytest.mark.parametrize("field,value", [("stem", 123), ("correct", 0.5)])
def test_wrong_field_types_are_reported_per_item(bank, field, value):
    good = bank.create(question("good"))
    bank.create(question("bad"))
    path = bank.folder / "bad.json"
    data = json.loads(path.read_text())
    row = data["revisions"][0]
    if field == "correct":
        for option in row["options"]:
            option[field] = value
    else:
        row[field] = value
    path.write_text(json.dumps(data))
    items, errors = bank.load()
    assert items == [good] and len(errors) == 1 and "bad.json" in str(errors[0])


def test_missing_index_with_question_files_refuses_creation(bank):
    bank.create(question())
    (bank.folder / "bank.json").unlink()
    with pytest.raises(storage.BankError, match="bank.json is missing"):
        Bank(bank.folder, create=True)
    assert not (bank.folder / "bank.json").exists()


V1_FILE = {"schema_version": 1, "source_changed": False, "revisions": [
    {"id": "old", "revision": 1, "stem": "Q1", "options": [{"id": "a", "text": "A", "correct": True, "rationale": ""},
                                                           {"id": "b", "text": "B", "correct": False, "rationale": ""}],
     "objective": "", "topic": "t", "difficulty": "easy", "status": "draft", "use": "revision",
     "sources": [{"slide_id": "s", "version": "1", "language": "en", "view": "instructor", "fingerprint": "f"}],
     "origin": {"kind": "author", "route": "", "model": ""}, "media": [], "assistant_note": ""},
    {"id": "old", "revision": 2, "stem": "Q2", "options": [], "objective": "", "topic": "t", "difficulty": "easy",
     "status": "draft", "use": "revision", "sources": [],
     "origin": {"kind": "author", "route": "", "model": ""}, "media": [], "assistant_note": ""}]}


def test_v1_file_reads_without_rewrite_and_is_saved_as_v2(tmp_path):
    bank = Bank(tmp_path, create=True)
    index = json.loads((tmp_path / "bank.json").read_text())
    assert index["schema_version"] == 2
    index["schema_version"] = 1
    index["items"].append("old")
    (tmp_path / "bank.json").write_text(json.dumps(index))
    path = tmp_path / "old.json"
    path.write_text(json.dumps(V1_FILE))
    before = (path.read_bytes(), (tmp_path / "bank.json").read_bytes())
    bank = Bank(tmp_path)
    first, second = bank.get("old", 1), bank.get("old")
    assert bank.load() == ([second], [])
    assert first.sources[0].text == "" and first.ai_edit_route == first.ai_edit_model == ""
    assert (path.read_bytes(), (tmp_path / "bank.json").read_bytes()) == before
    saved = bank.save(replace(second, stem="Q3"))
    data = json.loads(path.read_text())
    assert data["schema_version"] == 2 and len(data["revisions"]) == 3
    assert [r["stem"] for r in data["revisions"]] == ["Q1", "Q2", "Q3"]
    assert data["revisions"][0]["sources"][0]["fingerprint"] == "f"
    assert bank.get("old", 1) == first and bank.get("old") == saved
    assert json.loads((tmp_path / "bank.json").read_text())["schema_version"] == 1


def test_new_bank_and_items_are_v2_and_text_round_trips(bank):
    link = SourceLink("slide", "2", "en", "lecture", "hash", "Slide text")
    item = bank.create(replace(question(), sources=(link,), ai_edit_route="r", ai_edit_model="m"))
    assert json.loads((bank.folder / "bank.json").read_text())["schema_version"] == 2
    assert json.loads((bank.folder / "q1.json").read_text())["schema_version"] == 2
    assert Bank(bank.folder).get("q1") == item and item.sources[0].text == "Slide text"


def test_ai_assisted_stays_visible_on_later_revisions(bank):
    item = bank.create(question())
    ai = bank.save(replace(item, ai_edit_route="route-a", ai_edit_model="m"))
    with pytest.raises(ValueError, match="AI-assisted"):
        bank.save(replace(ai, ai_edit_route="", ai_edit_model=""))
    kept = bank.save(replace(ai, stem="Human edit"))
    assert kept.ai_edit_route == "route-a"
    replaced = bank.save(replace(kept, ai_edit_route="route-b", ai_edit_model="m2"))
    assert (replaced.ai_edit_route, replaced.ai_edit_model) == ("route-b", "m2")
    assert bank.get("q1", 1).ai_edit_route == ""
    reviewed = bank.set_status("q1", replaced.revision, "reviewed")
    assert reviewed.ai_edit_route == "route-b"


@pytest.mark.parametrize("status", ["draft", "reviewed", "retired"])
def test_rebaseline_keeps_status_and_clears_flag(bank, status):
    item = bank.create(question())
    current = item
    for target in ("reviewed", "retired")[:("draft", "reviewed", "retired").index(status)]:
        current = bank.set_status("q1", current.revision, target)
    assert current.source_changed
    links = (SourceLink("slide", "3", "en", "lecture", "new", "New text"),)
    updated = bank.rebaseline("q1", current.revision, links)
    assert updated == replace(current, revision=current.revision + 1, sources=links, source_changed=False)
    assert updated.status == status and updated.origin == current.origin
    assert bank.get("q1") == updated and bank.get("q1", current.revision) == replace(current, source_changed=False)


def test_rebaseline_refusals(bank):
    item = bank.create(question())
    same = (SourceLink("slide", "9", "en", "lecture", "x"),)
    with pytest.raises(ConflictError):
        bank.rebaseline("q1", 5, same)
    with pytest.raises(ConflictError):
        bank.rebaseline("missing", 1, same)
    for sources in ((), (SourceLink("other"),), same * 2):
        with pytest.raises(ValueError, match="same slides"):
            bank.rebaseline("q1", item.revision, sources)
    assert bank.get("q1") == item
