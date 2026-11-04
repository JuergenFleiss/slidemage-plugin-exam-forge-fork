# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""First-use selection and the privacy warnings."""
import subprocess

import pytest
from fake_host import FakeDeck, FakeHost
from PySide6.QtWidgets import QMessageBox

from exam_forge.folder import folder_warnings, remembered_folder
from exam_forge.ui.first_use import choose_bank


@pytest.fixture
def setup(app, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    host = FakeHost(project)
    return host, host.settings(), FakeDeck("deck", project / "deck.tex"), project


def select(monkeypatch, path, answer=QMessageBox.StandardButton.Yes):
    monkeypatch.setattr("exam_forge.ui.first_use.QFileDialog.getExistingDirectory", lambda *a: str(path))
    calls = []
    monkeypatch.setattr("exam_forge.ui.first_use.QMessageBox.warning", lambda *a: calls.append(a[2]) or answer)
    return calls


def test_cancel_stores_nothing(setup, monkeypatch):
    host, settings, deck, project = setup
    select(monkeypatch, "")
    assert choose_bank(host, deck) is None and settings.data == {} and settings.saves == 0
    assert not (project / "bank.json").exists()


def test_each_warning_needs_confirmation_and_no_creates_nothing(setup, monkeypatch, tmp_path):
    host, settings, deck, project = setup
    output = tmp_path / "output"
    output.mkdir()
    host.output_folders[(deck.path, "after-course")] = output
    # project/pdf is the deck's default output folder.
    for folder, says in ((project / "unignored", {"Git"}), (output, {"output"}), (project / "pdf" / "bank", {"Git", "output"})):
        folder.mkdir(parents=True, exist_ok=True)
        calls = select(monkeypatch, folder, QMessageBox.StandardButton.No)
        assert choose_bank(host, deck) is None and len(calls) == 1
        assert {word for word in ("Git", "output") if word in calls[0]} == says
        assert not (folder / "bank.json").exists()
    assert settings.data == {}
    select(monkeypatch, project / "pdf" / "bank")
    assert choose_bank(host, deck).folder == project / "pdf" / "bank" and settings.saves == 1


def test_git_ignored_folders_and_symlinks_to_output(setup, tmp_path):
    host, _, deck, project = setup
    (project / ".gitignore").write_text("private/\n")
    folder = project / "private" / "bank"
    folder.mkdir(parents=True)
    assert folder_warnings(host, deck, folder) == []
    (project / "pdf").mkdir()
    (tmp_path / "alias").symlink_to(project / "pdf", target_is_directory=True)
    assert any("output" in warning for warning in folder_warnings(host, deck, tmp_path / "alias"))


def test_a_moved_deck_a_missing_folder_or_a_risky_one_asks_again(setup, monkeypatch, tmp_path):
    host, settings, deck, project = setup
    folder = tmp_path / "private"
    folder.mkdir()
    select(monkeypatch, folder)
    bank = choose_bank(host, deck)
    select(monkeypatch, "")
    assert remembered_folder(host, FakeDeck("deck", project / "moved.tex")) is None
    assert choose_bank(host, deck).id == bank.id
    (folder / "bank.json").unlink()
    assert choose_bank(host, deck) is None and settings.saves == 1
    risky = project / "bank"
    risky.mkdir()
    select(monkeypatch, risky)
    assert choose_bank(host, deck)
    calls = select(monkeypatch, "", QMessageBox.StandardButton.No)
    assert choose_bank(host, deck) is None and len(calls) == 1  # The remembered risky folder warns again.
