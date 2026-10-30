# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exam Forge's API 1 contributions and callbacks against a FakeHost."""
from unittest.mock import create_autospec

import pytest

pytest.importorskip("slidemage.plugin_api", reason="SlideMage's plugin API is not installed")

import exam_forge
from fake_host import FakeDeck, FakeHost
from slidemage.plugin_api import Host


@pytest.fixture
def host():
    fake = FakeHost()
    api_host = create_autospec(Host, instance=True, spec_set=True)
    for method in ("add_command", "add_menu_entry", "show_message"):
        getattr(api_host, method).side_effect = getattr(fake, method)
    exam_forge.activate(api_host)
    fake.finish_activation()
    return fake


def test_registers_command_and_deck_menu(host):
    assert {id: command.title for id, command in host.commands.items()} == {"exam-forge.open": "Exam Forge…"}
    assert host.menus == {"deck": ["exam-forge.open"]}
    assert host.messages == []


def test_command_requires_a_deck_and_shows_placeholder(host, tmp_path):
    command = host.commands["exam-forge.open"]
    deck = FakeDeck("demo", tmp_path / "demo.tex")
    assert command.enabled(deck) is True
    assert command.enabled(None) is False
    host.run_command("exam-forge.open", deck)
    assert host.messages == [("Exam Forge is not built yet.", False)]
    with pytest.raises(RuntimeError, match="is not enabled"):
        host.run_command("exam-forge.open", None)
