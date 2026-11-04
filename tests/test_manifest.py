# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from fake_host import FakeDeck, FakeHost
from slidemage.plugin_api import SlideRef, SlideSource

from exam_forge.bank import Bank, ConflictError
from exam_forge.elp import ElpRules
from exam_forge.manifest import (
    ChangedSourcesError,
    Manifest,
    append_correction,
    freeze,
    read_corrections,
)
from exam_forge.model import Item, Option
from exam_forge.sources import link_slide


@pytest.fixture
def setup(tmp_path):
    host = FakeHost(tmp_path)
    deck = FakeDeck("course", tmp_path / "deck.tex")
    ref = SlideRef(id="s", id_slug="s", path=tmp_path / "s.tex", language="en",
                   language_read="en", version="1", position=1)
    host.slides[(deck.path, "instructor", "en")] = [ref]
    host.sources[ref] = SlideSource(text="original")
    bank = Bank(tmp_path / "bank", create=True)
    media = bank.store_media("image.png", b"image bytes")
    link = link_slide(host, ref, "instructor")
    for name in ("q1", "q2"):
        bank.create(Item(name, 1, stem="Original", use="elp", difficulty="easy",
                         options=tuple(Option(str(i), str(i), i == 0) for i in range(5)),
                         sources=(link,), media=(media,)))
        bank.set_status(name, 1, "reviewed")
    return host, deck, ref, bank


def test_issue_is_detached_and_persistent_after_edit_and_retirement(setup, tmp_path):
    host, deck, _, bank = setup
    blueprint = {"topics": {"x": 2}}
    manifest = freeze(host, deck, bank, [("q1", 2), ("q2", 2)], blueprint, ElpRules(), topic_of=lambda i: "T-" + i.id)
    path = tmp_path / "exam.json"
    manifest.write(path)
    blueprint["topics"]["x"] = 9
    detached = manifest.data()
    detached["items"][0]["stem"] = "tampered"
    bank.save(replace(bank.get("q1"), stem="Changed", status="draft"))
    bank.set_status("q2", 2, "retired")
    (bank.folder / "media" / bank.get("q2").media[0].name).write_bytes(b"corrupt")
    data = Manifest.read(path).data()
    assert data == manifest.data()
    assert [q["stem"] for q in data["items"]] == ["Original", "Original"]
    assert data["blueprint"]["topics"]["x"] == 2
    assert data["schema_version"] == 2 and [q["topic"] for q in data["items"]] == ["T-q1", "T-q2"]
    assert base64.b64decode(next(iter(data["media_base64"].values()))) == b"image bytes"
    assert data["items"][0]["media"][0]["sha256"]
    assert data["scoring"]["tables"]["12"][0]["wrong_tick"] == -3
    with pytest.raises(FileExistsError):
        manifest.write(path)


def test_manifest_is_complete_before_atomic_publication(tmp_path, monkeypatch):
    manifest = Manifest(b'{"schema_version":1}')
    path = tmp_path / "exam.manifest.json"
    link = os.link

    def publish(source, target):
        assert not path.exists()
        assert target == path
        assert path.parent == Path(source).parent
        assert Path(source).read_bytes() == manifest.encoded + b"\n"
        link(source, target)

    monkeypatch.setattr(os, "link", publish)
    manifest.write(path)
    assert Manifest.read(path).data() == manifest.data()
    assert list(tmp_path.iterdir()) == [path]


def test_failed_manifest_write_leaves_no_published_or_temporary_file(tmp_path, monkeypatch):
    def fail(_fd):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError, match="disk full"):
        Manifest(b'{"schema_version":1}').write(tmp_path / "exam.manifest.json")
    assert list(tmp_path.iterdir()) == []


def test_atomic_manifest_publication_refuses_a_concurrent_issuer(tmp_path, monkeypatch):
    path = tmp_path / "exam.manifest.json"
    link = os.link

    def publish(source, target):
        path.write_bytes(b"another issued manifest")
        link(source, target)

    monkeypatch.setattr(os, "link", publish)
    with pytest.raises(FileExistsError):
        Manifest(b'{"schema_version":1}').write(path)
    assert path.read_bytes() == b"another issued manifest"
    assert list(tmp_path.iterdir()) == [path]


def test_checks_all_sources_before_issue(setup):
    host, deck, ref, bank = setup
    host.sources[ref] = SlideSource(text="changed")
    with pytest.raises(ChangedSourcesError) as error:
        freeze(host, deck, bank, [("q1", 2), ("q2", 2)], {}, ElpRules(), topic_of=lambda i: i.topic)
    assert [c.item.id for c in error.value.checks] == ["q1", "q2"]
    assert all(c.sources[0].status == "changed" for c in error.value.checks)
    host.slides[(deck.path, "instructor", "en")] = []
    with pytest.raises(ChangedSourcesError) as error:
        freeze(host, deck, bank, [("q1", 2)], {}, ElpRules(), topic_of=lambda i: i.topic)
    assert error.value.checks[0].sources[0].status == "missing"


def test_detects_edit_during_source_check(setup, monkeypatch):
    host, deck, _, bank = setup
    read = host.slide_source
    def edit(ref):
        bank.save(replace(bank.get("q1"), status="draft", stem="new"))
        return read(ref)
    monkeypatch.setattr(host, "slide_source", edit)
    with pytest.raises(ConflictError):
        freeze(host, deck, bank, [("q1", 2)], {}, ElpRules(), topic_of=lambda i: i.topic)


def test_corrections_survive_a_truncated_line_and_refuse_a_corrupt_one(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_correction(path, "q1", "Fixed key", "Typo", time="2026-10-30T12:00:00Z")
    original = path.read_bytes()
    with path.open("ab") as stream:
        stream.write(b'{"item_id":"incomplete')
    append_correction(path, "q2", "Fixed text", "Clarity", time="2026-10-30T13:00:00Z")
    assert path.read_bytes().startswith(original)
    assert [row["item_id"] for row in read_corrections(path)] == ["q1", "q2"]
    path.write_bytes(b"bad\n")
    with pytest.raises(ValueError):
        append_correction(path, "q", "change", "reason")
    assert path.read_bytes() == b"bad\n"


def test_schema_versions_and_v1_manifest_still_open_and_render(tmp_path):
    from exam_forge.elp_document import export_elp_documents
    v1 = {"schema_version": 1, "bank_id": "b", "blueprint": {"counts": {"x": 1}, "difficulty_mix": {"easy": 1},
          "minutes": 2.5, "avoid_last": 0, "seed": 5}, "media_base64": {},
          "items": [{"id": "q", "revision": 1, "stem": "Stem", "difficulty": "easy", "topic": "x",
                     "future_field": {"a": 1}, "media": [], "assistant_note": "",
                     "options": [{"id": str(i), "text": f"O{i}", "correct": i == 0, "rationale": ""} for i in range(5)]}],
          "scoring": {"rules": {"easy": {"points": 12, "minutes": 2.5}, "hard": {"points": 24, "minutes": 5}},
                      "floor": 0, "tables": {"12": [{"correct_options": 1, "correct_tick": 12, "wrong_tick": -3}]}}}
    path = tmp_path / "old.manifest.json"
    path.write_text(json.dumps(v1), encoding="utf-8")
    manifest = Manifest.read(path)
    assert manifest.data()["blueprint"]["difficulty_mix"] == {"easy": 1}
    exam, key = export_elp_documents(manifest)
    assert "Stem" in exam and "Correct" in key
    for bad in (0, 4, None):
        with pytest.raises(ValueError):
            Manifest(json.dumps({"schema_version": bad}).encode())
    assert Manifest(b'{"schema_version":2}') and Manifest(b'{"schema_version":3}')
