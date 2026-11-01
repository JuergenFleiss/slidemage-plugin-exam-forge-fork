# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Word-level text diff for comparing slide versions."""
import re
from difflib import SequenceMatcher

Segments = list[tuple[str, bool]]


def _add(segments: Segments, text: str, changed: bool) -> None:
    if segments and segments[-1][1] == changed:
        segments[-1] = (segments[-1][0] + text, changed)
    else:
        segments.append((text, changed))


def word_diff(old: str, new: str) -> tuple[Segments, Segments]:
    """Return (old_segments, new_segments) of (text, changed); each side joins back to its input."""
    a, b = re.findall(r"\s+|\S+", old), re.findall(r"\s+|\S+", new)
    left, right = [], []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        changed = tag != "equal"
        if i2 > i1:
            _add(left, "".join(a[i1:i2]), changed)
        if j2 > j1:
            _add(right, "".join(b[j1:j2]), changed)
    return left, right
