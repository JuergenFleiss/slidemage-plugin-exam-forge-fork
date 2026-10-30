# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from exam_forge.transcripts import Segment, parse_transcript, read_transcript


def test_srt_multiline_bom_crlf_overlaps_and_boundary_selection(tmp_path):
    path = tmp_path / "talk.srt"
    path.write_bytes(("\ufeff1\r\n00:00:01,250 --> 00:00:03,000\r\nGröße\r\nSecond line\r\n\r\n"
                      "2\r\n00:00:02,000 --> 01:00:00,000\r\nOverlap").encode())
    transcript = read_transcript(path)
    assert transcript.errors == ()
    assert transcript.segments == (Segment(1.25, 3, "Größe\nSecond line"), Segment(2, 3600, "Overlap"))
    assert transcript.text_between(3, 4) == "Overlap"
    assert transcript.text_between(0, 1.25) == ""


def test_bad_srt_cues_report_original_lines_and_keep_later_text():
    transcript = parse_transcript(
        "1\n00:00:00,000 --> 00:00:02,000\nGood\n\n"
        "2\n00:61:00,000 --> 00:62:00,000\nBad minutes\n\n"
        "3\n00:00:04,000 --> 00:00:03,000\nBackwards\n\n"
        "4\n00:00:04,000 --> 00:00:05,000\n\n"
        "garbage\n\n"
        "6\n00:00:06,000 --> 00:00:07,000\nStill usable\n", "srt")
    assert [s.text for s in transcript.segments] == ["Good", "Still usable"]
    assert [e.line for e in transcript.errors] == [6, 10, 14, 16]
    assert all(e.message for e in transcript.errors)


def test_missing_separator_does_not_quote_timestamps_as_speech():
    transcript = parse_transcript("1\n00:00:00,000 --> 00:00:02,000\nFirst\n"
                                  "2\n00:00:02,000 --> 00:00:03,000\nSecond", "srt")
    assert not transcript.segments
    assert transcript.errors[0].line == 5


def test_vtt_header_identifier_settings_and_non_cue_blocks():
    transcript = parse_transcript(
        "WEBVTT lecture\nLanguage: en\n\nNOTE ignore this\ncomment\n\n"
        "STYLE\n::cue { color: white; }\n\nREGION\nid:r1\n\n"
        "intro\n00:01.500 --> 00:03.000 align:start position:10%\nHello\n\n"
        "01:00:00.000 --> 01:00:02.000\nLater", "vtt")
    assert transcript.errors == ()
    assert transcript.segments == (Segment(1.5, 3, "Hello"), Segment(3600, 3602, "Later"))


def test_bad_vtt_missing_header_and_bad_timestamp_are_reported():
    transcript = parse_transcript("00:01.000 --> 00:02.000\nGood\n\n"
                                  "00:03,000 --> 00:04.000\nBad separator\n\n"
                                  "00:04.000 -->\nMissing end\n\n"
                                  "00:05.000 --> 00:06.000\nGood again", "vtt")
    assert [s.text for s in transcript.segments] == ["Good", "Good again"]
    assert [e.line for e in transcript.errors] == [1, 4, 7]


def test_plain_text_is_searchable_but_has_no_guessed_timestamps():
    transcript = parse_transcript("First paragraph\ncontinued\n\nSecond paragraph", "txt")
    assert transcript.segments == (Segment(None, None, "First paragraph\ncontinued"),
                                    Segment(None, None, "Second paragraph"))
    assert transcript.text_between(0, 1000) == ""
    assert not transcript.errors
    assert not parse_transcript("", "txt").segments
    assert not parse_transcript("", "srt").errors


def test_file_and_format_errors_reach_caller(tmp_path):
    with pytest.raises(ValueError, match="format"):
        parse_transcript("text", "json")
    with pytest.raises(FileNotFoundError):
        read_transcript(tmp_path / "missing.srt")
    path = tmp_path / "bad.txt"
    path.write_bytes(b"\xff")
    with pytest.raises(UnicodeDecodeError):
        read_transcript(path)
