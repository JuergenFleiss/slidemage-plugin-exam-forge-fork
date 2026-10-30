# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exam Forge registers without constructing a window or reading a bank."""
from unittest.mock import create_autospec

import pytest
from fake_host import FakeDeck, FakeHost
from slidemage.plugin_api import Host

import exam_forge


@pytest.fixture
def host():
    fake = FakeHost()
    api_host = create_autospec(Host, instance=True, spec_set=True)
    for method in ("add_command", "add_menu_entry", "add_sidebar_box", "show_message"):
        getattr(api_host, method).side_effect = getattr(fake, method)
    exam_forge.activate(api_host)
    fake.finish_activation()
    return fake


def test_registers_command_deck_menu_and_compact_sidebar(host):
    assert {id: command.title for id, command in host.commands.items()} == {"exam-forge.open": "Exam Forge…"}
    assert host.menus == {"deck": ["exam-forge.open"]}
    assert host.boxes["bank"][0] == "Exam Forge"
    assert host.boxes["bank"][2] == "compact"
    assert host.messages == []


def test_command_requires_a_deck(host, tmp_path):
    command = host.commands["exam-forge.open"]
    assert command.enabled(FakeDeck("demo", tmp_path / "demo.tex")) is True
    assert command.enabled(None) is False
    with pytest.raises(RuntimeError, match="is not enabled"):
        host.run_command("exam-forge.open", None)
