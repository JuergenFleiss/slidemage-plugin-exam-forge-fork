# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Source fingerprints and checks, without Qt.

Call check_bank on bank open and on Check now. Approve and freeze use the same
call with item_ids and wait for done before proceeding. Inspect both the report's
errors and each source's status: missing, unresolved and error are distinct from
changed content, and all conservatively set source_changed.

link_slide and check_item do disk I/O; a UI caller runs them in a worker too.
Checks follow the deck's resolved slide, even when its version label changes.
They never update a link's saved fingerprint: accepting a new baseline is an edit.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from slidemage.plugin_api import Deck, Host, SlideRef, SlideSource

from .bank import Bank, CorruptFileError
from .model import Item, SourceLink


@dataclass(frozen=True)
class SourceCheck:
    source: SourceLink
    status: Literal["unchanged", "changed", "missing", "unresolved", "error"]
    fingerprint: str | None = None
    message: str = ""


@dataclass(frozen=True)
class ItemCheck:
    item: Item
    sources: tuple[SourceCheck, ...]

    @property
    def source_changed(self) -> bool:
        """Also flag unavailable sources, which cannot safely pass a review check."""
        return any(source.status != "unchanged" for source in self.sources)


@dataclass(frozen=True)
class BankCheck:
    items: tuple[ItemCheck, ...]
    errors: tuple[CorruptFileError, ...] = ()


def _media_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(source: SlideSource) -> str:
    """SHA-256 of a versioned, unambiguous UTF-8 JSON encoding.

    Text is exact (including line endings). Each media entry is the SHA-256 of
    its bytes, streamed from disk; absolute paths, mtimes and version labels are
    excluded. Media and description order follows SlideSource's stable order.
    Description keys are explicit so later plugin_api additions do not silently
    alter this format. Errors reading media propagate rather than hashing a
    missing file as empty content.
    """
    data = {
        "format": "exam-forge-source-v1",
        "text": source.text,
        "media": [_media_hash(path) for path in source.media],
        "descriptions": [
            {"image": entry.image, "decorative": entry.decorative,
             "short": entry.short, "long": entry.long, "takeaway": entry.takeaway}
            for entry in source.descriptions
        ],
    }
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def link_slide(host: Host, ref: SlideRef, view: str) -> SourceLink:
    """Capture a baseline from a host ref, retaining its requested reading language.

    The requested language matters for descriptions and translation fallbacks;
    ref.language may instead be the fallback language, 'mult', or None.
    """
    language = ref.language_read or ref.language
    if language not in ("de", "en"):
        raise ValueError("a source link needs the requested reading language (de or en)")
    if view not in ("instructor", "during-course", "after-course"):
        raise ValueError(f"unknown view: {view}")
    return SourceLink(slide_id=ref.id, version=ref.version or "", language=language,
                      view=view, fingerprint=fingerprint(host.slide_source(ref)))


class _Checker:
    """Cache deck resolution and file reads only for the duration of one check."""

    def __init__(self, host: Host, deck: Deck):
        self.host = host
        self.deck = deck
        self.decks: dict[tuple[str, str], list[SlideRef] | Exception] = {}
        self.fingerprints: dict[tuple[Path, str | None], str | Exception] = {}

    def source(self, link: SourceLink) -> SourceCheck:
        context = (link.view, link.language)
        if context not in self.decks:
            try:
                self.decks[context] = self.host.deck_slides(self.deck, *context)
            except (OSError, ValueError) as exc:
                self.decks[context] = exc
        refs = self.decks[context]
        if isinstance(refs, Exception):
            status = "unresolved" if isinstance(refs, ValueError) else "error"
            return SourceCheck(link, status, message=str(refs))

        candidates = [ref for ref in refs if ref.id == link.slide_id]
        if not candidates:
            return SourceCheck(link, "missing", message=f"Slide {link.slide_id} is missing from the deck.")
        if len(candidates) > 1:
            # A deck may use the same slide at several versions. Retain the
            # linked version when it still disambiguates those calls. A sole
            # ref, however, is followed regardless of its version label.
            matching = [ref for ref in candidates if (ref.version or "") == link.version]
            if matching:
                candidates = matching
            if len({(ref.path, ref.language_read or ref.language) for ref in candidates}) > 1:
                return SourceCheck(link, "error", message=f"Slide {link.slide_id} has ambiguous deck versions.")
        ref = candidates[0]
        key = (ref.path, ref.language_read or ref.language)
        if key not in self.fingerprints:
            try:
                self.fingerprints[key] = fingerprint(self.host.slide_source(ref))
            except (OSError, ValueError) as exc:
                self.fingerprints[key] = exc
        current = self.fingerprints[key]
        if isinstance(current, Exception):
            return SourceCheck(link, "error", message=str(current))
        return SourceCheck(link, "unchanged" if current == link.fingerprint else "changed", current)

    def item(self, item: Item) -> ItemCheck:
        return ItemCheck(item, tuple(self.source(link) for link in item.sources))


def check_item(host: Host, deck: Deck, item: Item) -> ItemCheck:
    """Check a snapshot without persisting it; call check_bank for stored markers."""
    return _Checker(host, deck).item(item)


def check_bank(
    host: Host,
    deck: Deck,
    bank: Bank,
    done: Callable[[BankCheck | None, BaseException | None], None],
    *,
    item_ids: Iterable[str] | None = None,
) -> None:
    """Check all live items, or the requested IDs, using host.run_in_background.

    All bank reads, source reads, hashing and marker writes run on the worker;
    done follows the host's UI-thread result/error convention. A failed check
    must not approve or freeze an item. Corrupt files from a full bank load are
    reported alongside the items that could be checked; newer schemas and other
    bank errors go to done's error argument. Concurrent source-link edits raise
    ConflictError rather than persisting a stale marker. Earlier marker writes
    may have succeeded if a later write fails; rerunning is safe.

    Pass a fresh Deck snapshot on each call: host deck_slides reads deck.text.
    """
    selected = None if item_ids is None else tuple(dict.fromkeys(item_ids))

    def work() -> BankCheck:
        if selected is None:
            items, errors = bank.load()
        else:
            items, errors = [bank.get(item_id) for item_id in selected], []
        checker = _Checker(host, deck)
        results = []
        for item in items:
            checked = checker.item(item)
            updated = bank.set_source_changed(item.id, item.revision, checked.source_changed,
                                              expected_sources=item.sources)
            results.append(ItemCheck(updated, checked.sources))
        return BankCheck(tuple(results), tuple(errors))

    host.run_in_background(work, done)
