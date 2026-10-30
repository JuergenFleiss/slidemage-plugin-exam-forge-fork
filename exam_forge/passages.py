# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Session attachments and proposed talk passages, without presentation or UI wiring.

G7 owns the logger's lifetime and calls Sessions after a bank is selected. Log
mapping and text matching only propose evidence; neither confirms a passage.
"""
from __future__ import annotations

import math
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .bank import Bank, _atomic_write, _read
from .presentation_log import read_log
from .transcripts import Transcript, read_transcript


@dataclass(frozen=True)
class SyncPoint:
    """One known presentation monotonic time and its recording time, in seconds."""

    monotonic: float
    recording: float = 0

    def __post_init__(self):
        if not math.isfinite(self.monotonic) or not math.isfinite(self.recording) or self.recording < 0:
            raise ValueError("Sync point needs finite times and nonnegative recording time")

    def offset(self, monotonic: float) -> float:
        return monotonic - self.monotonic + self.recording


@dataclass(frozen=True)
class Visit:
    slide_id: str
    version: str | None
    start: float
    end: float


def slide_visits(records: Sequence[dict]) -> tuple[Visit, ...]:
    """Read one log in event order. Times are monotonic, never wall-clock times.

    ScreenState is authoritative after its first event. A freeze still shows
    audience_page; only black or unknown page identities leave gaps. Reveal steps of
    the same slide/version stay in one visit; returning after a gap or another
    slide starts a new visit. An unfinished log stops at its last known time.
    """
    if not records:
        return ()
    if records[0]["type"] != "PresentationStarted":
        raise ValueError("A log must begin with PresentationStarted")
    pages, visits = {}, []
    audience = presenter = None
    state = "shown"
    has_screen = False
    current = None
    since = previous = records[0]["monotonic"]
    for row in records:
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
        identity = pages.get(page) if state != "black" else None
        if identity is not None and identity[0] is None:
            identity = None
        if kind == "PresentationEnded":
            identity = None
        if current != identity:
            if current is not None and now > since:
                visits.append(Visit(*current, since, now))
            current, since = identity, now
        if kind == "PresentationEnded":
            return tuple(visits)
    if current is not None and previous > since:
        visits.append(Visit(*current, since, previous))
    return tuple(visits)


def _segments(transcript: Transcript | None, start: float, end: float) -> tuple[int, ...]:
    if transcript is None:
        return ()
    return tuple(i for i, s in enumerate(transcript.segments)
                 if s.start is not None and s.start < end and s.end > start)


@dataclass(frozen=True)
class Passage:
    slide_id: str
    version: str | None
    start: float | None
    end: float | None
    segments: tuple[int, ...] = ()
    confirmed: bool = False
    suggest_exclusion: bool = False
    transcript_sha256: str = ""

    def __post_init__(self):
        object.__setattr__(self, "segments", tuple(self.segments))
        if (self.start is None) != (self.end is None):
            raise ValueError("Passage needs both range endpoints, or neither")
        if self.start is not None and (not math.isfinite(self.start) or not math.isfinite(self.end)
                                       or not 0 <= self.start < self.end):
            raise ValueError("Passage needs a finite, positive time range")
        if self.confirmed and self.start is None:
            raise ValueError("Confirmation needs a time range")

    def confirm(self, transcript: Transcript | None = None, *,
                start: float | None = None, end: float | None = None) -> Passage:
        """Explicitly accept or trim a proposal. Untimed text needs a manual range.

        Supply the transcript when trimming timed text so cue references follow
        the new range. Untimed paragraphs retain their original text references.
        Its SHA-256 is retained so later transcript edits invalidate confirmation.
        """
        left = self.start if start is None else start
        right = self.end if end is None else end
        if left is None or right is None:
            raise ValueError("Confirmation needs a time range")
        if self.start is not None and (left < self.start or right > self.end):
            raise ValueError("Trimming must stay within the proposed range")
        trimmed = (left, right) != (self.start, self.end)
        if trimmed and self.start is not None and self.segments and transcript is None:
            raise ValueError("Trimming transcript cues needs the transcript")
        indices = self.segments
        if transcript is not None and self.start is not None:
            indices = _segments(transcript, left, right)
        return replace(self, start=left, end=right, segments=indices, confirmed=True,
                       transcript_sha256=transcript.sha256 if transcript is not None else "")


def propose_passages(records: Sequence[dict], transcript: Transcript | None = None, *,
                     sync: SyncPoint | None = None, padding: float = 3,
                     short_visit: float = 5) -> tuple[Passage, ...]:
    """Pad each visit, clamp at recording zero, merge overlaps per slide/version.

    With no sync point, recording zero is presentation start. Short exposure is
    an exclusion suggestion only, measured before padding. Sync changes require
    new proposals and confirmation; previously confirmed ranges are not shifted.
    """
    if not math.isfinite(padding) or padding < 0 or not math.isfinite(short_visit) or short_visit < 0:
        raise ValueError("Padding and short-visit threshold must be finite and nonnegative")
    visits = slide_visits(records)
    if not records:
        return ()
    sync = sync or SyncPoint(records[0]["monotonic"])
    groups = {}
    for visit in visits:
        start, end = sync.offset(visit.start), sync.offset(visit.end)
        left, right = max(0, start - padding), end + padding
        if right > left:
            groups.setdefault((visit.slide_id, visit.version), []).append(
                (left, right, visit.end - visit.start))
    proposals = []
    for (slide_id, version), ranges in groups.items():
        merged = []
        for left, right, exposure in ranges:
            if merged and left <= merged[-1][1]:
                previous = merged[-1]
                merged[-1] = (previous[0], max(previous[1], right), previous[2] + exposure)
            else:
                merged.append((left, right, exposure))
        for left, right, exposure in merged:
            proposals.append(Passage(slide_id, version, left, right,
                                     _segments(transcript, left, right),
                                     suggest_exclusion=exposure < short_visit))
    return tuple(sorted(proposals, key=lambda p: (p.start, p.slide_id, p.version or "")))


def propose_text_matches(transcript: Transcript, slides: Mapping[str, str]) -> tuple[Passage, ...]:
    """No-log fallback: propose the strongest word overlap, including tied slides.

    At least two shared words of three or more characters are required. This is
    deliberately a simple suggestion, not semantic matching or a LaTeX parser.
    Plain text stays untimed and must get a range before confirmation.
    """
    def words(text):
        return {w for w in re.findall(r"\w+", text.casefold()) if len(w) >= 3}

    vocabulary = {slide_id: words(text) for slide_id, text in slides.items()}
    proposals = []
    for index, segment in enumerate(transcript.segments):
        tokens = words(segment.text)
        scores = {slide_id: len(tokens & terms) / math.sqrt(len(tokens) * len(terms))
                  for slide_id, terms in vocabulary.items() if len(tokens & terms) >= 2}
        for slide_id, score in scores.items():
            if score == max(scores.values()):
                proposals.append(Passage(slide_id, None, segment.start, segment.end, (index,)))
    return tuple(proposals)


@dataclass(frozen=True)
class Session:
    name: str
    id: str = field(default_factory=lambda: str(uuid4()))
    presentation: str = ""  # Bank-relative log path, absent for author-named sessions.
    recording: str = ""
    transcript: str = ""
    sync: SyncPoint | None = None
    passages: tuple[Passage, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "passages", tuple(self.passages))
        if not self.name.strip() or not re.fullmatch(r"[A-Za-z0-9_-]+", self.id):
            raise ValueError("Session needs a name and a simple ID")


class Sessions:
    """Persist session metadata beside the bank; attachments may be copied or referenced.

    Disk work belongs on a worker. G7 resolves course.sessions on the UI thread
    after activation and passes its cached schedule to load. The caller reports
    I/O and parse errors; no dialogs or implicit fallback.
    """

    def __init__(self, bank: Bank):
        self.folder = bank.folder.resolve()

    def path(self, stored: str) -> Path:
        if not stored:
            raise ValueError("No attachment")
        return (self.folder / stored).resolve()

    def save(self, session: Session) -> None:
        folder = self.folder / "sessions"
        folder.mkdir(exist_ok=True)
        _atomic_write(folder / f"{session.id}.json", {"schema_version": 1, **asdict(session)})

    def load(self, *, deck=None, schedule=()) -> list[Session]:
        """Discover logs plus saved author-named sessions; cached course data labels logs.

        Author names win. Schedule labels require an unambiguous date/time match;
        a missing provider, link or cached session leaves the timestamp label.
        """
        schedule = schedule or ()  # course.sessions returns None for an unlinked deck.
        sessions = {}
        for path in sorted((self.folder / "sessions").glob("*.json")):
            data = _read(path)
            data.pop("schema_version")
            data["sync"] = SyncPoint(**data["sync"]) if data.get("sync") else None
            data["passages"] = tuple(Passage(**p) for p in data.get("passages", ()))
            session = Session(**data)
            sessions[session.id] = session
        for path in sorted((self.folder / "presentations").glob("*.jsonl")):
            if path.stem in sessions:
                continue
            rows = read_log(path)
            if not rows or rows[0]["type"] != "PresentationStarted":
                continue
            first = rows[0]
            if deck is not None and (not first.get("deck_path") or
                                     Path(first["deck_path"]).resolve() != deck.path.resolve()):
                continue
            when = datetime.fromisoformat(first["wall_time"])
            clock = when.strftime("%H:%M")
            matches = [s for s in schedule if s.date == when.date().isoformat()
                       and (not s.start or s.start <= clock) and (not s.end or clock <= s.end)]
            label = when.isoformat(sep=" ", timespec="minutes")
            if len(matches) == 1:
                s = matches[0]
                label = f"{s.date} {s.start}–{s.end} {s.location}".strip()
            sessions[path.stem] = Session(label, path.stem, str(path.relative_to(self.folder)))
        return list(sessions.values())

    def attach(self, session: Session, kind: str, path: str | Path, *, copy: bool = False) -> Session:
        """Attach an existing local file and save. Copies get unique bank-relative names."""
        if kind not in ("recording", "transcript"):
            raise ValueError("Attachment kind must be recording or transcript")
        path = Path(path).resolve(strict=True)
        if not path.is_file():
            raise ValueError("Attachment must be a file")
        if copy:
            folder = self.folder / "attachments"
            folder.mkdir(exist_ok=True)
            target = folder / f"{uuid4()}{path.suffix.lower()}"
            shutil.copyfile(path, target)
            path = target
        stored = str(path.relative_to(self.folder)) if path.is_relative_to(self.folder) else str(path)
        # An attachment change invalidates old confirmations and cue references.
        updated = replace(session, **{kind: stored}, passages=())
        self.save(updated)
        return updated

    def read_transcript(self, session: Session) -> Transcript:
        return read_transcript(self.path(session.transcript))

    def open_recording(self, host, session: Session) -> None:
        """D8: show the passage range in G7, then open the system player (no seek API)."""
        host.open_file(self.path(session.recording))
