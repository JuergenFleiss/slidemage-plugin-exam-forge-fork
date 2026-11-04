# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue immutable exam snapshots and keep a separate correction journal.

freeze performs disk and host reads; run it in a worker with a fresh deck.
Manifest data includes answer keys and media bytes and must be kept private.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .bank import SCHEMA_VERSION, Bank, ConflictError
from .elp import SCORING_FLOOR, ElpRules, check_elp, scoring_table
from .model import Item
from .sources import ItemCheck, check_item


class ChangedSourcesError(ValueError):
    """All selected questions whose sources changed or could not be checked."""

    def __init__(self, checks: Iterable[ItemCheck]):
        self.checks = tuple(checks)
        super().__init__("Sources changed or unavailable: " + ", ".join(c.item.id for c in self.checks))


@dataclass(frozen=True)
class Manifest:
    """An immutable UTF-8 JSON snapshot; data() returns a detached copy."""

    encoded: bytes

    def __post_init__(self) -> None:
        data = json.loads(self.encoded)
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported manifest schema")

    def data(self) -> dict:
        return json.loads(self.encoded)

    def write(self, path: str | Path) -> None:
        """Create a new manifest without overwriting an issued one."""
        path = Path(path)
        name = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix="." + path.name + ".",
                                             delete=False) as stream:
                name = stream.name
                stream.write(self.encoded + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            # Linking publishes the complete file atomically and refuses an existing path.
            os.link(name, path)
        finally:
            if name is not None:
                os.unlink(name)

    @classmethod
    def read(cls, path: str | Path) -> Manifest:
        return cls(Path(path).read_bytes())


def freeze(host, deck, bank: Bank, selections: Iterable[tuple[str, int]],
           blueprint: dict, rules: ElpRules,
           *, topic_of: Callable[[Item], str]) -> Manifest:
    """Check and freeze selected current reviewed revisions, preserving their order.

    ChangedSourcesError carries every failing ItemCheck. Other failures abort the
    entire issue. Blueprint must contain JSON values; no caller-owned mutable
    object or bank media is retained in the returned snapshot. Each item snapshot
    stores the resolved topic name, so later topic changes do not alter it.
    """
    selections = tuple(selections)
    if not selections or len({item_id for item_id, _ in selections}) != len(selections):
        raise ValueError("select at least one question, without duplicates")
    items = []
    for item_id, revision in selections:
        item = bank.get(item_id)
        if item.revision != revision:
            raise ConflictError(f"stale revision for {item_id}")
        if item.status != "reviewed" or item.use != "elp":
            raise ValueError(f"{item_id} must be a reviewed exam question")
        errors = check_elp(item, rules.answers, rules.kinds)
        if errors:
            raise ValueError(f"{item_id}: {' '.join(errors)}")
        items.append(item)
    checks = [check_item(host, deck, item) for item in items]
    changed = [check for check in checks if check.source_changed]
    if changed:
        raise ChangedSourcesError(changed)
    attachments = {}
    for item in items:
        for media in item.media:
            attachments[media.name] = base64.b64encode(bank.read_media(media)).decode("ascii")
    # Detect edits or retirement while the checks and media reads were running.
    for item in items:
        if bank.get(item.id) != item:
            raise ConflictError(f"question changed during freeze: {item.id}")
    data = {
        "schema_version": SCHEMA_VERSION,
        "bank_id": bank.id,
        "items": [{**asdict(item), "topic": topic_of(item)} for item in items],
        "media_base64": attachments,
        "blueprint": blueprint,
        "scoring": {"rules": asdict(rules), "floor": SCORING_FLOOR,
                    "tables": {str(p): [asdict(row) for row in scoring_table(p, rules.answers)]
                               for p in (rules.easy.points, rules.hard.points)} if "choice" in rules.kinds else {}},
    }
    return Manifest(json.dumps(data, ensure_ascii=False, sort_keys=True,
                               allow_nan=False, separators=(",", ":")).encode("utf-8"))


def read_corrections(path: str | Path) -> list[dict]:
    """Read complete JSON lines; ignore an unfinished final line after a crash."""
    path = Path(path)
    if not path.exists():
        return []
    raw = path.read_bytes()
    lines = raw.split(b"\n")[:-1]
    result = []
    for line in lines:
        entry = json.loads(line)
        if not isinstance(entry, dict):
            raise ValueError("invalid correction entry")  # noqa: TRY004
        result.append(entry)
    return result


def append_correction(path: str | Path, item_id: str, change: str, reason: str,
                      *, time: str | None = None) -> None:
    """Append one correction, discarding only a crash-truncated final line.

    A single writer must serialize calls to this journal.
    """
    if not all(isinstance(value, str) and value.strip() for value in (item_id, change, reason)):
        raise ValueError("item, change and reason are required")
    if time is None:
        time = datetime.now(timezone.utc).isoformat()
    if not isinstance(time, str) or not time.strip():
        raise ValueError("time is required")
    path = Path(path)
    # Refuse corruption in complete lines before touching the file.
    read_corrections(path)
    entry = json.dumps({"item_id": item_id, "change": change, "reason": reason, "time": time},
                       ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    with path.open("a+b") as stream:
        stream.seek(0)
        raw = stream.read()
        if raw and not raw.endswith(b"\n"):
            stream.truncate(raw.rfind(b"\n") + 1)
        stream.seek(0, os.SEEK_END)
        stream.write(entry)
        stream.flush()
        os.fsync(stream.fileno())
