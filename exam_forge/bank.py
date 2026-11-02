# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Revision-preserving JSON bank."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, fields, replace
from pathlib import Path
from uuid import uuid4
from typing import Literal, get_type_hints

from .model import Item, Media, Option, Origin, SourceLink

SCHEMA_VERSION = 2
_READABLE = (1, 2)
_ID = re.compile(r"[A-Za-z0-9_-]+\Z")


class BankError(Exception):
    pass


class ConflictError(BankError):
    pass


class SchemaError(BankError):
    pass


class CorruptFileError(BankError):
    pass


def _atomic_write(path: Path, data: dict) -> None:
    """Replace only after the entire JSON document has been flushed to disk."""
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + ".", delete=False) as stream:
            name = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name is not None and os.path.exists(name):
            os.unlink(name)


def _read(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict) or type(data.get("schema_version")) is not int:
            raise ValueError("missing schema version")
        if data["schema_version"] > SCHEMA_VERSION:
            raise SchemaError(f"{path}: newer schema {data['schema_version']}")
        if data["schema_version"] not in _READABLE:
            raise ValueError("unsupported schema version")
        return data
    except (OSError, ValueError, TypeError) as exc:
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


def _item(data: dict) -> Item:
    data = dict(data)
    for name in ("options", "sources", "media"):
        if name in data and type(data[name]) is not list:
            raise ValueError(f"invalid {name} type")
    data["options"] = tuple(_typed(Option, option) for option in data["options"])
    data["sources"] = tuple(_typed(SourceLink, source) for source in data["sources"])
    data["origin"] = _typed(Origin, data["origin"])
    data["media"] = tuple(_typed(Media, entry) for entry in data.get("media", ()))
    return _typed(Item, data)


def _revision_data(item: Item) -> dict:
    """Serialize revision content without the item-level source check flag."""
    data = asdict(item)
    del data["source_changed"]
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
            _atomic_write(self._index_path, {"schema_version": SCHEMA_VERSION,
                                            "id": str(uuid4()), "items": []})
        self._index()

    @property
    def _index_path(self) -> Path:
        return self.folder / "bank.json"

    def _index(self) -> dict:
        data = _read(self._index_path)
        if not isinstance(data.get("id"), str) or not data["id"]:
            raise CorruptFileError(f"{self._index_path}: invalid bank index")
        items = data.get("items")
        if not isinstance(items, list) or any(not isinstance(k, str) or not _ID.fullmatch(k) for k in items):
            raise CorruptFileError(f"{self._index_path}: invalid bank index")
        data["items"] = items
        return data

    @property
    def id(self) -> str:
        return self._index()["id"]

    def _path(self, item_id: str) -> Path:
        if not isinstance(item_id, str) or not _ID.fullmatch(item_id):
            raise ValueError("invalid item ID")
        return self.folder / f"{item_id}.json"

    def _document(self, item_id: str) -> tuple[dict, list[Item]]:
        path = self._path(item_id)
        data = _read(path)
        try:
            rows = data["revisions"]
            changed = data["source_changed"]
            if type(changed) is not bool:
                raise ValueError("invalid source_changed flag")
            revisions = [_item({**row, "source_changed": changed}) for row in rows]
            if (not revisions or any(item.id != item_id or item.revision != i
                                     for i, item in enumerate(revisions, 1))
                    or any(item.origin != revisions[0].origin for item in revisions)):
                raise ValueError("invalid revision history")
            return data, revisions
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise CorruptFileError(f"{path}: {exc}") from exc

    def _revisions(self, item_id: str) -> list[Item]:
        return self._document(item_id)[1]

    def _write_item(self, item_id: str, data: dict, revisions: list[Item], changed: bool) -> None:
        data["schema_version"] = SCHEMA_VERSION
        data["source_changed"] = changed
        data["revisions"] = [_revision_data(row) for row in revisions]
        _atomic_write(self._path(item_id), data)

    def get(self, item_id: str, revision: int | None = None) -> Item:
        """Read a revision with the current source flag."""
        if item_id not in self._index()["items"]:
            raise KeyError(item_id)
        revisions = self._revisions(item_id)
        if revision is None:
            return revisions[-1]
        if type(revision) is not int or not 1 <= revision <= len(revisions):
            raise KeyError((item_id, revision))
        return revisions[revision - 1]

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
        _atomic_write(self._index_path, index)
        self._write_item(item.id, {"schema_version": SCHEMA_VERSION}, [item], item.source_changed)
        return item

    def save(self, item: Item) -> Item:
        """Save an edit based on item.revision, returning the new revision."""
        if item.status != "draft":
            raise ValueError("edits must have draft status")
        index = self._index()
        if item.id not in index["items"]:
            raise ConflictError(f"item is missing: {item.id}")
        data, revisions = self._document(item.id)
        current = revisions[-1]
        if item.revision != current.revision:
            raise ConflictError(f"stale revision for {item.id}")
        if item.origin != current.origin:
            raise ValueError("origin is permanent")
        if current.ai_edit_route and not item.ai_edit_route:
            raise ValueError("an AI-assisted item stays AI-assisted")
        item = replace(item, revision=current.revision + 1, status="draft",
                       source_changed=current.source_changed)
        revisions.append(item)
        self._write_item(item.id, data, revisions, current.source_changed)
        return item

    def set_status(self, item_id: str, revision: int, status: str) -> Item:
        """Move through the review lifecycle, preserving the previous revision."""
        transitions = {"draft": "reviewed", "reviewed": "retired", "retired": "draft"}
        index = self._index()
        if item_id not in index["items"]:
            raise ConflictError(f"item is missing: {item_id}")
        data, revisions = self._document(item_id)
        current = revisions[-1]
        if type(revision) is not int or current.revision != revision:
            raise ConflictError(f"stale revision for {item_id}")
        if status != transitions[current.status]:
            raise ValueError(f"invalid status transition: {current.status} to {status}")
        updated = replace(current, revision=current.revision + 1, status=status)
        revisions.append(updated)
        self._write_item(item_id, data, revisions, current.source_changed)
        return updated

    def rebaseline(self, item_id: str, revision: int, sources: tuple[SourceLink, ...]) -> Item:
        """Accept new source baselines ("Still correct"); status is kept, only sources change."""
        index = self._index()
        if item_id not in index["items"]:
            raise ConflictError(f"item is missing: {item_id}")
        data, revisions = self._document(item_id)
        current = revisions[-1]
        if type(revision) is not int or current.revision != revision:
            raise ConflictError(f"stale revision for {item_id}")
        sources = tuple(sources)
        if [s.slide_id for s in sources] != [s.slide_id for s in current.sources]:
            raise ValueError("a new baseline must keep the same slides in the same order")
        updated = replace(current, revision=current.revision + 1, sources=sources, source_changed=False)
        revisions.append(updated)
        self._write_item(item_id, data, revisions, False)
        return updated

    def set_source_changed(self, item_id: str, changed: bool, *,
                           expected_sources: tuple[SourceLink, ...] | None = None) -> Item:
        """Record a source check; optionally reject results for links edited during a check."""
        if type(changed) is not bool:
            raise ValueError("source_changed must be a boolean")
        index = self._index()
        if item_id not in index["items"]:
            raise ConflictError(f"item is missing: {item_id}")
        data, revisions = self._document(item_id)
        current = revisions[-1]
        if expected_sources is not None and current.sources != expected_sources:
            raise ConflictError(f"source links changed during check for {item_id}")
        if current.source_changed == changed:
            return current
        updated = replace(current, source_changed=changed)
        self._write_item(item_id, data, revisions, changed)
        return updated

    def files(self) -> list[Path]:
        """Every file that belongs to this bank, the index last; a folder can also hold unrelated files."""
        folder = self.folder
        paths = [self._path(item_id) for item_id in self._index()["items"]]
        paths += [folder / "topics.json", *folder.glob("*.manifest.json"), *folder.glob("*.corrections.jsonl")]
        paths += [p for p in (folder / "media").glob("*") if not p.name.startswith(".")]
        return [p for p in paths if p.is_file()] + [self._index_path]

    def store_media(self, name: str, data: bytes, alt: str = "") -> Media:
        """Atomically store content by hash; replacing an image never changes old revisions."""
        if not isinstance(data, bytes) or not data:
            raise ValueError("media data must be nonempty bytes")
        digest = hashlib.sha256(data).hexdigest()
        # Validate the supplied filename before using its suffix.
        Media(name, digest, alt)
        media = Media(digest + Path(name).suffix.lower(), digest, alt)
        folder = self.folder / "media"
        folder.mkdir(exist_ok=True)
        path = folder / media.name
        if path.exists():
            self.read_media(media)
            return media
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=folder, prefix=".media-", delete=False) as stream:
                temporary = stream.name
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)
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

