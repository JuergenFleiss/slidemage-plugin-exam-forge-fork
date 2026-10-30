# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Taught evidence and filtering, without host calls or storage.

G7 supplies one deck's refs keyed by (view, requested language), selected
sessions, and read_log records keyed by Session.id. It persists TaughtOptions
with the deck's settings and passes that default to filter_items. Schedule
coverage is only an estimate: G7 must show and confirm the cutoff position.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time

from slidemage.plugin_api import CourseSessionInfo, SlideRef

from .model import Item
from .passages import Session, slide_visits


@dataclass(frozen=True)
class TaughtOptions:
    slides: bool = True
    talk: bool = False


_DEFAULT_OPTIONS = TaughtOptions()


@dataclass(frozen=True)
class TaughtEvidence:
    slides: frozenset[str] | None
    talk: frozenset[str] | None
    slide_reason: str = ""
    talk_reason: str = ""
    slide_method: str = "log"
    cutoff_position: int | None = None
    slide_versions: Mapping[str, frozenset[str | None]] = field(default_factory=dict)


@dataclass(frozen=True)
class ExcludedItem:
    item: Item
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class TaughtFilter:
    included: tuple[Item, ...]
    excluded: tuple[ExcludedItem, ...]
    options: TaughtOptions


def _equivalents(refs: Sequence[SlideRef], ids: set[str]) -> set[str]:
    """Expand once by position, never by slug, language tag or list index."""
    positions = {ref.position for ref in refs if ref.id in ids and ref.position > 0}
    return ids | {ref.id for ref in refs if ref.position in positions}


def build_evidence(
    deck_slides: Mapping[tuple[str, str], Sequence[SlideRef]],
    sessions: Sequence[Session] = (),
    logs: Mapping[str, Sequence[dict]] | None = None,
    *,
    schedule: Sequence[CourseSessionInfo] | None = None,
    as_of: datetime | None = None,
    view: str = "during-course",
    language: str = "en",
    cutoff_position: int | None = None,
    transcript_hashes: Mapping[str, str] | None = None,
) -> TaughtEvidence:
    """Logs win even if empty or unmapped; confirmed talk needs an attachment.

    A logged passage also needs a trusted audience visit to that slide (or its
    translation). A manual, unlogged session can use confirmed text matches.
    No passage manufactures slide evidence. Logged pinned versions must match a
    source link's named version. PageShown.version=None is unpinned and cannot
    be compared, so it still counts. Transcript confirmations need a matching
    current SHA-256 in transcript_hashes, keyed by session ID; missing hashes
    (including old confirmations) do not confirm talk from a transcript.

    Without logs, divide visible slide calls equally over all scheduled
    sessions, rounding down after sessions completed by as_of. An explicit
    lecturer cutoff replaces that estimate, but still requires a schedule.
    Supply as_of explicitly so results do not depend on the machine clock.
    """
    logs = logs or {}
    transcript_hashes = transcript_hashes or {}
    shown, spoken = set(), set()
    versions = {}
    has_log = has_talk = False
    for session in sessions:
        trusted = set()
        refs = ()
        if session.presentation:
            has_log = True
            if session.id not in logs:
                raise ValueError(f"Read the presentation log for session {session.id} first")
            rows = logs[session.id]
            if rows:
                first = rows[0]
                refs = tuple(ref for lang in ("de", "en")
                             for ref in deck_slides.get((first["view"], lang), ()))
                for visit in slide_visits(rows):
                    # A removed/foreign ID is not evidence about this deck.
                    ids = _equivalents(refs, {visit.slide_id}) & {ref.id for ref in refs}
                    trusted.update(ids)
                    for slide_id in ids:
                        versions.setdefault(slide_id, set()).add(visit.version)
                shown.update(trusted)
        if session.recording or session.transcript:
            has_talk = True
            ids = {p.slide_id for p in session.passages if p.confirmed and p.slide_id
                   and (not session.transcript or (p.transcript_sha256
                        and p.transcript_sha256 == transcript_hashes.get(session.id)))}
            if session.presentation:
                ids = _equivalents(refs, ids) & trusted
            else:
                refs = tuple(ref for lang in ("de", "en")
                             for ref in deck_slides.get((view, lang), ()))
                ids = _equivalents(refs, ids) & {ref.id for ref in refs}
            spoken.update(ids)

    reason, method, cutoff = "", "log", None
    if has_log:
        slides = frozenset(shown)
        if not shown:
            reason = "The selected logs contain no identifiable audience-visible slides."
    elif schedule:
        if as_of is None:
            raise ValueError("Schedule fallback needs an explicit as_of time")
        refs = deck_slides.get((view, language), ())
        positions = sorted({ref.position for ref in refs if not ref.hidden and ref.position > 0})
        completed = 0
        for session in schedule:
            # An unknown end time is conservatively the end of that day.
            end = datetime.combine(date.fromisoformat(session.date),
                                   time.fromisoformat(session.end) if session.end else time.max,
                                   tzinfo=as_of.tzinfo)
            completed += end <= as_of
        count = len(positions) * completed // len(schedule)
        cutoff = positions[count - 1] if count else 0
        if cutoff_position is not None:
            if type(cutoff_position) is not int or cutoff_position < 0:
                raise ValueError("Cutoff position must be a nonnegative integer")
            cutoff = cutoff_position
        selected = {ref.id for ref in refs if not ref.hidden and 0 < ref.position <= cutoff}
        both = tuple(ref for lang in ("de", "en") for ref in deck_slides.get((view, lang), ()))
        slides = frozenset(_equivalents(both, selected))
        method = "schedule"
        reason = "Estimated from completed course sessions and slide order; confirm the cutoff."
    else:
        slides = None
        method = "unavailable"
        reason = "Slides unavailable: no presentation log or course schedule."
    return TaughtEvidence(slides, frozenset(spoken) if has_talk else None, reason,
                          "" if has_talk else "Talk unavailable: no recording or transcript attached.",
                          method, cutoff, {slide_id: frozenset(values) for slide_id, values in versions.items()})


def filter_items(items: Sequence[Item], evidence: TaughtEvidence, *,
                 deck_default: TaughtOptions = _DEFAULT_OPTIONS,
                 options: TaughtOptions | None = None) -> TaughtFilter:
    """Every linked source must be covered; Slides and Talk combine by union.

    Neither ticked permits unlinked items too. Unavailable evidence never
    silently disables a requested limit; the other tick can still cover it.
    """
    options = deck_default if options is None else options
    included, excluded = [], []
    for item in items:
        reasons = []
        if options.slides or options.talk:
            if not item.sources:
                reasons.append("No linked slide or confirmed talk source.")
            for source in item.sources:
                slide = options.slides and source.slide_id in (evidence.slides or ())
                versions = evidence.slide_versions.get(source.slide_id, ())
                mismatch = (slide and source.version and versions and None not in versions
                            and source.version not in versions)
                talk = options.talk and source.slide_id in (evidence.talk or ())
                if not ((slide and not mismatch) or talk):
                    if mismatch:
                        reasons.append(f"Source {source.slide_id} links version {source.version}, "
                                       f"but the logged versions shown were {', '.join(sorted(versions))}.")
                    else:
                        reasons.append(f"Source {source.slide_id or '(unidentified)'} is outside the taught cutoff.")
            if reasons and options.slides and evidence.slides is None:
                reasons.append(evidence.slide_reason)
            if reasons and options.talk and evidence.talk is None:
                reasons.append(evidence.talk_reason)
        if reasons:
            excluded.append(ExcludedItem(item, tuple(dict.fromkeys(reasons))))
        else:
            included.append(item)
    return TaughtFilter(tuple(included), tuple(excluded), options)
