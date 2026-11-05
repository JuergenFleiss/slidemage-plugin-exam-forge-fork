# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Source fingerprints, checks and slide-text diffs, without Qt.

Every function here that reads the deck, a slide or the bank does disk I/O: a UI
caller runs it on the host worker, with a fresh deck snapshot (fresh_deck), since
host deck_slides reads deck.text. Call check_bank on bank open and on Check slides.
Inspect both the report's errors and each source's status: missing and error are
distinct from changed content, and all are recorded as changed slides.

Checks follow the deck's resolved slide, even when its version label changes.
They never update a link's saved fingerprint: accepting a new baseline is
Bank.rebaseline, fed by rebaseline_links.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal

from slidemage.plugin_api import Deck, Host, SlideRef, SlideSource

from .bank import Bank, CorruptFileError
from .model import Item, SourceLink

VIEWS = ("instructor", "during-course", "after-course")


@dataclass(frozen=True)
class DeckSnapshot:
    name: str
    path: Path
    text: str
    groups: tuple[str, ...]


def fresh_deck(deck) -> DeckSnapshot:
    """The deck as its file is now; worker only (disk I/O)."""
    return DeckSnapshot(deck.name, deck.path, deck.path.read_text(encoding="utf-8"), deck.groups)


@dataclass(frozen=True)
class SourceCheck:
    source: SourceLink
    status: Literal["unchanged", "changed", "missing", "error"]
    message: str = ""


@dataclass(frozen=True)
class ItemCheck:
    item: Item
    sources: tuple[SourceCheck, ...]

    @property
    def changed_slides(self) -> tuple[str, ...]:
        """Also unavailable sources, which cannot safely pass a review check."""
        return tuple(dict.fromkeys(s.source.slide_id for s in self.sources if s.status != "unchanged"))

    @property
    def source_changed(self) -> bool:
        return bool(self.changed_slides)


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
    if not (isinstance(language, str) and len(language) == 2 and language.isalpha() and language.islower()):
        raise ValueError("a source link needs the requested reading language (a code such as en, de or fr)")
    if view not in VIEWS:
        raise ValueError(f"unknown view: {view}")
    source = host.slide_source(ref)
    return SourceLink(slide_id=ref.id, version=ref.version or "", language=language,
                      view=view, fingerprint=fingerprint(source), text=source.text)


class Unresolved(ValueError):
    """A link the deck no longer resolves to one slide; status is "missing" or "error"."""

    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


class SlideIndex:
    """A deck's slides, read once per view and language, and the slide each link resolves to."""

    def __init__(self, host: Host, deck: Deck):
        self.host, self.deck = host, deck
        self._refs: dict[tuple[str, str], list[SlideRef] | Exception] = {}

    def refs(self, view: str, language: str) -> list[SlideRef]:
        key = (view, language)
        if key not in self._refs:
            try:
                self._refs[key] = self.host.deck_slides(self.deck, view, language)
            except (OSError, ValueError) as exc:
                self._refs[key] = exc
        refs = self._refs[key]
        if isinstance(refs, Exception):
            raise refs
        return refs

    def find(self, link: SourceLink) -> SlideRef:
        """The linked slide as the deck now resolves it; raises Unresolved, or the deck's read error."""
        candidates = [ref for ref in self.refs(link.view, link.language) if ref.id == link.slide_id]
        if not candidates:
            raise Unresolved("missing", f"Slide {link.slide_id} is missing from the deck.")
        if len(candidates) > 1:
            # A deck may use the same slide at several versions. Retain the
            # linked version when it still disambiguates those calls. A sole
            # ref, however, is followed regardless of its version label.
            matching = [ref for ref in candidates if (ref.version or "") == link.version]
            if matching:
                candidates = matching
            if len({(ref.path, ref.language_read or ref.language) for ref in candidates}) > 1:
                raise Unresolved("error", f"Slide {link.slide_id} has ambiguous deck versions.")
        return candidates[0]


def rebaseline_links(host: Host, deck: Deck, item: Item) -> tuple[SourceLink, ...]:
    """Re-read each linked slide as the deck now resolves it."""
    index, links = SlideIndex(host, deck), []
    for link in item.sources:
        ref = index.find(link)
        source = host.slide_source(ref)
        links.append(replace(link, version=ref.version or "", fingerprint=fingerprint(source), text=source.text))
    return tuple(links)


class _Checker:
    """Cache deck resolution and file reads only for the duration of one check."""

    def __init__(self, host: Host, deck: Deck):
        self.host, self.index = host, SlideIndex(host, deck)
        self.fingerprints: dict[tuple[Path, str | None], str | Exception] = {}

    def source(self, link: SourceLink) -> SourceCheck:
        try:
            ref = self.index.find(link)
        except Unresolved as exc:
            return SourceCheck(link, exc.status, str(exc))
        except (OSError, ValueError) as exc:
            return SourceCheck(link, "error", str(exc))
        key = (ref.path, ref.language_read or ref.language)
        if key not in self.fingerprints:
            try:
                self.fingerprints[key] = fingerprint(self.host.slide_source(ref))
            except (OSError, ValueError) as exc:
                self.fingerprints[key] = exc
        current = self.fingerprints[key]
        if isinstance(current, Exception):
            return SourceCheck(link, "error", str(current))
        return SourceCheck(link, "unchanged" if current == link.fingerprint else "changed")

    def item(self, item: Item) -> ItemCheck:
        return ItemCheck(item, tuple(self.source(link) for link in item.sources))


def check_item(host: Host, deck: Deck, item: Item) -> ItemCheck:
    """Check a snapshot without storing the result; check_bank stores it."""
    return _Checker(host, deck).item(item)


def check_bank(host: Host, deck: Deck, bank: Bank, item_ids: Iterable[str] | None = None) -> BankCheck:
    """Check all live items, or the requested IDs, and store which of their slides changed.

    A failed check must not approve or freeze an item. Corrupt files from a full
    bank load are reported alongside the items that could be checked; newer
    schemas and other bank errors are raised. Concurrent source-link edits raise
    ConflictError rather than storing a stale result. Earlier writes may have
    succeeded if a later write fails; rerunning is safe.
    """
    if item_ids is None:
        items, errors = bank.load()
    else:
        items, errors = [bank.get(item_id) for item_id in dict.fromkeys(item_ids)], []
    checker = _Checker(host, deck)
    results = []
    for item in items:
        checked = checker.item(item)
        updated = bank.set_changed_slides(item.id, checked.changed_slides, expected_sources=item.sources)
        results.append(ItemCheck(updated, checked.sources))
    return BankCheck(tuple(results), tuple(errors))


Segments = list[tuple[str, bool]]


def _add(segments: Segments, text: str, changed: bool) -> None:
    if segments and segments[-1][1] == changed:
        segments[-1] = (segments[-1][0] + text, changed)
    else:
        segments.append((text, changed))


def word_diff(old: str, new: str) -> tuple[Segments, Segments]:
    """Compare two versions of a slide's text by words: (old_segments, new_segments) of (text, changed); each side
    joins back to its input."""
    a, b = re.findall(r"\s+|\S+", old), re.findall(r"\s+|\S+", new)
    left, right = [], []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        changed = tag != "equal"
        if i2 > i1:
            _add(left, "".join(a[i1:i2]), changed)
        if j2 > j1:
            _add(right, "".join(b[j1:j2]), changed)
    return left, right
