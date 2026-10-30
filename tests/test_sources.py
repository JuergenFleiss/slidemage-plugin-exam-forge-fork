# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Source changes, unavailable sources and the worker/UI boundary against FakeHost."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import get_ident
from types import SimpleNamespace

import pytest
from fake_host import FakeDeck, FakeHost
from slidemage.plugin_api import MediaDescription, SlideRef, SlideSource

from exam_forge.bank import Bank, ConflictError, CorruptFileError, SchemaError
from exam_forge.model import Item
from exam_forge.sources import check_bank, check_item, fingerprint, link_slide


class BackgroundHost(FakeHost):
    """Queue first, then run work on a real worker and deliver done on this thread."""

    def __init__(self, root):
        super().__init__(root)
        self.pending = []
        self.deck_reads = []
        self.source_reads = []

    def run_in_background(self, work, done):
        self.pending.append((work, done))

    def complete(self):
        work, done = self.pending.pop(0)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(work)
            error = future.exception()
            result = None if error is not None else future.result()
        done(result, error)

    def deck_slides(self, deck, view, language):
        self.deck_reads.append((get_ident(), view, language))
        return super().deck_slides(deck, view, language)

    def slide_source(self, ref):
        self.source_reads.append((get_ident(), ref))
        return super().slide_source(ref)


@pytest.fixture
def env(tmp_path):
    host = BackgroundHost(tmp_path)
    deck = FakeDeck("course", tmp_path / "course.tex")
    media = tmp_path / "plot.png"
    media.write_bytes(b"\x00\xffpicture\r\n")
    source = SlideSource(text="Grüße α\r\n", media=(media,), descriptions=(
        MediaDescription(image="media/plot.png", short="A plot", long="Values", takeaway="Rising"),
    ))
    ref = SlideRef(id="a", id_slug="a-EN-title", path=tmp_path / "a-EN-title.tex",
                   language="en", language_read="en", version="1-0-0", position=1)
    host.slides[(deck.path, "instructor", "en")] = [ref]
    host.sources[ref] = source
    return SimpleNamespace(host=host, deck=deck, ref=ref, source=source)


def question(env, item_id="q1", **changes):
    return Item(item_id, 1, sources=(link_slide(env.host, env.ref, "instructor"),), **changes)


def make_bank(tmp_path, *items):
    bank = Bank(tmp_path / "bank", create=True)
    for item in items:
        bank.create(item)
    return bank


def checked(env, item):
    return check_item(env.host, env.deck, item).sources[0]


def run_bank(env, bank, **kwargs):
    completed = []
    check_bank(env.host, env.deck, bank, lambda result, error: completed.append((result, error)), **kwargs)
    assert not completed
    env.host.complete()
    assert len(completed) == 1
    return completed[0]


def test_link_and_unchanged_content(env):
    item = question(env)
    link = item.sources[0]
    assert (link.slide_id, link.version, link.language, link.view) == ("a", "1-0-0", "en", "instructor")
    assert len(link.fingerprint) == 64
    result = checked(env, item)
    assert result.status == "unchanged" and result.fingerprint == link.fingerprint
    assert not check_item(env.host, env.deck, item).source_changed


def test_changed_text_in_overwritten_pinned_version(env):
    env.ref = replace(env.ref, pinned=True, path=env.ref.path.parent / "versions" / "a-v1-0-0.tex")
    env.host.slides[(env.deck.path, "instructor", "en")] = [env.ref]
    env.host.sources[env.ref] = env.source
    item = question(env)
    # Same permanent ID, version number and frozen path; bytes were overwritten.
    env.host.sources[env.ref] = replace(env.source, text="Overwritten source")
    result = checked(env, item)
    assert result.status == "changed"
    assert result.fingerprint != item.sources[0].fingerprint
    assert item.sources[0].version == env.ref.version


def test_media_bytes_change_even_at_same_size_and_mtime(env):
    item = question(env)
    media = env.source.media[0]
    stat = media.stat()
    media.write_bytes(b"x" * stat.st_size)
    os.utime(media, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert checked(env, item).status == "changed"


@pytest.mark.parametrize("field,value", [
    ("image", "tikz:drawing"), ("decorative", True), ("short", "New short"),
    ("long", "New long"), ("takeaway", "New takeaway"),
])
def test_every_description_field_is_checked(env, field, value):
    item = question(env)
    description = replace(env.source.descriptions[0], **{field: value})
    env.host.sources[env.ref] = replace(env.source, descriptions=(description,))
    assert checked(env, item).status == "changed"


def test_fingerprint_encoding_is_stable_and_independent_of_absolute_paths(env, tmp_path):
    copied = tmp_path / "other-root" / "plot.png"
    copied.parent.mkdir()
    copied.write_bytes(env.source.media[0].read_bytes())
    digest = fingerprint(env.source)
    assert digest == fingerprint(replace(env.source, media=(copied,)))
    # Fixed format fixture guards saved baselines across releases and processes.
    assert digest == "24691125f7ddfb325613aaaaa684b43f4ff0c2a92d447bfd738bba12310f81ed"


def test_encoding_preserves_text_and_sequence_boundaries(tmp_path):
    one, two, combined = (tmp_path / name for name in ("one", "two", "combined"))
    one.write_bytes(b"ab")
    two.write_bytes(b"c")
    combined.write_bytes(b"abc")
    assert fingerprint(SlideSource("", (one, two))) != fingerprint(SlideSource("", (combined,)))
    assert fingerprint(SlideSource("", (one, two))) != fingerprint(SlideSource("", (two, one)))
    assert fingerprint(SlideSource("a\r\n")) != fingerprint(SlideSource("a\n"))
    assert fingerprint(SlideSource("é")) != fingerprint(SlideSource("e\u0301"))
    assert fingerprint(SlideSource("a\x00b")) != fingerprint(SlideSource("ab"))


@pytest.mark.parametrize("text,status", [("Grüße α\r\n", "unchanged"), ("new", "changed")])
def test_follows_deck_ref_and_uses_content_not_version_label(env, text, status):
    item = question(env)
    new_ref = replace(env.ref, version="2-0-0", path=env.ref.path.with_name("a-v2.tex"))
    env.host.slides[(env.deck.path, "instructor", "en")] = [new_ref]
    env.host.sources[new_ref] = replace(env.source, text=text)
    assert checked(env, item).status == status
    assert env.host.source_reads[-1][1] == new_ref


def test_removed_slide_is_distinct_and_not_read_from_old_ref(env, tmp_path):
    item = question(env)
    bank = make_bank(tmp_path, item)
    env.host.slides[(env.deck.path, "instructor", "en")] = []
    env.host.source_reads.clear()
    report, error = run_bank(env, bank)
    assert error is None
    result = report.items[0]
    assert result.sources[0].status == "missing"
    assert result.sources[0].fingerprint is None and result.sources[0].message
    assert result.source_changed and bank.get(item.id).source_changed
    assert not env.host.source_reads


def test_unresolved_deck_is_distinct_from_missing_slide(env, tmp_path, monkeypatch):
    item = question(env)
    bank = make_bank(tmp_path, item)

    def unresolved(*args):
        raise ValueError("The deck's slides cannot be worked out: custom command")

    monkeypatch.setattr(env.host, "deck_slides", unresolved)
    report, error = run_bank(env, bank)
    assert error is None
    source = report.items[0].sources[0]
    assert source.status == "unresolved" and "custom command" in source.message
    assert source.fingerprint is None and bank.get(item.id).source_changed


@pytest.mark.parametrize("failure", ["source", "media", "deck"])
def test_read_failures_are_reported_and_set_marker(env, tmp_path, monkeypatch, failure):
    item = question(env)
    bank = make_bank(tmp_path, item)

    def unreadable(*args):
        raise OSError("unreadable file")

    if failure == "media":
        env.source.media[0].unlink()
    else:
        monkeypatch.setattr(env.host, "slide_source" if failure == "source" else "deck_slides", unreadable)
    report, error = run_bank(env, bank)
    assert error is None
    source = report.items[0].sources[0]
    assert source.status == "error" and source.message and source.fingerprint is None
    assert bank.get(item.id).source_changed


def test_bank_check_is_background_and_persists_without_new_revision(env, tmp_path, monkeypatch):
    item = question(env)
    bank = make_bank(tmp_path, item)
    reviewed = bank.set_status(item.id, 1, "reviewed")
    before = json.loads((bank.folder / "q1.json").read_text())["revisions"]
    env.host.sources[env.ref] = replace(env.source, text="New source")
    env.host.source_reads.clear()
    main_thread, flag_threads, callbacks = get_ident(), [], []
    setter = bank.set_source_changed

    def set_flag(*args, **kwargs):
        flag_threads.append(get_ident())
        return setter(*args, **kwargs)

    monkeypatch.setattr(bank, "set_source_changed", set_flag)
    check_bank(env.host, env.deck, bank,
               lambda report, error: callbacks.append((get_ident(), report, error)))
    assert len(env.host.pending) == 1 and not callbacks and not env.host.source_reads
    assert not bank.get(item.id).source_changed
    env.host.complete()
    callback_thread, report, error = callbacks[0]
    assert callback_thread == main_thread and error is None and not report.errors
    assert env.host.source_reads[0][0] != main_thread and flag_threads[0] != main_thread
    assert env.host.deck_reads[-1][0] != main_thread
    saved = Bank(bank.folder).get(item.id)
    assert saved == replace(reviewed, source_changed=True)
    assert report.items[0].item == saved
    assert json.loads((bank.folder / "q1.json").read_text())["revisions"] == before
    assert saved.sources == item.sources  # Checks do not accept the new baseline.


def test_check_now_recovers_after_restoring_source_and_can_check_subset(env, tmp_path):
    item = question(env)
    bank = make_bank(tmp_path, item, question(env, "q2", source_changed=True))
    env.host.sources[env.ref] = replace(env.source, text="Changed")
    first, error = run_bank(env, bank, item_ids=[item.id, item.id])
    assert error is None and len(first.items) == 1 and first.items[0].source_changed
    env.host.sources[env.ref] = env.source
    second, error = run_bank(env, bank, item_ids=[item.id])
    assert error is None and not second.items[0].source_changed
    assert not bank.get(item.id).source_changed
    assert bank.get("q2").source_changed  # Approve/freeze can limit their check.
    assert bank.get(item.id).revision == 1


def test_multiple_links_flag_item_and_empty_sources_clear_old_marker(env, tmp_path):
    item = question(env)
    other = replace(item.sources[0], slide_id="removed")
    bank = make_bank(tmp_path, replace(item, sources=(*item.sources, other)),
                     Item("unlinked", 1, source_changed=True))
    report, error = run_bank(env, bank)
    assert error is None
    assert [source.status for source in report.items[0].sources] == ["unchanged", "missing"]
    assert bank.get(item.id).source_changed
    assert not bank.get("unlinked").source_changed


def test_reading_language_and_view_survive_fallback_and_multilingual_refs(env):
    for file_language in ("de", "mult", None):
        ref = replace(env.ref, language=file_language)
        env.host.sources[ref] = env.source
        env.host.slides[(env.deck.path, "after-course", "en")] = [ref]
        link = link_slide(env.host, ref, "after-course")
        assert link.language == "en"
        assert check_item(env.host, env.deck, Item("q", 1, sources=(link,))).sources[0].status == "unchanged"
        assert env.host.deck_reads[-1][1:] == ("after-course", "en")
        assert env.host.source_reads[-1][1] == ref
    unversioned = replace(env.ref, version=None)
    env.host.sources[unversioned] = env.source
    assert link_slide(env.host, unversioned, "instructor").version == ""


def test_link_rejects_unknown_reading_context(env):
    with pytest.raises(ValueError, match="reading language"):
        link_slide(env.host, replace(env.ref, language="mult", language_read=None), "instructor")
    with pytest.raises(ValueError, match="view"):
        link_slide(env.host, env.ref, "unknown")


def test_repeated_slide_calls_disambiguate_saved_version_and_do_not_guess(env):
    item = question(env)
    second = replace(env.ref, path=env.ref.path.with_name("a-v2.tex"), version="2-0-0", position=2)
    env.host.sources[second] = replace(env.source, text="Another version")
    env.host.slides[(env.deck.path, "instructor", "en")] = [second, env.ref]
    assert checked(env, item).status == "unchanged"
    # Repeated calls to the same file are harmless, including hidden calls.
    env.host.slides[(env.deck.path, "instructor", "en")] = [env.ref, replace(env.ref, position=2, hidden=True)]
    assert checked(env, item).status == "unchanged"
    # Neither of two distinct remaining versions identifies the original link.
    third = replace(second, version="3-0-0", path=env.ref.path.with_name("a-v3.tex"))
    env.host.slides[(env.deck.path, "instructor", "en")] = [second, third]
    result = checked(env, item)
    assert result.status == "error" and "ambiguous" in result.message
    assert check_item(env.host, env.deck, item).source_changed


def test_shared_sources_are_read_once_per_check_but_rechecked_next_time(env, tmp_path):
    bank = make_bank(tmp_path, question(env), question(env, "q2"))
    env.host.source_reads.clear()
    report, error = run_bank(env, bank)
    assert error is None and len(report.items) == 2
    assert len(env.host.source_reads) == len(env.host.deck_reads) == 1
    run_bank(env, bank)
    assert len(env.host.source_reads) == len(env.host.deck_reads) == 2


def test_corrupt_items_reported_while_other_items_checked(env, tmp_path):
    bank = make_bank(tmp_path, question(env), question(env, "broken"))
    (bank.folder / "broken.json").write_text("{broken", encoding="utf-8")
    report, error = run_bank(env, bank)
    assert error is None
    assert [row.item.id for row in report.items] == ["q1"]
    assert len(report.errors) == 1 and isinstance(report.errors[0], CorruptFileError)


@pytest.mark.parametrize("failure", ["new_schema", "write", "unexpected"])
def test_worker_errors_reach_callback(env, tmp_path, monkeypatch, failure):
    bank = make_bank(tmp_path, question(env))
    if failure == "new_schema":
        path = bank.folder / "q1.json"
        data = json.loads(path.read_text())
        data["schema_version"] = 999
        path.write_text(json.dumps(data))
        expected = SchemaError
    else:
        expected = OSError if failure == "write" else RuntimeError

        def fail(*args, **kwargs):
            raise expected("failed")

        monkeypatch.setattr(bank if failure == "write" else env.host,
                            "set_source_changed" if failure == "write" else "slide_source", fail)
    report, error = run_bank(env, bank)
    assert report is None and isinstance(error, expected)


def test_background_check_cannot_overwrite_marker_for_edited_links(env, tmp_path, monkeypatch):
    original = question(env)
    bank = make_bank(tmp_path, original)
    source_reader = env.host.slide_source

    def edit_during_read(ref):
        bank.save(replace(original, sources=(), source_changed=True))
        return source_reader(ref)

    bank.set_source_changed(original.id, True)
    monkeypatch.setattr(env.host, "slide_source", edit_during_read)
    report, error = run_bank(env, bank)
    assert report is None and isinstance(error, ConflictError)
    assert bank.get(original.id).source_changed


def test_background_check_can_mark_concurrent_edits_retaining_same_links(env, tmp_path, monkeypatch):
    original = question(env)
    bank = make_bank(tmp_path, original)
    source_reader = env.host.slide_source

    def edit_during_read(ref):
        bank.save(replace(original, stem="Edited stem"))
        return source_reader(ref)

    env.host.sources[env.ref] = replace(env.source, text="Changed source")
    monkeypatch.setattr(env.host, "slide_source", edit_during_read)
    report, error = run_bank(env, bank)
    assert error is None and report.items[0].item.revision == 2
    assert bank.get(original.id).source_changed and bank.get(original.id).stem == "Edited stem"


def test_source_check_guard_applies_even_when_marker_would_be_unchanged(env, tmp_path):
    item = question(env)
    bank = make_bank(tmp_path, item)
    bank.save(replace(item, sources=()))
    with pytest.raises(ConflictError, match="source links"):
        bank.set_source_changed(item.id, False, expected_sources=item.sources)
