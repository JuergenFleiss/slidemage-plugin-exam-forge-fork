# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
import json
from dataclasses import replace
from pathlib import Path

from fake_host import FakeDeck, FakeHost
from slidemage.plugin_api import CourseSessionInfo

from exam_forge.bank import Bank
from exam_forge.presentation_log import Sessions


def event(kind, time, **fields):
    # Wall-clock ordering deliberately disagrees with monotonic ordering.
    return {"type": kind, "monotonic": time, "wall_time": "2026-10-30T12:00:00+01:00", **fields}


def start():
    return event("PresentationStarted", 100, deck_path="/deck.tex", view="instructor", language="en")


def page(time, number, slide, version="1-0-0", step=1):
    return event("PageShown", time, page=number, slide_id=slide, version=version, step=step, steps=2)


def screen(time, state, number):
    return event("ScreenState", time, state=state, audience_page=number)


def log():
    return [start(), page(100, 1, "a"), page(110, 2, "a", step=2), page(120, 3, "b"),
            page(130, 1, "a"), page(140, 9, "c"), event("PresentationEnded", 150)]


def write_log(folder, name="logged", rows=None):
    path = folder / "presentations" / f"{name}.jsonl"
    path.parent.mkdir(exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in (rows or log())), encoding="utf-8")
    return path


def test_sessions_from_logs_with_optional_cached_course_labels(tmp_path):
    store = Sessions(Bank(tmp_path, create=True))
    write_log(tmp_path)
    sessions = store.load()
    assert store.load(schedule=None) == sessions
    logged = next(s for s in sessions if s.presentation)
    assert logged.name == "2026-10-30 12:00+01:00"
    host = FakeHost(tmp_path)
    deck = FakeDeck("course", Path("/deck.tex"))
    course = CourseSessionInfo("2026-10-30", "11:00", "13:00", "Lecture hall")

    class Course:
        def sessions(self, requested):
            assert requested == deck
            return [course]

    host.offer("course.sessions", 1, Course())
    host.finish_activation()
    schedule = host.service("course.sessions", 1).sessions(deck)
    labeled = store.load(deck=deck, schedule=schedule)
    assert next(s for s in labeled if s.presentation).name == "2026-10-30 11:00–13:00 Lecture hall"
    assert store.load(schedule=[course, course]) == sessions  # ambiguous: timestamp label
    assert store.load(schedule=[replace(course, date="2026-10-29")]) == sessions


def test_other_deck_logs_are_not_attached_to_this_deck_and_unfinished_tail_loads(tmp_path):
    store = Sessions(Bank(tmp_path, create=True))
    path = write_log(tmp_path)
    with path.open("ab") as stream:
        stream.write(b'{"type":')
    assert len(store.load(deck=FakeDeck("course", Path("/deck.tex")))) == 1
    assert store.load(deck=FakeDeck("other", Path("/other.tex"))) == []
