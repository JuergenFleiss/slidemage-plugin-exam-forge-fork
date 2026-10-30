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
from .sessions import Session
import math


@dataclass(frozen=True)
class TaughtOptions:
    slides: bool = True


_DEFAULT_OPTIONS = TaughtOptions()


@dataclass(frozen=True)
class TaughtEvidence:
    slides: frozenset[str] | None
    slide_reason: str = ""
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
) -> TaughtEvidence:
    """Use audience-visible logged slides, or estimate coverage from the schedule."""
    logs = logs or {}
    shown, versions = set(), {}
    has_log = False
    for session in sessions:
        if not session.presentation:
            continue
        has_log = True
        if session.id not in logs:
            raise ValueError(f"Read the presentation log for session {session.id} first")
        rows = logs[session.id]
        if not rows:
            continue
        if rows[0]["type"] != "PresentationStarted":
            raise ValueError("A log must begin with PresentationStarted")
        refs = tuple(ref for lang in ("de", "en")
                     for ref in deck_slides.get((rows[0]["view"], lang), ()))
        pages = {}
        audience = presenter = None
        state, has_screen = "shown", False
        current = None
        since = previous = rows[0]["monotonic"]
        for row in rows:
            now = row["monotonic"]
            if not math.isfinite(now) or now < previous:
                raise ValueError("Log monotonic times must be finite and ordered")
            previous = now
            kind = row["type"]
            if kind == "PageShown":
                presenter = row["page"]
                pages[presenter] = (row["slide_id"], row["version"])
            elif kind == "ScreenState":
                has_screen = True
                state, audience = row["state"], row["audience_page"]
            page = audience if has_screen else presenter
            identity = pages.get(page) if state != "black" and kind != "PresentationEnded" else None
            if identity and identity[0] is None:
                identity = None
            if current != identity:
                if current and now > since:
                    ids = _equivalents(refs, {current[0]}) & {ref.id for ref in refs}
                    shown.update(ids)
                    for slide_id in ids:
                        versions.setdefault(slide_id, set()).add(current[1])
                current, since = identity, now
            if kind == "PresentationEnded":
                break
        if current and previous > since:
            ids = _equivalents(refs, {current[0]}) & {ref.id for ref in refs}
            shown.update(ids)
            for slide_id in ids:
                versions.setdefault(slide_id, set()).add(current[1])

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
    return TaughtEvidence(slides, reason, method, cutoff,
                          {slide_id: frozenset(values) for slide_id, values in versions.items()})


def filter_items(items: Sequence[Item], evidence: TaughtEvidence, *,
                 deck_default: TaughtOptions = _DEFAULT_OPTIONS,
                 options: TaughtOptions | None = None) -> TaughtFilter:
    """Require every linked slide when the taught-only filter is enabled."""
    options = deck_default if options is None else options
    included, excluded = [], []
    for item in items:
        reasons = []
        if options.slides:
            if not item.sources:
                reasons.append("No linked slide source.")
            for source in item.sources:
                slide = options.slides and source.slide_id in (evidence.slides or ())
                versions = evidence.slide_versions.get(source.slide_id, ())
                mismatch = (slide and source.version and versions and None not in versions
                            and source.version not in versions)
                if not (slide and not mismatch):
                    if mismatch:
                        reasons.append(f"Source {source.slide_id} links version {source.version}, "
                                       f"but the logged versions shown were {', '.join(sorted(versions))}.")
                    else:
                        reasons.append(f"Source {source.slide_id or '(unidentified)'} is outside the taught cutoff.")
            if reasons and options.slides and evidence.slides is None:
                reasons.append(evidence.slide_reason)
        if reasons:
            excluded.append(ExcludedItem(item, tuple(dict.fromkeys(reasons))))
        else:
            included.append(item)
    return TaughtFilter(tuple(included), tuple(excluded), options)
