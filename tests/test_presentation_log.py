# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import datetime, timezone
from pathlib import Path

from fake_host import FakeHost
from slidemage.plugin_api import (
    PageShown,
    PresentationEnded,
    PresentationStarted,
    ScreenState,
)

from exam_forge.presentation_log import PresentationLog, read_log


class Events:
    def __init__(self):
        self.callbacks = []

    def subscribe(self, callback):
        self.callbacks.append(callback)

        def unsubscribe():
            if callback in self.callbacks:
                self.callbacks.remove(callback)
        return unsubscribe

    def emit(self, event):
        for callback in self.callbacks[:]:
            callback(event)


def test_replay_jump_back_screen_state_and_separate_presentations(tmp_path):
    host = FakeHost(tmp_path)
    events = Events()
    host.offer("presenter.events", 1, events)
    log = PresentationLog(host, tmp_path)
    host.finish_activation()
    assert log.start() and log.start()
    assert len(events.callbacks) == 1
    now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    events.emit(PageShown(now, 0, 1, None, None, None, None))  # late subscription
    assert not (tmp_path / "presentations").exists()
    events.emit(PresentationStarted(Path("/deck.tex"), "instructor", "en", now, 1))
    events.emit(PageShown(now, 2, 1, "slide-a", "1-0-0", 1, 2))
    events.emit(PageShown(now, 3, 2, "slide-a", "1-0-0", 2, 2))
    events.emit(ScreenState(now, 4, "frozen", 2))
    events.emit(PageShown(now, 5, 7, None, None, None, None))
    events.emit(ScreenState(now, 6, "black", 2))
    events.emit(PageShown(now, 7, 1, "slide-a", "1-0-0", 1, 2))
    events.emit(ScreenState(now, 8, "shown", 1))
    events.emit(PresentationEnded(now, 9))
    first = next((tmp_path / "presentations").glob("*.jsonl"))
    rows = read_log(first)
    assert [row["type"] for row in rows] == ["PresentationStarted", "PageShown", "PageShown", "ScreenState",
                                               "PageShown", "ScreenState", "PageShown", "ScreenState",
                                               "PresentationEnded"]
    assert rows[0]["deck_path"] == "/deck.tex"
    assert all(row["wall_time"] == now.isoformat() for row in rows)
    assert [row["monotonic"] for row in rows] == list(range(1, 10))
    assert [row["page"] for row in rows if row["type"] == "PageShown"] == [1, 2, 7, 1]
    assert rows[4]["slide_id"] is None
    assert [(row["state"], row["audience_page"]) for row in rows if row["type"] == "ScreenState"] == [
        ("frozen", 2), ("black", 2), ("shown", 1)]
    events.emit(PresentationStarted(None, None, None, now, 10))
    files = list((tmp_path / "presentations").glob("*.jsonl"))
    assert len(files) == 2
    assert read_log(next(p for p in files if p != first))[0]["deck_path"] is None
    log.close()
    log.close()
    assert not events.callbacks


def test_crash_keeps_complete_lines_and_ignores_incomplete_tail(tmp_path):
    host = FakeHost(tmp_path)
    events = Events()
    host.offer("presenter.events", 1, events)
    host.finish_activation()
    log = PresentationLog(host, tmp_path)
    log.start()
    now = datetime.now(timezone.utc)
    events.emit(PresentationStarted(None, None, None, now, 1))
    events.emit(PageShown(now, 2, 3, None, None, None, None))
    path = next((tmp_path / "presentations").glob("*.jsonl"))
    with path.open("ab") as stream:
        stream.write(b'{"type":"PageShown", "slide_id": "\xc3')
    assert [row["type"] for row in read_log(path)] == ["PresentationStarted", "PageShown"]


def test_no_provider_reports_unavailable_without_creating_a_log(tmp_path):
    host = FakeHost(tmp_path)
    host.finish_activation()
    log = PresentationLog(host, tmp_path)
    assert not log.start()
    assert "unavailable" in host.messages[0][0]
    assert not list(tmp_path.iterdir())


def test_write_failure_is_reported_and_does_not_append_partial_session(tmp_path):
    host = FakeHost(tmp_path)
    events = Events()
    host.offer("presenter.events", 1, events)
    host.finish_activation()
    (tmp_path / "presentations").write_text("not a folder")
    log = PresentationLog(host, tmp_path)
    log.start()
    now = datetime.now(timezone.utc)
    events.emit(PresentationStarted(None, None, None, now, 1))
    events.emit(PageShown(now, 2, 1, None, None, None, None))
    assert len(host.messages) == 1
    assert host.messages[0][1]
