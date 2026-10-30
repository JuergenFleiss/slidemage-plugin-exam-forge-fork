# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Changelog validation and the release notes generated from it."""
import subprocess
import sys
from pathlib import Path

import pytest

import build
import release_notes


FIRST = """\
# History

v0.1.0 (2026-10-27, first release): Technical details.

NEW: The first version.
- Say hello.
"""
SECOND = """\

v0.2.0 (2026-10-28, feature): More technical details.

NEW: A better greeting.
- Add a greeting
  for everyone.
- Fix the message.
"""
MAJOR = """\

v1.0.0 (2026-10-29, incompatible change): Technical details.

NEW: A new version.
- Change greetings.

BREAKING: Old settings no longer work.
  Choose new settings.
"""


def test_notes_cover_intermediate_entries_and_breaking_changes():
    entries, errors = release_notes.check_history(FIRST + SECOND + MAJOR, "1.0.0")
    assert errors == []
    assert release_notes.render(entries, "v1.0.0", "v0.1.0", "owner/plugin") == (
        "A new version.\n\n*This release covers v0.2.0 and v1.0.0.*\n\n"
        "- Change greetings.\n- Add a greeting for everyone.\n- Fix the message.\n\n"
        "## Breaking changes\n\n- **v1.0.0:** Old settings no longer work. Choose new settings.\n\n"
        "## Full changelog\n\nhttps://github.com/owner/plugin/compare/v0.1.0...v1.0.0\n"
    )
    assert release_notes.render(entries, "v1.0.0") == (
        "A new version.\n\n- Change greetings.\n\n"
        "## Breaking changes\n\n- Old settings no longer work. Choose new settings.\n"
    )


@pytest.mark.parametrize("history, version, expected", [
    ("# No entries\n", "0.1.0", "has no entries"),
    (FIRST.replace("(2026-10-27, first release)", "(2026-10-27)"), "0.1.0", "date and reason"),
    (FIRST.replace("v0.1.0 (", "v0.1 ("), "0.1.0", "invalid entry"),
    (FIRST.replace("NEW:", "NEWS:"), "0.1.0", "expected one NEW:"),
    (FIRST.replace("- Say hello.", ""), "0.1.0", "at least one nonempty"),
    (FIRST.replace("- Say hello.", "- "), "0.1.0", "at least one nonempty"),
    (FIRST + SECOND, "0.1.0", "does not match newest"),
    (SECOND + FIRST, "0.1.0", "entries oldest first"),
    (FIRST + FIRST, "0.1.0", "entries oldest first"),
    (FIRST + MAJOR.replace("BREAKING:", "NOTICE:"), "1.0.0", "no nonempty BREAKING:"),
    (FIRST + MAJOR.replace("BREAKING: Old settings no longer work.\n  Choose new settings.", "BREAKING:"), "1.0.0", "no nonempty BREAKING:"),
])
def test_invalid_history(history, version, expected):
    _, errors = release_notes.check_history(history, version)
    assert any(expected in error for error in errors), errors


def test_build_check_enforces_history(tmp_path, monkeypatch):
    (tmp_path / "CHANGELOG.md").write_text(FIRST + SECOND, encoding="utf-8")
    (tmp_path / "README.md").write_text("Test", encoding="utf-8")
    (tmp_path / "LICENSE").write_text("Test", encoding="utf-8")
    monkeypatch.setattr(build, "ROOT", tmp_path)
    errors = build.check({"version": "0.1.0"})
    assert any("does not match newest" in error for error in errors)


def test_cli_release_notes_and_validation(tmp_path):
    script = Path(release_notes.__file__)
    # Copy the self-contained script to a plugin checkout, with no SlideMage dependency.
    (tmp_path / "release_notes.py").write_bytes(script.read_bytes())
    (tmp_path / "plugin.toml").write_text('version = "0.2.0"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(FIRST + SECOND, encoding="utf-8")
    cmd = [sys.executable, str(tmp_path / "release_notes.py"), "v0.2.0", "--previous", "v0.1.0"]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "A better greeting.\n\n- Add a greeting for everyone.\n- Fix the message.\n"
    result = subprocess.run([sys.executable, str(tmp_path / "release_notes.py"), "v0.1.0"], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode != 0
    assert "does not match" in result.stderr


def test_built_plugin_contains_changelog(tmp_path, monkeypatch):
    import zipfile

    (tmp_path / "plugin.toml").write_text('id = "exam-forge"\nversion = "0.1.0"\n', encoding="utf-8")
    (tmp_path / "LICENSE").write_text("license", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(FIRST, encoding="utf-8")
    (tmp_path / "exam_forge").mkdir()
    (tmp_path / "exam_forge" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(build, "ROOT", tmp_path)
    build.build({"id": "exam-forge", "version": "0.1.0"})
    with zipfile.ZipFile(tmp_path / "dist" / "exam-forge-0.1.0.smplugin") as archive:
        assert archive.read("CHANGELOG.md").decode("utf-8") == FIRST
