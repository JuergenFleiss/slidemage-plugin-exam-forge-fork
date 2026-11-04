# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The bank folder's files: a revision-preserving JSON bank, its images, topics and answer rules. Issued exams keep
their manifests and correction journals beside them."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import asdict, fields, replace
from pathlib import Path
from typing import Literal, get_type_hints
from uuid import uuid4

from .model import KINDS, NEXT_STATUS, Item, Media, Option, Origin, SourceLink, Topic, validate_topics
from .rules import AnswerRule, AnswerRules

FORMAT_VERSION = 1
_ID = re.compile(r"[A-Za-z0-9_-]+\Z")


class BankError(Exception):
    pass


class ConflictError(BankError):
    pass


class SchemaError(BankError):
    pass


class CorruptFileError(BankError):
    pass


def atomic_write_bytes(path: Path, data: bytes, prefix: str) -> None:
    """Replace only after the entire file has been flushed to disk."""
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=prefix, delete=False) as stream:
            name = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name is not None and os.path.exists(name):
            os.unlink(name)


def _write_json(path: Path, data: dict) -> None:
    """Replace only after the entire JSON document has been flushed to disk."""
    encoded = (json.dumps({"schema_version": FORMAT_VERSION, **data}, ensure_ascii=False, indent=2) + "\n")
    atomic_write_bytes(path, encoded.encode("utf-8"), "." + path.name + ".")


def _read_json(path: Path, read: Callable[[dict], object]):
    """read(data) of a file in this format; a newer format is a SchemaError, any other failure a CorruptFileError."""
    try:
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict) or type(data.get("schema_version")) is not int:
            raise ValueError("missing schema version")
        if data["schema_version"] > FORMAT_VERSION:
            raise SchemaError(f"{path}: newer schema {data['schema_version']}")
        if data["schema_version"] != FORMAT_VERSION:
            raise ValueError("unsupported schema version")
        return read(data)
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        raise CorruptFileError(f"{path}: {exc}") from exc


def _typed(cls, data):
    hints = get_type_hints(cls)
    for field in fields(cls):
        if field.name not in data:
            continue
        expected = hints[field.name]
        if expected in (str, int, bool) and type(data[field.name]) is not expected:
            raise ValueError(f"invalid {field.name} type")
        if getattr(expected, "__origin__", None) is Literal and type(data[field.name]) is not str:
            raise ValueError(f"invalid {field.name} type")
    return cls(**data)


def _item(data: dict, changed: tuple[str, ...]) -> Item:
    data = dict(data)
    for name in ("options", "sources", "media"):
        if type(data[name]) is not list:
            raise ValueError(f"invalid {name} type")
    data["options"] = tuple(_typed(Option, option) for option in data["options"])
    data["sources"] = tuple(_typed(SourceLink, source) for source in data["sources"])
    data["media"] = tuple(_typed(Media, entry) for entry in data["media"])
    data["origin"] = _typed(Origin, data["origin"])
    data["ai_edit"] = _typed(Origin, data["ai_edit"])
    return _typed(Item, {**data, "changed_slides": changed})


def _revision_data(item: Item) -> dict:
    """Serialize revision content without the item-level source check."""
    data = asdict(item)
    del data["changed_slides"]
    return data


class Bank:
    """Open an existing bank, or create one in the explicitly selected folder."""

    def __init__(self, folder: str | Path, *, create: bool = False):
        self.folder = Path(folder)
        if create:
            self.folder.mkdir(parents=True, exist_ok=True)
        if create and not self._index_path.exists():
            if any(p.name != "bank.json" for p in self.folder.glob("*.json")):
                raise BankError("bank.json is missing but question files exist in this folder")
            _write_json(self._index_path, {"id": str(uuid4()), "items": []})
        self._index()

    @property
    def _index_path(self) -> Path:
        return self.folder / "bank.json"

    def _index(self) -> dict:
        def read(data):
            items = data["items"]
            if (not isinstance(data["id"], str) or not data["id"] or not isinstance(items, list)
                    or any(not isinstance(k, str) or not _ID.fullmatch(k) for k in items)):
                raise ValueError("invalid bank index")
            return {"id": data["id"], "items": items}
        return _read_json(self._index_path, read)

    @property
    def id(self) -> str:
        return self._index()["id"]

    def _path(self, item_id: str) -> Path:
        if not isinstance(item_id, str) or not _ID.fullmatch(item_id):
            raise ValueError("invalid item ID")
        return self.folder / f"{item_id}.json"

    def _revisions(self, item_id: str) -> list[Item]:
        """Every revision, oldest first, each with the item's changed slides."""
        def read(data):
            changed = data["changed_slides"]
            if type(changed) is not list:
                raise ValueError("invalid changed slides")
            revisions = [_item(row, tuple(changed)) for row in data["revisions"]]
            if (not revisions or any(item.id != item_id or item.revision != i for i, item in enumerate(revisions, 1))
                    or any(item.origin != revisions[0].origin for item in revisions)):
                raise ValueError("invalid revision history")
            return revisions
        return _read_json(self._path(item_id), read)

    def _write_item(self, item_id: str, revisions: list[Item], changed: tuple[str, ...]) -> None:
        _write_json(self._path(item_id), {"changed_slides": list(changed),
                                          "revisions": [_revision_data(row) for row in revisions]})

    def _known(self, item_id: str) -> None:
        if item_id not in self._index()["items"]:
            raise ConflictError(f"item is missing: {item_id}")

    def _revise(self, item_id: str, revision: int, change: Callable[[Item], Item]) -> Item:
        """Append change(current) as the next revision of a current one; change raises to refuse."""
        self._known(item_id)
        revisions = self._revisions(item_id)
        current = revisions[-1]
        if type(revision) is not int or current.revision != revision:
            raise ConflictError(f"stale revision for {item_id}")
        updated = replace(change(current), revision=current.revision + 1)
        revisions.append(updated)
        self._write_item(item_id, revisions, updated.changed_slides)
        return updated

    def get(self, item_id: str) -> Item:
        """Read the current revision with the current source check."""
        if item_id not in self._index()["items"]:
            raise KeyError(item_id)
        return self._revisions(item_id)[-1]

    def load(self) -> tuple[list[Item], list[CorruptFileError]]:
        """Return current items and per-file errors; refuse newer schemas."""
        items, errors = [], []
        for item_id in self._index()["items"]:
            try:
                items.append(self._revisions(item_id)[-1])
            except CorruptFileError as exc:
                errors.append(exc)
        return items, errors

    def create(self, item: Item) -> Item:
        """Reserve an ID permanently before writing its initial draft."""
        path = self._path(item.id)
        if item.revision != 1 or item.status != "draft":
            raise ValueError("a new item must be revision 1 and draft")
        index = self._index()
        if item.id in index["items"] or path.exists():
            raise ConflictError(f"item ID already used: {item.id}")
        # A failed item write leaves a reported missing file and reserves the ID.
        index["items"].append(item.id)
        _write_json(self._index_path, index)
        self._write_item(item.id, [item], item.changed_slides)
        return item

    def save(self, item: Item) -> Item:
        """Save an edit based on item.revision, returning the new revision."""
        if item.status != "draft":
            raise ValueError("edits must have draft status")

        def change(current):
            if item.origin != current.origin:
                raise ValueError("origin is permanent")
            if current.ai_edit.kind == "ai" and item.ai_edit.kind != "ai":
                raise ValueError("an AI-assisted item stays AI-assisted")
            return replace(item, changed_slides=current.changed_slides)
        return self._revise(item.id, item.revision, change)

    def set_status(self, item_id: str, revision: int, status: str) -> Item:
        """Move through the review lifecycle, preserving the previous revision."""
        def change(current):
            if status != NEXT_STATUS[current.status]:
                raise ValueError(f"invalid status transition: {current.status} to {status}")
            return replace(current, status=status)
        return self._revise(item_id, revision, change)

    def rebaseline(self, item_id: str, revision: int, sources: tuple[SourceLink, ...]) -> Item:
        """Accept new source baselines ("Still correct"); status is kept, only sources change."""
        sources = tuple(sources)

        def change(current):
            if [s.slide_id for s in sources] != [s.slide_id for s in current.sources]:
                raise ValueError("a new baseline must keep the same slides in the same order")
            return replace(current, sources=sources, changed_slides=())
        return self._revise(item_id, revision, change)

    def set_changed_slides(self, item_id: str, slides, *,
                           expected_sources: tuple[SourceLink, ...] | None = None) -> Item:
        """Record a source check; optionally reject results for links edited during a check."""
        slides = tuple(slides)
        self._known(item_id)
        revisions = self._revisions(item_id)
        current = revisions[-1]
        if expected_sources is not None and current.sources != expected_sources:
            raise ConflictError(f"source links changed during check for {item_id}")
        updated = replace(current, changed_slides=slides)  # Validates the slides.
        if current.changed_slides != slides:
            self._write_item(item_id, revisions, slides)
        return updated

    def files(self) -> list[Path]:
        """Every file that belongs to this bank, the index last; a folder can also hold unrelated files."""
        folder = self.folder
        paths = [self._path(item_id) for item_id in self._index()["items"]]
        paths += [folder / "topics.json", folder / "answer-rules.json", *folder.glob("*.manifest.json"),
                  *folder.glob("*.corrections.jsonl")]
        paths += [p for p in (folder / "media").glob("*") if not p.name.startswith(".")]
        return [p for p in paths if p.is_file()] + [self._index_path]

    def store_media(self, name: str, data: bytes) -> Media:
        """Atomically store content by hash; replacing an image never changes old revisions."""
        if not isinstance(data, bytes) or not data:
            raise ValueError("media data must be nonempty bytes")
        digest = hashlib.sha256(data).hexdigest()
        # Validate the supplied filename before using its suffix.
        Media(name, digest)
        media = Media(digest + Path(name).suffix.lower(), digest)
        folder = self.folder / "media"
        folder.mkdir(exist_ok=True)
        path = folder / media.name
        if path.exists():
            self.read_media(media)
            return media
        atomic_write_bytes(path, data, ".media-")
        return media

    def read_media(self, media: Media) -> bytes:
        """Read and verify an attachment, failing on missing or modified content."""
        folder = self.folder / "media"
        path = folder / media.name
        if path.resolve().parent != folder.resolve():
            raise BankError(f"Media is outside the bank: {media.name}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != media.sha256:
            raise BankError(f"Media hash mismatch: {media.name}")
        return data


class TopicStore:
    """topics.json; a bank without one has no topics yet."""

    def __init__(self, folder):
        self.path = Path(folder) / "topics.json"

    def load(self) -> tuple[Topic, ...]:
        if not self.path.exists():
            return ()

        def read(data):
            if type(data["topics"]) is not list:
                raise ValueError("invalid topics")
            topics = []
            for row in data["topics"]:
                if type(row) is not dict or type(row.get("slides")) is not list:
                    raise ValueError("invalid topic")
                topics.append(Topic(row["id"], row["name"], row["color"], tuple(row["slides"])))
            validate_topics(topics)
            return tuple(topics)
        return _read_json(self.path, read)

    def save(self, topics) -> None:
        topics = tuple(topics)
        validate_topics(topics)
        _write_json(self.path, {"topics": [asdict(t) for t in topics]})


class AnswerRulesStore:
    """answer-rules.json; without the file, the defaults apply."""

    def __init__(self, folder):
        self.path = Path(folder) / "answer-rules.json"

    def load(self) -> AnswerRules:
        if not self.path.exists():
            return AnswerRules()

        def read(data):
            kinds = data["exam_kinds"]
            if type(kinds) is not list or not set(kinds) <= set(KINDS):
                raise ValueError("invalid exam question types")
            rules = AnswerRules(**{use: AnswerRule(**data[use]) for use in ("revision", "exam")},
                                exam_kinds=tuple(kinds))
            rules.exam_rules()
            return rules
        return _read_json(self.path, read)

    def save(self, rules: AnswerRules) -> None:
        rules.exam_rules()
        _write_json(self.path, asdict(rules))
