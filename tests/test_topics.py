# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
import json

import pytest

from exam_forge.bank import CorruptFileError, SchemaError, TopicStore
from exam_forge.model import (PALETTE, Item, SourceLink, Topic, add_topic, assign_slide, delete_topic, next_color,
                              rename_topic, topic_of)
from exam_forge.ui.kit import color
from fake_host import FakeHost


def item(*slides):
    return Item("q", 1, sources=tuple(SourceLink(s) for s in slides))


def test_topic_colours_follow_the_host_and_reserve_status_hues():
    host = FakeHost()
    for dark in (False, True):
        host.set_dark(dark)
        t = host.theme()
        for key in PALETTE:
            assert color(host, key) == t.accents[key]
            assert color(host, key) not in (t.warning, t.error, t.accents["violet"])
        assert color(host, "red") == t.accents["pink"]
        assert color(host, "error") == t.error


def test_legacy_red_round_trip_and_new_topics(tmp_path):
    store = TopicStore(tmp_path)
    store.save((Topic("old", "Legacy", "red"),))
    original = store.path.read_bytes()
    topics = store.load()
    assert topics[0].color == "red"
    store.save(topics)
    assert store.path.read_bytes() == original
    for n in range(30):
        assert next_color(topics) in PALETTE and next_color(topics) != "red"
        topics = add_topic(topics, f"Topic {n}")


def test_topic_validation():
    for args in (("", "A", "blue"), ("i", "  ", "blue"), ("i", "A", "mauve"),
                 ("i", "A", "blue", ("s", "s")), ("i", "A", "blue", (1,))):
        with pytest.raises(ValueError):
            Topic(*args)


def test_store_round_trip_and_refusals(tmp_path):
    store = TopicStore(tmp_path)
    assert store.load() == ()
    for conflict in ((Topic("a", "A", "blue", ("s",)), Topic("b", "B", "red", ("s",))),
                     (Topic("a", "Cells", "blue"), Topic("b", " cells ", "red"))):
        with pytest.raises(ValueError):
            store.save(conflict)
    assert not store.path.exists()
    topics = (Topic("a", "Cells", "blue", ("s1", "s2")), Topic("b", "Genes", "red"))
    store.save(topics)
    assert store.load() == topics and [p.name for p in tmp_path.iterdir()] == ["topics.json"]
    assert json.loads((tmp_path / "topics.json").read_text("utf-8"))["topics"][0] == {
        "id": "a", "name": "Cells", "color": "blue", "slides": ["s1", "s2"]}
    path = tmp_path / "topics.json"
    path.write_text(json.dumps({"schema_version": 2, "topics": []}), "utf-8")
    with pytest.raises(SchemaError):
        store.load()
    row = {"id": "a", "name": "A", "color": "blue", "slides": []}
    for bad in ("{nope", {"schema_version": 1}, {"schema_version": 1, "topics": [{**row, "color": "x"}]},
                {"schema_version": 1, "topics": [row, {**row, "id": "b"}]}):
        path.write_text(bad if isinstance(bad, str) else json.dumps(bad), "utf-8")
        with pytest.raises(CorruptFileError):
            store.load()


def test_helpers():
    topics = add_topic((), " Cells ")
    assert topics[0].name == "Cells" and topics[0].color == PALETTE[0] and len(topics[0].id) == 32
    for n in range(7):
        topics = add_topic(topics, f"T{n}")
    assert topics[len(PALETTE)].color == PALETTE[0] and len({t.id for t in topics}) == 8
    assert next_color(topics[:2]) == PALETTE[2] and next_color(()) == PALETTE[0]
    a, b = topics[:2]
    renamed = rename_topic(topics, a.id, "Renamed")
    assert renamed[0].name == "Renamed" and renamed[1] == b and topics[0].name == "Cells"
    assert delete_topic(topics, a.id) == topics[1:]
    t = assign_slide(topics, a.id, "s1")
    assert t[0].slides == ("s1",)
    t = assign_slide(t, b.id, "s1")
    assert t[0].slides == () and t[1].slides == ("s1",)
    assert assign_slide(t, None, "s1")[1].slides == ()


def test_topic_of_first_slide_only():
    topics = (Topic("a", "A", "blue", ("s2",)), Topic("b", "B", "red", ("s1",)))
    assert topic_of(item("s1", "s2"), topics).id == "b"
    assert topic_of(item("s3", "s2"), topics) is None
    assert topic_of(item(), topics) is None
    assert topic_of(item("s2"), topics).name == "A"



def test_saved_red_topic_dot_draws_pink(lab):
    from dataclasses import replace
    from PySide6.QtGui import QPalette
    from exam_forge.ui.kit import Mark
    from fake_host import entry

    host, _, _, _, window, _, _, topics = lab
    assert window.save_topics((replace(topics[0], color="red"), *topics[1:]))
    for dark in (False, True):
        host.set_dark(dark)
        listw = window.questions.topic_list
        widget = listw.itemWidget(entry(listw, topics[0].id))
        mark = widget.findChild(Mark)
        mark.ensurePolished()
        assert mark.palette().color(QPalette.ColorRole.WindowText).name() == host.theme().accents["pink"]
