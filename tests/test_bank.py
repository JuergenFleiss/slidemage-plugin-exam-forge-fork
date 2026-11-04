# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The bank on disk: revisions, the status cycle, provenance, media and failure safety."""
import json
from dataclasses import replace

import pytest
from fake_host import revision

from exam_forge import bank as storage
from exam_forge.bank import Bank, BankError, ConflictError, SchemaError
from exam_forge.model import Item, Media, Option, Origin, SourceLink


@pytest.fixture
def bank(tmp_path):
    return Bank(tmp_path, create=True)


def question(item_id="q1"):
    return Item(item_id, 1, "Question?", (Option("a", "Yes", True, "Why"), Option("b", "Also", True)),
                "objective", "hard", "draft", "exam",
                (SourceLink("slide", "2", "en", "lecture", "hash", "Slide text"),), ("slide",),
                Origin("ai", "route", "model"))


def stored(bank, item_id="q1"):
    return json.loads((bank.folder / f"{item_id}.json").read_text())


def test_round_trip(bank):
    item = bank.create(question())
    reopened = Bank(bank.folder)
    assert reopened.id == bank.id and reopened.load() == ([item], []) and reopened.get(item.id) == item
    assert json.loads((bank.folder / "bank.json").read_text())["schema_version"] == stored(bank)["schema_version"] == 1
    assert set(stored(bank)["revisions"][0]) == {"id", "revision", "stem", "options", "objective", "difficulty",
                                                 "status", "use", "sources", "origin", "media", "assistant_note",
                                                 "ai_edit", "kind", "model_answer", "grading_notes"}  # Every field.


def test_status_cycle_refuses_other_transitions_and_stale_revisions(bank):
    current = bank.create(question())
    for target in ("reviewed", "retired", "draft"):
        for refused in {"draft", "reviewed", "retired", "invalid"} - {target}:
            with pytest.raises(ValueError, match="transition"):
                bank.set_status(current.id, current.revision, refused)
        before = current
        current = bank.set_status(current.id, current.revision, target)
        assert current == replace(before, revision=before.revision + 1, status=target)
        assert revision(bank, before.id, before.revision) == before
        with pytest.raises(ConflictError, match="stale"):
            bank.set_status(before.id, before.revision, target)
    for status in ("reviewed", "retired"):
        with pytest.raises(ValueError, match="draft"):
            bank.save(replace(current, status=status))


def test_edits_are_new_draft_revisions_and_stale_saves_conflict(bank):
    original = bank.create(question())
    reviewed = bank.set_status(original.id, original.revision, "reviewed")
    edited = bank.save(replace(reviewed, stem="Changed", status="draft"))
    assert edited.revision == 3 and revision(bank, original.id, 2) == reviewed
    assert revision(bank, original.id, 1) == original
    with pytest.raises(ConflictError):
        bank.save(replace(reviewed, stem="Stale", status="draft"))


def test_ai_provenance_is_permanent(bank):
    item = bank.create(question())
    with pytest.raises(ValueError, match="origin"):
        bank.save(replace(item, origin=Origin()))
    ai = bank.save(replace(item, ai_edit=Origin("ai", "route-a", "m")))
    with pytest.raises(ValueError, match="AI-assisted"):
        bank.save(replace(ai, ai_edit=Origin()))
    with pytest.raises(ValueError, match="route"):
        Origin("ai")
    assert bank.save(replace(ai, stem="Human edit")).ai_edit.route == "route-a"
    assert bank.set_status("q1", 3, "reviewed").ai_edit.route == "route-a"
    assert revision(bank, "q1", 1).ai_edit == Origin()


def test_ids_are_never_reused_even_after_a_failed_creation(bank, monkeypatch):
    item = bank.create(question())
    bank.set_status(item.id, bank.set_status(item.id, 1, "reviewed").revision, "retired")
    with pytest.raises(ConflictError):
        bank.create(question())
    write = storage._write_json

    def fail_item(path, data):
        if path.name == "q2.json":
            raise OSError("interrupted")
        write(path, data)

    monkeypatch.setattr(storage, "_write_json", fail_item)
    with pytest.raises(OSError):
        bank.create(question("q2"))
    with pytest.raises(ConflictError):
        bank.create(question("q2"))


def test_the_changed_slides_are_stored_beside_the_revisions(bank):
    item = bank.create(replace(question(), changed_slides=()))
    changed = bank.set_changed_slides(item.id, ("slide",))
    assert changed.revision == 1 and changed.source_changed and stored(bank)["changed_slides"] == ["slide"]
    assert len(stored(bank)["revisions"]) == 1 and "changed_slides" not in stored(bank)["revisions"][0]
    edited = bank.save(replace(item, stem="Edited"))
    assert edited.revision == 2 and Bank(bank.folder).get(item.id).changed_slides == ("slide",)
    assert bank.set_changed_slides(item.id, ()).revision == 2 and len(stored(bank)["revisions"]) == 2
    with pytest.raises(ValueError):
        bank.set_changed_slides(item.id, ("",))


@pytest.mark.parametrize("status", ["draft", "reviewed", "retired"])
def test_rebaseline_keeps_the_status_and_clears_the_marker(bank, status):
    current = bank.create(question())
    for target in ("reviewed", "retired")[:("draft", "reviewed", "retired").index(status)]:
        current = bank.set_status("q1", current.revision, target)
    links = (SourceLink("slide", "3", "en", "lecture", "new", "New text"),)
    updated = bank.rebaseline("q1", current.revision, links)
    assert updated == replace(current, revision=current.revision + 1, sources=links, changed_slides=())


def test_rebaseline_refusals(bank):
    item = bank.create(question())
    same = (SourceLink("slide", "9", "en", "lecture", "x"),)
    for item_id, number in (("q1", 5), ("missing", 1)):
        with pytest.raises(ConflictError):
            bank.rebaseline(item_id, number, same)
    for sources in ((), (SourceLink("other"),), same * 2):
        with pytest.raises(ValueError, match="same slides"):
            bank.rebaseline("q1", item.revision, sources)
    assert bank.get("q1") == item


def test_media_is_stored_by_hash_and_a_reviewed_image_never_changes(bank):
    first = replace(bank.store_media("first.png", b"first picture"), alt="First image")
    assert first.sha256 in first.name
    assert bank.store_media("same.png", b"first picture").name == first.name
    bank.create(replace(question(), media=(first,)))
    reviewed = bank.set_status("q1", 1, "reviewed")
    second = bank.store_media("replacement.png", b"second picture")
    bank.save(replace(reviewed, status="draft", media=(second,)))
    reopened = Bank(bank.folder)
    assert reopened.get("q1").media == (second,) and revision(reopened, "q1", reviewed.revision).media == (first,)
    assert reopened.read_media(first) == b"first picture"
    (bank.folder / "media" / first.name).write_bytes(b"tampered")
    with pytest.raises(BankError, match="hash mismatch"):
        reopened.read_media(first)
    for name, digest, match in (("../outside.png", first.sha256, "filename"), ("ok.png", "invalid", "SHA-256")):
        with pytest.raises(ValueError, match=match):
            Media(name, digest)


@pytest.mark.parametrize("target", ["item", "media"])
def test_an_interrupted_write_leaves_the_old_files(bank, monkeypatch, target):
    item = bank.create(question())
    first = bank.store_media("first.png", b"old image")
    before = sorted(p.read_bytes() for p in bank.folder.rglob("*") if p.is_file())

    def fail(*args):
        raise OSError("interrupted")

    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(OSError, match="interrupted"):
        bank.save(replace(item, stem="New")) if target == "item" else bank.store_media("new.png", b"new image")
    assert sorted(p.read_bytes() for p in bank.folder.rglob("*") if p.is_file()) == before
    assert bank.get(item.id) == item and bank.read_media(first) == b"old image"


@pytest.mark.parametrize("field,value", [("json", None), ("stem", 123), ("correct", 0.5)])
def test_a_corrupt_item_is_reported_and_the_others_load(bank, field, value):
    good = bank.create(question("good"))
    bank.create(question("bad"))
    data = stored(bank, "bad")
    row = data["revisions"][0]
    if field == "correct":
        for option in row["options"]:
            option[field] = value
    else:
        row[field] = value
    (bank.folder / "bad.json").write_text("{broken" if field == "json" else json.dumps(data))
    items, errors = bank.load()
    assert items == [good] and len(errors) == 1 and "bad.json" in str(errors[0])


def test_newer_schemas_and_a_missing_index_are_refused(bank):
    bank.create(question())
    for path, read in ((bank.folder / "q1.json", bank.load), (bank.folder / "bank.json", lambda: Bank(bank.folder))):
        path.write_text(json.dumps({**json.loads(path.read_text()), "schema_version": storage.FORMAT_VERSION + 1}))
        with pytest.raises(SchemaError):
            read()
    (bank.folder / "bank.json").unlink()
    with pytest.raises(BankError, match="bank.json is missing"):
        Bank(bank.folder, create=True)

