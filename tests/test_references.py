# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""SlideMage asks Exam Forge whether a question still links a slide before it retires the slide's ID."""
import re

import pytest
from fake_host import FakeDeck, FakeHost, add
from slidemage.plugin_api import ReferenceCheckUnavailable

from exam_forge.folder import open_chosen_folder
from exam_forge.model import SourceLink
from exam_forge.references import slide_references


@pytest.fixture
def setup(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    host = FakeHost(project)
    deck = FakeDeck("Finance 1", project / "finance.tex")
    (tmp_path / "bank").mkdir()
    return host, deck, tmp_path / "bank"


def test_a_question_linking_the_slide_is_named_and_others_are_not(setup):
    host, deck, folder = setup
    bank = open_chosen_folder(host, deck, folder)
    add(bank, "q1", "What is  two\nplus two?", [SourceLink(slide_id="abc"), SourceLink(slide_id="abc", view="other")])
    add(bank, "q2", "Unrelated", [SourceLink(slide_id="xyz")])
    assert slide_references(host, frozenset({"abc"})) == [
        'Bank for deck "finance": question "What is two plus two?" uses abc']
    assert slide_references(host, frozenset({"nope"})) == []


def test_a_missing_bank_folder_or_unreadable_question_stops_the_check(setup):
    host, deck, folder = setup
    bank = open_chosen_folder(host, deck, folder)
    add(bank, "q1", "Question", [SourceLink(slide_id="abc")])
    (folder / "q1.json").write_text("{")
    with pytest.raises(ReferenceCheckUnavailable, match="q1.json"):
        slide_references(host, frozenset({"abc"}))
    (folder / "q1.json").unlink()
    folder.rename(folder.with_name("moved"))
    with pytest.raises(ReferenceCheckUnavailable, match=re.escape(str(folder))):
        slide_references(host, frozenset({"abc"}))


def test_a_bank_of_a_deck_in_another_project_is_ignored(setup, tmp_path):
    host, deck, folder = setup
    host.settings().set("bank_folders", {str(tmp_path / "other" / "deck.tex"): str(tmp_path / "missing")})
    assert slide_references(host, frozenset({"abc"})) == []
