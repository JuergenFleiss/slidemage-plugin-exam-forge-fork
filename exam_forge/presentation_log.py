# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Record presenter events as one JSON Lines file per presentation."""
from __future__ import annotations

import json
import os
from dataclasses import asdict
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
                os.fsync(stream.fileno())
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
