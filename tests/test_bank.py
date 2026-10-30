# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import replace
import json

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


def test_ids_never_reused_after_retirement_or_deletion(bank):
    item = bank.create(question())
    reviewed = bank.set_status(item.id, item.revision, "reviewed")
    retired = bank.set_status(item.id, reviewed.revision, "retired")
    with pytest.raises(ConflictError):
        bank.create(question())
    bank.delete(item.id, retired.revision)
    assert bank.load() == ([], [])
    assert bank.get(item.id, 1) == item
    with pytest.raises(ConflictError):
        bank.create(question())


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
    changed = bank.set_source_changed(item.id, item.revision, True)
    assert changed.revision == item.revision and changed.source_changed
    data = json.loads((bank.folder / "q1.json").read_text())
    assert data["source_changed"] is True
    assert len(data["revisions"]) == 1
    assert "source_changed" not in data["revisions"][0]
    assert bank.get(item.id).source_changed and bank.load()[0][0].source_changed
    edited = bank.save(replace(item, stem="Edited"))
    assert edited.revision == 2 and edited.source_changed
    assert bank.get(item.id, 1).source_changed
    # An old revision is irrelevant to a source check.
    cleared = bank.set_source_changed(item.id, item.revision, False)
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


def test_source_flag_legacy_format_migrates_on_write(bank):
    item = bank.create(question())
    path = bank.folder / "q1.json"
    data = json.loads(path.read_text())
    del data["source_changed"]
    data["revisions"][0]["source_changed"] = True
    path.write_text(json.dumps(data))
    assert bank.get(item.id) == item
    bank.save(replace(item, stem="New", source_changed=False))
    data = json.loads(path.read_text())
    assert data["source_changed"] is True
    assert all("source_changed" not in row for row in data["revisions"])


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
