# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
import base64
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
    manifest = freeze(host, deck, bank, [("q1", 2), ("q2", 2)], blueprint, ElpRules())
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
        freeze(host, deck, bank, [("q1", 2), ("q2", 2)], {}, ElpRules())
    assert [c.item.id for c in error.value.checks] == ["q1", "q2"]
    assert all(c.sources[0].status == "changed" for c in error.value.checks)
    host.slides[(deck.path, "instructor", "en")] = []
    with pytest.raises(ChangedSourcesError) as error:
        freeze(host, deck, bank, [("q1", 2)], {}, ElpRules())
    assert error.value.checks[0].sources[0].status == "missing"


def test_detects_edit_during_source_check(setup, monkeypatch):
    host, deck, _, bank = setup
    read = host.slide_source
    def edit(ref):
        bank.save(replace(bank.get("q1"), status="draft", stem="new"))
        return read(ref)
    monkeypatch.setattr(host, "slide_source", edit)
    with pytest.raises(ConflictError):
        freeze(host, deck, bank, [("q1", 2)], {}, ElpRules())


def test_corrections_survive_truncated_line(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_correction(path, "q1", "Fixed key", "Typo", time="2026-10-30T12:00:00Z")
    original = path.read_bytes()
    with path.open("ab") as stream:
        stream.write(b'{"item_id":"incomplete')
    assert len(read_corrections(path)) == 1
    append_correction(path, "q2", "Fixed text", "Clarity", time="2026-10-30T13:00:00Z")
    assert path.read_bytes().startswith(original)
    assert read_corrections(path) == [
        {"item_id": "q1", "change": "Fixed key", "reason": "Typo", "time": "2026-10-30T12:00:00Z"},
        {"item_id": "q2", "change": "Fixed text", "reason": "Clarity", "time": "2026-10-30T13:00:00Z"},
    ]


def test_rejects_corrupt_complete_audit_line(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_bytes(b"bad\n")
    with pytest.raises(ValueError):
        append_correction(path, "q", "change", "reason")
    assert path.read_bytes() == b"bad\n"
