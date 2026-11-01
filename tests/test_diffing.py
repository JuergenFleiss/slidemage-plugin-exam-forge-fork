# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from exam_forge.diffing import word_diff


def test_changed_words_are_flagged_and_adjacent_segments_merge():
    old, new = word_diff("The cat sat on the mat", "The dog sat on the red mat")
    assert old == [("The ", False), ("cat", True), (" sat on the mat", False)]
    assert new == [("The ", False), ("dog", True), (" sat on the ", False), ("red ", True), ("mat", False)]


@pytest.mark.parametrize("old,new", [
    ("", ""), ("", "new text"), ("old text", ""), ("same\n\n text", "same\n\n text"),
    (" a  b\r\n", "a b"), ("α β γ", "α γ δ"),
])
def test_both_sides_reproduce_their_input(old, new):
    left, right = word_diff(old, new)
    assert "".join(t for t, _ in left) == old and "".join(t for t, _ in right) == new
    for side in (left, right):
        assert all(a[1] != b[1] for a, b in zip(side, side[1:]))
    if old == new:
        assert not any(c for _, c in left + right)
