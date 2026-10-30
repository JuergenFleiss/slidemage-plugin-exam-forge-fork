# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read externally made transcripts; keep usable cues and report bad lines."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Segment:
    start: float | None
    end: float | None
    text: str


@dataclass(frozen=True)
class TranscriptError:
    line: int
    message: str


@dataclass(frozen=True)
class Transcript:
    """Parsed cues and errors."""

    segments: tuple[Segment, ...]
    errors: tuple[TranscriptError, ...] = ()

    def text_between(self, start: float, end: float) -> str:
        """Whole cues overlapping a range, without inventing word timestamps."""
        return "\n".join(s.text for s in self.segments
                         if s.start is not None and s.start < end and s.end > start)


def _timestamp(value: str, format: str) -> float:
    pattern = (r"(\d{2,}):(\d{2}):(\d{2}),(\d{3})" if format == "srt"
               else r"(?:(\d{2,}):)?(\d{2}):(\d{2})\.(\d{3})")
    match = re.fullmatch(pattern, value)
    if match is None:
        raise ValueError(f"Invalid timestamp: {value}")
    hours, minutes, seconds, millis = (int(n or 0) for n in match.groups())
    if minutes >= 60 or seconds >= 60:
        raise ValueError(f"Invalid timestamp: {value}")
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def parse_transcript(text: str, format: str) -> Transcript:
    """Parse SRT, WebVTT or TXT. Plain text paragraphs deliberately stay untimed.

    Error line numbers are 1-based. Bad timing/text omits a cue; bad numbering
    is reported but retains usable text. Later blocks still load. Overlapping
    and out-of-order cues are legitimate.
    """
    format = format.lower().lstrip(".")
    if format not in ("srt", "vtt", "txt"):
        raise ValueError("Transcript format must be srt, vtt or txt")
    lines = text.lstrip("\ufeff").splitlines()
    blocks = []
    block = []
    for number, line in enumerate(lines, 1):
        if line.strip():
            block.append((number, line))
        elif block:
            blocks.append(block)
            block = []
    if block:
        blocks.append(block)
    if format == "txt":
        return Transcript(tuple(Segment(None, None, "\n".join(line for _, line in block))
                                for block in blocks))

    segments, errors = [], []
    if format == "vtt":
        if blocks and re.fullmatch(r"WEBVTT(?:[ \t].*)?", blocks[0][0][1]):
            blocks.pop(0)  # header and optional header metadata
        else:
            errors.append(TranscriptError(1, "Missing WEBVTT header"))
    for block in blocks:
        first = block[0][1]
        if format == "vtt" and (first in ("STYLE", "REGION") or re.match(r"NOTE(?:\s|$)", first)):
            continue
        timing = 0
        if "-->" not in first and len(block) > 1 and "-->" in block[1][1]:
            if format == "srt" and not first.strip().isdigit():
                errors.append(TranscriptError(block[0][0], "Invalid SRT cue number"))
            timing = 1
        number, line = block[timing]
        try:
            left, arrow, right = line.partition("-->")
            if not arrow:
                raise ValueError("Expected a timestamp range")
            start = _timestamp(left.strip(), format)
            end_value = right.strip().split()
            if not end_value:
                raise ValueError("Missing end timestamp")
            end = _timestamp(end_value[0] if format == "vtt" else right.strip(), format)
            if end <= start:
                raise ValueError("End timestamp must be after start")
            body = block[timing + 1:]
            # A missing blank line must not turn another cue into quoted speech.
            bad = next(((n, line) for n, line in body if "-->" in line), None)
            if bad:
                errors.append(TranscriptError(bad[0], "Missing blank line between cues"))
                continue
            if not body:
                raise ValueError("Cue has no text")
            segments.append(Segment(start, end, "\n".join(line for _, line in body)))
        except ValueError as exc:
            errors.append(TranscriptError(number, str(exc)))
    return Transcript(tuple(segments), tuple(errors))


def read_transcript(path: str | Path) -> Transcript:
    """Read UTF-8 (including BOM). File and decoding errors reach the caller."""
    path = Path(path)
    return parse_transcript(path.read_text(encoding="utf-8-sig"), path.suffix)
