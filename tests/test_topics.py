# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
import json

import pytest

from exam_forge.bank import CorruptFileError, SchemaError
from exam_forge.model import Item, SourceLink
from exam_forge.topics import (PALETTE, Topic, TopicStore, add_topic, assign_slide, delete_topic,
                               migrate, rename_topic, topic_color, topic_name, topic_of)


def item(topic="", *slides, id="q"):
    return Item(id, 1, topic=topic, sources=tuple(SourceLink(s) for s in slides))


def test_topic_colours_never_use_the_violet_or_amber_accents():
    for key in PALETTE:
        assert topic_color(key, True) != topic_color(key, False)
        for dark in (False, True):
            assert topic_color(key, dark).lower() not in ("#7040ac", "#c4b5fd", "#8a5000", "#fbbf24")


def test_topic_validation():
    for args in (("", "A", "blue"), ("i", "  ", "blue"), ("i", "A", "mauve"),
                 ("i", "A", "blue", ("s", "s")), ("i", "A", "blue", (1,))):
        with pytest.raises(ValueError):
            Topic(*args)


def test_store_round_trip_and_refusals(tmp_path):
    store = TopicStore(tmp_path)
    assert not store.exists() and store.load() == ()
    for conflict in ((Topic("a", "A", "blue", ("s",)), Topic("b", "B", "red", ("s",))),
                     (Topic("a", "Cells", "blue"), Topic("b", " cells ", "red"))):
        with pytest.raises(ValueError):
            store.save(conflict)
    assert not store.exists()
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
    assert topics[6].color == PALETTE[0] and len({t.id for t in topics}) == 8
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
    assert topic_of(item("", "s1", "s2"), topics).id == "b"
    assert topic_of(item("", "s3", "s2"), topics) is None
    assert topic_of(item(""), topics) is None
    assert topic_name(item("", "s2"), topics) == "A"
    assert topic_name(item(""), topics) == ""


def test_migrate_majority_tie_and_conflicts():
    items = (item("Z", "s1", id="1"), item("Z", "s1", id="2"), item("A", "s1", id="3"),
             item("B", "s2", id="4"), item("Z", "s4", id="8"), item("Z", "s4", id="9"),
             item("A", "s4", id="a"), item("A", "s5", id="b"), item("A", "s5", "s4", id="c"),
             item(" ", "s9", id="7"))
    topics, notes = migrate(items)
    by = {t.name: t for t in topics}
    assert set(by) == {"A", "B", "Z"}
    assert by["Z"].slides == ("s1",)          # 2 votes against 1
    assert by["B"].slides == ("s2",)
    assert by["A"].slides == ("s4", "s5")     # s4: A 2 vs Z 2 tie -> alphabetically first
    assert [n.split(" is ")[0] for n in notes] == ["Slide s1", "Slide s4"]
    assert all("\n" not in n for n in notes)
    assert "s9" not in "".join(t for tp in topics for t in tp.slides)


def test_migrate_unlinked():
    topics, notes = migrate((item("Solo"), item("Solo", id="b"), item("")))
    assert [(t.name, t.slides) for t in topics] == [("Solo", ())]
    assert len(notes) == 1 and notes[0].startswith("2 questions")
    assert migrate(()) == ((), ())
    topics, notes = migrate((item("T", "s"),))
    assert topics[0].slides == ("s",) and notes == ()
