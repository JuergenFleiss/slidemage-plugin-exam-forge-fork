# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
import json
from dataclasses import replace
from pathlib import Path

import pytest
from fake_host import FakeDeck, FakeHost
from slidemage.plugin_api import CourseSessionInfo

from exam_forge.bank import Bank, SchemaError
from exam_forge.passages import (
    Passage,
    Session,
    Sessions,
    SyncPoint,
    Visit,
    propose_passages,
    propose_text_matches,
    slide_visits,
)
from exam_forge.transcripts import parse_transcript


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


def ranges(passages):
    return [(p.slide_id, p.start, p.end) for p in passages]


def test_reveals_one_visit_jump_back_new_visit_skipped_slides_and_padding():
    assert slide_visits(log()) == (Visit("a", "1-0-0", 100, 120), Visit("b", "1-0-0", 120, 130),
                                   Visit("a", "1-0-0", 130, 140), Visit("c", "1-0-0", 140, 150))
    proposals = propose_passages(log())
    assert ranges(proposals) == [("a", 0, 23), ("b", 17, 33), ("a", 27, 43), ("c", 37, 53)]
    assert all(p.version == "1-0-0" and not p.confirmed for p in proposals)
    # Padding overlaps merge within a slide, never between slides.
    assert ranges(propose_passages(log(), padding=6)) == [("a", 0, 46), ("b", 14, 36), ("c", 34, 56)]


def test_audience_page_counts_while_frozen_and_only_black_and_unknown_pages_leave_gaps():
    rows = [start(), page(100, 1, "a"), screen(110, "frozen", 1), page(115, 2, "b"),
            screen(120, "shown", 1), page(125, 3, "c"), screen(130, "shown", 2),
            screen(135, "black", 2), page(138, 4, "hidden"), screen(140, "black", 4),
            screen(145, "shown", 3), screen(148, "shown", 99), event("PresentationEnded", 150)]
    assert slide_visits(rows) == (Visit("a", "1-0-0", 100, 130),
                                  Visit("b", "1-0-0", 130, 135), Visit("c", "1-0-0", 145, 148))
    assert ranges(propose_passages(rows, padding=0)) == [("a", 0, 30),
                                                       ("b", 30, 35), ("c", 45, 48)]
    assert propose_passages([start(), page(100, 1, None), event("PresentationEnded", 150)]) == ()


def test_freeze_exposure_counts_and_an_unidentified_held_page_does_not():
    rows = [start(), page(100, 1, "a"), screen(101, "frozen", 1), page(102, 2, "b"),
            screen(110, "frozen", 99), event("PresentationEnded", 120)]
    assert slide_visits(rows) == (Visit("a", "1-0-0", 100, 110),)
    proposals = propose_passages(rows, padding=0)
    assert ranges(proposals) == [("a", 0, 10)]
    assert not proposals[0].suggest_exclusion


def test_manual_sync_uses_monotonic_even_if_wall_clock_jumps():
    rows = log()
    rows[2]["wall_time"] = "2020-01-01T00:00:00+01:00"
    rows[-1]["wall_time"] = "2040-01-01T00:00:00+01:00"
    assert ranges(propose_passages(rows, sync=SyncPoint(110, 20), padding=0)) == [
        ("a", 10, 30), ("b", 30, 40), ("a", 40, 50), ("c", 50, 60)]
    assert ranges(propose_passages(rows, sync=SyncPoint(125), padding=0)) == [
        ("b", 0, 5), ("a", 5, 15), ("c", 15, 25)]


def test_versions_are_distinct_short_exposure_is_only_an_exclusion_suggestion():
    rows = [start(), page(100, 1, "a"), page(102, 2, "a", version="2-0-0"),
            event("PresentationEnded", 112)]
    proposals = propose_passages(rows, padding=10)
    assert [(p.version, p.suggest_exclusion) for p in proposals] == [("1-0-0", True), ("2-0-0", False)]
    assert len(proposals) == 2 and not any(p.confirmed for p in proposals)


def test_mapping_cues_padding_overlap_and_explicit_confirmation_or_trim():
    transcript = parse_transcript("1\n00:00:21,000 --> 00:00:22,000\nShared thought\n\n"
                                  "2\n00:00:24,000 --> 00:00:26,000\nOnly b", "srt")
    proposals = propose_passages(log(), transcript)
    assert [p.segments for p in proposals] == [(0,), (0, 1), (), ()]
    a = proposals[0].confirm()
    b = proposals[1].confirm(transcript, start=23, end=28)
    assert a.confirmed and b.confirmed and b.segments == (1,)
    assert not proposals[0].confirmed and not proposals[1].confirmed
    with pytest.raises(ValueError, match="transcript"):
        proposals[1].confirm(start=23)
    with pytest.raises(ValueError, match="within"):
        proposals[1].confirm(start=0)


def test_text_match_fallback_timed_and_untimed_needs_author_range():
    slides = {"a": "Bayesian probability prior posterior", "b": "Lecture about cellular biology"}
    plain = parse_transcript("The prior probability and posterior probability change.\n\nUnrelated anecdote", "txt")
    proposals = propose_text_matches(plain, slides)
    assert len(proposals) == 1 and proposals[0].slide_id == "a" and proposals[0].segments == (0,)
    assert proposals[0].start is None and not proposals[0].confirmed
    with pytest.raises(ValueError, match="time range"):
        proposals[0].confirm()
    assert proposals[0].confirm(start=10, end=20).confirmed
    timed = parse_transcript("00:01.000 --> 00:02.000\nPrior probability changes", "vtt")
    assert ranges(propose_text_matches(timed, slides)) == [("a", 1, 2)]
    assert len(propose_text_matches(plain, {"a": slides["a"], "same": slides["a"]})) == 2
    assert propose_text_matches(plain, {}) == ()


def test_partial_log_has_no_guessed_end_and_bad_clocks_are_refused():
    assert slide_visits(log()[:-1])[-1] == Visit("a", "1-0-0", 130, 140)
    assert propose_passages([]) == ()
    with pytest.raises(ValueError, match="begin"):
        slide_visits(log()[1:])
    for bad in (99, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="monotonic"):
            slide_visits([start(), page(bad, 1, "a")])
    for bad in (-1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            propose_passages(log(), padding=bad)
        with pytest.raises(ValueError):
            SyncPoint(100, bad)
    with pytest.raises(ValueError):
        Passage("a", None, 10, 5)


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


def test_attachments_paths_copy_open_player_and_persist_sync_and_confirmation(tmp_path):
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
    proposed = propose_passages(log(), parsed)[0]
    session = replace(session, sync=SyncPoint(100, 2), passages=(proposed, proposed.confirm(parsed)))
    store.save(session)
    assert Sessions(Bank(folder)).load() == [session]
    host = FakeHost(tmp_path)
    store.open_recording(host, session)
    assert host.opened_files == [recording]
    # Changed attachments must not retain stale confirmations/cue indices.
    assert not store.attach(session, "transcript", transcript).passages
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
