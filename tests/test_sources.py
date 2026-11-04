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
from exam_forge.sources import check_bank, check_item, fingerprint, link_slide, rebaseline_links


class BackgroundHost(FakeHost):
    """Queues work; complete() runs it on a real worker and calls done on this thread. Records every read."""

    def __init__(self, root):
        super().__init__(root)
        self.pending, self.deck_reads, self.source_reads = [], [], []

    def run_in_background(self, work, done):
        self.pending.append((work, done))

    def complete(self):
        work, done = self.pending.pop(0)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(work)
            error = future.exception()
        done(None if error else future.result(), error)

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
        MediaDescription(image="media/plot.png", short="A plot", long="Values", takeaway="Rising"),))
    ref = SlideRef(id="a", id_slug="a-EN-title", path=tmp_path / "a-EN-title.tex",
                   language="en", language_read="en", version="1-0-0", position=1)
    host.slides[(deck.path, "instructor", "en")] = [ref]
    host.sources[ref] = source
    return SimpleNamespace(host=host, deck=deck, ref=ref, source=source, tmp_path=tmp_path)


def question(env, item_id="q1", **changes):
    return Item(item_id, 1, sources=(link_slide(env.host, env.ref, "instructor"),), **changes)


def make_bank(env, *items):
    bank = Bank(env.tmp_path / "bank", create=True)
    for item in items:
        bank.create(item)
    return bank


def status(env, item):
    return check_item(env.host, env.deck, item).sources[0].status


def run_bank(env, bank, **kwargs):
    completed = []
    check_bank(env.host, env.deck, bank, lambda result, error: completed.append((result, error)), **kwargs)
    assert not completed
    env.host.complete()
    return completed[0]


def deck(env, *refs):
    env.host.slides[(env.deck.path, "instructor", "en")] = list(refs)


def test_a_link_captures_the_slide_once_and_matches_it(env):
    item = question(env)
    link = item.sources[0]
    assert (link.slide_id, link.version, link.language, link.view) == ("a", "1-0-0", "en", "instructor")
    assert link.text == env.source.text and link.fingerprint == fingerprint(env.source)
    assert len(env.host.source_reads) == 1
    assert status(env, item) == "unchanged" and not check_item(env.host, env.deck, item).source_changed


def test_overwritten_text_in_a_pinned_version_is_a_change(env):
    env.ref = replace(env.ref, pinned=True, path=env.ref.path.parent / "versions" / "a-v1-0-0.tex")
    deck(env, env.ref)
    env.host.sources[env.ref] = env.source
    item = question(env)
    env.host.sources[env.ref] = replace(env.source, text="Overwritten source")  # Same ID, version and path.
    assert status(env, item) == "changed"


def test_media_bytes_change_even_at_same_size_and_mtime(env):
    item = question(env)
    media = env.source.media[0]
    stat = media.stat()
    media.write_bytes(b"x" * stat.st_size)
    os.utime(media, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert status(env, item) == "changed"


@pytest.mark.parametrize("field,value", [("image", "tikz:drawing"), ("decorative", True), ("short", "New short"),
                                         ("long", "New long"), ("takeaway", "New takeaway")])
def test_every_description_field_is_checked(env, field, value):
    item = question(env)
    description = replace(env.source.descriptions[0], **{field: value})
    env.host.sources[env.ref] = replace(env.source, descriptions=(description,))
    assert status(env, item) == "changed"


def test_the_fingerprint_is_stable_and_keeps_every_boundary(env, tmp_path):
    copied = tmp_path / "other-root" / "plot.png"
    copied.parent.mkdir()
    copied.write_bytes(env.source.media[0].read_bytes())
    assert fingerprint(env.source) == fingerprint(replace(env.source, media=(copied,)))
    # A fixed value guards saved baselines across releases and processes.
    assert fingerprint(env.source) == "24691125f7ddfb325613aaaaa684b43f4ff0c2a92d447bfd738bba12310f81ed"
    one, two, combined = (tmp_path / name for name in ("one", "two", "combined"))
    one.write_bytes(b"ab")
    two.write_bytes(b"c")
    combined.write_bytes(b"abc")
    assert fingerprint(SlideSource("", (one, two))) != fingerprint(SlideSource("", (combined,)))
    assert fingerprint(SlideSource("", (one, two))) != fingerprint(SlideSource("", (two, one)))
    for a, b in (("a\r\n", "a\n"), ("é", "é"), ("a\x00b", "ab")):
        assert fingerprint(SlideSource(a)) != fingerprint(SlideSource(b))


@pytest.mark.parametrize("text,expected", [("Grüße α\r\n", "unchanged"), ("new", "changed")])
def test_the_check_follows_the_deck_and_compares_content_not_the_version_label(env, text, expected):
    item = question(env)
    new_ref = replace(env.ref, version="2-0-0", path=env.ref.path.with_name("a-v2.tex"))
    deck(env, new_ref)
    env.host.sources[new_ref] = replace(env.source, text=text)
    assert status(env, item) == expected and env.host.source_reads[-1][1] == new_ref


def test_repeated_slides_disambiguate_by_saved_version_and_never_guess(env):
    item = question(env)
    second = replace(env.ref, path=env.ref.path.with_name("a-v2.tex"), version="2-0-0", position=2)
    env.host.sources[second] = replace(env.source, text="Another version")
    deck(env, second, env.ref)
    assert status(env, item) == "unchanged"
    deck(env, env.ref, replace(env.ref, position=2, hidden=True))  # Repeated calls to the same file are harmless.
    assert status(env, item) == "unchanged"
    deck(env, second, replace(second, version="3-0-0", path=env.ref.path.with_name("a-v3.tex")))
    result = check_item(env.host, env.deck, item)
    assert result.sources[0].status == "error" and "ambiguous" in result.sources[0].message and result.source_changed


def test_reading_language_and_view_survive_fallback_and_multilingual_files(env):
    for file_language in ("de", "mult", None):
        ref = replace(env.ref, language=file_language)
        env.host.sources[ref] = env.source
        env.host.slides[(env.deck.path, "after-course", "en")] = [ref]
        link = link_slide(env.host, ref, "after-course")
        assert link.language == "en"
        assert check_item(env.host, env.deck, Item("q", 1, sources=(link,))).sources[0].status == "unchanged"
        assert env.host.deck_reads[-1][1:] == ("after-course", "en")
    french = replace(env.ref, language="fr", language_read="fr", version=None)
    env.host.sources[french] = env.source
    assert (link_slide(env.host, french, "instructor").language, link_slide(env.host, french, "instructor").version) \
        == ("fr", "")
    with pytest.raises(ValueError, match="reading language"):
        link_slide(env.host, replace(env.ref, language="mult", language_read=None), "instructor")
    with pytest.raises(ValueError, match="view"):
        link_slide(env.host, env.ref, "unknown")


def test_a_removed_slide_is_missing_and_never_read_from_the_old_file(env):
    item = question(env)
    bank = make_bank(env, item)
    deck(env)
    env.host.source_reads.clear()
    report, error = run_bank(env, bank)
    result = report.items[0]
    assert error is None and result.sources[0].status == "missing" and result.sources[0].fingerprint is None
    assert bank.get(item.id).source_changed and not env.host.source_reads


@pytest.mark.parametrize("failure", ["source", "media", "deck", "unresolved deck"])
def test_read_failures_are_reported_and_set_the_marker(env, monkeypatch, failure):
    item = question(env)
    bank = make_bank(env, item)
    problem = (ValueError("The deck's slides cannot be worked out: custom command") if failure == "unresolved deck"
               else OSError("unreadable file"))

    def unreadable(*args):
        raise problem

    if failure == "media":
        env.source.media[0].unlink()
    else:
        monkeypatch.setattr(env.host, "slide_source" if failure == "source" else "deck_slides", unreadable)
    report, error = run_bank(env, bank)
    source = report.items[0].sources[0]
    assert error is None and source.status == "error" and source.fingerprint is None
    assert (failure == "media" and source.message) or str(problem) in source.message
    assert bank.get(item.id).source_changed


def test_the_bank_check_runs_on_the_worker_and_sets_the_marker_without_a_revision(env, monkeypatch):
    item = question(env)
    bank = make_bank(env, item)
    reviewed = bank.set_status(item.id, 1, "reviewed")
    before = json.loads((bank.folder / "q1.json").read_text())["revisions"]
    env.host.sources[env.ref] = replace(env.source, text="New source")
    env.host.source_reads.clear()
    flag_threads, setter = [], bank.set_source_changed
    monkeypatch.setattr(bank, "set_source_changed", lambda *a, **k: flag_threads.append(get_ident()) or setter(*a, **k))
    report, error = run_bank(env, bank)
    assert error is None and not report.errors
    assert env.host.source_reads[0][0] != get_ident() and flag_threads[0] != get_ident()
    saved = Bank(bank.folder).get(item.id)
    assert saved == replace(reviewed, source_changed=True) and report.items[0].item == saved
    assert json.loads((bank.folder / "q1.json").read_text())["revisions"] == before  # No new baseline either.


def test_a_restored_source_clears_the_marker_and_a_check_can_take_a_subset(env):
    item = question(env)
    bank = make_bank(env, item, question(env, "q2", source_changed=True))
    env.host.sources[env.ref] = replace(env.source, text="Changed")
    first, _ = run_bank(env, bank, item_ids=[item.id, item.id])
    assert len(first.items) == 1 and first.items[0].source_changed
    env.host.sources[env.ref] = env.source
    run_bank(env, bank, item_ids=[item.id])
    assert not bank.get(item.id).source_changed and bank.get("q2").source_changed
    assert bank.get(item.id).revision == 1


def test_one_missing_link_flags_the_item_and_no_links_clear_an_old_marker(env):
    item = question(env)
    bank = make_bank(env, replace(item, sources=(*item.sources, replace(item.sources[0], slide_id="removed"))),
                     Item("unlinked", 1, source_changed=True))
    report, _ = run_bank(env, bank)
    assert [source.status for source in report.items[0].sources] == ["unchanged", "missing"]
    assert bank.get(item.id).source_changed and not bank.get("unlinked").source_changed


def test_corrupt_items_are_reported_while_the_others_are_checked(env):
    bank = make_bank(env, question(env), question(env, "broken"))
    (bank.folder / "broken.json").write_text("{broken", encoding="utf-8")
    report, error = run_bank(env, bank)
    assert error is None and [row.item.id for row in report.items] == ["q1"]
    assert len(report.errors) == 1 and isinstance(report.errors[0], CorruptFileError)


@pytest.mark.parametrize("failure", ["new_schema", "write", "unexpected"])
def test_worker_errors_reach_the_callback(env, monkeypatch, failure):
    bank = make_bank(env, question(env))
    if failure == "new_schema":
        path = bank.folder / "q1.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "schema_version": 999}))
        expected = SchemaError
    else:
        expected = OSError if failure == "write" else RuntimeError

        def fail(*args, **kwargs):
            raise expected("failed")

        monkeypatch.setattr(bank if failure == "write" else env.host,
                            "set_source_changed" if failure == "write" else "slide_source", fail)
    report, error = run_bank(env, bank)
    assert report is None and isinstance(error, expected)


@pytest.mark.parametrize("edit", ["links", "stem"])
def test_an_edit_during_the_check_is_never_overwritten(env, monkeypatch, edit):
    original = question(env)
    bank = make_bank(env, original)
    read = env.host.slide_source
    if edit == "links":
        bank.set_source_changed(original.id, True)
        change = replace(original, sources=(), source_changed=True)
    else:
        env.host.sources[env.ref] = replace(env.source, text="Changed source")
        change = replace(original, stem="Edited stem")
    monkeypatch.setattr(env.host, "slide_source", lambda ref: bank.save(change) and read(ref))
    report, error = run_bank(env, bank)
    if edit == "links":  # The check cannot clear a marker for links it did not read.
        assert report is None and isinstance(error, ConflictError) and bank.get(original.id).source_changed
    else:  # The same links: the check may mark the edited revision.
        assert error is None and report.items[0].item.revision == 2
        assert bank.get(original.id).source_changed and bank.get(original.id).stem == "Edited stem"


def test_rebaseline_follows_the_current_slide_and_refuses_missing_or_ambiguous(env):
    item = question(env)
    new_ref = replace(env.ref, version="2-0-0", path=env.ref.path.with_name("a-v2.tex"))
    env.host.sources[new_ref] = replace(env.source, text="Updated")
    deck(env, new_ref)
    (link,) = rebaseline_links(env.host, env.deck, item)
    assert (link.version, link.text, link.fingerprint) == ("2-0-0", "Updated", fingerprint(env.host.sources[new_ref]))
    assert status(env, replace(item, sources=(link,))) == "unchanged"
    deck(env, env.ref, new_ref)
    assert rebaseline_links(env.host, env.deck, item)[0].text == env.source.text  # The saved version decides.
    deck(env, replace(env.ref, version="3"), new_ref)
    with pytest.raises(ValueError, match="ambiguous"):
        rebaseline_links(env.host, env.deck, item)
    deck(env)
    with pytest.raises(ValueError, match="missing"):
        rebaseline_links(env.host, env.deck, item)
