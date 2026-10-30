# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Session metadata and attachments."""
from __future__ import annotations
import re
import shutil
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from .bank import Bank, _atomic_write, _read
from .presentation_log import read_log
from .transcripts import Transcript, read_transcript

@dataclass(frozen=True)
class Session:
    name: str
    id: str = field(default_factory=lambda: str(uuid4()))
    presentation: str = ""  # Bank-relative log path, absent for author-named sessions.
    recording: str = ""
    transcript: str = ""

    def __post_init__(self):
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
        updated = replace(session, **{kind: stored})
        self.save(updated)
        return updated

    def read_transcript(self, session: Session) -> Transcript:
        return read_transcript(self.path(session.transcript))

    def open_recording(self, host, session: Session) -> None:
        """Open the recording in the system player."""
        host.open_file(self.path(session.recording))
