# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""A deck's answer rules: the store, the checks, scoring, Moodle and ELP output, and the Settings section."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import re
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QSpinBox
from slidemage.plugin_api import Focus
from test_questions_tab import (  # noqa: F401 - shared fixtures
    add,
    keys,
    lab,
    make_slides,
    pick,
    row_text,
)
from test_ui import app, env  # noqa: F401 - shared real-window fixtures

from exam_forge.answer_rules import AnswerRulesStore
from exam_forge.bank import Bank, CorruptFileError
from exam_forge.elp import ElpRules, check_elp, scoring_table
from exam_forge.elp_document import export_elp_documents
from exam_forge.manifest import freeze
from exam_forge.model import AnswerRule, AnswerRules, Item, Option, check_phase1
from exam_forge.moodle_xml import export_moodle_xml


def question(n, correct, use="revision"):
    return Item(f"q{n}{correct}", 1, "Stem", tuple(Option(f"o{i}", f"Answer {i}", i < correct, "Why")
                                                  for i in range(n)), use=use, difficulty="easy")


def test_rules_refuse_what_cannot_work():
    for args in ((1, 4, 1, 1), (5, 4, 1, 1), (2, 11, 1, 1), (2, 4, 0, 1), (2, 4, 2, 1), (2, 4, 1, 4)):
        with pytest.raises(ValueError):
            AnswerRule(*args)
    with pytest.raises(ValueError, match="same number"):
        AnswerRules(elp=AnswerRule(4, 5, 1, 3))
    with pytest.raises(ValueError, match="With 6 answers and 1 correct, the 12 points of an easy question"):
        ElpRules(answers=AnswerRule(6, 6, 1, 4))
    assert ElpRules(answers=AnswerRule(6, 6, 2, 3)).answers.max_options == 6


def test_checks_follow_the_rule():
    multi = AnswerRule(3, 6, 1, 2)
    assert check_phase1(question(4, 2), multi) == [] and check_phase1(question(4, 2))
    assert check_phase1(question(2, 1), multi) == ["A revision-quiz question must have three to six answers."]
    assert check_phase1(question(3, 3), multi) == ["A revision-quiz question must mark one to two correct answers."]
    assert check_elp(question(4, 2, "elp"), AnswerRule(4, 4, 1, 3)) == [] and check_elp(question(4, 2, "elp"))


def test_scoring_follows_the_number_of_answers():
    rows = scoring_table(24, AnswerRule(4, 4, 1, 3))
    assert [(r.correct_options, r.correct_tick, r.wrong_tick) for r in rows] == [(1, 24, -8), (2, 12, -12), (3, 8, -24)]
    assert [r.correct_options for r in scoring_table(12, AnswerRule(6, 6, 2, 3))] == [2, 3]


def test_store_defaults_round_trip_and_corrupt_file(tmp_path):
    bank = Bank(tmp_path / "bank", create=True)
    store = AnswerRulesStore(bank.folder)
    assert store.load() == AnswerRules()
    rules = AnswerRules(AnswerRule(3, 6, 1, 2), AnswerRule(4, 4, 1, 3))
    store.save(rules)
    assert AnswerRulesStore(bank.folder).load() == rules
    assert bank.folder / "answer-rules.json" in bank.files()   # copied when the deck's folder changes
    with pytest.raises(ValueError):
        store.save(AnswerRules(elp=AnswerRule(6, 6, 1, 4)))
    assert store.load() == rules
    store.path.write_text(json.dumps({"schema_version": 1, "revision": {"min_options": 1}}))
    with pytest.raises(CorruptFileError):
        store.load()


def test_moodle_single_and_multiple_response():
    xml = export_moodle_xml([question(3, 1)]).decode()
    assert "<single>true</single>" in xml and re.findall(r'fraction="([^"]+)"', xml) == ["100", "0", "0"]
    xml = export_moodle_xml([question(4, 2)], rule=AnswerRule(3, 6, 1, 2)).decode()
    assert "<single>false</single>" in xml and re.findall(r'fraction="([^"]+)"', xml) == ["50", "50", "-50", "-50"]
    xml = export_moodle_xml([question(3, 1)], rule=AnswerRule(3, 6, 1, 2)).decode()
    assert re.findall(r'fraction="([^"]+)"', xml) == ["100", "-50", "-50"]
    with pytest.raises(ValueError, match="mark exactly one correct answer"):
        export_moodle_xml([question(4, 2)])


def test_an_elp_exam_with_four_answers(tmp_path):
    bank = Bank(tmp_path / "bank", create=True)
    for n, difficulty in ((1, "easy"), (3, "hard")):
        item = replace(question(4, n, "elp"), id=f"q{n}", difficulty=difficulty)
        bank.create(item)
        bank.set_status(item.id, 1, "reviewed")
    rules = ElpRules(answers=AnswerRule(4, 4, 1, 3))
    manifest = freeze(None, None, bank, [("q1", 2), ("q3", 2)], {}, rules, topic_of=lambda i: "t")
    assert manifest.data()["scoring"]["rules"]["answers"]["max_options"] == 4
    _, key = export_elp_documents(manifest)
    assert "four options per question" in key and "−P/(4−n)" in key
    with pytest.raises(ValueError, match="exactly five answers"):
        freeze(None, None, bank, [("q1", 2)], {}, ElpRules(), topic_of=lambda i: "t")


def test_settings_change_the_open_windows_rules(lab):
    host, deck, bank, _, window, *_ = lab
    window.card_opened(window.cards.item(0))       # q1, reviewed, one of two answers correct
    host.current_focus = Focus(deck.path, None, "slide")
    page = host.settings_tabs["Exam Forge"]()
    rev_min, rev_max, _, rev_high, elp_count, _, elp_high = page.findChildren(QSpinBox)
    assert [rev_min.value(), rev_max.value(), rev_high.value(), elp_count.value(), elp_high.value()] == [2, 8, 1, 5, 4]
    rev_min.setValue(3)
    assert AnswerRulesStore(bank.folder).load().revision == AnswerRule(3, 8, 1, 1)
    assert window.answer_rules.revision.min_options == 3
    assert "Off the answer rules" in row_text(window.cards, "q1") and "attention" in keys(window.topic_list)
    assert window.status_line.text().startswith("Reviewed, but it no longer fits the answer rules: have three")
    assert "three to eight answers" in window.hint.text()
    pick(window.topic_list, "attention")
    assert "q1" in keys(window.slide_list)
    elp_count.setValue(6)                          # 12 points cannot be split for one correct of six
    notice = next(label for label in page.findChildren(type(page.ai_notice)) if label.text().startswith("Not saved"))
    assert "With 6 answers" in notice.text() and AnswerRulesStore(bank.folder).load().elp.max_options == 5
    next(b for b in page.findChildren(type(window.write_button)) if b.text() == "Default answer rules").click()
    assert AnswerRulesStore(bank.folder).load() == AnswerRules() and window.answer_rules == AnswerRules()
    assert rev_min.value() == 2 and not notice.text()


def test_moodle_export_leaves_out_reviewed_questions_off_the_rules(lab, tmp_path, monkeypatch):
    host, _, bank, _, window, _, link, _ = lab
    bank.create(replace(question(3, 1), id="q9", sources=(link["a"],)))
    bank.set_status("q9", 1, "reviewed")
    window.reload()
    AnswerRulesStore(bank.folder).save(AnswerRules(revision=AnswerRule(3, 8, 1, 1)))
    window.answer_rules_changed()
    exported = []
    monkeypatch.setattr("exam_forge.ui.questions_tab.export_moodle_xml",
                        lambda items, bank, rule: exported.extend(items) or b"<quiz/>")
    monkeypatch.setattr("exam_forge.ui.questions_tab.QFileDialog.getSaveFileName",
                        lambda *a: (str(tmp_path / "o.xml"), ""))
    window.export_button.click()
    window.export_menu.moodle_action.trigger()
    host.drain()
    assert [i.id for i in exported] == ["q9"]
    assert "2 reviewed questions that no longer fit the answer rules were left out." in window.notice.text()
