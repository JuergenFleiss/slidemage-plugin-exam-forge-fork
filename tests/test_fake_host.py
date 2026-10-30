# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The fake exposes configurable slide data and records open requests."""
from unittest.mock import create_autospec

import pytest

from fake_host import FakeDeck, FakeHost
from slidemage.plugin_api import Host, SlideRef, SlideSource


def test_slide_calls(tmp_path):
    fake = FakeHost(tmp_path)
    host = create_autospec(Host, instance=True, spec_set=True)
    for method in ("project_root", "deck_output_folder", "deck_slides", "slide_source", "open_slide", "open_file"):
        getattr(host, method).side_effect = getattr(fake, method)
    deck = FakeDeck("deck", tmp_path / "deck.tex")
    path = tmp_path / "slide.tex"
    path.write_text("slide")
    ref = SlideRef(id="a", id_slug="a-EN-slide", path=path, language="en", language_read="en")
    source = SlideSource(text="slide")
    fake.slides[(deck.path, "instructor", "en")] = [ref]
    fake.sources[ref] = source
    assert host.project_root() == tmp_path
    assert host.deck_output_folder(deck) == tmp_path / "pdf"
    assert host.deck_slides(deck, "instructor", "en") == [ref]
    assert host.slide_source(ref) == source
    host.open_slide(ref)
    host.open_file(path.name)
    assert fake.opened_slides == [ref]
    assert fake.opened_files == [path]
    with pytest.raises(FileNotFoundError):
        host.open_file("missing")
