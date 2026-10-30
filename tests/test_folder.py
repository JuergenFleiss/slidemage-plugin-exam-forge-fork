# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""First-use selection and the privacy warnings."""
import os
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMessageBox

from exam_forge.bank import CorruptFileError
from exam_forge.folder import folder_warnings, remembered_folder
from exam_forge.ui.first_use import choose_bank
from fake_host import FakeDeck, FakeHost


class Settings:
    def __init__(self):
        self.data = {}
        self.saves = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        self.saves += 1


@pytest.fixture
def setup(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    host = FakeHost(project)
    settings = Settings()
    host.settings = lambda: settings
    deck = FakeDeck("deck", project / "deck.tex")
    return host, settings, deck, project


def select(monkeypatch, path, answer=QMessageBox.StandardButton.Yes):
    monkeypatch.setattr("exam_forge.ui.first_use.QFileDialog.getExistingDirectory", lambda *a: str(path))
    calls = []
    def warning(*args):
        calls.append(args)
        return answer
    monkeypatch.setattr("exam_forge.ui.first_use.QMessageBox.warning", warning)
    return calls


def test_cancel_does_not_store_anything(setup, monkeypatch):
    host, settings, deck, project = setup
    select(monkeypatch, "")
    assert choose_bank(host, deck) is None
    assert settings.data == {} and settings.saves == 0
    assert not (project / "bank.json").exists()


def test_output_and_git_warnings_require_confirmation(setup, monkeypatch):
    host, settings, deck, project = setup
    folder = project / "pdf" / "bank"
    folder.mkdir(parents=True)
    warnings = folder_warnings(host, deck, folder)
    assert len(warnings) == 2
    calls = select(monkeypatch, folder, QMessageBox.StandardButton.No)
    assert choose_bank(host, deck) is None
    assert len(calls) == 1 and "output" in calls[0][2] and "Git" in calls[0][2]
    assert settings.data == {} and not (folder / "bank.json").exists()
    calls = select(monkeypatch, folder)
    bank = choose_bank(host, deck)
    assert bank.folder == folder and len(calls) == 1 and settings.saves == 1


def test_git_ignore_and_all_output_views(setup):
    host, _, deck, project = setup
    (project / ".gitignore").write_text("private/\n")
    folder = project / "private" / "bank"
    folder.mkdir(parents=True)
    assert folder_warnings(host, deck, folder) == []
    host.output_folders[(deck.path, "after-course")] = folder
    warnings = folder_warnings(host, deck, folder)
    assert len(warnings) == 1 and "output" in warnings[0]
    assert "Git" in folder_warnings(host, deck, project)[0]


def test_moved_deck_and_missing_folder_ask_again(setup, monkeypatch, tmp_path):
    host, settings, deck, project = setup
    folder = tmp_path / "private"
    folder.mkdir()
    select(monkeypatch, folder)
    bank = choose_bank(host, deck)
    select(monkeypatch, "")
    moved = FakeDeck("deck", project / "moved.tex")
    assert remembered_folder(host, moved) is None
    assert choose_bank(host, moved) is None
    assert choose_bank(host, deck).id == bank.id
    (folder / "bank.json").unlink()
    with pytest.raises(CorruptFileError):
        choose_bank(host, deck)
    (folder / ".bank.lock").unlink()
    folder.rmdir()
    assert choose_bank(host, deck) is None
    assert settings.saves == 1
    assert not (project / "bank.json").exists()


def test_each_warning_can_independently_block_selection(setup, monkeypatch, tmp_path):
    host, settings, deck, project = setup
    unignored = project / "unignored"
    unignored.mkdir()
    calls = select(monkeypatch, unignored, QMessageBox.StandardButton.No)
    assert choose_bank(host, deck) is None
    assert len(calls) == 1 and "Git" in calls[0][2] and "output" not in calls[0][2]
    output = tmp_path / "output"
    output.mkdir()
    host.output_folders[(deck.path, "instructor")] = output
    calls = select(monkeypatch, output, QMessageBox.StandardButton.No)
    assert choose_bank(host, deck) is None
    assert len(calls) == 1 and "output" in calls[0][2] and "Git" not in calls[0][2]
    assert settings.data == {} and not (unignored / "bank.json").exists()
    assert not (output / "bank.json").exists()


def test_symlink_to_output_is_detected(setup, tmp_path):
    host, _, deck, project = setup
    output = project / "pdf"
    output.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(output, target_is_directory=True)
    assert any("output" in warning for warning in folder_warnings(host, deck, alias))
