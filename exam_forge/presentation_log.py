# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Record presenter events as one JSON Lines file per presentation."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from slidemage.plugin_api import (
    PageShown,
    PresentationEnded,
    PresentationStarted,
    ScreenState,
)


class PresentationLog:
    """Start after activation, with the folder of the selected bank.

    Events before the next presentation start cannot be reconstructed: the service
    does not replay events. Keep this object alive until close() is called.
    Each event is flushed to survive an app crash, not a power cut.
    """

    def __init__(self, host, folder: str | Path):
        self.host = host
        self.folder = Path(folder)
        self._unsubscribe = None
        self._path = None
        self._failed = False

    def start(self) -> bool:
        """Subscribe on first use; return False and tell the user if unavailable."""
        if self._unsubscribe is not None:
            return True
        service = self.host.service("presenter.events", 1)
        if service is None:
            self.host.show_message("Presentation logging is unavailable: presenter events are not offered.")
            return False
        self._unsubscribe = service.subscribe(self._on_event)
        return True

    def close(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._path = None

    def _on_event(self, event) -> None:
        if isinstance(event, PresentationStarted):
            self._path = self.folder / "presentations" / f"{uuid4()}.jsonl"
            self._failed = False
        elif self._path is None or self._failed:
            return
        if not isinstance(event, (PresentationStarted, PageShown, ScreenState, PresentationEnded)):
            return
        data = asdict(event)
        data["type"] = type(event).__name__
        data["wall_time"] = event.wall_time.isoformat()
        if isinstance(event, PresentationStarted) and event.deck_path is not None:
            data["deck_path"] = str(event.deck_path)
        try:
            self._path.parent.mkdir(exist_ok=True)
            with self._path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(data, ensure_ascii=False) + "\n")
                stream.flush()
        except OSError as exc:
            self._failed = True
            self.host.show_message(f"Could not write the presentation log: {exc}", error=True)
        if isinstance(event, PresentationEnded):
            self._path = None


def read_log(path: str | Path) -> list[dict]:
    """Read complete records; a crash may leave an unfinished final line."""
    records = []
    with Path(path).open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                break
            records.append(json.loads(line))
    return records


@dataclass(frozen=True)
class Session:
    name: str
    id: str
    presentation: str


class Sessions:
    """Discover presentation logs for a bank."""

    def __init__(self, bank):
        self.folder = bank.folder.resolve()

    def path(self, stored: str) -> Path:
        return (self.folder / stored).resolve()

    def load(self, *, deck=None, schedule=()) -> list[Session]:
        """Discover logs and label them using cached course data.

        Schedule labels require an unambiguous date/time match;
        a missing provider, link or cached session leaves the timestamp label.
        """
        schedule = schedule or ()  # course.sessions returns None for an unlinked deck.
        sessions = []
        for path in sorted((self.folder / "presentations").glob("*.jsonl")):
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
            sessions.append(Session(label, path.stem, str(path.relative_to(self.folder))))
        return sessions
