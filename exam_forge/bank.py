# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Revision-preserving JSON bank. All writers cooperate using the bank lock."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from .model import Item, Option, Origin, SourceLink

SCHEMA_VERSION = 1
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
        if data["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported schema version")
        return data
    except (OSError, ValueError, TypeError) as exc:
        raise CorruptFileError(f"{path}: {exc}") from exc


def _item(data: dict) -> Item:
    data = dict(data)
    data["options"] = tuple(Option(**option) for option in data["options"])
    data["sources"] = tuple(SourceLink(**source) for source in data["sources"])
    data["origin"] = Origin(**data["origin"])
    return Item(**data)


def _revision_data(item: Item) -> dict:
    """Serialize revision content without the item-level source check flag."""
    data = asdict(item)
    del data["source_changed"]
    return data


@contextmanager
def _lock(folder: Path):
    # A persistent lock file avoids stale locks after a crash. Locking the bank
    # rather than a replaced JSON inode also serializes independent processes.
    with (folder / ".bank.lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            if not stream.read(1):
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


class Bank:
    """Open an existing bank, or create one in the explicitly selected folder."""

    def __init__(self, folder: str | Path, *, create: bool = False):
        self.folder = Path(folder)
        if create:
            self.folder.mkdir(parents=True, exist_ok=True)
        with _lock(self.folder):
            if create and not self._index_path.exists():
                _atomic_write(self._index_path, {"schema_version": SCHEMA_VERSION,
                                                "id": str(uuid4()), "items": {}})
            self._index()

    @property
    def _index_path(self) -> Path:
        return self.folder / "bank.json"

    def _index(self) -> dict:
        data = _read(self._index_path)
        if (not isinstance(data.get("id"), str) or not data["id"]
                or not isinstance(data.get("items"), dict)
                or any(not isinstance(k, str) or not _ID.fullmatch(k) or type(v) is not bool
                       for k, v in data["items"].items())):
            raise CorruptFileError(f"{self._index_path}: invalid bank index")
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
            # Accept files written by the earlier F1 format and migrate them on
            # the next write, using the most recent revision's flag.
            changed = data.get("source_changed", rows[-1].get("source_changed", False))
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
        data["source_changed"] = changed
        data["revisions"] = [_revision_data(row) for row in revisions]
        _atomic_write(self._path(item_id), data)

    def get(self, item_id: str, revision: int | None = None) -> Item:
        """Historical revisions remain readable after deletion, with the current source flag."""
        with _lock(self.folder):
            if item_id not in self._index()["items"]:
                raise KeyError(item_id)
            revisions = self._revisions(item_id)
            if revision is None:
                return revisions[-1]
            if type(revision) is not int or not 1 <= revision <= len(revisions):
                raise KeyError((item_id, revision))
            return revisions[revision - 1]

    def load(self) -> tuple[list[Item], list[CorruptFileError]]:
        """Return current, non-deleted items and per-file errors; refuse newer schemas."""
        items, errors = [], []
        with _lock(self.folder):
            for item_id, deleted in self._index()["items"].items():
                if not deleted:
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
        with _lock(self.folder):
            index = self._index()
            if item.id in index["items"] or path.exists():
                raise ConflictError(f"item ID already used: {item.id}")
            # A failed item write leaves a reported missing file and reserves the ID.
            index["items"][item.id] = False
            _atomic_write(self._index_path, index)
            self._write_item(item.id, {"schema_version": SCHEMA_VERSION}, [item], item.source_changed)
        return item

    def save(self, item: Item) -> Item:
        """Save an edit based on item.revision, returning the new revision."""
        if item.status != "draft":
            raise ValueError("edits must have draft status")
        with _lock(self.folder):
            index = self._index()
            if item.id not in index["items"] or index["items"][item.id]:
                raise ConflictError(f"item is missing or deleted: {item.id}")
            data, revisions = self._document(item.id)
            current = revisions[-1]
            if item.revision != current.revision:
                raise ConflictError(f"stale revision for {item.id}")
            if item.origin != current.origin:
                raise ValueError("origin is permanent")
            item = replace(item, revision=current.revision + 1, status="draft",
                           source_changed=current.source_changed)
            revisions.append(item)
            self._write_item(item.id, data, revisions, current.source_changed)
            return item

    def set_status(self, item_id: str, revision: int, status: str) -> Item:
        """Move through the review lifecycle, preserving the previous revision."""
        transitions = {"draft": "reviewed", "reviewed": "retired", "retired": "draft"}
        with _lock(self.folder):
            index = self._index()
            if item_id not in index["items"] or index["items"][item_id]:
                raise ConflictError(f"item is missing or deleted: {item_id}")
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

    def set_source_changed(self, item_id: str, revision: int, changed: bool, *,
                           expected_sources: tuple[SourceLink, ...] | None = None) -> Item:
        """Record a source check; optionally reject results for links edited during a check.

        revision is kept for API compatibility; edits retaining the same sources are safe.
        """
        if type(changed) is not bool:
            raise ValueError("source_changed must be a boolean")
        with _lock(self.folder):
            index = self._index()
            if item_id not in index["items"] or index["items"][item_id]:
                raise ConflictError(f"item is missing or deleted: {item_id}")
            data, revisions = self._document(item_id)
            current = revisions[-1]
            if expected_sources is not None and current.sources != expected_sources:
                raise ConflictError(f"source links changed during check for {item_id}")
            if current.source_changed == changed:
                return current
            updated = replace(current, source_changed=changed)
            self._write_item(item_id, data, revisions, changed)
            return updated

    def delete(self, item_id: str, revision: int) -> None:
        """Hide an item without removing its history or releasing its ID."""
        with _lock(self.folder):
            index = self._index()
            if item_id not in index["items"] or index["items"][item_id]:
                raise ConflictError(f"item is missing or deleted: {item_id}")
            if self._revisions(item_id)[-1].revision != revision:
                raise ConflictError(f"stale revision for {item_id}")
            index["items"][item_id] = True
            _atomic_write(self._index_path, index)
