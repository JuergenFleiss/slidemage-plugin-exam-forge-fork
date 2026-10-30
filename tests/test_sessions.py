# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
import json
from dataclasses import replace
from pathlib import Path

import pytest
from fake_host import FakeDeck, FakeHost
from slidemage.plugin_api import CourseSessionInfo

from exam_forge.bank import Bank, SchemaError
from exam_forge.sessions import Session, Sessions


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


def test_sessions_from_logs_and_author_names_with_optional_cached_course_labels(tmp_path):
    store = Sessions(Bank(tmp_path, create=True))
    write_log(tmp_path)
    named = Session("Guest lecture")
    store.save(named)
    sessions = store.load()
    assert store.load(schedule=None) == sessions
    logged = next(s for s in sessions if s.presentation)
    assert logged.name == "2026-10-30 12:00+01:00" and named in sessions
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
    assert named in labeled
    assert store.load(schedule=[course, course]) == sessions  # ambiguous: timestamp label
    assert store.load(schedule=[replace(course, date="2026-10-29")]) == sessions
    store.save(replace(logged, name="Author's title"))
    assert next(s for s in store.load(schedule=schedule) if s.presentation).name == "Author's title"
    assert len(store.load()) == 2


def test_attachments_paths_copy_open_player_and_persist(tmp_path):
    folder = tmp_path / "bank"
    store = Sessions(Bank(folder, create=True))
    recording = tmp_path / "talk.wav"
    recording.write_bytes(b"audio")
    transcript = tmp_path / "talk.srt"
    transcript.write_text("1\n00:00:00,000 --> 00:00:20,000\nA thought", encoding="utf-8")
    session = store.attach(Session("Named session"), "recording", recording)
    assert session.recording == str(recording)
    session = store.attach(session, "transcript", transcript, copy=True)
    assert not Path(session.transcript).is_absolute()
    assert store.path(session.transcript).read_bytes() == transcript.read_bytes()
    parsed = store.read_transcript(session)
    assert parsed.segments[0].text == "A thought"
    store.save(session)
    assert Sessions(Bank(folder)).load() == [session]
    host = FakeHost(tmp_path)
    store.open_recording(host, session)
    assert host.opened_files == [recording]
    assert store.attach(session, "transcript", transcript).transcript == str(transcript)
    with pytest.raises(FileNotFoundError):
        store.attach(session, "recording", tmp_path / "missing")
    with pytest.raises(ValueError, match="kind"):
        store.attach(session, "other", recording)
    with pytest.raises(ValueError, match="No attachment"):
        store.open_recording(host, Session("No recording"))
    saved = folder / "sessions" / f"{session.id}.json"
    saved.write_text('{"schema_version": 999}', encoding="utf-8")
    with pytest.raises(SchemaError):
        store.load()


def test_other_deck_logs_are_not_attached_to_this_deck_and_unfinished_tail_loads(tmp_path):
    store = Sessions(Bank(tmp_path, create=True))
    path = write_log(tmp_path)
    with path.open("ab") as stream:
        stream.write(b'{"type":')
    assert len(store.load(deck=FakeDeck("course", Path("/deck.tex")))) == 1
    assert store.load(deck=FakeDeck("other", Path("/other.tex"))) == []
    with pytest.raises(ValueError):
        Session("", id="valid")
    with pytest.raises(ValueError):
        Session("Title", id="../escape")
